"""Facebook Page publishing commands used by AutoPost automation."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from .facebook import FacebookError, post_photo


def main(
    argv: list[str] | None = None,
    *,
    base_dir: Path | None = None,
    default_page_id: str = "",
) -> int:
    page_id_default = os.getenv("FB_PAGE_ID") or default_page_id
    parser = argparse.ArgumentParser(
        description="Publish or schedule an image on a Facebook Page."
    )
    parser.add_argument("image", help="Image file path")
    parser.add_argument("caption", nargs="?", default="", help="Post caption")
    parser.add_argument("--schedule", help="Local time: YYYY-MM-DD HH:MM")
    parser.add_argument(
        "--page-id",
        default=page_id_default,
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    image = Path(args.image).expanduser()
    if base_dir is not None and not image.is_absolute():
        image = base_dir / image
    schedule = None
    if args.schedule:
        try:
            schedule = datetime.strptime(args.schedule, "%Y-%m-%d %H:%M")
        except ValueError:
            print("Schedule time must use YYYY-MM-DD HH:MM.", file=sys.stderr)
            return 1
    if args.dry_run:
        print(
            f"Dry run: image={image}, page={args.page_id or '(missing)'}, "
            f"scheduled_at={schedule or 'now'}"
        )
        return 0
    if not args.page_id:
        print("Provide --page-id or set FB_PAGE_ID.", file=sys.stderr)
        return 1
    token = os.getenv("FB_PAGE_TOKEN", "")
    if not token:
        try:
            from .vault import get_secret

            if args.page_id:
                token = get_secret(f"facebook_page_token_{args.page_id}")
            if not token:
                token = get_secret("facebook_page_token")
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    try:
        result = post_photo(args.page_id, token, str(image), args.caption, schedule)
    except (FacebookError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Success. Post ID: {result.get('post_id') or result.get('id')}")
    return 0
