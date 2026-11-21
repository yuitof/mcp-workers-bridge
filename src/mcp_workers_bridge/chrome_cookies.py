"""Export macOS Chrome cookies as MCP storage-state JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


SERVICES = {
    "youtube": {"youtube.com", "www.youtube.com"},
    "x": {"x.com", "www.x.com"},
    "reddit": {"reddit.com", "www.reddit.com", "old.reddit.com"},
}
CHROME_ROOT = Path.home() / "Library/Application Support/Google/Chrome"
CHROME_EPOCH_OFFSET = 11_644_473_600
KEY_ITERATIONS = 1003
SALT = b"saltysalt"
IV = b" " * 16


def chrome_password() -> bytes:
    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-w",
            "-s",
            "Chrome Safe Storage",
            "-a",
            "Chrome",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise SystemExit(
            "Could not read the Chrome Safe Storage password from Keychain."
        )
    return result.stdout.strip().encode()


def storage_key(password: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha1", password, SALT, KEY_ITERATIONS, dklen=16)


def decrypt_value(blob: bytes, key: bytes, host_key: str | None = None) -> str:
    if not blob.startswith((b"v10", b"v11")):
        return blob.decode("utf-8")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(IV)).decryptor()
    padded = decryptor.update(blob[3:]) + decryptor.finalize()
    padding = padded[-1] if padded else 0
    if padding < 1 or padding > 16 or padded[-padding:] != bytes([padding]) * padding:
        raise ValueError("Chrome cookie decryption failed.")
    plain = padded[:-padding]
    if (
        host_key is not None
        and len(plain) > 32
        and plain[:32] == hashlib.sha256(host_key.encode()).digest()
    ):
        plain = plain[32:]
    return plain.decode("utf-8")


def unix_expiry(expires_utc: int) -> int:
    if not expires_utc:
        return -1
    return int(expires_utc / 1_000_000 - CHROME_EPOCH_OFFSET)


def read_cookies(profile: str, chrome_root: Path, service: str) -> list[dict]:
    domains = SERVICES[service]
    source = chrome_root / profile / "Cookies"
    if not source.is_file():
        raise SystemExit(f"Chrome cookie database not found: {source}")
    key = storage_key(chrome_password())
    with tempfile.TemporaryDirectory(prefix="chrome-cookies-") as temporary:
        copy = Path(temporary) / "Cookies"
        shutil.copy2(source, copy)
        for suffix in ("-wal", "-shm"):
            extra = source.with_name(source.name + suffix)
            if extra.is_file():
                shutil.copy2(extra, copy.with_name(copy.name + suffix))
        connection = sqlite3.connect(copy)
        try:
            rows = connection.execute(
                "SELECT host_key, name, value, encrypted_value, path,"
                " expires_utc, is_secure, is_httponly FROM cookies"
            ).fetchall()
        finally:
            connection.close()
    cookies = []
    for host, name, value, encrypted, path, expires, secure, http_only in rows:
        if host.lstrip(".") not in domains or not name:
            continue
        try:
            decoded = value or decrypt_value(encrypted, key, host)
        except (UnicodeDecodeError, ValueError):
            continue
        cookies.append(
            {
                "name": name,
                "value": decoded,
                "domain": host,
                "path": path or "/",
                "expires": unix_expiry(int(expires)),
                "secure": bool(secure),
                "httpOnly": bool(http_only),
            }
        )
    required = {
        "youtube": {"SAPISID", "__Secure-3PAPISID"},
        "x": {"auth_token", "ct0"},
        "reddit": {"reddit_session"},
    }[service]
    if not required & {cookie["name"] for cookie in cookies}:
        raise SystemExit(
            f"No authenticated {service} cookies were found in Chrome profile "
            f"{profile!r}. Sign in to {service} in that profile and retry."
        )
    return cookies


def write_payload(payload: dict, output: Path) -> Path:
    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(output)
    return output


def export(service: str, output: Path, profile: str, chrome_root: Path) -> Path:
    cookies = read_cookies(profile, chrome_root, service)
    return write_payload({"cookies": cookies, "origins": []}, output)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Chrome cookies as a private MCP storage-state file."
    )
    parser.add_argument("service", choices=sorted(SERVICES))
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print storage-state JSON instead of writing a file.",
    )
    parser.add_argument("--profile", default="Default")
    parser.add_argument("--chrome-root", type=Path, default=CHROME_ROOT)
    arguments = parser.parse_args()
    if arguments.stdout and arguments.output is not None:
        parser.error("use either --stdout or --output, not both")
    if not arguments.stdout and arguments.output is None:
        parser.error("--output is required unless --stdout is used")
    if sys.platform != "darwin":
        raise SystemExit(
            "Automatic Chrome export is supported on macOS only. Export the "
            "cookies with Chrome DevTools instead."
        )
    cookies = read_cookies(
        arguments.profile,
        arguments.chrome_root.expanduser(),
        arguments.service,
    )
    payload = {"cookies": cookies, "origins": []}
    if arguments.stdout:
        print(json.dumps(payload, separators=(",", ":")))
        return
    output = write_payload(payload, arguments.output)
    print(f"wrote {len(cookies)} cookies to {output}")


if __name__ == "__main__":
    main()
