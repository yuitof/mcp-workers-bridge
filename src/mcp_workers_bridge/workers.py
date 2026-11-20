"""Access-protected Cloudflare Workers bridge client."""

import ipaddress
import json
import os
import platform
import ssl
from urllib.parse import unquote

import requests

from .captions import CaptionFlow
from .config import WorkersConfig
from .errors import AccessDeniedError, BridgeError, TransportError


class WorkersClient:
    def __init__(self, config: WorkersConfig):
        self.config = config
        self.session = requests.Session()
        self.session.auth = lambda request: request
        if os.environ.get("SSL_CERT_FILE"):
            self.session.verify = os.environ["SSL_CERT_FILE"]
        elif platform.system() == "Linux":
            system_ca = ssl.get_default_verify_paths().cafile
            if system_ca:
                self.session.verify = system_ca

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.session.close()

    def request(self, method: str, path: str, **kwargs):
        try:
            response = self.session.request(
                method,
                self.config.base_url + path,
                headers={
                    "CF-Access-Client-Id": self.config.client_id,
                    "CF-Access-Client-Secret": self.config.client_secret,
                },
                allow_redirects=False,
                timeout=(10, self.config.timeout),
                **kwargs,
            )
        except requests.exceptions.ProxyError:
            raise TransportError(
                "Worker HTTPS proxy connection failed before the Worker responded."
            ) from None
        except requests.exceptions.SSLError:
            raise TransportError("Worker HTTPS certificate verification failed.") from None
        except requests.RequestException:
            raise TransportError("Worker HTTPS request failed.") from None

        if (
            response.status_code == 200
            and response.headers.get("X-Bridge-Version") == "1"
        ):
            return response
        reached_worker = response.headers.get("X-Bridge-Version") == "1"
        status = response.status_code
        response.close()
        if not reached_worker and (status in (401, 403) or 300 <= status < 400):
            raise AccessDeniedError(status)
        raise TransportError(
            f"Worker HTTPS endpoint returned HTTP {status} without a successful bridge response."
        )

    def probe(self):
        with self.request("GET", "/v1/health") as response:
            try:
                health = response.json()
                if health.get("ok") is not True:
                    raise ValueError
            except (ValueError, AttributeError):
                raise TransportError("Invalid Worker health response.") from None
        with self.request("GET", "/v1/signed-out-comparison/ip") as response:
            try:
                payload = response.json()
                if payload.get("status") != 200:
                    raise ValueError
                address = ipaddress.ip_address(payload["ip"])
                if not address.is_global:
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                raise TransportError(
                    "Worker did not return a successful Gateway public-IP observation."
                ) from None
        return {
            "ok": True,
            "worker_url": self.config.base_url,
            "access_verified": True,
            "gateway_egress_ip": str(address),
        }

    def captions(self, video_id: str, include_segments: bool, max_segments: int):
        flow = CaptionFlow(self)
        try:
            result = flow.fetch(video_id)
        finally:
            flow.close_origin()
        segments = result["segments"]
        return {
            "ok": True,
            "video_id": video_id,
            "language": result["language"],
            "caption_type": result["caption_type"],
            "segment_count": len(segments),
            "segments": segments[:max_segments] if include_segments else [],
            "segments_truncated": include_segments and len(segments) > max_segments,
        }

    def relay(
        self,
        service: str,
        operation: str,
        parameters: dict,
        *,
        query: str = "",
        headers: dict[str, str] | None = None,
        body: str | None = None,
        max_bytes: int = 2_000_000,
    ):
        response = self.request(
            "POST",
            f"/v1/relay/{service}/{operation}",
            json={
                "parameters": parameters,
                "query": query,
                "headers": headers or {},
                "body": body,
            },
        )
        try:
            origin_status = response.headers.get("X-Origin-Http", "")
            if not origin_status.isdecimal():
                raise TransportError(
                    "Worker relay returned an invalid upstream status."
                )
            raw = response.content[: max_bytes + 1]
            truncated = len(raw) > max_bytes
            raw = raw[:max_bytes]
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            text = raw.decode("utf-8", errors="replace")
            origin_code = int(origin_status)
            result = {
                "ok": 200 <= origin_code < 300,
                "bridge_ok": True,
                "service": service,
                "operation": operation,
                "upstream_http": origin_code,
                "content_type": content_type,
                "body": text,
                "body_truncated": truncated,
                "_set_cookies": [
                    unquote(value.strip())
                    for value in response.headers.get("X-Origin-Set-Cookie", "").split(",")
                    if value.strip()
                ],
            }
            if not result["ok"]:
                result["error"] = "upstream_http"
        finally:
            response.close()
        if "json" in content_type.lower() and not truncated:
            try:
                result["json"] = json.loads(result["body"])
            except ValueError:
                pass
        return result


def error_result(error: BridgeError):
    result = {
        "ok": False,
        "error": error.code,
        "message": str(error),
    }
    status = getattr(error, "status", None)
    if status is not None:
        result["upstream_status"] = status
    stage = getattr(error, "stage", None)
    if stage is not None:
        result["stage"] = stage
    return result
