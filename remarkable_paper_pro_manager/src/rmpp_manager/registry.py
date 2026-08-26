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
    """Merge local entries into the live device registry without dropping unknown fields.

    Identity is filename + orientation. Exact duplicates are left untouched.
    A different entry with the same identity is reported as a conflict. By default
    the live-device entry wins; callers may explicitly request replacement.
    """
    merged = deepcopy(remote_registry)
    remote_templates = list(merged.get("templates", []))
    if not isinstance(remote_templates, list):
        raise ValueError("Remote templates.json does not contain a 'templates' list.")

    result = RegistryMerge(merged=merged)
    by_key = {entry_key(entry): i for i, entry in enumerate(remote_templates)}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for entry in remote_templates:
        by_name.setdefault(str(entry.get("name", "")).strip(), []).append(entry)

    for local in local_entries:
        candidate = deepcopy(local)
        key = entry_key(candidate)
        if not key[0]:
            raise ValueError(f"Template entry has no filename: {candidate!r}")

        if key in by_key:
            idx = by_key[key]
            remote = remote_templates[idx]
            if canonical(remote) == canonical(candidate):
                result.unchanged.append(candidate)
            else:
                result.conflicts.append((remote, candidate))
                if replace_conflicts:
                    remote_templates[idx] = candidate
            continue

        name = str(candidate.get("name", "")).strip()
        for remote in by_name.get(name, []):
            if entry_key(remote) != key:
                result.name_collisions.append((remote, candidate))

        remote_templates.append(candidate)
        by_key[key] = len(remote_templates) - 1
        by_name.setdefault(name, []).append(candidate)
        result.added.append(candidate)

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
