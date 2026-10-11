"""Detect and summarize uploaded modpack archives (Modrinth, CurseForge / Prism)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.modrinth import mrpack

MANIFEST_CANDIDATES = ("manifest.json", "modlist.json")
_LOADER_PREFIXES = (
    ("neoforge-", "neoforge"),
    ("fabric-", "fabric"),
    ("quilt-", "quilt"),
    ("forge-", "forge"),
)


class UnsupportedPackArchiveError(Exception):
    """Raised when an upload is not a recognized modpack zip."""


def _parse_cf_loader_id(loader_id: str) -> Tuple[str, str]:
    raw = (loader_id or "").strip()
    if not raw:
        return "", ""
    lowered = raw.lower()
    for prefix, name in _LOADER_PREFIXES:
        if lowered.startswith(prefix):
            return name, raw[len(prefix) :]
    return "", ""


def _read_manifest_json(zf: zipfile.ZipFile) -> Tuple[str, Dict[str, Any]]:
    for name in MANIFEST_CANDIDATES:
        if name not in zf.namelist():
            continue
        try:
            info = zf.getinfo(name)
        except KeyError:
            continue
        if info.file_size > mrpack._MAX_INDEX_BYTES:
            raise UnsupportedPackArchiveError(f"{name} is implausibly large")
        with zf.open(info) as fh:
            raw = fh.read(mrpack._MAX_INDEX_BYTES + 1)
        try:
            manifest = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise UnsupportedPackArchiveError(f"{name} is not valid JSON") from None
        if not isinstance(manifest, dict):
            raise UnsupportedPackArchiveError(f"{name} is not a JSON object")
        if manifest.get("manifestType") != "minecraftModpack":
            raise UnsupportedPackArchiveError(
                f"{name} is not a CurseForge / Prism modpack manifest"
            )
        return name, manifest
    raise UnsupportedPackArchiveError(
        "Archive does not contain manifest.json — not a CurseForge / Prism modpack zip"
    )


def read_curseforge_manifest(archive_path: Path) -> Dict[str, Any]:
    """Parse ``manifest.json`` from a CurseForge-format zip."""
    try:
        with zipfile.ZipFile(archive_path) as zf:
            mrpack._check_extracted_size(zf)
            _manifest_name, manifest = _read_manifest_json(zf)
    except zipfile.BadZipFile:
        raise UnsupportedPackArchiveError(
            "Upload is not a readable zip archive"
        ) from None
    except OSError as exc:
        raise UnsupportedPackArchiveError(f"Could not read the uploaded file: {exc}") from exc
    return manifest


def describe_curseforge_manifest(
    manifest: Dict[str, Any], archive_path: Optional[Path] = None
) -> Dict[str, Any]:
    """Build a summary aligned with :func:`mrpack.describe` for the UI."""
    minecraft = manifest.get("minecraft") or {}
    mc_version = str(minecraft.get("version") or "").strip()

    loader = ""
    loader_version = ""
    mod_loaders = minecraft.get("modLoaders") or []
    if isinstance(mod_loaders, list):
        primary = None
        for entry in mod_loaders:
            if isinstance(entry, dict) and entry.get("primary"):
                primary = entry
                break
        if primary is None:
            for entry in mod_loaders:
                if isinstance(entry, dict) and entry.get("id"):
                    primary = entry
                    break
        if isinstance(primary, dict):
            loader, loader_version = _parse_cf_loader_id(str(primary.get("id") or ""))

    files = manifest.get("files") or []
    file_count = len(files) if isinstance(files, list) else 0

    summary: Dict[str, Any] = {
        "catalog": "curseforge",
        "name": str(manifest.get("name") or "").strip(),
        "version": str(manifest.get("version") or "").strip(),
        "summary": str(manifest.get("author") or "").strip(),
        "minecraft_version": mc_version,
        "loader": loader,
        "loader_version": loader_version,
        "file_count": file_count,
        "client_only_count": 0,
        "dependencies": {"minecraft": mc_version} if mc_version else {},
    }
    if loader and loader_version:
        key = {
            "fabric": "fabric-loader",
            "quilt": "quilt-loader",
            "forge": "forge",
            "neoforge": "neoforge",
        }.get(loader, f"{loader}-loader")
        summary["dependencies"][key] = loader_version

    if archive_path is not None:
        summary.update(mrpack._override_flags(archive_path))

    return summary


def inspect_uploaded_archive(archive_path: Path) -> Tuple[str, Dict[str, Any]]:
    """Return ``(catalog, summary)`` for a staged modpack upload.

    Supports Modrinth ``.mrpack`` / Prism Modrinth exports (``modrinth.index.json``)
    and CurseForge / Prism Flame exports (``manifest.json``).
    """
    try:
        index = mrpack.read_index(archive_path)
    except mrpack.InvalidMrpackError:
        manifest = read_curseforge_manifest(archive_path)
        summary = describe_curseforge_manifest(manifest, archive_path)
        return "curseforge", summary

    summary = mrpack.describe(index, archive_path)
    summary["catalog"] = "modrinth"
    return "modrinth", summary
