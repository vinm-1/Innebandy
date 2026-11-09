"""One-connection client for the public iBIS stats API.

The stats site loads https://api.innebandy.se/v2/api/auth/startkit, then
POSTs that token to /v2/api/auth/handshake. Later GET requests send the
session cookie plus an HMAC-SHA256 signature over

    METHOD \\n path \\n timestamp \\n nonce \\n sha256(body)

which is the same construction the public frontend uses. No credentials
beyond that anonymous startkit are used.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import logging
import ssl
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger("ibis")

API_HOST = "api.innebandy.se"
STATS_HOST = "stats.innebandy.se"
STARTKIT_PATH = "/v2/api/auth/startkit"
HANDSHAKE_PATH = "/v2/api/auth/handshake"
PUBLIC_PREFIX = "/v2/api/public"
DEFAULT_USER_AGENT = (
    "IbisHistoricalStatsScraper/0.1 "
    "(private historical collection; single connection; respects robots.txt)"
)


def body_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_string(method: str, path: str, timestamp_ms: int, nonce: str, body: bytes) -> str:
    return "\n".join(
        [
            method.upper(),
            path,
            str(timestamp_ms),
            nonce,
            body_sha256(body),
        ]
    )


def sign_request(secret: str, method: str, path: str, timestamp_ms: int, nonce: str, body: bytes) -> str:
    message = canonical_string(method, path, timestamp_ms, nonce, body).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


@dataclass
class HttpResult:
    status: int
    body: bytes
    elapsed_ms: int
    headers: dict[str, str]
    set_cookie: str | None = None


class IbisApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, path: str | None = None):
        super().__init__(message)
        self.status = status
        self.path = path


class IbisClient:
    def __init__(
        self,
        min_interval: float = 1.0,
        user_agent: str = DEFAULT_USER_AGENT,
        max_retries: int = 5,
        timeout: float = 60.0,
    ) -> None:
        if min_interval < 0:
            raise ValueError("min_interval must be >= 0")
        self.min_interval = min_interval
        self.user_agent = user_agent
        self.max_retries = max_retries
        self.timeout = timeout
        self._conn: http.client.HTTPSConnection | None = None
        self._last_started = 0.0
        self._token: str | None = None
        self._token_exp_ms = 0
        self._secret: str | None = None
        self._clock_offset_ms = 0
        self._cookie: str | None = None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _connection(self) -> http.client.HTTPSConnection:
        if self._conn is None:
            context = ssl.create_default_context()
            self._conn = http.client.HTTPSConnection(API_HOST, timeout=self.timeout, context=context)
        return self._conn

    def _reset_connection(self) -> None:
        self.close()

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_started)
        if wait > 0:
            time.sleep(wait)
        self._last_started = time.monotonic()

    def _read_set_cookie(self, headers: http.client.HTTPMessage) -> str | None:
        cookies = headers.get_all("Set-Cookie") or []
        pairs = []
        for cookie in cookies:
            pairs.append(cookie.split(";", 1)[0])
        return "; ".join(pairs) if pairs else None

    def _raw(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> HttpResult:
        self._throttle()
        started = time.monotonic()
        last_error: Exception | None = None
        for attempt in range(2):
            conn = self._connection()
            try:
                conn.request(method, path, body=body, headers=headers)
                response = conn.getresponse()
                payload = response.read()
                elapsed = int((time.monotonic() - started) * 1000)
                header_map = {k.lower(): v for k, v in response.headers.items()}
                return HttpResult(
                    status=response.status,
                    body=payload,
                    elapsed_ms=elapsed,
                    headers=header_map,
                    set_cookie=self._read_set_cookie(response.headers),
                )
            except (http.client.HTTPException, OSError, TimeoutError) as exc:
                last_error = exc
                self._reset_connection()
                if attempt == 0:
                    time.sleep(self.min_interval or 0.5)
                    continue
                raise IbisApiError(f"{method} {path} failed: {exc}", path=path) from exc
        raise IbisApiError(f"{method} {path} failed: {last_error}", path=path)

    def _decode_json(self, result: HttpResult) -> Any:
        if not result.body:
            return None
        try:
            return json.loads(result.body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise IbisApiError(
                f"response was not JSON (HTTP {result.status})",
                status=result.status,
            ) from exc

    def _token_is_fresh(self) -> bool:
        return bool(self._token) and (self._token_exp_ms - 120_000) > int(time.time() * 1000)

    def refresh_session(self) -> None:
        result = self._raw(
            "GET",
            STARTKIT_PATH,
            b"",
            {
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        if result.status != 200:
            raise IbisApiError("startkit was refused", status=result.status, path=STARTKIT_PATH)
        payload = self._decode_json(result)
        if not isinstance(payload, dict) or not payload.get("accessToken"):
            raise IbisApiError("startkit response had no access token", status=result.status)
        token = str(payload["accessToken"])
        exp_ms = _jwt_exp_ms(token)
        handshake = self._raw(
            "POST",
            HANDSHAKE_PATH,
            b"",
            {
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        if handshake.status != 200 or not handshake.set_cookie:
            raise IbisApiError(
                "handshake did not return a session",
                status=handshake.status,
                path=HANDSHAKE_PATH,
            )
        data = self._decode_json(handshake)
        if not isinstance(data, dict) or not data.get("ss") or data.get("st") is None:
            raise IbisApiError("handshake response had no session secret", status=handshake.status)
        server_ms = int(data["st"])
        self._token = token
        self._token_exp_ms = exp_ms
        self._secret = str(data["ss"])
        self._cookie = handshake.set_cookie
        self._clock_offset_ms = server_ms - int(time.time() * 1000)
        log.info("session ready; token expires at unix ms %s", exp_ms)

    def ensure_session(self) -> None:
        if not self._token_is_fresh() or not self._secret or not self._cookie:
            self.refresh_session()

    def get_json(self, path: str) -> tuple[int, Any, HttpResult]:
        """GET a public API path. `path` includes the query string and starts with /v2."""
        if not path.startswith("/"):
            raise ValueError(f"path must be absolute, got {path}")
        attempt = 0
        refreshed_for_signature = False
        while True:
            self.ensure_session()
            assert self._secret and self._token and self._cookie
            timestamp = int(time.time() * 1000) + self._clock_offset_ms
            nonce = str(uuid.uuid4())
            signature = sign_request(self._secret, "GET", path, timestamp, nonce, b"")
            result = self._raw(
                "GET",
                path,
                b"",
                {
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._token}",
                    "Cookie": self._cookie,
                    "X-Signature": signature,
                    "X-Timestamp": str(timestamp),
                    "X-Nonce": nonce,
                },
            )
            if result.status == 401 and not refreshed_for_signature:
                error = _error_code(result.body)
                log.info("HTTP 401 %s on %s; refreshing session", error, path)
                self._secret = None
                self.refresh_session()
                refreshed_for_signature = True
                continue
            if result.status == 429 or result.status >= 500:
                attempt += 1
                if attempt > self.max_retries:
                    raise IbisApiError(
                        f"giving up on {path} after HTTP {result.status}",
                        status=result.status,
                        path=path,
                    )
                delay = _retry_delay(result, attempt, self.min_interval)
                log.warning("HTTP %s on %s; sleeping %.1fs", result.status, path, delay)
                time.sleep(delay)
                continue
            payload = self._decode_json(result) if result.body else None
            return result.status, payload, result

    def fetch_text(self, url: str) -> tuple[int, str]:
        """Fetch a single URL on the stats host, still through the same throttle."""
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != STATS_HOST:
            raise ValueError(f"refusing non-stats URL {url}")
        self._throttle()
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        context = ssl.create_default_context()
        conn = http.client.HTTPSConnection(STATS_HOST, timeout=self.timeout, context=context)
        try:
            conn.request(
                "GET",
                path,
                headers={"User-Agent": self.user_agent, "Accept": "text/plain"},
            )
            response = conn.getresponse()
            body = response.read()
            return response.status, body.decode("utf-8", errors="replace")
        finally:
            conn.close()


def _jwt_exp_ms(token: str) -> int:
    try:
        payload = token.split(".")[1]
        pad = "=" * ((4 - len(payload) % 4) % 4)
        import base64

        data = json.loads(base64.urlsafe_b64decode(payload + pad))
        return int(data["exp"]) * 1000
    except (IndexError, KeyError, ValueError, json.JSONDecodeError):
        return int(time.time() * 1000) + 20 * 60 * 1000


def _error_code(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if isinstance(data, dict):
        return str(data.get("Error") or data.get("error") or "")
    return ""


def _retry_delay(result: HttpResult, attempt: int, min_interval: float) -> float:
    header = result.headers.get("retry-after")
    if header:
        try:
            return max(float(header), min_interval)
        except ValueError:
            pass
    return max(min_interval, min(60.0, 2.0 ** attempt))
