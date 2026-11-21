"""Vault-compatible local and private S3 cache for YouTube history."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from .errors import BridgeError, ConfigurationError
from .paths import local_cache_root, vault_cache_root


VIDEO_ID = re.compile(r"[-_A-Za-z0-9]{11}\Z")
TRANSCRIPT = re.compile(
    r"transcripts/([-_A-Za-z0-9]{11})\.([A-Za-z0-9-]+)\.json\Z"
)
REMOTE_TRANSCRIPT = re.compile(
    r"youtube/transcripts/([-_A-Za-z0-9]{11})\."
    r"([A-Za-z0-9-]+)\.([0-9a-f]{64})\.json\Z"
)
HISTORY_KEY = "youtube/history.json"
CACHE_PREFIX = "youtube/"
BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
SAFE_ERROR = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class CacheError(BridgeError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_index() -> dict[str, Any]:
    return {"metadata": {}, "videos": []}


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix="." + path.name
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def state_time(state: dict[str, Any]) -> str:
    return max(
        (
            state.get(key, "")
            for key in ("retrieved_at", "fetched_at", "attempted_at", "checked_at")
        ),
        default="",
    )


def merge_indexes(
    left: dict[str, Any], right: dict[str, Any]
) -> dict[str, Any]:
    """Union watches while preserving coverage and cached transcript state."""
    result = copy.deepcopy(left)
    left_metadata = left.get("metadata", {})
    right_metadata = right.get("metadata", {})
    if right_metadata.get("updated_at", "") >= left_metadata.get("updated_at", ""):
        metadata = copy.deepcopy(left_metadata | right_metadata)
    else:
        metadata = copy.deepcopy(right_metadata | left_metadata)
    result["metadata"] = metadata
    coverages = [
        metadata["cloud_history"]
        for metadata in (left_metadata, right_metadata)
        if isinstance(metadata.get("cloud_history"), dict)
    ]
    if coverages:
        result["metadata"]["cloud_history"] = copy.deepcopy(
            max(coverages, key=lambda item: item.get("updated_at", ""))
        )
    videos = {
        video["video_id"]: copy.deepcopy(video)
        for video in result.get("videos", [])
    }
    for incoming in right.get("videos", []):
        identifier = incoming["video_id"]
        if identifier not in videos:
            videos[identifier] = copy.deepcopy(incoming)
            continue
        video = videos[identifier]
        for key in ("watched_on", "watched_at", "history_sources"):
            video[key] = sorted(
                set(video.get(key, [])) | set(incoming.get(key, []))
            )
        for key in ("title", "channel", "url"):
            if incoming.get(key):
                video[key] = incoming[key]
        old = video.get("transcript", {})
        new = incoming.get("transcript", {})
        rank = lambda state: (state.get("status") == "cached", state_time(state))
        if rank(new) >= rank(old):
            video["transcript"] = copy.deepcopy(new)
    result["videos"] = sorted(
        videos.values(),
        key=lambda video: max(
            video.get("watched_on", []) + video.get("watched_at", []),
            default="",
        ),
        reverse=True,
    )
    return result


def valid_transcript(data: Any, identifier: str) -> bool:
    if (
        not isinstance(data, dict)
        or data.get("video_id") != identifier
        or not data.get("language")
        or not isinstance(data.get("segments"), list)
        or not data["segments"]
    ):
        return False
    for segment in data["segments"]:
        if (
            not isinstance(segment, dict)
            or not isinstance(segment.get("text"), str)
            or not isinstance(segment.get("start"), (int, float))
            or not math.isfinite(segment["start"])
            or segment["start"] < 0
        ):
            return False
    return True


def validate_index(index: Any) -> None:
    if (
        not isinstance(index, dict)
        or not isinstance(index.get("videos"), list)
        or not isinstance(index.get("metadata"), dict)
    ):
        raise ConfigurationError("The YouTube history cache index is invalid.")
    coverage = index["metadata"].get("cloud_history")
    if coverage is not None:
        if not isinstance(coverage, dict):
            raise ConfigurationError(
                "The YouTube history cache coverage metadata is invalid."
            )
        for field_name in ("coverage_start_date", "coverage_end_date"):
            try:
                date.fromisoformat(coverage[field_name])
            except (KeyError, TypeError, ValueError):
                raise ConfigurationError(
                    "The YouTube history cache coverage dates are invalid."
                ) from None
    for video in index["videos"]:
        if (
            not isinstance(video, dict)
            or not isinstance(video.get("video_id"), str)
            or not VIDEO_ID.fullmatch(video["video_id"])
        ):
            raise ConfigurationError(
                "The YouTube history cache contains an invalid video ID."
            )
        for field_name in ("watched_on", "watched_at", "history_sources"):
            values = video.get(field_name, [])
            if not isinstance(values, list) or not all(
                isinstance(value, str) for value in values
            ):
                raise ConfigurationError(
                    "The YouTube history cache contains invalid watch metadata."
                )
        if not isinstance(video.get("transcript", {}), dict):
            raise ConfigurationError(
                "The YouTube history cache contains invalid transcript metadata."
            )


def safe_remote_code(error: BaseException) -> str:
    response = getattr(error, "response", {})
    code = (
        response.get("Error", {}).get("Code")
        if isinstance(response, dict)
        else None
    )
    return code if isinstance(code, str) and SAFE_ERROR.fullmatch(code) else "s3_error"


def body(data: Any) -> bytes:
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def clear_local_cache(root: Path) -> dict[str, Any]:
    removed_history = False
    removed_transcripts = False
    history = root / "history.json"
    if history.is_file():
        history.unlink()
        removed_history = True
    transcripts = root / "transcripts"
    if transcripts.is_dir():
        shutil.rmtree(transcripts)
        removed_transcripts = True
    return {
        "root": str(root),
        "history_removed": removed_history,
        "transcripts_removed": removed_transcripts,
    }


class S3Cache:
    def __init__(self, bucket: str, client=None):
        if not BUCKET.fullmatch(bucket):
            raise ConfigurationError("S3_CACHE_BUCKET is invalid.")
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                region_name="us-west-1",
                config=Config(
                    signature_version="s3v4",
                    connect_timeout=10,
                    read_timeout=30,
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
        self.client = client
        self.bucket = bucket
        self.uploaded: set[str] = set()

    def get(self, key: str) -> tuple[Any | None, str | None]:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return json.loads(response["Body"].read()), response["ETag"]
        except Exception as error:
            if safe_remote_code(error) in ("NoSuchKey", "404"):
                return None, None
            raise

    def restore(
        self, local: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        remote, _ = self.get(HISTORY_KEY)
        if remote is None:
            return local, False
        validate_index(remote)
        return merge_indexes(remote, local), True

    def put(self, key: str, data: Any, **conditions):
        return self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body(data),
            ContentType="application/json",
            ServerSideEncryption="AES256",
            **conditions,
        )

    def keys(self) -> list[str]:
        keys = []
        token = None
        while True:
            arguments = {"Bucket": self.bucket, "Prefix": CACHE_PREFIX}
            if token:
                arguments["ContinuationToken"] = token
            response = self.client.list_objects_v2(**arguments)
            keys.extend(
                item["Key"] for item in response.get("Contents", []) if "Key" in item
            )
            if not response.get("IsTruncated"):
                return keys
            token = response.get("NextContinuationToken")

    def clear(self) -> int:
        keys = self.keys()
        for start in range(0, len(keys), 1000):
            chunk = keys[start : start + 1000]
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={
                    "Objects": [{"Key": key} for key in chunk],
                    "Quiet": True,
                },
            )
        return len(keys)

    def upload_transcripts(
        self, index: dict[str, Any], root: Path
    ) -> None:
        for video in index["videos"]:
            state = video.get("transcript", {})
            if state.get("status") != "cached":
                continue
            path = state.get("path", "")
            match = TRANSCRIPT.fullmatch(path)
            if not match or match.group(1) != video["video_id"]:
                raise ConfigurationError(
                    "Only validated transcript paths may be uploaded."
                )
            target = (root / path).resolve()
            if not target.is_relative_to((root / "transcripts").resolve()):
                raise ConfigurationError("Unsafe transcript cache path.")
            if not target.exists():
                if REMOTE_TRANSCRIPT.fullmatch(state.get("s3_key", "")):
                    continue
                state.update(
                    status="retry_needed",
                    error="Missing or invalid cached transcript",
                )
                continue
            try:
                data = json.loads(target.read_text())
            except (OSError, ValueError):
                raise ConfigurationError("Cached transcript JSON is invalid.") from None
            if not valid_transcript(data, video["video_id"]):
                raise ConfigurationError(
                    "Invalid cached transcript; refusing S3 upload."
                )
            digest = hashlib.sha256(body(data)).hexdigest()
            key = (
                f"youtube/transcripts/{match.group(1)}."
                f"{match.group(2)}.{digest}.json"
            )
            if state.get("s3_key") == key:
                continue
            if key not in self.uploaded:
                try:
                    self.put(key, data, IfNoneMatch="*")
                except Exception as error:
                    if safe_remote_code(error) != "PreconditionFailed":
                        raise
                self.uploaded.add(key)
            state["s3_key"] = key

    def save(
        self, index: dict[str, Any], root: Path
    ) -> dict[str, Any]:
        """Upload transcripts first, then publish a conditional history manifest."""
        validate_index(index)
        self.upload_transcripts(index, root)
        for _ in range(8):
            remote, etag = self.get(HISTORY_KEY)
            if remote is not None:
                validate_index(remote)
            merged = merge_indexes(remote or empty_index(), index)
            if remote is not None and body(remote) == body(merged):
                return merged
            try:
                self.put(
                    HISTORY_KEY,
                    merged,
                    **({"IfMatch": etag} if etag else {"IfNoneMatch": "*"}),
                )
                return merged
            except Exception as error:
                if safe_remote_code(error) not in (
                    "PreconditionFailed",
                    "ConditionalRequestConflict",
                ):
                    raise
        raise CacheError(
            "cache_conflict",
            "The S3 history manifest changed repeatedly; local progress was retained.",
        )


@dataclass
class CacheManager:
    root: Path | None
    remote: S3Cache | None = None
    warnings: list[str] = field(default_factory=list)
    restored_remote: bool = False
    local_written: bool = False
    s3_status: str = "not_configured"

    @classmethod
    def from_env(cls, enabled: bool = True) -> "CacheManager":
        if not enabled:
            return cls(root=None, s3_status="disabled")
        root_value = local_cache_root()
        if root_value:
            root = vault_cache_root(root_value)
        else:
            if os.environ.get("S3_CACHE_BUCKET", "").strip():
                raise ConfigurationError(
                    "Set LOCAL_CACHE_ROOT when S3_CACHE_BUCKET is configured."
                )
            return cls(
                root=None,
                warnings=[
                    "Set LOCAL_CACHE_ROOT to enable the history cache."
                ],
            )
        bucket = os.environ.get("S3_CACHE_BUCKET", "").strip()
        remote = S3Cache(bucket) if bucket else None
        return cls(root=root, remote=remote)

    @property
    def enabled(self) -> bool:
        return self.root is not None

    @property
    def s3_configured(self) -> bool:
        return self.remote is not None

    @property
    def path(self) -> Path | None:
        return self.root / "history.json" if self.root else None

    def load(self) -> dict[str, Any]:
        if self.root is None:
            return empty_index()
        path = self.path
        if path and path.is_file():
            try:
                local = json.loads(path.read_text())
            except (OSError, ValueError):
                raise ConfigurationError(
                    "The local YouTube history cache is invalid."
                ) from None
            validate_index(local)
        else:
            local = empty_index()
        if self.remote:
            try:
                local, self.restored_remote = self.remote.restore(local)
                self.s3_status = "ok"
            except ConfigurationError:
                raise
            except Exception as error:
                self.s3_status = "failed"
                self.warnings.append(
                    "S3 history restore failed with " + safe_remote_code(error) + "."
                )
        if path:
            try:
                atomic_json(path, local)
            except OSError:
                raise CacheError(
                    "cache_write_failed",
                    "The local YouTube history checkpoint could not be written.",
                ) from None
            self.local_written = True
        return local

    def save(self, index: dict[str, Any]) -> dict[str, Any]:
        validate_index(index)
        if self.root is None:
            return index
        try:
            atomic_json(self.path, index)
        except OSError:
            raise CacheError(
                "cache_write_failed",
                "The local YouTube history checkpoint could not be written.",
            ) from None
        self.local_written = True
        if self.remote:
            try:
                index = self.remote.save(index, self.root)
                self.s3_status = "ok"
            except ConfigurationError:
                raise
            except Exception as error:
                self.s3_status = "failed"
                warning = (
                    "S3 history sync failed with "
                    + safe_remote_code(error)
                    + "; local progress was retained."
                )
                if warning not in self.warnings:
                    self.warnings.append(warning)
            try:
                atomic_json(self.path, index)
            except OSError:
                raise CacheError(
                    "cache_write_failed",
                    "The merged YouTube history checkpoint could not be written.",
                ) from None
        return index

    def describe(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "local_checkpoint_written": self.local_written,
            "s3_configured": self.s3_configured,
            "s3_status": self.s3_status,
            "remote_manifest_restored": self.restored_remote,
            "warnings": list(self.warnings),
        }
