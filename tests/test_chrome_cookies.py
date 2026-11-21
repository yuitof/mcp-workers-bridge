import hashlib
import json

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
