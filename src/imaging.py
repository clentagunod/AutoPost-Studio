"""Image loading, fitting, compositing, and export operations."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

LOG = logging.getLogger("frame_studio")
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
RESAMPLE = Image.Resampling.LANCZOS


def load_frame(path: Path) -> Image.Image:
    """Load a frame as RGBA and warn when it has no transparent pixels."""
    with Image.open(path) as source:
        source.load()
        frame = source.convert("RGBA")

    if frame.getchannel("A").getextrema()[0] == 255:
        LOG.warning(
            "Frame '%s' is fully opaque; the photo will be completely hidden.",
            path.name,
        )
    return frame


def load_photo(path: Path) -> Image.Image:
    """Load a photo, apply EXIF orientation, and convert it to RGBA."""
    with Image.open(path) as source:
        source.load()
        return ImageOps.exif_transpose(source).convert("RGBA")


def fit_photo(
    photo: Image.Image,
    size: tuple[int, int],
    mode: str = "cover",
    anchor: tuple[float, float] = (0.5, 0.5),
) -> Image.Image:
    """Resize a photo to the frame, cropping or letterboxing as requested."""
    target_width, target_height = size
    source_width, source_height = photo.size
    anchor_x = min(max(anchor[0], 0.0), 1.0)
    anchor_y = min(max(anchor[1], 0.0), 1.0)

    if mode == "cover":
        scale = max(target_width / source_width, target_height / source_height)
    elif mode == "contain":
        scale = min(target_width / source_width, target_height / source_height)
    else:
        raise ValueError(f"Unknown fit mode: {mode!r}")

    resized = photo.resize(
        (max(1, round(source_width * scale)), max(1, round(source_height * scale))),
        RESAMPLE,
    )
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    offset = (
        round((target_width - resized.width) * anchor_x),
        round((target_height - resized.height) * anchor_y),
    )
    canvas.paste(resized, offset)
    return canvas


def attach_frame(
    frame: Image.Image,
    photo: Image.Image,
    fit: str = "cover",
    anchor: tuple[float, float] = (0.5, 0.5),
) -> Image.Image:
    """Composite a fitted photo beneath the transparent frame."""
    return Image.alpha_composite(fit_photo(photo, frame.size, fit, anchor), frame)


def save_image(img: Image.Image, path: Path, quality: int = 95) -> None:
    """Save an image, flattening transparency for JPEG output."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        flattened = Image.new("RGB", img.size, (255, 255, 255))
        flattened.paste(img, mask=img.getchannel("A"))
        flattened.save(path, "JPEG", quality=quality, subsampling=0, optimize=True)
    elif path.suffix.lower() == ".webp":
        img.save(path, "WEBP", quality=quality, method=6)
    else:
        img.save(path)


def process_one(
    frame: Image.Image,
    photo_path: Path,
    out_path: Path,
    fit: str,
    anchor: tuple[float, float],
    quality: int,
) -> bool:
    """Process and save one photo, returning whether the operation succeeded."""
    try:
        result = attach_frame(frame, load_photo(photo_path), fit, anchor)
        save_image(result, out_path, quality)
        LOG.info("Saved %s", out_path)
        return True
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        LOG.error("Skipped %s: %s", photo_path.name, exc)
        return False
