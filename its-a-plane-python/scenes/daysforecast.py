from datetime import datetime, timedelta
from pathlib import Path
from PIL import Image

from utilities.animator import Animator
from setup import colours, fonts, frames, screen
from utilities.temperature import grab_forecast
from config import NIGHT_START, NIGHT_END
from rgbmatrix import graphics
from utilities.temperature import grab_temperature_and_humidity  # <-- ADD THIS

# ---------- Path helpers ----------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ICONS_DIR = PROJECT_ROOT / "icons"

def find_icon_path(icon_name: str) -> Path:
    """
    Recursively search for an icon named '<icon_name>.png' starting at BASE_DIR.
    Raises FileNotFoundError if not found to preserve existing behavior.
    """
    candidate = ICONS_DIR / f"{icon_name}.png"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"Icon not found: {candidate}")


# Setup
DAY_COLOUR = colours.LIGHT_PINK
MIN_T_COLOUR = colours.LIGHT_MID_BLUE
MAX_T_COLOUR = colours.LIGHT_DARK_ORANGE
TEXT_FONT = fonts.extrasmall
FONT_HEIGHT = 5
DISTANCE_FROM_TOP = 32
ICON_SIZE = 10
FORECAST_SIZE = FONT_HEIGHT * 2 + ICON_SIZE
DAY_POSITION = DISTANCE_FROM_TOP - FONT_HEIGHT - ICON_SIZE
ICON_POSITION = DISTANCE_FROM_TOP - FONT_HEIGHT - ICON_SIZE
TEMP_POSITION = DISTANCE_FROM_TOP
NIGHT_START_TIME = datetime.strptime(NIGHT_START, "%H:%M")
NIGHT_END_TIME = datetime.strptime(NIGHT_END, "%H:%M")

CURRENT_TEMP_FONT = fonts.extrasmall
CURRENT_TEMP_POSITION_X = 45
CURRENT_TEMP_POSITION_Y = 6
CURRENT_TEMP_COLOUR = colours.LIGHT_YELLOW

class DaysForecastScene(object):
    def __init__(self):
        super().__init__()
        self._redraw_forecast = True
        self._last_hour = None
        self._cached_forecast = None
        self._last_temp_fetch = None
        self._cached_current_temp = None

    @Animator.KeyFrame.add(frames.PER_SECOND * 1)
    def day(self, count):
        now = datetime.now().replace(microsecond=0)

        # Redraw on night transitions
        if now.time() in (NIGHT_START_TIME.time(), NIGHT_END_TIME.time()):
            self._redraw_forecast = True
            return

        if len(self._data):
            self._redraw_forecast = True
            return

        current_hour = now.hour
        if self._last_hour != current_hour or self._redraw_forecast:
            if self._last_hour is not None:
                self.draw_square(0, 12, 64, 32, colours.BLACK)
            self._last_hour = current_hour

            # Fetch forecast
            forecast = self._cached_forecast if self._cached_forecast and not self._redraw_forecast else grab_forecast()
            self._cached_forecast = forecast
            self._redraw_forecast = False

            # Fetch and cache current temperature every 5 min
            if not self._last_temp_fetch or (now - self._last_temp_fetch).seconds > 300:
                temp, _ = grab_temperature_and_humidity()
                self._cached_current_temp = temp
                self._last_temp_fetch = now

            # Draw current temp
            if self._cached_current_temp is not None:
                temp_str = f"{self._cached_current_temp:.1f}°"
                self.draw_square(CURRENT_TEMP_POSITION_X, CURRENT_TEMP_POSITION_Y - 6, 20, CURRENT_TEMP_POSITION_Y + 2, colours.BLACK)
                graphics.DrawText(
                    self.canvas,
                    CURRENT_TEMP_FONT,
                    CURRENT_TEMP_POSITION_X,
                    CURRENT_TEMP_POSITION_Y,
                    CURRENT_TEMP_COLOUR,
                    temp_str
                )

            if forecast:
                offset = 1
                space_width = screen.WIDTH // 3
                for day in forecast:
                    day_name = datetime.fromisoformat(day["startTime"].rstrip("Z")).strftime("%a")
                    icon = day["values"]["weatherCodeFullDay"]
                    min_temp = f"{day['values']['temperatureMin']:.0f}"
                    max_temp = f"{day['values']['temperatureMax']:.0f}"

                    min_temp_width = len(min_temp) * 4
                    max_temp_width = len(max_temp) * 4
                    temp_x = offset + (space_width - min_temp_width - max_temp_width - 1) // 2 + 1
                    min_temp_x = temp_x + max_temp_width
                    max_temp_x = temp_x
                    icon_x = offset + (space_width - ICON_SIZE) // 2
                    day_x = offset + (space_width - 12) // 2 + 1

                    _ = graphics.DrawText(self.canvas, TEXT_FONT, day_x, DAY_POSITION, DAY_COLOUR, day_name)

                    icon_path = find_icon_path(str(icon))
                    image = Image.open(icon_path)
                    image.thumbnail((ICON_SIZE, ICON_SIZE), Image.ANTIALIAS)
                    self.matrix.SetImage(image.convert('RGB'), icon_x, ICON_POSITION)

                    self.draw_square(min_temp_x, TEMP_POSITION - FONT_HEIGHT, max_temp_x + max_temp_width, TEMP_POSITION + FONT_HEIGHT, colours.BLUE)

                    _ = graphics.DrawText(self.canvas, TEXT_FONT, min_temp_x, TEMP_POSITION, MIN_T_COLOUR, min_temp)
                    _ = graphics.DrawText(self.canvas, TEXT_FONT, max_temp_x, TEMP_POSITION, MAX_T_COLOUR, max_temp)

                    offset += space_width