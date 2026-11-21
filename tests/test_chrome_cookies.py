import hashlib
import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from mcp_workers_bridge import chrome_cookies


def encrypt(value: str) -> bytes:
    key = chrome_cookies.storage_key(b"password")
    padder = padding.PKCS7(128).padder()
    payload = padder.update(value.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(chrome_cookies.IV)).encryptor()
    return b"v10" + encryptor.update(payload) + encryptor.finalize()


def encrypt_for_host(host: str, value: str) -> bytes:
    key = chrome_cookies.storage_key(b"password")
    payload = hashlib.sha256(host.encode()).digest() + value.encode()
    padder = padding.PKCS7(128).padder()
    payload = padder.update(payload) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(chrome_cookies.IV)).encryptor()
    return b"v10" + encryptor.update(payload) + encryptor.finalize()


def test_decrypts_chrome_v10_cookie():
    key = chrome_cookies.storage_key(b"password")
    assert chrome_cookies.decrypt_value(encrypt("synthetic-value"), key) == (
        "synthetic-value"
    )


def test_strips_modern_host_hash_prefix():
    assert chrome_cookies.decrypt_value(
        encrypt_for_host(".youtube.com", "synthetic-value"),
        chrome_cookies.storage_key(b"password"),
        ".youtube.com",
    ) == "synthetic-value"


def test_rejects_invalid_padding():
    key = chrome_cookies.storage_key(b"password")
    with pytest.raises(ValueError):
        chrome_cookies.decrypt_value(b"v10" + b"\x00" * 16, key)


def test_converts_chrome_expiry():
    assert chrome_cookies.unix_expiry(0) == -1
    assert chrome_cookies.unix_expiry(13_305_648_000_000_000) == 1_661_174_400


def test_derives_stable_storage_key():
    assert chrome_cookies.storage_key(b"password").hex() == (
        hashlib.pbkdf2_hmac(
            "sha1", b"password", b"saltysalt", 1003, dklen=16
        ).hex()
    )


def test_stdout_emits_only_storage_state_json(monkeypatch, capsys):
    monkeypatch.setattr(chrome_cookies.sys, "platform", "darwin")
    monkeypatch.setattr(
        chrome_cookies,
        "read_cookies",
        lambda profile, root, service: [
            {
                "name": "SAPISID",
                "value": "synthetic",
                "domain": ".youtube.com",
                "path": "/",
                "expires": -1,
                "secure": True,
                "httpOnly": False,
            }
        ],
    )
    monkeypatch.setattr(
        chrome_cookies.sys,
        "argv",
        ["chrome-storage-state", "youtube", "--stdout"],
    )
    chrome_cookies.main()
    capture = capsys.readouterr()
    payload = json.loads(capture.out)
    assert payload["cookies"][0]["name"] == "SAPISID"
    assert payload["origins"] == []


def test_automatic_storage_state_extracts_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(chrome_cookies.sys, "platform", "darwin")
    monkeypatch.delenv("MCP_CHROME_PROFILE", raising=False)
    chrome_cookies.clear_cache()

    def read(profile, root, service):
        calls.append((profile, service))
        return [{"name": "SAPISID", "value": "synthetic"}]

    monkeypatch.setattr(chrome_cookies, "read_cookies", read)
    first = chrome_cookies.automatic_storage_state("youtube")
    second = chrome_cookies.automatic_storage_state("youtube")
    assert first["cookies"][0]["name"] == "SAPISID"
    assert second == first
    assert calls == [("Default", "youtube")]


def test_automatic_storage_state_requires_macos(monkeypatch):
    monkeypatch.setattr(chrome_cookies.sys, "platform", "linux")
    with pytest.raises(chrome_cookies.ConfigurationError):
        chrome_cookies.automatic_storage_state("x")


def test_approve_for_me_caches_keychain_password(monkeypatch, tmp_path):
    cache = tmp_path / "chrome-safe-storage"
    calls = []
    monkeypatch.setenv("MCP_APPROVE_FOR_ME", "1")
    monkeypatch.setattr(chrome_cookies, "approval_cache", lambda: cache)

    def run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="synthetic-password\n")

    monkeypatch.setattr(chrome_cookies.subprocess, "run", run)
    assert chrome_cookies.chrome_password() == b"synthetic-password"
    assert chrome_cookies.chrome_password() == b"synthetic-password"
    assert len(calls) == 1
    assert cache.read_text() == "synthetic-password"
    assert cache.stat().st_mode & 0o777 == 0o600


def test_cloud_mode_disables_extension(monkeypatch):
    monkeypatch.setenv("MCP_CLOUD_MODE", "1")
    with pytest.raises(chrome_cookies.ConfigurationError, match="Codex Cloud"):
        chrome_cookies.automatic_storage_state("youtube")


def test_detects_cloud_home(monkeypatch):
    monkeypatch.delenv("CODEX_CLOUD", raising=False)
    monkeypatch.delenv("MCP_CLOUD_MODE", raising=False)
    monkeypatch.setattr(chrome_cookies.sys, "platform", "linux")
    monkeypatch.setenv("HOME", "/home/agent")
    assert chrome_cookies.in_codex_cloud() is True


def test_extraction_disable_flag(monkeypatch):
    monkeypatch.setenv("MCP_DISABLE_COOKIE_EXTRACTION", "1")
    assert chrome_cookies.cookie_extraction_disabled() is True


def test_automatic_storage_state_uses_requested_profile(monkeypatch):
    calls = []
    monkeypatch.setattr(chrome_cookies.sys, "platform", "darwin")
    monkeypatch.setenv("MCP_CHROME_PROFILE", "Profile 2")
    chrome_cookies.clear_cache()
    monkeypatch.setattr(
        chrome_cookies,
        "read_cookies",
        lambda profile, root, service: calls.append(profile) or [],
    )
    chrome_cookies.automatic_storage_state("x")
    assert calls == ["Profile 2"]
