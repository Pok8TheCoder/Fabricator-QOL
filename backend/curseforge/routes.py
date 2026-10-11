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


def _installed_project_ids(server_id: str) -> set[str]:
    manifest = storage.get_content_manifest(server_id)
    ids: set[str] = set()
    for entry in manifest.values():
        if isinstance(entry, dict) and entry.get("projectId"):
            ids.add(str(entry["projectId"]))
    return ids


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
    return jsonify(_client.probe())


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


@curseforge_bp.route("/mod/<mod_id>/dependencies", methods=["GET"])
def mod_install_dependencies(mod_id):
    """Required CurseForge dependencies for a mod that are not installed yet."""
    if not _client.configured():
        return jsonify({"error": "CurseForge API key is not configured"}), 503
    server_id = (request.args.get("server_id") or "").strip()
    if not server_id:
        return jsonify({"error": "server_id is required"}), 400
    server = storage.get_server(server_id)
    if not server:
        return jsonify({"error": "Server not found"}), 404
    mc_version = request.args.get("mc_version")
    loader = request.args.get("loader", "fabric")
    if not mc_version:
        return jsonify({"error": "mc_version is required"}), 400

    installed = _installed_project_ids(server_id)
    try:
        order = _client.build_mod_install_order(mod_id, mc_version, loader, installed)
    except CurseForgeApiError as exc:
        return _cf_error(exc)

    missing = []
    for mid in order:
        if str(mid) == str(mod_id):
            continue
        if str(mid) in installed:
            continue
        try:
            meta = _client.get_mod(mid)
            title = meta.get("title") or meta.get("slug") or str(mid)
        except CurseForgeApiError:
            title = str(mid)
        missing.append({"modId": str(mid), "modTitle": title})

    return jsonify({"missing": missing})


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
    install_dependencies = data.get("install_dependencies", True)
    if isinstance(install_dependencies, str):
        install_dependencies = install_dependencies.strip().lower() not in (
            "0", "false", "no", "off",
        )
    if not mc_version:
        return jsonify({"error": "mc_version is required"}), 400

    mods_folder, error = _resolve_mods_folder(server)
    if error:
        payload, status = error
        return jsonify(payload), status

    mods_path = Path(mods_folder)
    installed = _installed_project_ids(server_id)

    try:
        if install_dependencies:
            install_order = _client.build_mod_install_order(
                mod_id, mc_version, loader, installed,
            )
        else:
            install_order = [int(mod_id)] if str(mod_id) not in installed else []

        if not install_order:
            return jsonify({"error": "Mod is already installed"}), 409

        dependency_files: list[str] = []
        main_file = None
        main_resolved = None

        for idx, mid in enumerate(install_order):
            resolved = _client.install_mod_to_folder(
                mid, mods_path, mc_version, loader,
            )
            try:
                meta = _client.get_mod(mid)
                resolved["title"] = meta.get("title")
                resolved["slug"] = meta.get("slug")
                resolved["icon_url"] = meta.get("icon_url")
            except CurseForgeApiError:
                pass
            _record_cf_install(server_id, str(mid), resolved["file"], resolved)
            installed.add(str(mid))
            if str(mid) == str(mod_id):
                main_file = resolved["file"]
                main_resolved = resolved
            elif install_dependencies:
                dependency_files.append(resolved["file"])

        if main_resolved is None:
            main_resolved = resolved
            main_file = resolved["file"]
    except CurseForgeApiError as exc:
        return _cf_error(exc)

    return jsonify({
        "success": True,
        "message": "Mod installed successfully",
        "file": main_file,
        "path": main_resolved.get("path"),
        "versionId": main_resolved.get("file_id"),
        "versionNumber": main_resolved.get("version_number"),
        "dependenciesInstalled": dependency_files,
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
