"""CurseForge single-mod install pulls required dependencies first."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.curseforge.client import CurseForgeClient, FILE_RELATION_REQUIRED


def test_build_mod_install_order_dependencies_before_dependent():
    client = CurseForgeClient(api_key="$2a$10$testkeyfortestonly000000000000000000000")

    def fake_resolve(mod_id, mc_version, loader):
        mid = int(mod_id)
        return {
            "file_id": mid * 10,
            "url": f"https://example/{mid}.jar",
            "filename": f"{mid}.jar",
            "hashes": {},
        }

    def fake_get_file(mod_id, file_id):
        mid = int(mod_id)
        if mid == 100:
            return {
                "dependencies": [
                    {"modId": 200, "relationType": FILE_RELATION_REQUIRED},
                ],
            }
        if mid == 200:
            return {"dependencies": []}
        return {"dependencies": []}

    client.resolve_mod_download = fake_resolve  # type: ignore[method-assign]
    client.get_mod_file = fake_get_file  # type: ignore[method-assign]

    order = client.build_mod_install_order(100, "1.20.1", "fabric", set())
    assert order == [200, 100]

    order_skip = client.build_mod_install_order(100, "1.20.1", "fabric", {"200"})
    assert order_skip == [100]


def test_install_mod_route_installs_dependencies(client, tmp_servers_root):
    resp = client.post("/api/servers", json={
        "name": "cf-deps",
        "version": "1.20.1",
        "loader": "fabric",
        "port": 26581,
        "installPath": "cf-deps",
    })
    assert resp.status_code in (200, 201)
    sid = resp.get_json()["id"]

    cf = MagicMock()
    cf.configured.return_value = True
    cf.build_mod_install_order.return_value = [200, 100]
    cf.install_mod_to_folder.side_effect = [
        {"file": "dep.jar", "file_id": 2, "path": "/x/dep.jar", "version_number": "d"},
        {"file": "main.jar", "file_id": 1, "path": "/x/main.jar", "version_number": "m"},
    ]
    cf.get_mod.side_effect = lambda mid: {"title": f"Mod {mid}"}

    mods_dir = tmp_servers_root / "cf-deps" / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)

    with patch("backend.curseforge.routes._client", cf):
        with patch("backend.curseforge.routes._resolve_mods_folder", return_value=(str(mods_dir), None)):
            install = client.post(
                "/api/curseforge/mod/100/install",
                json={
                    "server_id": sid,
                    "mc_version": "1.20.1",
                    "loader": "fabric",
                    "install_dependencies": True,
                },
            )
    assert install.status_code == 200, install.get_json()
    body = install.get_json()
    assert body["file"] == "main.jar"
    assert body["dependenciesInstalled"] == ["dep.jar"]
    assert cf.install_mod_to_folder.call_count == 2
