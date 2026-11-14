"""Image helper shared by the API and the seed script."""
import base64
import io

from PIL import Image, ImageOps

MAX_SIDE = 600
JPEG_QUALITY = 80


def to_data_url(raw: bytes) -> str:
    """Resize to at most 600px on the longest side and return a JPEG base64 data URL."""
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)  # respect phone camera rotation
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def decode_base64_image(value: str) -> bytes:
    """Accept either a bare base64 string or a data URL."""
    if value.startswith("data:"):
        value = value.split(",", 1)[1]
    return base64.b64decode(value)
