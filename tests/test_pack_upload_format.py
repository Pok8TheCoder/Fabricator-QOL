"""Uploaded modpack archives: Modrinth .mrpack and Prism / CurseForge zips."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from backend.modpack.upload_format import inspect_uploaded_archive


def _cf_zip(tmp_path: Path, manifest: dict, name="prism-pack.zip") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("overrides/config/example.cfg", "x=1")
    return path


def test_inspect_curseforge_prism_export(tmp_path):
    manifest = {
        "manifestType": "minecraftModpack",
        "manifestVersion": 1,
        "name": "My Prism Pack",
        "version": "1.0.0",
        "author": "me",
        "overrides": "overrides",
        "minecraft": {
            "version": "1.20.1",
            "modLoaders": [{"id": "fabric-0.15.11", "primary": True}],
        },
        "files": [
            {"projectID": 1, "fileID": 2, "required": True},
        ],
    }
    path = _cf_zip(tmp_path, manifest)
    catalog, summary = inspect_uploaded_archive(path)
    assert catalog == "curseforge"
    assert summary["name"] == "My Prism Pack"
    assert summary["minecraft_version"] == "1.20.1"
    assert summary["loader"] == "fabric"
    assert summary["loader_version"] == "0.15.11"
    assert summary["file_count"] == 1
    assert summary["has_overrides"] is True


def test_upload_route_accepts_curseforge_zip(client, tmp_path):
    manifest = {
        "manifestType": "minecraftModpack",
        "manifestVersion": 1,
        "name": "CF upload",
        "version": "1",
        "minecraft": {
            "version": "1.21",
            "modLoaders": [{"id": "forge-51.0.0", "primary": True}],
        },
        "files": [],
    }
    path = _cf_zip(tmp_path, manifest, "pack.zip")
    with path.open("rb") as fh:
        resp = client.post(
            "/api/modrinth/modpack/upload?filename=pack.zip",
            data=fh.read(),
            content_type="application/octet-stream",
        )
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["catalog"] == "curseforge"
    assert body["loader"] == "forge"
