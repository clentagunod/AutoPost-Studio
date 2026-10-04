"""Automation commands for AutoPost Studio."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .facebook_cli import main as facebook_cli_main
from .scheduler import PostRecord, delete_post_files, load_posts
from .storage import OUTPUT_DIR, load_preferences

POST_STATUSES = (
    "draft",
    "queued",
    "paused",
    "processing",
    "scheduled",
    "published",
    "failed",
)


def _scheduler_folder() -> Path:
    preferences = load_preferences()
    configured = preferences.get("scheduler_dir")
    return Path(str(configured)).expanduser() if configured else OUTPUT_DIR


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autopost",
        description="Automate AutoPost Studio image processing and Page posts.",
        epilog=(
            "Image framing: autopost frame FRAME PHOTO [options]. "
            "Publishing and scheduling: autopost publish IMAGE [CAPTION] [options]."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser(
        "frame",
        add_help=False,
        help="Frame one image or a folder of images",
    )
    commands.add_parser(
        "publish",
        add_help=False,
        help="Publish now or submit a post to Meta's scheduler",
    )
    status = commands.add_parser("status", help="Show workspace and post status")
    status.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    queue = commands.add_parser(
        "queue", help="Inspect or delete local post metadata"
    )
    queue_commands = queue.add_subparsers(dest="queue_command", required=True)

    listing = queue_commands.add_parser("list", help="List saved posts")
    listing.add_argument("--status", choices=POST_STATUSES)
    listing.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    delete = queue_commands.add_parser(
        "delete", help="Delete a post sidecar and its associated image"
    )
    delete.add_argument("post_id", help="Exact post ID shown by queue list")
    delete.add_argument(
        "--yes",
        action="store_true",
        required=True,
        help="Confirm deletion of the sidecar and image",
    )
    return parser


def _post_to_dict(record: PostRecord) -> dict[str, str]:
    return {
        "id": record.id,
        "image": record.image,
        "caption": record.caption,
        "scheduled_at": record.scheduled_at,
        "page_id": record.page_id,
        "status": record.status,
        "remote_id": record.remote_id,
        "last_error": record.last_error,
    }


def _write_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _run_status(as_json: bool) -> int:
    folder = _scheduler_folder()
    try:
        records = load_posts(folder)
    except (OSError, ValueError) as exc:
        print(f"Could not read scheduler folder '{folder}': {exc}", file=sys.stderr)
        return 1
    counts = {status: 0 for status in POST_STATUSES}
    for record in records:
        counts[record.status] = counts.get(record.status, 0) + 1
    result = {
        "app_data": str(OUTPUT_DIR.parent),
        "outputs": str(OUTPUT_DIR),
        "scheduler_folder": str(folder),
        "posts": len(records),
        "counts": counts,
    }
    if as_json:
        _write_json(result)
    else:
        print(f"Workspace: {result['app_data']}")
        print(f"Outputs: {OUTPUT_DIR}")
        print(f"Scheduler folder: {folder}")
        print(f"Posts: {len(records)}")
        print("Status: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    return 0


def _queue_list(status: str | None, as_json: bool) -> int:
    folder = _scheduler_folder()
    try:
        records = load_posts(folder)
    except (OSError, ValueError) as exc:
        print(f"Could not read scheduler folder '{folder}': {exc}", file=sys.stderr)
        return 1
    if status:
        records = [record for record in records if record.status == status]
    if as_json:
        _write_json([_post_to_dict(record) for record in records])
        return 0
    if not records:
        print("No matching posts.")
        return 0
    for record in records:
        print(
            f"{record.id}  {record.status:<10}  "
            f"{record.scheduled_at or '(not scheduled)':<16}  "
            f"{Path(record.image).name}"
        )
        if record.last_error:
            print(f"  error: {record.last_error}")
    return 0


def _queue_delete(post_id: str) -> int:
    folder = _scheduler_folder()
    try:
        records = load_posts(folder)
        matches = [record for record in records if record.id == post_id]
        if not matches:
            print(f"No post found with ID '{post_id}'.", file=sys.stderr)
            return 1
        delete_post_files(matches, folder)
    except (OSError, ValueError) as exc:
        print(f"Could not delete post: {exc}", file=sys.stderr)
        return 1
    print(f"Deleted post {post_id} and its unshared image.")
    return 0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    return _build_parser().parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """Run automation commands; `frame` and `publish` reuse the existing CLIs."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "frame":
        from .cli import main as image_cli_main

        return image_cli_main(args_list[1:])
    if args_list and args_list[0] == "publish":
        return facebook_cli_main(args_list[1:])

    args = _parse_args(args_list)
    if args.command == "status":
        return _run_status(args.json)
    if args.command == "queue":
        if args.queue_command == "list":
            return _queue_list(args.status, args.json)
        if args.queue_command == "delete":
            return _queue_delete(args.post_id)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
