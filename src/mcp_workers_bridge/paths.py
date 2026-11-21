"""Filesystem layout for vault-compatible sessions and caches."""

import os
from pathlib import Path


def local_cache_root() -> str | None:
    value = os.environ.get("LOCAL_CACHE_ROOT", "").strip()
    return value or None


def _root(vault_root: str) -> Path:
    return Path(vault_root).expanduser()


def vault_auth_path(vault_root: str, service: str) -> Path:
    preferred = _root(vault_root) / service / "auth/storage-state.json"
    legacy = (
        _root(vault_root)
        / ".codex/tmp"
        / service
        / "auth/storage-state.json"
    )
    if preferred.is_file() or not legacy.is_file():
        return preferred
    return legacy


def vault_cache_root(vault_root: str, service: str = "youtube") -> Path:
    preferred = _root(vault_root) / service
    legacy = _root(vault_root) / ".codex/tmp" / service
    if preferred.is_dir() or not legacy.is_dir():
        return preferred
    return legacy
