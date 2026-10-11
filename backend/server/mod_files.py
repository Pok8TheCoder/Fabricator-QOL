"""Helpers for mod/plugin jar files in the content folder (mods/ or plugins/)."""
from __future__ import annotations

MOD_DISABLED_SUFFIX = ".disabled"


def is_mod_content_filename(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(".jar") or lowered.endswith(".jar.disabled")


def is_disabled_mod_filename(name: str) -> bool:
    return name.lower().endswith(".jar.disabled")


def enabled_mod_filename(name: str) -> str:
    """Basename when the mod is enabled (strip a trailing ``.disabled``)."""
    if is_disabled_mod_filename(name):
        return name[: -len(MOD_DISABLED_SUFFIX)]
    return name


def disabled_mod_filename(enabled_name: str) -> str:
    if is_disabled_mod_filename(enabled_name):
        return enabled_name
    if not enabled_name.lower().endswith(".jar"):
        raise ValueError("Not a mod jar filename")
    return f"{enabled_name}{MOD_DISABLED_SUFFIX}"


def manifest_key_for_filename(name: str) -> str:
    return enabled_mod_filename(name)
