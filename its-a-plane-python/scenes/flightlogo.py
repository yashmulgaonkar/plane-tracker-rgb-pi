from pathlib import Path
from PIL import Image

from utilities.animator import Animator
from setup import colours

LOGO_SIZE = 16
DEFAULT_IMAGE = "default"

# ---- Path helpers (project-root aware) ----
# /.../plane-tracker-rgb-pi/its-a-plane-python/scenes/this_file.py
# parents[2] -> /.../plane-tracker-rgb-pi
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGOS_DIR = PROJECT_ROOT / "logos"

def _open_logo_by_name(name: str) -> Image.Image:
    """
    Open <name>.png from logos/, falling back to a recursive search under the project root.
    Raises FileNotFoundError if not found.
    """
    # 1) Fast path: direct file in /logos
    candidate = LOGOS_DIR / f"{name}.png"
    if candidate.exists():
        return Image.open(candidate)

    # 2) Safety net: look anywhere under project root (handles subfolders)
    match = next(PROJECT_ROOT.rglob(f"{name}.png"), None)
    if match:
        return Image.open(match)

    # 3) Not found
    raise FileNotFoundError(f"Logo '{name}.png' not found in {LOGOS_DIR} or anywhere under {PROJECT_ROOT}")

class FlightLogoScene:
    @Animator.KeyFrame.add(0)
    def logo_details(self):

        # Guard against no data
        if len(self._data) == 0:
            return

        # Clear the whole area
        self.draw_square(
            0,
            0,
            LOGO_SIZE,
            LOGO_SIZE,
            colours.BLACK,
        )

        icao = self._data[self._data_index]["owner_icao"]
        if icao in ("", "N/A"):
            icao = DEFAULT_IMAGE

        # Open the file: preserve original behavior (specific → default)
        try:
            image = _open_logo_by_name(str(icao))
        except FileNotFoundError:
            # Fallback to default logo; if even that is missing, re-raise a clear error
            try:
                image = _open_logo_by_name(DEFAULT_IMAGE)
            except FileNotFoundError as e:
                # Surface a helpful path in the error
                raise FileNotFoundError(
                    f"Default logo '{DEFAULT_IMAGE}.png' not found in {LOGOS_DIR} or under {PROJECT_ROOT}"
                ) from e

        # Make image fit our screen.
        image.thumbnail((LOGO_SIZE, LOGO_SIZE), Image.ANTIALIAS)
        self.matrix.SetImage(image.convert('RGB'))
