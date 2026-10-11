"""CurseForge catalog routes (mods + modpacks)."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from backend.curseforge.client import CurseForgeApiError, CurseForgeClient
from backend.modrinth.routes import (
    _get_install_progress,
    _resolve_mods_folder,
    _run_modpack_install,
)
from backend.server import storage
from backend.utils.routes import require_server, with_server_lock
from backend.utils.time import iso_z_now

curseforge_bp = Blueprint("curseforge", __name__, url_prefix="/api/curseforge")
_client = CurseForgeClient()


def _cf_error(exc: CurseForgeApiError):
    status = exc.status_code or 502
    if status < 400 or status > 599:
        status = 502
    return jsonify({"error": str(exc)}), status


def _record_cf_install(server_id: str, mod_id: str, filename: str, resolved: dict) -> None:
    entry = {
        "projectId": str(mod_id),
        "versionId": str(resolved.get("file_id")),
        "versionNumber": resolved.get("version_number"),
        "installedAt": iso_z_now(),
        "pinned": False,
        "source": "curseforge",
        "title": resolved.get("title"),
        "slug": resolved.get("slug"),
        "iconUrl": resolved.get("icon_url"),
    }
    try:
        storage.record_content_install(server_id, filename, entry)
    except OSError as exc:
        current_app.logger.warning("Failed to record CurseForge manifest for %s: %s", filename, exc)


@curseforge_bp.route("/status", methods=["GET"])
def curseforge_status():
    return jsonify({"configured": _client.configured()})


@curseforge_bp.route("/search", methods=["GET"])
def search_mods():
    if not _client.configured():
        return jsonify({"error": "CurseForge API key is not configured"}), 503
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    try:
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        result = _client.search(
            query=request.args.get("query", ""),
            mc_version=request.args.get("mc_version"),
            loader=request.args.get("loader"),
            modpack=False,
            limit=limit,
            offset=offset,
            index=request.args.get("index", "downloads"),
        )
        return jsonify(result)
    except CurseForgeApiError as exc:
        return _cf_error(exc)


@curseforge_bp.route("/modpacks/search", methods=["GET"])
def search_modpacks():
    if not _client.configured():
        return jsonify({"error": "CurseForge API key is not configured"}), 503
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    try:
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        result = _client.search(
            query=request.args.get("query", ""),
            mc_version=request.args.get("mc_version"),
            loader=request.args.get("loader"),
            modpack=True,
            limit=limit,
            offset=offset,
            index=request.args.get("index", "downloads"),
        )
        return jsonify(result)
    except CurseForgeApiError as exc:
        return _cf_error(exc)


@curseforge_bp.route("/mod/<mod_id>", methods=["GET"])
def get_mod(mod_id):
    if not _client.configured():
        return jsonify({"error": "CurseForge API key is not configured"}), 503
    try:
        return jsonify(_client.get_mod(mod_id))
    except CurseForgeApiError as exc:
        return _cf_error(exc)


@curseforge_bp.route("/project/<project_id>", methods=["GET"])
def get_project(project_id):
    return get_mod(project_id)


@curseforge_bp.route("/mod/<mod_id>/install", methods=["POST"])
@require_server(source="body")
@with_server_lock(source="body")
def install_mod(mod_id, server):
    if not _client.configured():
        return jsonify({"error": "CurseForge API key is not configured"}), 503
    data = request.get_json() or {}
    mc_version = data.get("mc_version")
    loader = data.get("loader", "fabric")
    server_id = data.get("server_id")
    if not mc_version:
        return jsonify({"error": "mc_version is required"}), 400

    mods_folder, error = _resolve_mods_folder(server)
    if error:
        payload, status = error
        return jsonify(payload), status

    try:
        resolved = _client.resolve_mod_download(mod_id, mc_version, loader)
        if not resolved:
            return jsonify({"error": "No suitable CurseForge file found"}), 404
        meta = _client.get_mod(mod_id)
        resolved["title"] = meta.get("title")
        resolved["slug"] = meta.get("slug")
        resolved["icon_url"] = meta.get("icon_url")
        file_path = _client.download_to(
            resolved["url"],
            Path(mods_folder),
            resolved["filename"],
            sha1=(resolved.get("hashes") or {}).get("sha1"),
        )
    except CurseForgeApiError as exc:
        return _cf_error(exc)

    _record_cf_install(server_id, mod_id, file_path.name, resolved)
    return jsonify({
        "success": True,
        "message": "Mod installed successfully",
        "file": file_path.name,
        "path": str(file_path),
        "versionId": resolved.get("file_id"),
        "versionNumber": resolved.get("version_number"),
    })


@curseforge_bp.route("/modpack/<project_id>/install", methods=["POST"])
@require_server(source="body")
@with_server_lock(
    source="body",
    busy_message="A modpack install is already in progress for this server",
)
def install_modpack(project_id, server):
    if not _client.configured():
        return jsonify({"error": "CurseForge API key is not configured"}), 503
    data = request.get_json() or {}

    def _run(install_path, progress_cb):
        return _client.install_modpack(
            project_id,
            install_path,
            data.get("mc_version"),
            data.get("loader"),
            clean_install=bool(data.get("clean_install", True)),
            progress_callback=progress_cb,
        )

    def _modpack_info(result):
        return {
            "projectId": str(project_id),
            "versionId": result.get("version_id"),
            "name": result.get("name"),
            "version": result.get("version"),
            "mcVersion": data.get("mc_version"),
            "loaders": result.get("loaders") or [data.get("loader")],
            "source": "curseforge",
            "installedAt": iso_z_now(),
        }

    return _run_modpack_install(
        server, data, _run, _modpack_info, log_label=f"curseforge:{project_id}"
    )


@curseforge_bp.route("/modpack/install-progress/<server_id>", methods=["GET"])
def modpack_install_progress(server_id):
    return jsonify(_get_install_progress(server_id))
