"""Signed-out YouTube pagination for client-controlled Worker requests."""

from email.message import Message
import html
import json
import math
import re
from urllib.parse import parse_qs, unquote, urlsplit

import requests
from requests.cookies import MockRequest, MockResponse

from .errors import CaptionError, UpstreamError


VIDEO_ID = re.compile(r"[-_A-Za-z0-9]{11}\Z")
YOUTUBE_HOSTS = {"www.youtube.com", "youtube.com"}
ORIGIN = "https://www.youtube.com"
PLAYER_CONTEXT = {
    "client": {"clientName": "ANDROID", "clientVersion": "20.10.38", "hl": "en"}
}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


def seconds(value, scale=1):
    if isinstance(value, bool):
        raise ValueError
    result = float(value) / scale
    if not math.isfinite(result) or result < 0:
        raise ValueError
    return result


def caption_text(value):
    return re.sub(r"<[^>]*>", "", html.unescape(value))


def parse_segments(source: str):
    from defusedxml import ElementTree
    from defusedxml.common import DefusedXmlException

    segments = []
    try:
        if source.lstrip().startswith("{"):
            data = json.loads(source)
            if not isinstance(data.get("events"), list):
                raise ValueError
            for event in data["events"]:
                if "segs" not in event:
                    continue
                text = "".join(part["utf8"] for part in event["segs"])
                if text:
                    segments.append(
                        {
                            "start": seconds(event["tStartMs"], 1000),
                            "duration": seconds(event.get("dDurationMs", 0), 1000),
                            "text": caption_text(text),
                        }
                    )
        else:
            root = ElementTree.fromstring(source)
            if root.tag == "transcript":
                for item in root:
                    if item.tag != "text":
                        raise ValueError
                    text = "".join(item.itertext())
                    if text:
                        segments.append(
                            {
                                "start": seconds(item.attrib["start"]),
                                "duration": seconds(item.attrib.get("dur", 0)),
                                "text": caption_text(text),
                            }
                        )
            elif root.tag == "timedtext":
                body = root.find("body")
                if body is None:
                    raise ValueError
                for item in body:
                    if item.tag == "w":
                        continue
                    if item.tag != "p":
                        raise ValueError
                    text = "".join(item.itertext())
                    if text and not text.isspace():
                        segments.append(
                            {
                                "start": seconds(item.attrib["t"], 1000),
                                "duration": seconds(item.attrib.get("d", 0), 1000),
                                "text": caption_text(text),
                            }
                        )
            else:
                raise ValueError
        if not segments or any(not segment["text"] for segment in segments):
            raise ValueError
        return segments
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        ElementTree.ParseError,
        DefusedXmlException,
    ):
        raise CaptionError("invalid_caption_response", "Invalid caption response.") from None


def caption_url(raw: str, video_id: str) -> str:
    try:
        parsed = urlsplit(raw)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in YOUTUBE_HOSTS
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or parsed.path != "/api/timedtext"
            or parsed.fragment
            or parse_qs(parsed.query).get("v") != [video_id]
        ):
            raise ValueError
        if "xpe" in parse_qs(parsed.query).get("exp", []):
            raise CaptionError(
                "proof_token_required",
                "YouTube requires a proof token for this caption URL.",
            )
        return raw
    except (ValueError, TypeError):
        raise CaptionError("invalid_caption_url", "Invalid YouTube caption URL.") from None


def english_track(payload):
    try:
        if payload.get("error"):
            raise ValueError
        status = payload["playabilityStatus"]
        if status["status"] != "OK":
            if status["status"] == "ERROR" and status.get("reason") == "This video is unavailable":
                raise CaptionError("video_unavailable", "Video unavailable.")
            raise CaptionError(
                "video_access_restricted",
                "YouTube restricted this signed-out video request.",
            )
        captions = payload.get("captions", {}).get("playerCaptionsTracklistRenderer")
        if captions is None:
            raise CaptionError("captions_disabled", "Captions are disabled.")
        tracks = captions["captionTracks"]
        if not isinstance(tracks, list) or not tracks:
            raise CaptionError("captions_disabled", "Captions are disabled.")
        english = [
            track
            for track in tracks
            if isinstance(track, dict)
            and isinstance(track.get("languageCode"), str)
            and track["languageCode"].lower().split("-")[0] == "en"
        ]
        if not english:
            raise CaptionError("no_english_track", "No English caption track.")
        manual = [track for track in english if track.get("kind") is None]
        automatic = [track for track in english if track.get("kind") == "asr"]
        selected = (manual or automatic)[0]
        if not isinstance(selected.get("baseUrl"), str):
            raise ValueError
        return selected
    except (KeyError, TypeError, AttributeError, ValueError):
        raise CaptionError("invalid_player_response", "Invalid YouTube player response.") from None


class OriginRequestMixin:
    def __init__(self):
        self.origin_cookies = requests.cookies.RequestsCookieJar()
        self._cookie_session = requests.Session()
        self._cookie_session.auth = lambda request: request
        self._cookie_session.trust_env = False
        self._cookie_session.cookies = self.origin_cookies

    def prepared_origin(self, method, url, **kwargs):
        return self._cookie_session.prepare_request(
            requests.Request(method, url, **kwargs)
        )

    def consume_origin_cookies(self, response, prepared):
        encoded = response.headers.get("X-Origin-Set-Cookie")
        if not encoded:
            return
        message = Message()
        for value in encoded.split(","):
            message.add_header("Set-Cookie", unquote(value.strip()))
        self.origin_cookies.extract_cookies(
            MockResponse(message), MockRequest(prepared)
        )

    def close_origin(self):
        self._cookie_session.close()


class CaptionFlow(OriginRequestMixin):
    def __init__(self, transport):
        super().__init__()
        self.transport = transport

    def stage(self, name, video_id, search, body=None):
        if name == "watch":
            url = ORIGIN + "/watch?" + search
        elif name == "player":
            url = ORIGIN + "/youtubei/v1/player?" + search
        else:
            raise ValueError
        prepared = self.prepared_origin(
            "POST" if body else "GET",
            url,
            json=None,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "*/*",
                **({"Content-Type": "application/json"} if body else {}),
            },
        )
        response = self.transport.request(
            "POST",
            f"/v1/signed-out-comparison/{name}",
            json={
                "headers": {
                    key: value
                    for key, value in prepared.headers.items()
                    if key.lower()
                    in {"user-agent", "accept-language", "accept", "content-type", "cookie"}
                },
                "search": search,
                "host": "www.youtube.com",
                "body": body,
                "videoId": video_id,
            },
        )
        self.consume_origin_cookies(response, prepared)
        status = response.headers.get("X-Origin-Http", "")
        if status != "200":
            response.close()
            if re.fullmatch(r"[1-5][0-9]{2}", status):
                raise UpstreamError(name, int(status))
            raise CaptionError(
                "invalid_upstream_status",
                f"Worker returned an invalid YouTube status at {name}.",
            )
        response.encoding = "utf-8"
        return response

    def fetch(self, video_id: str):
        if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
            raise CaptionError(
                "invalid_video_id",
                "Video ID must be exactly 11 YouTube ID characters.",
            )
        watch = self.stage("watch", video_id, f"v={video_id}&hl=en")
        try:
            match = re.search(
                r'"INNERTUBE_API_KEY"\s*:\s*"([A-Za-z0-9_-]+)"',
                watch.text,
            )
        finally:
            watch.close()
        if not match:
            raise CaptionError(
                "player_configuration_missing",
                "YouTube player configuration was missing.",
            )
        player = self.stage(
            "player",
            video_id,
            "key=" + match.group(1),
            json.dumps(
                {"context": PLAYER_CONTEXT, "videoId": video_id},
                separators=(",", ":"),
            ),
        )
        try:
            try:
                payload = player.json()
            except ValueError:
                raise CaptionError(
                    "invalid_player_response",
                    "Invalid YouTube player response.",
                ) from None
            track = english_track(payload)
        finally:
            player.close()

        signed_url = caption_url(track["baseUrl"], video_id)
        parsed = urlsplit(signed_url)
        prepared = self.prepared_origin(
            "GET",
            signed_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "*/*",
            },
        )
        caption = self.transport.request(
            "POST",
            "/v1/signed-out-comparison/timedtext",
            json={
                "headers": {
                    key: value
                    for key, value in prepared.headers.items()
                    if key.lower()
                    in {"user-agent", "accept-language", "accept", "content-type", "cookie"}
                },
                "search": parsed.query,
                "host": parsed.hostname,
                "body": None,
                "videoId": video_id,
            },
        )
        try:
            status = caption.headers.get("X-Origin-Http", "")
            if status != "200":
                if re.fullmatch(r"[1-5][0-9]{2}", status):
                    raise UpstreamError("timedtext", int(status))
                raise CaptionError(
                    "invalid_upstream_status",
                    "Worker returned an invalid YouTube status at timedtext.",
                )
            caption.encoding = "utf-8"
            segments = parse_segments(caption.text)
        finally:
            caption.close()
        return {
            "video_id": video_id,
            "language": track["languageCode"],
            "caption_type": "automatic" if track.get("kind") == "asr" else "manual",
            "segments": segments,
        }
