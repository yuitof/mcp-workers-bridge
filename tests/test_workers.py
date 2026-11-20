from email.message import Message
import json
from urllib.parse import quote

import pytest
import requests

from mcp_workers_bridge.config import WorkersConfig
from mcp_workers_bridge.errors import AccessDeniedError, CaptionError
from mcp_workers_bridge.workers import WorkersClient


VIDEO = "aircAruvnKk"
OTHER = "zyxWVU98765"


def response(body="", status=200, **headers):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers)
    result._content = body.encode()
    result._content_consumed = True
    return result


def config():
    return WorkersConfig(
        base_url="https://bridge.example.org",
        client_id="synthetic-id",
        client_secret="synthetic-secret",
        timeout=30,
    )


def player(video_id, automatic=False):
    track = {
        "languageCode": "en",
        "baseUrl": f"https://www.youtube.com/api/timedtext?v={video_id}&sig=a%2Fb",
    }
    if automatic:
        track["kind"] = "asr"
    return {
        "playabilityStatus": {"status": "OK"},
        "captions": {"playerCaptionsTracklistRenderer": {"captionTracks": [track]}},
    }


def test_probe_checks_access_and_gateway(monkeypatch):
    replies = [
        response('{"ok":true}', **{"X-Bridge-Version": "1"}),
        response(
            '{"status":200,"ip":"104.28.206.182"}',
            **{"X-Bridge-Version": "1"},
        ),
    ]
    with WorkersClient(config()) as client:
        monkeypatch.setattr(client.session, "request", lambda *args, **kwargs: replies.pop(0))
        assert client.probe() == {
            "ok": True,
            "worker_url": "https://bridge.example.org",
            "access_verified": True,
            "gateway_egress_ip": "104.28.206.182",
        }


@pytest.mark.parametrize("automatic", [False, True])
def test_caption_flow_supports_manual_and_automatic(monkeypatch, automatic):
    cookie = quote("visitor=synthetic; Domain=.youtube.com; Path=/", safe="")
    replies = [
        response(
            '{"INNERTUBE_API_KEY":"synthetic"}',
            **{
                "X-Bridge-Version": "1",
                "X-Origin-Http": "200",
                "X-Origin-Set-Cookie": cookie,
            },
        ),
        response(
            json.dumps(player(OTHER, automatic)),
            **{"X-Bridge-Version": "1", "X-Origin-Http": "200"},
        ),
        response(
            '<transcript><text start="0" dur="1">Verified</text></transcript>',
            **{"X-Bridge-Version": "1", "X-Origin-Http": "200"},
        ),
    ]
    with WorkersClient(config()) as client:
        monkeypatch.setattr(client.session, "request", lambda *args, **kwargs: replies.pop(0))
        result = client.captions(OTHER, True, 10)
    assert result["caption_type"] == ("automatic" if automatic else "manual")
    assert result["segments"][0]["text"] == "Verified"
    assert result["segment_count"] == 1


def test_access_denial_is_classified_before_worker(monkeypatch):
    with WorkersClient(config()) as client:
        monkeypatch.setattr(
            client.session,
            "request",
            lambda *args, **kwargs: response("Forbidden", status=403),
        )
        with pytest.raises(AccessDeniedError):
            client.probe()


def test_relay_preserves_upstream_denial_as_structured_result(monkeypatch):
    reply = response(
        "blocked",
        **{"X-Bridge-Version": "1", "X-Origin-Http": "403", "Content-Type": "text/html"},
    )
    with WorkersClient(config()) as client:
        monkeypatch.setattr(client.session, "request", lambda *args, **kwargs: reply)
        result = client.relay("reddit", "home", {})
    assert result["bridge_ok"] is True
    assert result["ok"] is False
    assert result["error"] == "upstream_http"
    assert result["upstream_http"] == 403
    assert result["body"] == "blocked"


def test_relay_forwards_post_body_in_the_worker_envelope(monkeypatch):
    reply = response("{}", **{"X-Bridge-Version": "1", "X-Origin-Http": "200"})
    captured = {}

    def request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return reply

    with WorkersClient(config()) as client:
        monkeypatch.setattr(client.session, "request", request)
        result = client.relay(
            "youtube",
            "browse",
            {},
            query="prettyPrint=false",
            headers={"Cookie": "SAPISID=synthetic"},
            body='{"browseId":"FEhistory","context":{}}',
        )
    assert result["ok"] is True
    assert captured["json"] == {
        "parameters": {},
        "query": "prettyPrint=false",
        "headers": {"Cookie": "SAPISID=synthetic"},
        "body": '{"browseId":"FEhistory","context":{}}',
    }
    assert "synthetic-id" not in str(captured["json"])
    assert "synthetic-secret" not in str(captured["json"])


@pytest.mark.parametrize("video_id", ["short", "AAAAAAAAAAAA", "bad/id12345", ""])
def test_invalid_video_id_is_rejected(monkeypatch, video_id):
    with WorkersClient(config()) as client:
        with pytest.raises(CaptionError):
            client.captions(video_id, False, 10)
