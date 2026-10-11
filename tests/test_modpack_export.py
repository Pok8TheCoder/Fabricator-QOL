"""Export installed mods as Modrinth or CurseForge modpack archives."""
from __future__ import annotations

import io
import json
import zipfile
from unittest.mock import MagicMock, patch

from backend.server import modpack_export, storage


def _create_server(client, name="export-test"):
    resp = client.post("/api/servers", json={
        "name": name,
        "version": "1.20.1",
        "loader": "fabric",
        "port": 26580,
        "installPath": name,
    })
    assert resp.status_code in (200, 201), resp.get_json()
    return resp.get_json()["id"]


def test_export_modrinth_mrpack_from_manifest(app, client, tmp_servers_root):
    sid = _create_server(client)
    mods_dir = tmp_servers_root / "export-test" / "mods"
    mods_dir.mkdir(parents=True)
    jar = mods_dir / "fabric-api.jar"
    jar.write_bytes(b"fake-jar-bytes-for-test")

    storage.record_content_install(sid, "fabric-api.jar", {
        "projectId": "P7dR8mSH",
        "versionId": "ver123",
        "versionNumber": "0.92.0",
    })

    version_payload = {
        "id": "ver123",
        "project_id": "P7dR8mSH",
        "files": [{
            "url": "https://cdn.modrinth.com/data/fabric-api.jar",
            "filename": "fabric-api.jar",
            "primary": True,
            "size": len(jar.read_bytes()),
            "hashes": {"sha1": "abc", "sha512": "def"},
        }],
    }

    modrinth = MagicMock()
    modrinth.get_version.return_value = version_payload

    with patch("backend.server.modpack_export.resolve_jar_files", return_value={}):
        result = modpack_export.build_modpack_export(
        storage.get_server(sid),
        mods_dir,
        format="modrinth",
        modrinth_client=modrinth,
        curseforge_client=MagicMock(),
        )

    assert result.included == 1
    assert not result.unresolved
    with zipfile.ZipFile(io.BytesIO(result.data)) as zf:
        index = json.loads(zf.read("modrinth.index.json"))
    assert index["name"] == "export-test"
    assert index["dependencies"]["minecraft"] == "1.20.1"
    assert index["files"][0]["path"] == "mods/fabric-api.jar"


def test_export_route_modrinth(app, client, tmp_servers_root):
    sid = _create_server(client)
    mods_dir = tmp_servers_root / "export-test" / "mods"
    mods_dir.mkdir(parents=True)
    (mods_dir / "a.jar").write_bytes(b"jar-a")

    storage.record_content_install(sid, "a.jar", {
        "projectId": "proj",
        "versionId": "v1",
    })

    from backend.modrinth import client as modrinth_client_module

    version = {
        "id": "v1",
        "files": [{
            "url": "https://example.com/a.jar",
            "primary": True,
            "size": 5,
            "hashes": {"sha1": "deadbeef"},
        }],
    }

    with patch.object(modrinth_client_module.ModrinthClient, "get_version", return_value=version):
        with patch("backend.server.modpack_export.resolve_jar_files", return_value={}):
            resp = client.get(f"/api/servers/{sid}/mods/export?format=modrinth")
    assert resp.status_code == 200
    assert "attachment" in (resp.headers.get("Content-Disposition") or "")


def test_export_curseforge_manifest_from_content_manifest(app, client, tmp_servers_root):
    sid = _create_server(client)
    mods_dir = tmp_servers_root / "export-test" / "mods"
    mods_dir.mkdir(parents=True)
    (mods_dir / "cf-mod.jar").write_bytes(b"cf-jar")

    storage.record_content_install(sid, "cf-mod.jar", {
        "projectId": "12345",
        "versionId": "67890",
        "source": "curseforge",
    })

    cf = MagicMock()
    cf.configured.return_value = True

    result = modpack_export.build_modpack_export(
        storage.get_server(sid),
        mods_dir,
        format="curseforge",
        modrinth_client=MagicMock(get_versions_by_hashes=MagicMock(return_value={})),
        curseforge_client=cf,
    )

    assert result.included == 1
    with zipfile.ZipFile(io.BytesIO(result.data)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["manifestType"] == "minecraftModpack"
    assert manifest["files"] == [{"projectID": 12345, "fileID": 67890, "required": True}]
