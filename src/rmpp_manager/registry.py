from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_ICON_CODE = "\ue9b9"


@dataclass(slots=True)
class RegistryMerge:
    merged: dict[str, Any]
    added: list[dict[str, Any]] = field(default_factory=list)
    updated: list[dict[str, Any]] = field(default_factory=list)
    unchanged: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[tuple[dict[str, Any], dict[str, Any]]] = field(default_factory=list)
    name_collisions: list[tuple[dict[str, Any], dict[str, Any]]] = field(default_factory=list)


def is_landscape(entry: dict[str, Any]) -> bool:
    return bool(entry.get("landscape", False))


def entry_key(entry: dict[str, Any]) -> tuple[str, bool]:
    return (str(entry.get("filename", "")).strip(), is_landscape(entry))


def canonical(entry: dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def merge_registry(
    remote_registry: dict[str, Any],
    local_entries: list[dict[str, Any]],
    *,
    replace_conflicts: bool = False,
) -> RegistryMerge:
    """Upsert custom entries into the live reMarkable registry.

    The live device registry is always the base document. Existing device
    entries, ordering and unknown fields are preserved as far as possible.

    Identity is filename + orientation. If a selected custom template already
    exists, fields supplied by the local custom entry update that entry in
    place while device-only fields are retained.

    replace_conflicts is retained for backwards compatibility with older
    callers; selected custom templates are now always upserted.
    """
    del replace_conflicts

    merged = deepcopy(remote_registry)
    raw_templates = merged.get("templates", [])

    if not isinstance(raw_templates, list):
        raise ValueError(
            "Remote templates.json does not contain a 'templates' list."
        )

    remote_templates: list[dict[str, Any]] = []

    for entry in raw_templates:
        if not isinstance(entry, dict):
            raise ValueError(
                "Remote templates.json contains a non-object template entry."
            )
        remote_templates.append(deepcopy(entry))

    result = RegistryMerge(merged=merged)

    def reindex() -> tuple[
        dict[tuple[str, bool], int],
        dict[str, list[dict[str, Any]]],
    ]:
        by_key: dict[tuple[str, bool], int] = {}
        by_name: dict[str, list[dict[str, Any]]] = {}

        for index, entry in enumerate(remote_templates):
            by_key.setdefault(entry_key(entry), index)
            by_name.setdefault(
                str(entry.get("name", "")).strip(),
                [],
            ).append(entry)

        return by_key, by_name

    by_key, by_name = reindex()

    # Known-working manifests place custom entries immediately after the
    # stock Blank portrait/landscape entries. Preserve everything else.
    blank_indexes = [
        index
        for index, entry in enumerate(remote_templates)
        if str(entry.get("filename", "")).strip() == "Blank"
    ]

    insert_at = (
        max(blank_indexes) + 1
        if blank_indexes
        else len(remote_templates)
    )

    for local in local_entries:
        if not isinstance(local, dict):
            raise ValueError(
                f"Template entry is not an object: {local!r}"
            )

        candidate = deepcopy(local)
        key = entry_key(candidate)

        if not key[0]:
            raise ValueError(
                f"Template entry has no filename: {candidate!r}"
            )

        if key in by_key:
            index = by_key[key]
            existing = remote_templates[index]

            if canonical(existing) == canonical(candidate):
                result.unchanged.append(candidate)
                continue

            # Custom metadata wins for fields it supplies. Any additional
            # firmware/device metadata remains intact.
            updated = deepcopy(existing)
            updated.update(candidate)

            remote_templates[index] = updated
            result.updated.append(deepcopy(updated))

            by_key, by_name = reindex()
            continue

        name = str(candidate.get("name", "")).strip()

        for existing in by_name.get(name, []):
            if entry_key(existing) != key:
                result.name_collisions.append(
                    (deepcopy(existing), deepcopy(candidate))
                )

        remote_templates.insert(insert_at, candidate)
        insert_at += 1

        result.added.append(deepcopy(candidate))
        by_key, by_name = reindex()

    merged["templates"] = remote_templates
    return result

def _manifest_entries(folder: Path) -> list[dict[str, Any]]:
    manifest = folder / "templates.json"
    if not manifest.exists():
        return []
    with manifest.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    entries = data.get("templates", [])
    if not isinstance(entries, list):
        raise ValueError(f"{manifest} does not contain a templates list.")
    return entries


def discover_local_templates(folder: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    """Return .template assets and registry entries representing them.

    If the folder has templates.json its matching metadata is preserved. Otherwise
    metadata is inferred from each native .template document.
    """
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"Template folder does not exist: {folder}")

    assets = sorted(folder.glob("*.template"))
    manifest_entries = _manifest_entries(folder)
    manifest_by_stem: dict[str, list[dict[str, Any]]] = {}
    for entry in manifest_entries:
        manifest_by_stem.setdefault(str(entry.get("filename", "")), []).append(entry)

    entries: list[dict[str, Any]] = []
    for asset in assets:
        with asset.open("r", encoding="utf-8") as handle:
            template = json.load(handle)
        template_landscape = str(template.get("orientation", "portrait")).lower() == "landscape"

        matches = [
            entry
            for entry in manifest_by_stem.get(asset.stem, [])
            if is_landscape(entry) == template_landscape
        ]
        if matches:
            entries.extend(deepcopy(matches))
            continue

        categories = template.get("categories") or ["Creative"]
        entry: dict[str, Any] = {
            "name": template.get("name") or asset.stem.replace("_", " "),
            "filename": asset.stem,
            "iconCode": DEFAULT_ICON_CODE,
            "categories": categories,
        }
        if template_landscape:
            entry["landscape"] = True
        entries.append(entry)

    return assets, entries


def safe_filename(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._ -]+", "", name).strip()
    value = re.sub(r"\s+", "_", value)
    return value or "Custom_Template"


def update_local_manifest(
    folder: Path,
    *,
    name: str,
    filename: str,
    categories: list[str],
    landscape: bool,
    icon_code: str = DEFAULT_ICON_CODE,
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / "templates.json"
    if manifest.exists():
        with manifest.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        data = {"templates": []}

    entries = data.setdefault("templates", [])
    entry: dict[str, Any] = {
        "name": name,
        "filename": filename,
        "iconCode": icon_code,
        "categories": categories or ["Creative"],
    }
    if landscape:
        entry["landscape"] = True

    key = entry_key(entry)
    replaced = False
    for i, existing in enumerate(entries):
        if entry_key(existing) == key:
            entries[i] = entry
            replaced = True
            break
    if not replaced:
        entries.append(entry)

    with manifest.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return manifest
