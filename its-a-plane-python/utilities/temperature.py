"""
Temperature and weather utilities for the plane tracker.

This module handles API calls to tomorrow.io for weather data.
Expected API call frequency:
- Current temperature/humidity: Every 5 minutes (cached)
- Weather forecast: Every hour (cached)

Rate limiting: Maximum 25 API calls per hour to comply with tomorrow.io limits.
"""

from datetime import datetime, timedelta
import requests as r
import pytz
import time
import json 
import logging

# Rate limiting for tomorrow.io API (25 calls per hour)
_last_api_call_time = None
_api_calls_this_hour = 0
_hour_start_time = None

# Caching for temperature data
_cached_temp_data = None
_last_temp_fetch_time = None
TEMP_CACHE_DURATION = 300  # 5 minutes in seconds

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
    
    # Check if we've hit the hourly limit (25 calls per hour)
    if _api_calls_this_hour >= 25:
        time_until_reset = 3600 - (now - _hour_start_time).total_seconds()
        logging.warning(f"Rate limit reached (25 calls/hour). Resets in {time_until_reset:.0f} seconds.")
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

        except (r.exceptions.RequestException, ValueError) as e:
            logging.error(f"Request failed. Error: {e}")
            retries += 1
            if retries < max_retries:
                logging.info(f"Retrying in {delay} seconds...")
                time.sleep(delay)

    # Return cached data if available, otherwise None
    return _cached_temp_data if _cached_temp_data else (None, None)


def grab_forecast(delay=2, max_retries=3):
    retries = 0
    while retries < max_retries:
        try:
            # Check rate limit before making API call
            if not _check_rate_limit():
                logging.warning("Rate limit reached, returning cached data if available")
                return None
            
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
                }
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

            return forecast

        except (r.exceptions.RequestException, KeyError) as e:
            logging.error(f"Request failed. Error: {e}")
            retries += 1
            if retries < max_retries:
                logging.info(f"Retrying in {delay} seconds... (attempt {retries}/{max_retries})")
                time.sleep(delay)
            else:
                logging.error(f"Max retries ({max_retries}) reached. Giving up.")
    
    return None
    
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
