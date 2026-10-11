"""Build Modrinth (.mrpack) or CurseForge modpack zips from installed mods."""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.curseforge.client import CurseForgeApiError, CurseForgeClient
from backend.modrinth.client import ModrinthApiError, ModrinthClient
from backend.modrinth.installed import file_sha1, resolve_jar_files
_LOADER_TO_MR_DEPENDENCY = {
    "quilt": "quilt-loader",
    "fabric": "fabric-loader",
    "neoforge": "neoforge",
    "forge": "forge",
}
from backend.server import mod_files, storage
from backend.utils.cf_fingerprint import curseforge_fingerprint

_EXPORT_VERSION = "1.0.0"
_FINGERPRINT_CHUNK = 80


def _safe_download_stem(name: str) -> str:
    stem = re.sub(r"[^\w.\-]+", "-", (name or "server").strip())[:80].strip(".-")
    return stem or "server"


def _loader_dependency_key(loader: str) -> Optional[str]:
    loader = (loader or "").lower()
    return _LOADER_TO_MR_DEPENDENCY.get(loader)


def _cf_mod_loader_id(loader: str, loader_version: str) -> str:
    loader = (loader or "fabric").lower()
    version = (loader_version or "0.0.0").strip() or "0.0.0"
    if loader in ("forge", "neoforge"):
        return f"{loader}-{version}"
    return f"{loader}-{version}"


def _list_export_jars(mods_path: Path) -> List[Path]:
    if not mods_path.is_dir():
        return []
    jars: List[Path] = []
    for path in sorted(mods_path.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        if mod_files.is_disabled_mod_filename(path.name):
            continue
        if path.suffix.lower() != ".jar":
            continue
        jars.append(path)
    return jars


def _manifest_hints(
    manifest: Dict[str, Any], filename: str
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (catalog, project_id, version_id) from content manifest."""
    entry = manifest.get(mod_files.manifest_key_for_filename(filename))
    if not isinstance(entry, dict):
        return None, None, None
    source = (entry.get("source") or "modrinth").lower()
    project = entry.get("projectId")
    version = entry.get("versionId")
    if not project or not version:
        return source if source == "curseforge" else "modrinth", None, None
    return source, str(project), str(version)


def _mr_index_file_from_version(
    version: Dict[str, Any],
    jar_path: Path,
    rel_path: str,
) -> Optional[Dict[str, Any]]:
    sha1 = file_sha1(jar_path)
    chosen: Optional[Dict[str, Any]] = None
    for file_obj in version.get("files") or []:
        if not isinstance(file_obj, dict):
            continue
        hashes = file_obj.get("hashes") or {}
        if sha1 and str(hashes.get("sha1", "")).lower() == sha1.lower():
            chosen = file_obj
            break
    if chosen is None:
        files = [f for f in (version.get("files") or []) if isinstance(f, dict)]
        primary = [f for f in files if f.get("primary")]
        chosen = (primary[0] if primary else (files[0] if files else None))
    if not chosen:
        return None
    url = chosen.get("url")
    if not url:
        return None
    hashes = chosen.get("hashes") or {}
    entry: Dict[str, Any] = {
        "path": rel_path,
        "downloads": [url],
        "fileSize": chosen.get("size") or jar_path.stat().st_size,
        "hashes": {k: v for k, v in hashes.items() if k in ("sha1", "sha512") and v},
        "env": {"client": "optional", "server": "required"},
    }
    return entry


class ModpackExportResult:
    def __init__(
        self,
        *,
        data: bytes,
        download_name: str,
        unresolved: List[str],
        included: int,
    ):
        self.data = data
        self.download_name = download_name
        self.unresolved = unresolved
        self.included = included


def build_modpack_export(
    server: Dict[str, Any],
    mods_path: Path,
    *,
    format: str,
    modrinth_client: ModrinthClient,
    curseforge_client: CurseForgeClient,
) -> ModpackExportResult:
    """Assemble a modpack archive for ``format`` (``modrinth`` or ``curseforge``)."""
    fmt = (format or "modrinth").lower()
    if fmt not in ("modrinth", "curseforge"):
        raise ValueError("format must be modrinth or curseforge")

    server_id = str(server.get("id") or "")
    manifest = storage.get_content_manifest(server_id) if server_id else {}
    jars = _list_export_jars(mods_path)

    mc_version = str(server.get("version") or "unknown")
    loader = str(server.get("loader") or "fabric").lower()
    loader_version = str(server.get("loaderVersion") or "0.0.0")
    pack_name = server.get("name") or server.get("installPath") or "server"
    version_label = datetime.now(timezone.utc).strftime("%Y%m%d")

    unresolved: List[str] = []
    mr_files: List[Dict[str, Any]] = []
    cf_files: List[Dict[str, Any]] = []

    # Resolve Modrinth versions for jars without manifest version ids.
    hash_resolved = resolve_jar_files(modrinth_client, jars) if jars else {}

    version_cache: Dict[str, Dict[str, Any]] = {}

    def _modrinth_version(version_id: str) -> Dict[str, Any]:
        if version_id not in version_cache:
            version_cache[version_id] = modrinth_client.get_version(version_id)
        return version_cache[version_id]

    # CurseForge fingerprint map: fingerprint -> (modId, fileId)
    jar_fingerprints: Dict[str, int] = {}
    for jar in jars:
        try:
            jar_fingerprints[jar.name] = curseforge_fingerprint(jar.read_bytes())
        except OSError:
            jar_fingerprints[jar.name] = -1

    cf_by_fingerprint: Dict[int, Tuple[int, int]] = {}
    if fmt == "curseforge" and jars and curseforge_client.configured():
        fps = [fp for fp in jar_fingerprints.values() if fp >= 0]
        for start in range(0, len(fps), _FINGERPRINT_CHUNK):
            chunk = fps[start : start + _FINGERPRINT_CHUNK]
            try:
                matches = curseforge_client.get_fingerprint_matches(chunk)
            except CurseForgeApiError:
                matches = {}
            for row in matches.get("exactMatches") or []:
                if not isinstance(row, dict):
                    continue
                fp = row.get("id")
                file_obj = row.get("file") or {}
                mod_id = file_obj.get("modId")
                file_id = file_obj.get("id")
                if isinstance(fp, int) and mod_id and file_id:
                    cf_by_fingerprint[fp] = (int(mod_id), int(file_id))

    for jar in jars:
        rel_path = f"mods/{jar.name}"
        catalog, project_id, version_id = _manifest_hints(manifest, jar.name)

        if fmt == "modrinth":
            if not version_id and jar.name in hash_resolved:
                version_id = hash_resolved[jar.name].get("versionId")
            if not version_id:
                unresolved.append(jar.name)
                continue
            try:
                version = _modrinth_version(str(version_id))
            except ModrinthApiError:
                unresolved.append(jar.name)
                continue
            entry = _mr_index_file_from_version(version, jar, rel_path)
            if not entry:
                unresolved.append(jar.name)
                continue
            mr_files.append(entry)
            continue

        # CurseForge export
        if catalog == "curseforge" and project_id and version_id:
            try:
                cf_files.append({
                    "projectID": int(project_id),
                    "fileID": int(version_id),
                    "required": True,
                })
            except (TypeError, ValueError):
                unresolved.append(jar.name)
            continue

        fp = jar_fingerprints.get(jar.name, -1)
        if fp < 0:
            unresolved.append(jar.name)
            continue
        match = cf_by_fingerprint.get(fp)
        if not match:
            unresolved.append(jar.name)
            continue
        mod_id, file_id = match
        cf_files.append({
            "projectID": mod_id,
            "fileID": file_id,
            "required": True,
        })

    if fmt == "curseforge" and not curseforge_client.configured():
        raise CurseForgeApiError("CurseForge API key is not configured")

    stem = _safe_download_stem(str(pack_name))
    if fmt == "modrinth":
        index: Dict[str, Any] = {
            "formatVersion": 1,
            "game": "minecraft",
            "versionId": _EXPORT_VERSION,
            "name": str(pack_name),
            "summary": f"Exported from Fabricator ({version_label})",
            "files": mr_files,
            "dependencies": {"minecraft": mc_version},
        }
        dep_key = _loader_dependency_key(loader)
        if dep_key:
            index["dependencies"][dep_key] = loader_version

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("modrinth.index.json", json.dumps(index, indent=2))
        ext = "mrpack"
        included = len(mr_files)
    else:
        cf_manifest = {
            "minecraft": {
                "version": mc_version,
                "modLoaders": [
                    {"id": _cf_mod_loader_id(loader, loader_version), "primary": True},
                ],
            },
            "manifestType": "minecraftModpack",
            "manifestVersion": 1,
            "name": str(pack_name),
            "version": version_label,
            "author": "Fabricator",
            "files": cf_files,
            "overrides": "overrides",
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(cf_manifest, indent=2))
            zf.writestr("overrides/.gitkeep", "")
        ext = "zip"
        included = len(cf_files)

    return ModpackExportResult(
        data=buffer.getvalue(),
        download_name=f"{stem}-mods-{version_label}.{ext}",
        unresolved=unresolved,
        included=included,
    )
