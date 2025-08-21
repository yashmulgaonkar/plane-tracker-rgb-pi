from datetime import datetime
from rgbmatrix import graphics
from utilities.animator import Animator
from setup import colours, fonts, frames
from utilities.temperature import grab_temperature_and_humidity
from config import NIGHT_START, NIGHT_END
from datetime import datetime, timedelta
import requests as r
import logging
import time

from config import TOMORROW_API_KEY, TEMPERATURE_UNITS, FORECAST_DAYS, TEMPERATURE_LOCATION

TOMORROW_API_URL = "https://api.tomorrow.io/v4/"
CACHE_DURATION = timedelta(hours=1)  # <-- Cache duration
_cached_forecast = None
_last_forecast_time = None

class TemperatureScene(object):
    def __init__(self):
        super().__init__()
        self._last_temperature = None
        self._last_temperature_str = None
        self._last_updated = None
        self._cached_temp = None
        self._cached_humidity = None
        self._redraw_temp = True

    def colour_gradient(self, colour_A, colour_B, ratio):
        return graphics.Color(
            colour_A.red + ((colour_B.red - colour_A.red) * ratio),
            colour_A.green + ((colour_B.green - colour_A.green) * ratio),
            colour_A.blue + ((colour_B.blue - colour_A.blue) * ratio),
            )

        return graphics.Color(int(r), int(g), int(b))

def grab_forecast(delay=2):
    global _cached_forecast, _last_forecast_time

    now = datetime.utcnow()

    if _cached_forecast and _last_forecast_time and (now - _last_forecast_time) < CACHE_DURATION:
        return _cached_forecast  # Return cached data

    try:
        dt = now + timedelta(hours=6)
        print(f"[{datetime.now()}] Calling Tomorrow.io API: {TOMORROW_API_URL}/timelines")  # <-- ADDED
        
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
                "timesteps": ["1d"],
                "startTime": dt.isoformat(),
                "endTime": (dt + timedelta(days=int(FORECAST_DAYS))).isoformat()
            },
            timeout=10
        )
        resp.raise_for_status()

        data = resp.json().get("data", {})
        timelines = data.get("timelines", [])

        if not timelines:
            raise KeyError("Timelines not found in response.")

        forecast = timelines[0].get("intervals", [])
        if not forecast:
            raise KeyError("Forecast intervals not found.")

        _cached_forecast = forecast
        _last_forecast_time = now
        return forecast

    except (r.exceptions.RequestException, KeyError) as e:
        logging.error(f"Forecast request failed: {e}")
        logging.info(f"Retrying in {delay} seconds...")
        time.sleep(delay)
        return _cached_forecast  # Return cached data even if outdated

