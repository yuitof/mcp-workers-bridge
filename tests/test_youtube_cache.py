import copy
import io
import json

import pytest

from mcp_workers_bridge import youtube_cache
from mcp_workers_bridge.youtube_cache import (
    CacheManager,
    S3Cache,
    atomic_json,
    body,
    merge_indexes,
)
from mcp_workers_bridge.errors import ConfigurationError


VIDEO = "abcDEF123_-"


class S3Failure(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.serial = 0
        self.conflict_once = None

    def get_object(self, Bucket, Key):
        self.calls.append(("get", Key))
        if Key not in self.objects:
            raise S3Failure("NoSuchKey")
        content, etag = self.objects[Key]
        return {"Body": io.BytesIO(content), "ETag": etag}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.calls.append(("put", Key))
        if self.conflict_once and Key.endswith("history.json"):
            concurrent = self.conflict_once
            self.conflict_once = None
            self.serial += 1
            self.objects[Key] = (body(concurrent), str(self.serial))
            raise S3Failure("PreconditionFailed")
        if kwargs.get("IfNoneMatch") == "*" and Key in self.objects:
            raise S3Failure("PreconditionFailed")
        if (
            "IfMatch" in kwargs
            and kwargs["IfMatch"] != self.objects.get(Key, (None, None))[1]
        ):
            raise S3Failure("PreconditionFailed")
        self.serial += 1
        self.objects[Key] = (Body, str(self.serial))
        return {"ETag": str(self.serial)}


def transcript():
    return {
        "video_id": VIDEO,
        "language": "en",
        "caption_type": "manual",
        "audio_verified": False,
        "segments": [{"text": "Synthetic transcript", "start": 1.0}],
    }


def sample():
    return {
        "metadata": {},
        "videos": [
            {
                "video_id": VIDEO,
                "title": "Synthetic",
                "url": f"https://www.youtube.com/watch?v={VIDEO}",
                "watched_on": ["2026-11-15"],
                "transcript": {
                    "status": "cached",
                    "path": f"transcripts/{VIDEO}.en.json",
                    "retrieved_at": "2026-11-16",
                },
            }
        ],
    }


def setup_cache(tmp_path):
    client = FakeS3()
    cache = S3Cache("private-test-bucket", client)
    index = sample()
    atomic_json(tmp_path / index["videos"][0]["transcript"]["path"], transcript())
    return client, cache, index


def test_uploads_transcript_before_manifest_and_excludes_auth(tmp_path):
    client, cache, index = setup_cache(tmp_path)
    atomic_json(tmp_path / "auth/storage-state.json", {"cookies": ["private"]})
    cache.save(index, tmp_path)
    puts = [key for action, key in client.calls if action == "put"]
    assert puts[0].startswith("youtube/transcripts/")
    assert puts[-1] == "youtube/history.json"
    assert not any(
        b"private" in content for content, _ in client.objects.values()
    )


def test_remote_restore_and_merge_preserve_cached_transcript(tmp_path):
    _, cache, index = setup_cache(tmp_path)
    cache.save(index, tmp_path)
    local = sample()
    local["videos"][0]["watched_on"] = ["2026-11-16"]
    merged, restored = cache.restore(local)
    assert restored is True
    assert merged["videos"][0]["watched_on"] == [
        "2026-11-15",
        "2026-11-16",
    ]
    assert merged["videos"][0]["transcript"]["status"] == "cached"


def test_conditional_retry_preserves_concurrent_watch(tmp_path):
    client, cache, index = setup_cache(tmp_path)
    concurrent = copy.deepcopy(index)
    concurrent["videos"][0]["watched_on"] = ["2026-11-14"]
    concurrent["videos"][0]["transcript"] = {"status": "unchecked"}
    client.conflict_once = concurrent
    saved = cache.save(index, tmp_path)
    assert saved["videos"][0]["watched_on"] == [
        "2026-11-14",
        "2026-11-15",
    ]
    assert saved["videos"][0]["transcript"]["status"] == "cached"


def test_coverage_is_not_stretched_and_cached_beats_failure():
    first = {
        "metadata": {
            "cloud_history": {
                "updated_at": "2026-11-16",
                "range_complete": False,
            }
        },
        "videos": [],
    }
    second = {
        "metadata": {
            "cloud_history": {
                "updated_at": "2026-11-14",
                "range_complete": True,
            }
        },
        "videos": [],
    }
    assert merge_indexes(first, second)["metadata"]["cloud_history"]["range_complete"] is False

    cached = sample()
    failed = sample()
    failed["videos"][0]["transcript"] = {
        "status": "retry_needed",
        "attempted_at": "2027-01-01",
    }
    assert merge_indexes(cached, failed)["videos"][0]["transcript"]["status"] == "cached"


@pytest.mark.parametrize(
    "path",
    (
        "auth/storage-state.json",
        "../secrets.json",
        "transcripts/../../../secret.json",
    ),
)
def test_rejects_non_transcript_cache_paths(tmp_path, path):
    _, cache, index = setup_cache(tmp_path)
    index["videos"][0]["transcript"]["path"] = path
    with pytest.raises(ConfigurationError):
        cache.save(index, tmp_path)
    assert not cache.client.objects


def test_cache_manager_retains_local_progress_when_s3_fails(
    monkeypatch, tmp_path
):
    index = sample()
    index["videos"][0]["transcript"] = {"status": "unchecked"}
    manager = CacheManager(root=tmp_path)

    class FailingS3:
        def get(self, key):
            raise S3Failure("AccessDenied")

        def save(self, value, root):
            raise S3Failure("AccessDenied")

    manager.remote = FailingS3()
    output = manager.save(index)
    assert output["videos"][0]["video_id"] == VIDEO
    assert manager.s3_status == "failed"
    assert "AccessDenied" in manager.warnings[0]
    assert json.loads((tmp_path / "history.json").read_text())["videos"][0][
        "video_id"
    ] == VIDEO


def test_manager_reads_bucket_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("S3_CACHE_BUCKET", "private-test-bucket")
    monkeypatch.setenv("LOCAL_CACHE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        youtube_cache,
        "S3Cache",
        lambda bucket: S3Cache(bucket, FakeS3()),
    )
    manager = CacheManager.from_env()
    assert manager.s3_configured is True


def test_cache_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("LOCAL_CACHE_ROOT", "/unused")
    manager = CacheManager.from_env(enabled=False)
    assert manager.enabled is False
    assert manager.s3_status == "disabled"


def test_clear_local_cache_preserves_auth(tmp_path):
    atomic_json(tmp_path / "history.json", sample())
    atomic_json(tmp_path / "transcripts/latest.json", transcript())
    atomic_json(tmp_path / "auth/storage-state.json", {"cookies": []})
    result = youtube_cache.clear_local_cache(tmp_path)
    assert result["history_removed"] is True
    assert result["transcripts_removed"] is True
    assert not (tmp_path / "history.json").exists()
    assert not (tmp_path / "transcripts").exists()
    assert (tmp_path / "auth/storage-state.json").is_file()


def test_s3_clear_uses_cache_prefix():
    class Client:
        def __init__(self):
            self.deleted = []

        def list_objects_v2(self, Bucket, Prefix):
            assert Prefix == "youtube/"
            return {
                "Contents": [
                    {"Key": "youtube/history.json"},
                    {"Key": "youtube/transcripts/example.json"},
                ],
                "IsTruncated": False,
            }

        def delete_objects(self, Bucket, Delete):
            self.deleted.extend(
                item["Key"] for item in Delete["Objects"]
            )

    client = Client()
    removed = S3Cache("private-test-bucket", client).clear()
    assert removed == 2
    assert client.deleted == [
        "youtube/history.json",
        "youtube/transcripts/example.json",
    ]
