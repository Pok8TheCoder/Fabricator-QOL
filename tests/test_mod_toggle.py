"""Toggle installed mods via .jar.disabled rename."""


def _create_server(client):
    resp = client.post(
        "/api/servers",
        json={
            "name": "toggle-test",
            "version": "1.21.1",
            "loader": "neoforge",
            "memory": 4,
        },
    )
    assert resp.status_code == 201
    return resp.get_json()["id"]


def test_toggle_mod_disable_and_enable(client, tmp_servers_root):
    from backend.server.registry import get_server_process_registry
    from backend.server import storage

    sid = _create_server(client)
    server = storage.get_server(sid)
    mods = get_server_process_registry().resolve_content_path(server)
    (mods / "example.jar").write_bytes(b"jar-bytes")

    listed = client.get(f"/api/servers/{sid}/mods").get_json()
    assert len(listed) == 1
    assert listed[0]["name"] == "example.jar"
    assert listed[0]["disabled"] is False

    off = client.post(f"/api/servers/{sid}/mods/example.jar/toggle")
    assert off.status_code == 200
    body = off.get_json()
    assert body["enabled"] is False
    assert body["filename"] == "example.jar.disabled"
    assert not (mods / "example.jar").exists()
    assert (mods / "example.jar.disabled").is_file()

    listed = client.get(f"/api/servers/{sid}/mods").get_json()
    assert listed[0]["disabled"] is True
    assert listed[0]["enabledFilename"] == "example.jar"

    on = client.post(f"/api/servers/{sid}/mods/example.jar.disabled/toggle")
    assert on.status_code == 200
    assert on.get_json()["enabled"] is True
    assert (mods / "example.jar").is_file()


def test_delete_disabled_mod_forgets_manifest(client, tmp_servers_root):
    from backend.server.registry import get_server_process_registry
    from backend.server import storage

    sid = _create_server(client)
    server = storage.get_server(sid)
    mods = get_server_process_registry().resolve_content_path(server)
    (mods / "p.jar").write_bytes(b"x")
    storage.record_content_install(sid, "p.jar", {"projectId": "ABC"})
    client.post(f"/api/servers/{sid}/mods/p.jar/toggle")

    resp = client.delete(f"/api/servers/{sid}/mods/p.jar.disabled")
    assert resp.status_code == 200
    assert storage.get_content_manifest(sid) == {}
