# Copy this file to `config.py` and fill in your own values.
# `config.py` is gitignored so your API keys and personal location won't
# end up in commits.

# Bounding box used for OpenSky flight discovery. Top-left and bottom-right
# corners of the box you care about (in WGS84 decimal degrees).
# Use https://www.latlong.net/ or Google Maps to pick the corners. Bigger
# zone -> more planes. ~10 miles corner-to-corner is a reasonable start.
ZONE_HOME = {
    "tl_y":  51.520000,  # Top-Left Latitude  (north-west corner)
    "tl_x":  -0.150000,  # Top-Left Longitude
    "br_y":  51.490000,  # Bottom-Right Latitude (south-east corner)
    "br_x":  -0.080000,  # Bottom-Right Longitude
}

# Your home location. Used as the reference point for plane distance and
# bearing on the LED display.
LOCATION_HOME = [
    51.509865,   # Latitude  (deg)
    -0.118092,   # Longitude (deg)
]

# Used by the tomorrow.io weather lookups; usually the same as LOCATION_HOME.
TEMPERATURE_LOCATION = "51.509865,-0.118092"

# Get a key at https://app.tomorrow.io/development/keys (free tier: 25/hour,
# 500/day - the app caches calls and stays well under those limits).
TOMORROW_API_KEY = "your-tomorrow-io-api-key"

TEMPERATURE_UNITS = "imperial"   # "metric" or "imperial"
DISTANCE_UNITS    = "imperial"   # "metric" or "imperial"
CLOCK_FORMAT      = "12hr"       # "12hr" or "24hr"

# Filters out low-altitude noise (helicopters, GA pattern work, etc.).
# Value is feet above sea level. If you live at 1000 ft elevation, set this
# to your-elevation + the lowest cruising altitude you care about.
MIN_ALTITUDE = 500

# LED panel brightness (0-100). NIGHT values apply between NIGHT_START and
# NIGHT_END if NIGHT_BRIGHTNESS is True.
BRIGHTNESS        = 100
BRIGHTNESS_NIGHT  = 50
NIGHT_BRIGHTNESS  = True
NIGHT_START       = "22:00"
NIGHT_END         = "07:00"

# rpi-rgb-led-matrix GPIO slowdown. Use 2 for Pi 3, 1 for Pi Zero, 4 for Pi 4.
GPIO_SLOWDOWN = 2

# Your "home" airport code - rendered in bold on the journey scene when a
# flight's origin or destination matches.
JOURNEY_CODE_SELECTED = "LHR"
JOURNEY_BLANK_FILLER  = " ? "

# True if you have NOT soldered the PWM bridge on the Adafruit bonnet.
HAT_PWM_ENABLED = True

# Number of days of forecast to render at the bottom of the clock screen.
FORECAST_DAYS = 3

# OpenSky Network API credentials (OAuth2 client-credentials).
# Anonymous polling works but has very low rate limits, especially for
# bounding-box state queries. Create an API client at
# https://opensky-network.org/my-opensky and paste the values here.
# Leave both empty strings to fall back to anonymous access.
OPENSKY_CLIENT_ID     = ""
OPENSKY_CLIENT_SECRET = ""
