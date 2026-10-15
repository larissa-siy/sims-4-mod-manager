"""
thumbnails.py
Builds the small tile images used in the Tray Creations gallery view:
either the creation's real embedded thumbnail (decoded via Pillow), or
a plain colored placeholder when there's no embedded image / Pillow
isn't installed.

Kept separate from app.py so the image-generation logic can be tested
without needing a real Tk display.

Deliberately does NOT use PIL.ImageTk to hand images to Tkinter -
ImageTk is a separate native binding that has to be built against
whatever exact Tk version is running, and can silently misbehave (or
fail in ways that don't cleanly raise ImportError) on newer Tk builds.
Instead, images are exported as PNG bytes via image_to_png_bytes() and
handed to Tk's OWN built-in PNG loader (tk.PhotoImage(data=...)), which
is guaranteed to match whatever Tk version is actually running since
it's part of Tk itself, not a separate compiled bridge.
"""

import io

try:
    from PIL import Image, ImageDraw
    HAVE_PIL = True
except Exception as _pil_import_error:  # noqa: BLE001 - broader than ImportError on purpose
    HAVE_PIL = False
    import sys
    print(f"[thumbnails] Pillow not available, using text-only placeholders: {_pil_import_error}",
          file=sys.stderr)

TILE_SIZE = (140, 100)

KIND_COLORS = {
    "Household / Sim": (58, 92, 138),   # blue
    "Lot": (69, 133, 84),               # green
    "Room": (130, 88, 148),             # purple
    "Unknown": (90, 90, 90),            # gray
}

KIND_ABBREVIATIONS = {
    "Household / Sim": "SIM",
    "Lot": "LOT",
    "Room": "ROOM",
    "Unknown": "?",
}


def placeholder_image(kind, size=TILE_SIZE):
    """A plain colored tile with a short label, used when there's no
    real embedded thumbnail to show (or Pillow isn't installed - in
    which case the caller won't be able to render this either, but the
    color/label choice itself has no Tk/Pillow dependency and is safe
    to unit test)."""
    color = KIND_COLORS.get(kind, KIND_COLORS["Unknown"])
    label = KIND_ABBREVIATIONS.get(kind, "?")
    if not HAVE_PIL:
        return None
    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    text = label
    # textbbox needs a font; default bitmap font is fine for a small tag
    bbox = draw.textbbox((0, 0), text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size[0] - tw) / 2, (size[1] - th) / 2), text, fill=(255, 255, 255))
    return img


def thumbnail_or_placeholder(raw_bytes, kind, size=TILE_SIZE):
    """Returns a Pillow Image sized to fit `size`, either decoded from
    raw_bytes (an embedded JPEG/PNG) or a colored placeholder. Returns
    None only if Pillow itself isn't available."""
    if not HAVE_PIL:
        return None

    if raw_bytes:
        try:
            import io
            img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
            img.thumbnail(size)
            # letterbox onto a fixed-size canvas so the grid stays even
            canvas = Image.new("RGB", size, (30, 30, 30))
            x = (size[0] - img.width) // 2
            y = (size[1] - img.height) // 2
            canvas.paste(img, (x, y))
            return canvas
        except Exception:
            pass  # fall through to placeholder

    return placeholder_image(kind, size)


def image_to_png_bytes(img):
    """Encode a PIL Image as PNG bytes, for handing to tk.PhotoImage
    directly - see module docstring for why this is preferred over
    PIL.ImageTk."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
