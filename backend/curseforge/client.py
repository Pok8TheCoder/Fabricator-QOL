"""CurseForge API client (mods + modpacks)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from backend.modrinth.client import ModrinthClient

logger = logging.getLogger(__name__)

MINECRAFT_GAME_ID = 432
CLASS_MOD = 6
CLASS_MODPACK = 4471

# https://docs.curseforge.com/#getting-started
LOADER_TYPE = {
    "forge": 1,
    "fabric": 4,
    "quilt": 5,
    "neoforge": 6,
}

SORT_INDEX = {
    "relevance": (1, "desc"),
    "downloads": (6, "desc"),
    "updated": (3, "desc"),
    "newest": (11, "desc"),
}


class CurseForgeApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class CurseForgeClient:
    BASE_URL = "https://api.curseforge.com/v1"
    USER_AGENT = "Pok8TheCoder/Fabricator-QOL (CurseForge integration)"

    def __init__(self, api_key: str | None = None):
        self.api_key = (api_key or os.environ.get("CURSEFORGE_API_KEY") or "").strip()

    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise CurseForgeApiError(
                "CurseForge API key is not configured. Set CURSEFORGE_API_KEY in "
                "/etc/fabricator/fabricator.env (get a key from CurseForge for Studios)."
            )
        return {
            "x-api-key": self.api_key,
            "Accept": "application/json",
            "User-Agent": self.USER_AGENT,
        }

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.BASE_URL}{path}"
        kwargs.setdefault("headers", self._headers())
        kwargs.setdefault("timeout", kwargs.pop("timeout", 30))
        try:
            response = requests.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise CurseForgeApiError(f"CurseForge request failed: {exc}") from exc
        if response.status_code == 401:
            raise CurseForgeApiError("CurseForge API key rejected (401)", 401)
        if response.status_code == 403:
            raise CurseForgeApiError("CurseForge API forbidden (403)", 403)
        if not response.ok:
            raise CurseForgeApiError(
                f"CurseForge API error {response.status_code}: {response.text[:200]}",
                response.status_code,
            )
        return response

    def _json_data(self, response: requests.Response) -> Any:
        payload = response.json()
        return payload.get("data") if isinstance(payload, dict) else payload

    def _normalize_mod_hit(self, mod: Dict[str, Any], *, modpack: bool = False) -> Dict[str, Any]:
        logo = mod.get("logo") or {}
        icon = logo.get("thumbnailUrl") or logo.get("url") or ""
        slug = mod.get("slug") or ""
        return {
            "project_id": str(mod.get("id")),
            "slug": slug,
            "title": mod.get("name") or slug or str(mod.get("id")),
            "description": mod.get("summary") or "",
            "downloads": mod.get("downloadCount") or 0,
            "icon_url": icon,
            "project_type": "modpack" if modpack else "mod",
            "catalog": "curseforge",
            "categories": [],
            "game_versions": [],
            "loaders": [],
        }

    def search(
        self,
        *,
        query: str = "",
        mc_version: str | None = None,
        loader: str | None = None,
        modpack: bool = False,
        limit: int = 20,
        offset: int = 0,
        index: str = "downloads",
    ) -> Dict[str, Any]:
        sort_field, sort_order = SORT_INDEX.get(index, SORT_INDEX["downloads"])
        params: Dict[str, Any] = {
            "gameId": MINECRAFT_GAME_ID,
            "classId": CLASS_MODPACK if modpack else CLASS_MOD,
            "pageSize": max(1, min(limit, 50)),
            "index": max(0, offset // max(1, min(limit, 50))),
            "sortField": sort_field,
            "sortOrder": sort_order,
        }
        if query.strip():
            params["searchFilter"] = query.strip()
        if mc_version:
            params["gameVersion"] = mc_version
        loader_key = (loader or "").lower()
        if loader_key in LOADER_TYPE:
            params["modLoaderType"] = LOADER_TYPE[loader_key]

        response = self._request("GET", "/mods/search", params=params)
        data = self._json_data(response) or []
        hits = [self._normalize_mod_hit(m, modpack=modpack) for m in data if isinstance(m, dict)]
        return {"hits": hits, "offset": offset, "limit": limit, "total_hits": len(hits)}

    def get_mod(self, mod_id: str | int) -> Dict[str, Any]:
        ref = str(mod_id).strip()
        if ref.isdigit():
            response = self._request("GET", f"/mods/{int(ref)}")
            data = self._json_data(response)
        else:
            data = None
            hit = self.find_mod_by_slug(ref, modpack=True)
            if not hit:
                hit = self.find_mod_by_slug(ref, modpack=False)
            if hit:
                return self.get_mod(hit["project_id"])
            raise CurseForgeApiError("Mod not found")
        if not isinstance(data, dict):
            raise CurseForgeApiError("Mod not found")
        modpack = (data.get("classId") == CLASS_MODPACK)
        hit = self._normalize_mod_hit(data, modpack=modpack)
        links = data.get("links") or {}
        hit["website_url"] = links.get("websiteUrl") or ""
        return hit

    def find_mod_by_slug(self, slug: str, *, modpack: bool = False) -> Dict[str, Any] | None:
        slug = (slug or "").strip().lower()
        if not slug:
            return None
        params = {
            "gameId": MINECRAFT_GAME_ID,
            "classId": CLASS_MODPACK if modpack else CLASS_MOD,
            "slug": slug,
        }
        response = self._request("GET", "/mods/search", params=params)
        data = self._json_data(response) or []
        for entry in data:
            if isinstance(entry, dict) and str(entry.get("slug", "")).lower() == slug:
                return self._normalize_mod_hit(entry, modpack=modpack)
        if data and isinstance(data[0], dict):
            return self._normalize_mod_hit(data[0], modpack=modpack)
        return None

    def list_files(
        self,
        mod_id: str | int,
        *,
        mc_version: str | None = None,
        loader: str | None = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"pageSize": 50}
        if mc_version:
            params["gameVersion"] = mc_version
        loader_key = (loader or "").lower()
        if loader_key in LOADER_TYPE:
            params["modLoaderType"] = LOADER_TYPE[loader_key]
        response = self._request("GET", f"/mods/{int(mod_id)}/files", params=params)
        data = self._json_data(response) or []
        return [f for f in data if isinstance(f, dict)]

    def pick_best_file(
        self,
        mod_id: str | int,
        mc_version: str,
        loader: str | None,
    ) -> Optional[Dict[str, Any]]:
        files = self.list_files(mod_id, mc_version=mc_version, loader=loader)
        if not files:
            files = self.list_files(mod_id, mc_version=mc_version, loader=None)
        if not files:
            return None

        loader_key = (loader or "").lower()
        loader_type = LOADER_TYPE.get(loader_key)

        def score(file_entry: Dict[str, Any]) -> tuple:
            versions = file_entry.get("gameVersions") or []
            version_match = mc_version in versions
            loaders = file_entry.get("modLoaders") or []
            if loaders and isinstance(loaders[0], dict):
                loader_match = not loader_type or any(
                    l.get("id") == loader_type for l in loaders if isinstance(l, dict)
                )
            else:
                loader_match = not loader_type or loader_type in loaders
            date = file_entry.get("fileDate") or ""
            return (version_match and loader_match, date)

        files.sort(key=score, reverse=True)
        return files[0]

    def file_download_url(self, mod_id: str | int, file_id: str | int) -> str:
        response = self._request(
            "GET", f"/mods/{int(mod_id)}/files/{int(file_id)}/download-url"
        )
        data = self._json_data(response)
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            url = data.get("downloadUrl") or data.get("url")
            if url:
                return str(url)
        raise CurseForgeApiError("No download URL in CurseForge response")

    def download_to(
        self,
        url: str,
        target_folder: Path,
        filename: str,
        *,
        sha1: str | None = None,
    ) -> Path:
        target_folder = target_folder.resolve()
        target_folder.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", filename) or "download.jar"
        target_path = (target_folder / safe_name).resolve()
        try:
            target_path.relative_to(target_folder)
        except ValueError:
            raise CurseForgeApiError("Refusing to save file outside target folder")

        digest = hashlib.sha1() if sha1 else None
        try:
            response = requests.get(
                url,
                stream=True,
                timeout=120,
                headers={"User-Agent": self.USER_AGENT},
            )
        except requests.RequestException as exc:
            raise CurseForgeApiError(f"Download failed: {exc}") from exc
        if not response.ok:
            raise CurseForgeApiError(f"Download failed with status {response.status_code}")
        with open(target_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                handle.write(chunk)
                if digest:
                    digest.update(chunk)
        response.close()
        if digest and sha1 and digest.hexdigest().lower() != sha1.lower():
            target_path.unlink(missing_ok=True)
            raise CurseForgeApiError(f"SHA-1 mismatch for {safe_name}")
        return target_path

    def resolve_mod_download(
        self, mod_id: str | int, mc_version: str, loader: str | None
    ) -> Optional[Dict[str, Any]]:
        file_entry = self.pick_best_file(mod_id, mc_version, loader)
        if not file_entry:
            return None
        file_id = file_entry.get("id")
        url = self.file_download_url(mod_id, file_id)
        hashes = file_entry.get("hashes") or []
        sha1 = None
        for h in hashes:
            if isinstance(h, dict) and h.get("algo") == 1:
                sha1 = h.get("value")
        return {
            "url": url,
            "filename": file_entry.get("fileName") or f"{mod_id}-{file_id}.jar",
            "file_id": file_id,
            "version_number": file_entry.get("displayName") or str(file_id),
            "hashes": {"sha1": sha1} if sha1 else {},
        }

    def install_modpack(
        self,
        mod_id: str | int,
        install_path: Path,
        mc_version: str | None,
        loader: str | None,
        *,
        clean_install: bool = False,
        progress_callback=None,
    ) -> Dict[str, Any]:
        def _report(stage: str, current: int = 0, total: int = 0, detail: str = ""):
            if progress_callback:
                progress_callback(stage=stage, current=current, total=total, detail=detail)

        _report("resolving")
        mod_meta = self.get_mod(mod_id)
        file_entry = self.pick_best_file(mod_id, mc_version or "", loader)
        if not file_entry:
            raise CurseForgeApiError("No compatible CurseForge modpack file found")

        file_id = file_entry["id"]
        pack_url = self.file_download_url(mod_id, file_id)

        if clean_install:
            _report("cleaning")
            ModrinthClient().clean_modpack_switch_paths(install_path)

        _report("downloading_pack")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = self.download_to(
                pack_url, tmp_path, file_entry.get("fileName") or "modpack.zip"
            )
            result = self._install_curseforge_archive(
                archive, install_path, progress_callback=_report
            )

        result.update({
            "name": mod_meta.get("title") or mod_meta.get("slug"),
            "version": file_entry.get("displayName"),
            "mc_version": mc_version,
            "loaders": [loader] if loader else [],
            "project_id": str(mod_id),
            "version_id": str(file_id),
            "source": "curseforge",
        })
        return result

    def _install_curseforge_archive(
        self,
        archive_path: Path,
        install_path: Path,
        *,
        progress_callback=None,
    ) -> Dict[str, Any]:
        install_path = install_path.resolve()
        files_installed: List[str] = []
        files_skipped: List[str] = []

        with zipfile.ZipFile(archive_path, "r") as zf:
            manifest_name = None
            for candidate in ("manifest.json", "modlist.json"):
                if candidate in zf.namelist():
                    manifest_name = candidate
                    break
            if not manifest_name:
                raise CurseForgeApiError("CurseForge pack is missing manifest.json")

            manifest = json.loads(zf.read(manifest_name).decode("utf-8"))
            entries = manifest.get("files") or []
            total = len(entries)
            mods_dir = install_path / "mods"
            mods_dir.mkdir(parents=True, exist_ok=True)

            for idx, entry in enumerate(entries):
                if progress_callback:
                    progress_callback(
                        stage="installing_files",
                        current=idx,
                        total=total,
                        detail=entry.get("projectID") or "",
                    )
                project_id = entry.get("projectID") or entry.get("projectId")
                file_id = entry.get("fileID") or entry.get("fileId")
                if not project_id or not file_id:
                    files_skipped.append(str(entry))
                    continue
                try:
                    url = self.file_download_url(project_id, file_id)
                    meta = self._request(
                        "GET", f"/mods/{int(project_id)}/files/{int(file_id)}"
                    )
                    file_data = self._json_data(meta) or {}
                    name = file_data.get("fileName") or f"{project_id}-{file_id}.jar"
                    dest = self.download_to(url, mods_dir, name)
                    files_installed.append(f"mods/{dest.name}")
                except CurseForgeApiError as exc:
                    logger.warning("Skipped CF mod file %s/%s: %s", project_id, file_id, exc)
                    files_skipped.append(f"{project_id}:{file_id}")

            override_prefixes = ("overrides/", "override/")
            for name in zf.namelist():
                if not any(name.startswith(p) for p in override_prefixes):
                    continue
                rel = name
                for p in override_prefixes:
                    if rel.startswith(p):
                        rel = rel[len(p):]
                        break
                if not rel or rel.endswith("/"):
                    continue
                target = (install_path / rel).resolve()
                try:
                    target.relative_to(install_path)
                except ValueError:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))
                files_installed.append(rel)

        return {
            "files_installed": files_installed,
            "files_skipped": files_skipped,
            "manifest_type": "curseforge",
        }
