"""Image validation and preparation. Uploaded bytes are only ever decoded as an image, never executed."""

from __future__ import annotations

import io
import os
import re

from PIL import Image, UnidentifiedImageError

from .errors import ImageError

ALLOWED_MIME = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}
MIN_SIDE = 32
MAX_PIXELS = 40_000_000

Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # decompression-bomb guard


def safe_filename(name: str | None) -> str:
    base = os.path.basename((name or "upload").replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .") or "upload"
    return base[:100]


def validate_image(data: bytes, filename: str | None, content_type: str | None, max_mb: float = 8) -> dict:
    """Return {"format", "width", "height", "filename"} or raise ImageError."""
    if not data:
        raise ImageError("The uploaded file is empty.")
    if len(data) > max_mb * 1024 * 1024:
        raise ImageError(f"The image is too large. Maximum size is {max_mb:g} MB.")

    name = safe_filename(filename)
    ext = os.path.splitext(name)[1].lower()
    if ext and ext not in ALLOWED_EXT:
        raise ImageError("Unsupported file type. Please upload a PNG, JPG/JPEG or WEBP image.")
    if content_type:
        mime = content_type.split(";")[0].strip().lower()
        if mime not in ALLOWED_MIME:
            raise ImageError("Unsupported file type. Please upload a PNG, JPG/JPEG or WEBP image.")

    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()  # structural check
        with Image.open(io.BytesIO(data)) as img:
            img.load()  # full decode: catches truncated files
            width, height = img.size
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise ImageError("The file could not be read as an image. It may be corrupted.") from None

    if fmt not in ALLOWED_FORMATS:
        raise ImageError("Unsupported image content. Please upload a PNG, JPG/JPEG or WEBP image.")
    if min(width, height) < MIN_SIDE:
        raise ImageError("The image is too small to read. Please upload a larger image.")
    return {"format": fmt, "width": width, "height": height, "filename": name}


def prepare_for_model(data: bytes, max_side: int = 1280) -> bytes:
    """Flatten transparency onto white, downscale very large images, re-encode as PNG."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                img = bg
            else:
                img = img.convert("RGB")
            w, h = img.size
            longest = max(w, h)
            if longest > max_side:
                scale = max_side / longest
                img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="PNG", optimize=False)
            return out.getvalue()
    except Exception:
        raise ImageError("The image could not be processed. Please try another file.") from None
