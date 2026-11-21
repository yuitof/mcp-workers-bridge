from mcp_workers_bridge import paths


def test_local_cache_root_returns_configured_value(monkeypatch):
    monkeypatch.setenv("LOCAL_CACHE_ROOT", "/cache")
    assert paths.local_cache_root() == "/cache"


def test_vault_auth_prefers_simplified_layout(tmp_path):
    preferred = tmp_path / "youtube/auth/storage-state.json"
    legacy = tmp_path / ".codex/tmp/youtube/auth/storage-state.json"
    preferred.parent.mkdir(parents=True)
    legacy.parent.mkdir(parents=True)
    preferred.write_text("{}")
    legacy.write_text("{}")
    assert paths.vault_auth_path(str(tmp_path), "youtube") == preferred


def test_vault_auth_falls_back_to_legacy_layout(tmp_path):
    legacy = tmp_path / ".codex/tmp/x/auth/storage-state.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}")
    assert paths.vault_auth_path(str(tmp_path), "x") == legacy


def test_vault_cache_prefers_simplified_layout(tmp_path):
    preferred = tmp_path / "youtube"
    legacy = tmp_path / ".codex/tmp/youtube"
    preferred.mkdir()
    legacy.mkdir(parents=True)
    assert paths.vault_cache_root(str(tmp_path)) == preferred


def test_vault_cache_falls_back_to_legacy_layout(tmp_path):
    legacy = tmp_path / ".codex/tmp/youtube"
    legacy.mkdir(parents=True)
    assert paths.vault_cache_root(str(tmp_path)) == legacy
