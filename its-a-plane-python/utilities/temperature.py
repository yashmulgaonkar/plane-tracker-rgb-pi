"""
Temperature and weather utilities for the plane tracker.

This module handles API calls to tomorrow.io for weather data.
Expected API call frequency:
- Current temperature/humidity: Every 5 minutes (cached) -> 12/hour
- Weather forecast: Every hour (cached)                  -> 1/hour
Total: ~13 calls/hour, comfortably under the free-tier hourly cap.

Tomorrow.io free-tier limits (as of 2026):
    3 requests / second
    25 requests / hour
    500 requests / day

Caching is done at this module level (not per-scene) so that the multiple
scenes which call `grab_forecast()` (clock, date, daysforecast) share one
cached response and we hit the API at most once per `FORECAST_CACHE_DURATION`.
"""

from datetime import datetime, timedelta
from threading import Lock
import requests as r
import pytz
import time
import json
import logging

# Rate limiting for tomorrow.io API (25 calls per hour)
_last_api_call_time = None
_api_calls_this_hour = 0
_hour_start_time = None

# Caching for current temperature data
_cached_temp_data = None
_last_temp_fetch_time = None
TEMP_CACHE_DURATION = 300  # 5 minutes in seconds

# Caching for forecast data. The forecast only changes meaningfully on the
# scale of hours, and several scenes call grab_forecast() independently, so
# we cache the result here and hand the same payload to all callers.
_cached_forecast = None
_last_forecast_fetch_time = None
FORECAST_CACHE_DURATION = 3600  # 1 hour in seconds
_forecast_lock = Lock()

# Tomorrow.io's free tier is 25 requests/hour and 500 requests/day. The
# hourly counter resets at the top of every wall-clock hour, so when we get
# a 429 we back off only until the next hour boundary (plus a tiny buffer
# for clock skew). With our caching budget of ~13 calls/hour we should
# never legitimately hit the daily 500-request cap.
_rate_limited_until = None
_RATE_LIMIT_BUFFER_SECONDS = 30


def _next_hour_boundary(after):
    """Return the first datetime > `after` aligned to a wall-clock hour."""
    return (after + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)


def _is_rate_limited():
    return (
        _rate_limited_until is not None
        and datetime.now() < _rate_limited_until
    )


def _mark_rate_limited():
    global _rate_limited_until
    now = datetime.now()
    _rate_limited_until = _next_hour_boundary(now) + timedelta(
        seconds=_RATE_LIMIT_BUFFER_SECONDS
    )
    wait_seconds = (_rate_limited_until - now).total_seconds()
    logging.warning(
        "Tomorrow.io returned 429 (rate limited). Backing off until %s "
        "(~%d minutes; resets at the top of the hour).",
        _rate_limited_until.strftime("%H:%M:%S"),
        max(1, int(wait_seconds // 60)),
    )


def _is_429(exc):
    resp = getattr(exc, "response", None)
    return resp is not None and resp.status_code == 429

# Attempt to load config data
try:
    from config import TOMORROW_API_KEY
    from config import TEMPERATURE_UNITS
    from config import FORECAST_DAYS

except (ModuleNotFoundError, NameError, ImportError):
    # If there's no config data
    TOMORROW_API_KEY = None
    TEMPERATURE_UNITS = "metric"
    FORECAST_DAYS = 3

if TEMPERATURE_UNITS != "metric" and TEMPERATURE_UNITS != "imperial":
    TEMPERATURE_UNITS = "metric"

from config import TEMPERATURE_LOCATION

# Weather API
TOMORROW_API_URL = "https://api.tomorrow.io/v4/"

def _check_rate_limit():
    """Check if we can make an API call based on rate limits"""
    global _last_api_call_time, _api_calls_this_hour, _hour_start_time
    
    now = datetime.now()
    
    # Reset counter if an hour has passed
    if _hour_start_time is None or (now - _hour_start_time).total_seconds() >= 3600:
        _hour_start_time = now
        _api_calls_this_hour = 0
    
    # Self-imposed cap well below tomorrow.io's 25-calls/hour ceiling.
    # Acts as a safety net behind the result caches.
    if _api_calls_this_hour >= 20:
        time_until_reset = 3600 - (now - _hour_start_time).total_seconds()
        logging.warning(f"Self-imposed rate limit reached (20/hour). Resets in {time_until_reset:.0f} seconds.")
        return False
    
    # Ensure minimum delay between calls (at least 1 second)
    if _last_api_call_time and (now - _last_api_call_time).total_seconds() < 1:
        time.sleep(1)
    
    _last_api_call_time = now
    _api_calls_this_hour += 1
    return True


def grab_temperature_and_humidity(delay=2, max_retries=1):
    global _cached_temp_data, _last_temp_fetch_time

    # Check cache first
    now = datetime.now()
    if (_cached_temp_data and _last_temp_fetch_time and
        (now - _last_temp_fetch_time).total_seconds() < TEMP_CACHE_DURATION):
        return _cached_temp_data

    # Honour the global 429 backoff window.
    if _is_rate_limited():
        return _cached_temp_data if _cached_temp_data else (None, None)

    current_temp, humidity = None, None
    retries = 0

    while retries < max_retries:
        try:
            # Check rate limit before making API call
            if not _check_rate_limit():
                logging.warning("Rate limit reached, returning cached data if available")
                return _cached_temp_data if _cached_temp_data else (None, None)

            print(f"[{datetime.now()}] Calling Tomorrow.io API: {TOMORROW_API_URL}/weather/realtime")  # Log API calls
            request = r.get(
                f"{TOMORROW_API_URL}/weather/realtime",
                params={
                    "location": TEMPERATURE_LOCATION,
                    "units": TEMPERATURE_UNITS,
                    "apikey": TOMORROW_API_KEY
                },
                timeout=10
            )
            request.raise_for_status()

            data = request.json().get("data", {}).get("values", {})
            current_temp = data.get("temperature")
            humidity = data.get("humidity")

            if current_temp is None:
                logging.warning("Temperature data missing, defaulting to 0.")
                current_temp = 0

            if humidity is None:
                logging.warning("Humidity data missing, defaulting to 0.")
                humidity = 0

            # Cache the result
            _cached_temp_data = (current_temp, humidity)
            _last_temp_fetch_time = now

            return current_temp, humidity

        except r.exceptions.HTTPError as e:
            if _is_429(e):
                _mark_rate_limited()
                return _cached_temp_data if _cached_temp_data else (None, None)
            logging.error(f"Request failed. Error: {e}")
            retries += 1
            if retries < max_retries:
                logging.info(f"Retrying in {delay} seconds...")
                time.sleep(delay)
        except (r.exceptions.RequestException, ValueError) as e:
            logging.error(f"Request failed. Error: {e}")
            retries += 1
            if retries < max_retries:
                logging.info(f"Retrying in {delay} seconds...")
                time.sleep(delay)

    # Return cached data if available, otherwise None
    return _cached_temp_data if _cached_temp_data else (None, None)


def grab_forecast(delay=2, max_retries=3):
    """Return the multi-day forecast, hitting tomorrow.io at most once per
    `FORECAST_CACHE_DURATION` seconds across all callers.

    On API failure we keep returning the previously cached forecast (if any)
    so callers that iterate the result (clock/date scenes) don't blow up.
    A 429 from tomorrow.io trips a 1-hour module-wide backoff so we don't
    burn quota while rate-limited.
    """
    global _cached_forecast, _last_forecast_fetch_time

    now = datetime.now()

    # Fast path: serve from cache without taking the lock.
    if (
        _cached_forecast is not None
        and _last_forecast_fetch_time is not None
        and (now - _last_forecast_fetch_time).total_seconds() < FORECAST_CACHE_DURATION
    ):
        return _cached_forecast

    # If we're inside the 429 backoff window, return whatever we have cached
    # without bothering the API.
    if _is_rate_limited():
        return _cached_forecast

    # Serialise concurrent refreshes so we don't stampede the API when
    # multiple scenes notice the cache is stale at the same time.
    with _forecast_lock:
        # Re-check inside the lock in case another thread just refreshed.
        now = datetime.now()
        if (
            _cached_forecast is not None
            and _last_forecast_fetch_time is not None
            and (now - _last_forecast_fetch_time).total_seconds() < FORECAST_CACHE_DURATION
        ):
            return _cached_forecast

        if _is_rate_limited():
            return _cached_forecast

        retries = 0
        while retries < max_retries:
            try:
                # Check rate limit before making API call
                if not _check_rate_limit():
                    logging.warning("Rate limit reached, returning cached data if available")
                    return _cached_forecast

                current_time = datetime.utcnow()
                dt = current_time + timedelta(hours=6)

                print(f"[{datetime.now()}] Calling Tomorrow.io API: {TOMORROW_API_URL}/timelines")  # Log API calls

                resp = r.post(
                    f"{TOMORROW_API_URL}/timelines",
                    headers={
                        "Accept-Encoding": "gzip",
                        "accept": "application/json",
                        "content-type": "application/json"
                    },
                    params={"apikey": TOMORROW_API_KEY},
                    json={
                        "location": TEMPERATURE_LOCATION,
                        "units": TEMPERATURE_UNITS,
                        "fields": [
                            "temperatureMin",
                            "temperatureMax",
                            "weatherCodeFullDay",
                            "sunriseTime",
                            "sunsetTime",
                            "moonPhase"
                        ],
                        "timesteps": [
                            "1d"
                        ],
                        "startTime": dt.isoformat(),
                        "endTime": (dt + timedelta(days=int(FORECAST_DAYS))).isoformat()
                    },
                    timeout=10,
                )
                resp.raise_for_status()  # Raise an exception for 4xx or 5xx status codes

                # Safely access the JSON response to avoid KeyError
                data = resp.json().get("data", {})
                timelines = data.get("timelines", [])

                if not timelines:
                    raise KeyError("Timelines not found in response.")

                forecast = timelines[0].get("intervals", [])

                if not forecast:
                    raise KeyError("Forecast intervals not found in timelines.")

                _cached_forecast = forecast
                _last_forecast_fetch_time = datetime.now()
                return forecast

            except r.exceptions.HTTPError as e:
                # 429 -> trip the global backoff and bail; retrying within
                # the same minute will only burn the daily quota.
                if _is_429(e):
                    _mark_rate_limited()
                    return _cached_forecast
                logging.error(f"Request failed. Error: {e}")
                retries += 1
                if retries < max_retries:
                    logging.info(f"Retrying in {delay} seconds... (attempt {retries}/{max_retries})")
                    time.sleep(delay)
                else:
                    logging.error(f"Max retries ({max_retries}) reached. Giving up.")
            except (r.exceptions.RequestException, KeyError) as e:
                logging.error(f"Request failed. Error: {e}")
                retries += 1
                if retries < max_retries:
                    logging.info(f"Retrying in {delay} seconds... (attempt {retries}/{max_retries})")
                    time.sleep(delay)
                else:
                    logging.error(f"Max retries ({max_retries}) reached. Giving up.")

        # All retries failed; fall back to the last good cached forecast (if
        # any). Returning None here would crash callers that iterate the
        # result without a None-check.
        return _cached_forecast
    
#forecast_data = grab_forecast()
#if forecast_data is not None:
#    print("Weather forecast:")
#    for interval in forecast_data:
#        temperature_min = interval["values"]["temperatureMin"]
#        temperature_max = interval["values"]["temperatureMax"]
#        weather_code_day = interval["values"]["weatherCodeFullDay"]
#        sunrise = interval["values"]["sunriseTime"]
#        sunset = interval["values"]["sunsetTime"]
#        moon_phase = interval["values"]["moonPhase"]
#        print(f"Date: {interval['startTime'][:10]}, Min Temp: {temperature_min}, Max Temp: {temperature_max}, Weather Code: {weather_code_day}, Sunrise: {sunrise}, Sunset: {sunset}, Moon Phase: {moon_phase}")
#else:
#    print("Failed to retrieve forecast.")
