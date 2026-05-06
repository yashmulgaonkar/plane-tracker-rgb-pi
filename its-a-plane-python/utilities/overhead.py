"""
Overhead-flight discovery and lookup.

- Flight discovery uses the OpenSky Network API (`opensky_api` Python library):
    https://github.com/openskynetwork/opensky-api
  We pull state vectors inside the bounding box defined by ZONE_HOME.

- Flight details (aircraft type, airline, route) are looked up from
  api.adsbdb.com via the combined endpoint:
    GET /v0/aircraft/{mode_s}?callsign={callsign}
  The `pyadsbdb` package is a thin wrapper around the same service; we call
  the JSON endpoint directly with `requests` so we can get aircraft +
  flightroute in a single round-trip.
"""

from threading import Thread, Lock
from time import sleep
import math

import requests
from requests.exceptions import ConnectionError, RequestException
from urllib3.exceptions import NewConnectionError, MaxRetryError

from opensky_api import OpenSkyApi

from config import DISTANCE_UNITS

try:
    from config import MIN_ALTITUDE
except (ModuleNotFoundError, NameError, ImportError):
    MIN_ALTITUDE = 0  # feet

try:
    from config import ZONE_HOME, LOCATION_HOME
    ZONE_DEFAULT = ZONE_HOME
    LOCATION_DEFAULT = LOCATION_HOME
except (ModuleNotFoundError, NameError, ImportError):
    ZONE_DEFAULT = {"tl_y": 62.61, "tl_x": -13.07, "br_y": 49.71, "br_x": 3.46}
    LOCATION_DEFAULT = [51.509865, -0.118092]

# OpenSky now requires OAuth2 client-credentials for anything beyond very
# limited anonymous polling. Credentials are optional in config.
try:
    from config import OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET
except (ModuleNotFoundError, NameError, ImportError):
    OPENSKY_CLIENT_ID = ""
    OPENSKY_CLIENT_SECRET = ""


RETRIES = 3
RATE_LIMIT_DELAY = 1
MAX_FLIGHT_LOOKUP = 5
MAX_ALTITUDE = 100000  # feet
EARTH_RADIUS_M = 3958.8  # Earth's radius in miles
BLANK_FIELDS = ["", "N/A", "NONE"]

METERS_TO_FEET = 3.28084
MS_TO_FT_PER_MIN = 196.8504  # m/s -> ft/min

ADSBDB_BASE_URL = "https://api.adsbdb.com/v0"
ADSBDB_TIMEOUT = 10  # seconds


def polar_to_cartesian(lat, long, alt):
    DEG2RAD = math.pi / 180
    return [
        alt * math.cos(DEG2RAD * lat) * math.sin(DEG2RAD * long),
        alt * math.sin(DEG2RAD * lat),
        alt * math.cos(DEG2RAD * lat) * math.cos(DEG2RAD * long),
    ]


def _haversine_miles(lat1, lon1, lat2, lon2):
    lat1, lon1 = math.radians(lat1), math.radians(lon1)
    lat2, lon2 = math.radians(lat2), math.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_M * c


def _to_user_units(dist_miles):
    if DISTANCE_UNITS == "metric":
        return dist_miles * 1.609
    return dist_miles


def distance_from_flight_to_home(flight, home=LOCATION_DEFAULT):
    try:
        return _to_user_units(
            _haversine_miles(flight.latitude, flight.longitude, home[0], home[1])
        )
    except (AttributeError, TypeError):
        return 1e6


def plane_bearing(flight, home=LOCATION_DEFAULT):
    lat1 = math.radians(home[0])
    long1 = math.radians(home[1])
    lat2 = math.radians(flight.latitude)
    long2 = math.radians(flight.longitude)
    bearing = math.atan2(
        math.sin(long2 - long1) * math.cos(lat2),
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(long2 - long1),
    )
    bearing = math.degrees(bearing)
    return (bearing + 360) % 360


def degrees_to_cardinal(d):
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    ix = int((d + 22.5) / 45)
    return dirs[ix % 8]


def distance_from_flight_to_origin(flight, origin_latitude, origin_longitude, origin_altitude):
    if not (hasattr(flight, "latitude") and hasattr(flight, "longitude")):
        return None
    try:
        return _to_user_units(
            _haversine_miles(flight.latitude, flight.longitude, origin_latitude, origin_longitude)
        )
    except Exception as e:
        print("Error:", e)
        return None


def distance_from_flight_to_destination(flight, destination_latitude, destination_longitude, destination_altitude):
    if not (hasattr(flight, "latitude") and hasattr(flight, "longitude")):
        return None
    try:
        return _to_user_units(
            _haversine_miles(
                flight.latitude, flight.longitude, destination_latitude, destination_longitude
            )
        )
    except Exception as e:
        print("Error:", e)
        return None


class _Flight:
    """Lightweight flight wrapper that mirrors the attributes the rest of the
    file (and the distance/bearing helpers) expect from a flight object."""

    __slots__ = (
        "icao24",
        "callsign",
        "latitude",
        "longitude",
        "altitude",
        "vertical_speed",
        "true_track",
    )

    def __init__(self, state):
        self.icao24 = (state.icao24 or "").lower()
        self.callsign = (state.callsign or "").strip()
        self.latitude = state.latitude
        self.longitude = state.longitude

        # Prefer geo_altitude (GPS), fall back to baro_altitude. Convert m -> ft
        # since MIN_ALTITUDE/MAX_ALTITUDE are expressed in feet.
        alt_m = state.geo_altitude if state.geo_altitude is not None else state.baro_altitude
        self.altitude = (alt_m * METERS_TO_FEET) if alt_m is not None else 0.0

        # Convert m/s -> ft/min so vertical_speed has the same scale that
        # the previous FlightRadar24 integration produced.
        self.vertical_speed = (
            state.vertical_rate * MS_TO_FT_PER_MIN if state.vertical_rate is not None else 0.0
        )
        self.true_track = state.true_track


def _adsbdb_lookup(session, icao24, callsign):
    """Look up combined aircraft + flightroute info from adsbdb.

    Returns a tuple (aircraft_dict, flightroute_dict). Either side can be
    None if the lookup failed or that piece is unknown.
    """
    if not icao24:
        return None, None

    params = {}
    if callsign:
        params["callsign"] = callsign

    try:
        resp = session.get(
            f"{ADSBDB_BASE_URL}/aircraft/{icao24}",
            params=params,
            timeout=ADSBDB_TIMEOUT,
        )
    except (ConnectionError, RequestException, NewConnectionError, MaxRetryError):
        return None, None

    if resp.status_code == 404:
        # Aircraft unknown to adsbdb; still try to fetch the route by callsign
        # alone so we can at least populate origin/destination/airline.
        return None, _adsbdb_route_only(session, callsign)

    if resp.status_code != 200:
        return None, None

    try:
        body = resp.json().get("response") or {}
    except ValueError:
        return None, None

    return body.get("aircraft"), body.get("flightroute")


def _adsbdb_route_only(session, callsign):
    if not callsign:
        return None
    try:
        resp = session.get(
            f"{ADSBDB_BASE_URL}/callsign/{callsign}",
            timeout=ADSBDB_TIMEOUT,
        )
    except (ConnectionError, RequestException, NewConnectionError, MaxRetryError):
        return None
    if resp.status_code != 200:
        return None
    try:
        return (resp.json().get("response") or {}).get("flightroute")
    except ValueError:
        return None


def _bbox_from_zone(zone):
    """Convert ZONE_HOME (top-left / bottom-right corners) to OpenSky bbox
    tuple (min_lat, max_lat, min_lon, max_lon)."""
    return (
        min(zone["tl_y"], zone["br_y"]),
        max(zone["tl_y"], zone["br_y"]),
        min(zone["tl_x"], zone["br_x"]),
        max(zone["tl_x"], zone["br_x"]),
    )


class Overhead:
    def __init__(self):
        if OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET:
            self._opensky = OpenSkyApi(
                client_id=OPENSKY_CLIENT_ID,
                client_secret=OPENSKY_CLIENT_SECRET,
            )
        else:
            self._opensky = OpenSkyApi()

        self._adsbdb_session = requests.Session()
        self._adsbdb_session.headers.update({"User-Agent": "plane-tracker-rgb-pi"})

        self._lock = Lock()
        self._data = []
        self._new_data = False
        self._processing = False

    def grab_data(self):
        Thread(target=self._grab_data, daemon=True).start()

    def _grab_data(self):
        with self._lock:
            self._new_data = False
            self._processing = True

        data = []

        try:
            bbox = _bbox_from_zone(ZONE_DEFAULT)
            states = self._opensky.get_states(bbox=bbox)

            # `get_states` returns None when the OpenSky client-side rate
            # limiter blocks the request or the server returns a non-200.
            # Treat that as "no fresh data this cycle" rather than "no
            # planes overhead", so the screen keeps showing the last result.
            if states is None:
                with self._lock:
                    self._new_data = False
                    self._processing = False
                return

            raw_states = states.states or []

            flights = [
                _Flight(s)
                for s in raw_states
                if s.latitude is not None and s.longitude is not None and not s.on_ground
            ]
            flights = [f for f in flights if MIN_ALTITUDE < f.altitude < MAX_ALTITUDE]
            flights = sorted(flights, key=lambda f: distance_from_flight_to_home(f))

            for flight in flights[:MAX_FLIGHT_LOOKUP]:
                # Be polite to adsbdb between lookups.
                sleep(RATE_LIMIT_DELAY)

                aircraft_info, route_info = _adsbdb_lookup(
                    self._adsbdb_session, flight.icao24, flight.callsign
                )

                # ---- Aircraft type ----
                plane = ""
                if aircraft_info:
                    plane = (aircraft_info.get("icao_type") or "").strip()
                    if plane.upper() in BLANK_FIELDS:
                        plane = (aircraft_info.get("type") or "").strip()
                if plane.upper() in BLANK_FIELDS:
                    plane = ""

                # ---- Airline / route ----
                airline_name = ""
                owner_icao = ""
                owner_iata = ""
                origin = ""
                destination = ""
                origin_lat = origin_lon = origin_alt = None
                dest_lat = dest_lon = dest_alt = None

                if route_info:
                    airline = route_info.get("airline") or {}
                    airline_name = (airline.get("name") or "").strip()
                    owner_icao = (airline.get("icao") or "").strip()
                    owner_iata = (airline.get("iata") or "").strip()

                    origin_info = route_info.get("origin") or {}
                    destination_info = route_info.get("destination") or {}
                    origin = (origin_info.get("iata_code") or "").strip()
                    destination = (destination_info.get("iata_code") or "").strip()
                    origin_lat = origin_info.get("latitude")
                    origin_lon = origin_info.get("longitude")
                    origin_alt = origin_info.get("elevation")
                    dest_lat = destination_info.get("latitude")
                    dest_lon = destination_info.get("longitude")
                    dest_alt = destination_info.get("elevation")

                # Fall back to the registered owner when we have no airline
                # match (general aviation, private operators, etc.).
                if not airline_name and aircraft_info:
                    airline_name = (aircraft_info.get("registered_owner") or "").strip()
                if not owner_icao and aircraft_info:
                    owner_icao = (
                        aircraft_info.get("registered_owner_operator_flag_code") or ""
                    ).strip()

                # Normalise blanks
                if origin.upper() in BLANK_FIELDS:
                    origin = ""
                if destination.upper() in BLANK_FIELDS:
                    destination = ""
                if owner_icao.upper() in BLANK_FIELDS:
                    owner_icao = ""
                callsign = flight.callsign if flight.callsign.upper() not in BLANK_FIELDS else ""
                owner_iata = owner_iata if owner_iata else "N/A"

                # ---- Distances ----
                distance_origin = 0
                distance_destination = 0
                if origin_lat is not None and origin_lon is not None:
                    distance_origin = (
                        distance_from_flight_to_origin(
                            flight, origin_lat, origin_lon, origin_alt or 0
                        )
                        or 0
                    )
                if dest_lat is not None and dest_lon is not None:
                    distance_destination = (
                        distance_from_flight_to_destination(
                            flight, dest_lat, dest_lon, dest_alt or 0
                        )
                        or 0
                    )

                data.append(
                    {
                        "airline": airline_name,
                        "plane": plane,
                        "origin": origin,
                        "owner_iata": owner_iata,
                        "owner_icao": owner_icao,
                        "destination": destination,
                        # OpenSky/adsbdb don't expose scheduled vs. actual
                        # times, so we leave these as None. The journey
                        # scene treats None as "unknown" and renders the
                        # airport codes in neutral grey.
                        "time_scheduled_departure": None,
                        "time_scheduled_arrival": None,
                        "time_real_departure": None,
                        "time_estimated_arrival": None,
                        "vertical_speed": flight.vertical_speed,
                        "callsign": callsign,
                        "distance_origin": distance_origin,
                        "distance_destination": distance_destination,
                        "distance": distance_from_flight_to_home(flight),
                        "direction": degrees_to_cardinal(plane_bearing(flight)),
                    }
                )

            with self._lock:
                self._new_data = True
                self._processing = False
                self._data = data

        except (ConnectionError, NewConnectionError, MaxRetryError, RequestException):
            with self._lock:
                self._new_data = False
                self._processing = False
        except Exception as e:
            print(f"Overhead error: {e}")
            with self._lock:
                self._new_data = False
                self._processing = False

    @property
    def new_data(self):
        with self._lock:
            return self._new_data

    @property
    def processing(self):
        with self._lock:
            return self._processing

    @property
    def data(self):
        with self._lock:
            self._new_data = False
            return self._data

    @property
    def data_is_empty(self):
        return len(self._data) == 0


# Main function
if __name__ == "__main__":

    o = Overhead()
    o.grab_data()
    while o.processing:
        print("processing...")
        sleep(1)

    print(o.data)
