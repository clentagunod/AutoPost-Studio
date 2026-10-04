"""JSON sidecar records for editable scheduled and draft posts."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SIDECAR_SUFFIX = ".autopost"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass
class PostRecord:
    image: str
    caption: str = ""
    scheduled_at: str = ""
    destination: str = "facebook_page"
    page_id: str = ""
    status: str = "draft"
    id: str = ""
    remote_id: str = ""
    group_id: str = ""
    last_error: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex


def save_post(record: PostRecord, folder: Path) -> Path:
    """Atomically save one portable JSON post manifest beside its image."""
    if not record.image:
        raise ValueError("A post must reference an image.")
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"{Path(record.image).stem}_{record.id[:10]}{SIDECAR_SUFFIX}"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(asdict(record), file, indent=2, ensure_ascii=False)
        file.write("\n")
    temporary.replace(destination)
    return destination


def load_posts(folder: Path) -> list[PostRecord]:
    """Load sidecars and surface image files without metadata as draft posts."""
    if not folder.exists():
        return []
    records: list[PostRecord] = []
    for path in sorted(folder.glob(f"*{SIDECAR_SUFFIX}")):
        try:
            with path.open("r", encoding="utf-8") as file:
                value: Any = json.load(file)
            if not isinstance(value, dict) or not isinstance(value.get("image"), str):
                raise ValueError("Expected an object containing an image path.")
            records.append(
                PostRecord(
                    image=value["image"],
                    caption=str(value.get("caption", "")),
                    scheduled_at=str(value.get("scheduled_at", "")),
                    destination=str(value.get("destination", "facebook_page")),
                    page_id=str(value.get("page_id", "")),
                    status=str(value.get("status", "draft")),
                    id=str(value.get("id", path.stem)),
                    remote_id=str(value.get("remote_id", "")),
                    group_id=str(value.get("group_id", "")),
                    last_error=str(value.get("last_error", "")),
                )
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"Cannot read post metadata '{path}': {exc}") from exc
    known_images = {Path(record.image).resolve() for record in records}
    for path in sorted(folder.iterdir()):
        if (
            path.is_file()
            and path.suffix.lower() in IMAGE_EXTENSIONS
            and path.resolve() not in known_images
        ):
            records.append(PostRecord(image=str(path.resolve())))
    return records


def delete_post_files(records: list[PostRecord], folder: Path) -> None:
    """Delete selected post sidecars and their images, preserving shared images."""
    selected_ids = {record.id for record in records}
    if not selected_ids:
        return

    sidecars: dict[str, Path] = {}
    in_progress: list[str] = []
    referenced_by_remaining: set[Path] = set()
    for sidecar in folder.glob(f"*{SIDECAR_SUFFIX}"):
        try:
            with sidecar.open("r", encoding="utf-8") as file:
                value: Any = json.load(file)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        record_id = str(value.get("id", ""))
        image = value.get("image")
        if record_id in selected_ids:
            sidecars[record_id] = sidecar
            if value.get("status") == "processing":
                in_progress.append(str(image or sidecar.name))
        elif isinstance(image, str) and image:
            referenced_by_remaining.add(Path(image).expanduser().resolve())

    if in_progress:
        raise OSError(
            "Cannot delete posts while publishing is in progress:\n"
            + "\n".join(in_progress)
        )

    errors: list[str] = []
    for record in records:
        sidecar = sidecars.get(record.id)
        if sidecar is not None:
            try:
                sidecar.unlink(missing_ok=True)
            except OSError as exc:
                errors.append(f"{sidecar.name}: {exc}")

    deleted_images: set[Path] = set()
    for record in records:
        image = Path(record.image).expanduser().resolve()
        if image in deleted_images or image in referenced_by_remaining:
            continue
        deleted_images.add(image)
        try:
            image.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{image.name}: {exc}")

    if errors:
        raise OSError("Some selected scheduler files could not be deleted:\n" + "\n".join(errors))
