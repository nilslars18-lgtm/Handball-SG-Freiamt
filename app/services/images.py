"""Bilder beim Hochladen verkleinern, damit die Neon-Datenbank (Free: ~0.5 GB)
nicht voll laeuft. Ein Handyfoto (4-6 MB) wird so zu ca. 200-400 KB."""
import io

from PIL import Image, ImageOps

MAX_SIDE = 1600
JPEG_QUALITY = 80


def compress_image(raw: bytes, max_side: int = MAX_SIDE) -> bytes:
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)  # Handyfotos richtig drehen
    if img.mode not in ("RGB", "L"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()
