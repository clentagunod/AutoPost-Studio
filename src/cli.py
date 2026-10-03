"""Command-line interface retained alongside the desktop application."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PIL import UnidentifiedImageError

from .imaging import SUPPORTED_EXTS, load_frame, process_one
from .storage import OUTPUT_DIR


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Attach a transparent frame to photo(s)."
    )
    parser.add_argument("frame", type=Path, help="Frame PNG with transparency")
    parser.add_argument("photo", type=Path, help="Photo file, or a folder of photos")
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--fit", choices=["cover", "contain"], default="cover")
    parser.add_argument(
        "--anchor",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        default=(0.5, 0.5),
    )
    parser.add_argument("--quality", type=int, default=95)
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Include images in nested folders when photo is a directory",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    log = logging.getLogger("frame_studio")

    if not args.frame.is_file():
        log.error("Frame not found: %s", args.frame)
        return 1
    if not args.photo.exists():
        log.error("Photo path not found: %s", args.photo)
        return 1
    if not 1 <= args.quality <= 100:
        log.error("Quality must be between 1 and 100.")
        return 1

    try:
        frame = load_frame(args.frame)
    except (UnidentifiedImageError, OSError) as exc:
        log.error("Cannot read frame: %s", exc)
        return 1

    anchor = tuple(args.anchor)
    if args.photo.is_dir():
        output_dir = args.output or OUTPUT_DIR
        candidates = args.photo.rglob("*") if args.recursive else args.photo.iterdir()
        output_root = output_dir.resolve()
        source_root = args.photo.resolve()
        photos = sorted(
            path
            for path in candidates
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTS
            and not (
                args.recursive
                and output_root != source_root
                and output_root.is_relative_to(source_root)
                and path.resolve().is_relative_to(output_root)
            )
        )
        if not photos:
            log.error("No supported images in %s", args.photo)
            return 1
        succeeded = sum(
            process_one(
                frame,
                path,
                output_dir
                / (
                    f"{path.relative_to(args.photo).with_suffix('')}_framed.png"
                    if args.recursive
                    else f"{path.stem}_framed.png"
                ),
                args.fit,
                anchor,
                args.quality,
            )
            for path in photos
        )
        log.info("Done: %d/%d succeeded", succeeded, len(photos))
        return 0 if succeeded == len(photos) else 2

    output = args.output or OUTPUT_DIR / f"{args.photo.stem}_framed.png"
    return (
        0
        if process_one(frame, args.photo, output, args.fit, anchor, args.quality)
        else 2
    )


if __name__ == "__main__":
    sys.exit(main())
