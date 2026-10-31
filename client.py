"""HTTP access and the on-disk cache.

Only three hosts are ever contacted (see ALLOWED_HOSTS). Every request is HTTPS,
paced to at most one request per second per host, has a socket timeout and a
response size cap. Redirects are followed by hand and only to allowed hosts.

The cache lives under <HERMES_HOME>/plugin-data/bg-law/cache/. It holds public
legal texts and indexes only (never a user's question). Writes are atomic
(temp file + os.replace), so parallel Hermes processes can share it.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .version import __version__

ALLOWED_HOSTS = frozenset({
    "lsource.net",
    "publications.europa.eu",
    "www.constcourt.bg",
})
USER_AGENT = f"hermes-bg-law/{__version__} (+https://github.com/apoapostolov/hermes-bg-eu-law)"
TIMEOUT_S = 30
MAX_REDIRECTS = 5
MIN_INTERVAL_S = 1.0
DEFAULT_MAX_BYTES = 40 * 1024 * 1024


class LawError(Exception):
    """An error the tool reports to the model as JSON (never raised past a handler)."""

    def __init__(self, kind: str, message: str, hint: str = "", details: Optional[dict] = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.hint = hint
        self.details = details or {}


@dataclass
class Response:
    status: int
    body: bytes
    headers: Dict[str, str]
    url: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _check_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise LawError("host_not_allowed",
                       f"Refused to contact {host or url!r}: not one of the plugin's source hosts.")
    if parts.scheme == "http":  # every source host serves the same paths over HTTPS
        parts = parts._replace(scheme="https")
    elif parts.scheme != "https":
        raise LawError("network", f"Refused non-HTTPS URL {url!r}.")
    return urllib.parse.urlunsplit(parts)


class Http:
    """Small urllib wrapper. ``transport`` can be replaced in tests (url, headers) -> Response."""

    def __init__(self, transport: Optional[Callable[[str, Dict[str, str]], Response]] = None):
        self._transport = transport or self._urllib
        self._lock = threading.Lock()
        self._last: Dict[str, float] = {}
        self.requests = 0

    def _pace(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._last.get(host, 0.0) + MIN_INTERVAL_S - now
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    @staticmethod
    def _urllib(url: str, headers: Dict[str, str]) -> Response:
        opener = urllib.request.build_opener(_NoRedirect())
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            resp = opener.open(req, timeout=TIMEOUT_S)
            status = resp.status
        except urllib.error.HTTPError as err:
            resp = err
            status = err.code
        try:
            raw = resp.read(DEFAULT_MAX_BYTES + 1) if status == 200 else resp.read(65536)
        finally:
            try:
                resp.close()
            except Exception:
                pass
        hdrs = {k.lower(): v for k, v in (resp.headers.items() if resp.headers else [])}
        return Response(status, raw, hdrs, url)

    def get(self, url: str, headers: Optional[Dict[str, str]] = None,
            max_bytes: int = DEFAULT_MAX_BYTES) -> Response:
        url = _check_url(url)
        hdrs = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
        hdrs.update(headers or {})
        for _ in range(MAX_REDIRECTS + 1):
            host = urllib.parse.urlsplit(url).hostname or ""
            self._pace(host)
            self.requests += 1
            try:
                resp = self._transport(url, hdrs)
            except LawError:
                raise
            except TimeoutError as err:
                raise LawError("timeout", f"{host} did not answer within {TIMEOUT_S} s.",
                               "Wait a moment and retry once.") from err
            except (urllib.error.URLError, OSError) as err:
                raise LawError("network", f"Could not reach {host}: {err}",
                               "Check the network connection, then retry once.") from err
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                if not loc:
                    raise LawError("bad_response", f"{host} sent a redirect without a target.")
                url = _check_url(urllib.parse.urljoin(url, loc))
                continue
            body = resp.body
            if resp.headers.get("content-encoding", "").lower() == "gzip" and body:
                body = _gunzip(body, max_bytes)
            if len(body) > max_bytes:
                raise LawError("too_large", f"The response from {host} is larger than {max_bytes // (1024 * 1024)} MB.")
            return Response(resp.status, body, resp.headers, url)
        raise LawError("bad_response", "Too many redirects.")


def _gunzip(data: bytes, max_bytes: int) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
        out = gz.read(max_bytes + 1)
    if len(out) > max_bytes:
        raise LawError("too_large", f"The unpacked response is larger than {max_bytes // (1024 * 1024)} MB.")
    return out


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # public helper, stable across versions
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_key(key: str) -> str:
    clean = _SAFE.sub("_", key).strip("._")[:80] or "x"
    if clean != key:
        clean = f"{clean}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"
    return clean


@dataclass
class Cached:
    data: Any
    state: str  # fresh | revalidated | cached | stale
    fetched_at: float
    meta: Dict[str, Any] = field(default_factory=dict)


class Cache:
    """JSON documents plus a small meta file (etag, last-modified, fetch time) per key."""

    def __init__(self, root: Optional[Path] = None):
        self._root = root
        self._memo: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        root = self._root or hermes_home() / "plugin-data" / "bg-law" / "cache"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _paths(self, key: str):
        base = self.root / _safe_key(key)
        return base.with_suffix(".json"), base.with_suffix(".meta")

    def read(self, key: str):
        data_p, meta_p = self._paths(key)
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            stat = data_p.stat()
        except (OSError, ValueError):
            return None, None
        memo = self._memo.get(key)
        if memo and memo[0] == stat.st_mtime_ns:
            return memo[1], meta
        try:
            data = json.loads(data_p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, None
        with self._lock:
            if len(self._memo) > 24:
                self._memo.clear()
            self._memo[key] = (stat.st_mtime_ns, data)
        return data, meta

    def write(self, key: str, data: Any, meta: Dict[str, Any]) -> None:
        data_p, meta_p = self._paths(key)
        _atomic_write(data_p, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        _atomic_write(meta_p, json.dumps(meta).encode("utf-8"))

    def write_meta(self, key: str, meta: Dict[str, Any]) -> None:
        _atomic_write(self._paths(key)[1], json.dumps(meta).encode("utf-8"))

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.root.iterdir() if p.is_file())

    def prune(self, max_bytes: int, keep: tuple = ()) -> int:
        """Delete least recently fetched entries until the cache fits. Returns entries removed."""
        keep_names = {_safe_key(k) for k in keep}
        entries = []
        total = 0
        for meta_p in self.root.glob("*.meta"):
            data_p = meta_p.with_suffix(".json")
            try:
                size = meta_p.stat().st_size + (data_p.stat().st_size if data_p.exists() else 0)
                used = json.loads(meta_p.read_text(encoding="utf-8")).get("used_at", 0)
            except (OSError, ValueError):
                continue
            total += size
            entries.append((used, size, meta_p, data_p))
        removed = 0
        for used, size, meta_p, data_p in sorted(entries, key=lambda e: e[0]):
            if total <= max_bytes:
                break
            if meta_p.stem in keep_names:
                continue
            for p in (data_p, meta_p):
                try:
                    p.unlink()
                except OSError:
                    pass
            total -= size
            removed += 1
        return removed


def _atomic_write(path: Path, payload: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Store:
    """Fetch-through cache: fresh entries are served from disk, older ones are revalidated
    with ETag / If-Modified-Since, and on a network failure the last copy is served as stale."""

    def __init__(self, http: Optional[Http] = None, cache: Optional[Cache] = None,
                 settings: Optional[Callable[[str, Any], Any]] = None):
        self.http = http or Http()
        self.cache = cache or Cache()
        self._settings = settings or (lambda key, default=None: default)

    def setting(self, key: str, default: Any = None) -> Any:
        try:
            value = self._settings(key, default)
        except Exception:
            value = default
        return default if value is None else value

    def max_age_s(self) -> float:
        try:
            hours = float(self.setting("max_age_hours", 24))
        except (TypeError, ValueError):
            hours = 24.0
        return max(0.0, hours) * 3600

    def fetch(self, key: str, url: str, parse: Callable[[bytes, Response], Any], *,
              max_age_s: Optional[float] = None, headers: Optional[Dict[str, str]] = None,
              max_bytes: int = DEFAULT_MAX_BYTES, immutable: bool = False) -> Cached:
        age_limit = self.max_age_s() if max_age_s is None else max_age_s
        data, meta = self.cache.read(key)
        now = time.time()
        if data is not None and (immutable or now - meta.get("fetched_at", 0) < age_limit):
            self._touch(key, meta, now)
            return Cached(data, "cached", meta.get("fetched_at", now), meta)
        cond = dict(headers or {})
        if data is not None:
            if meta.get("etag"):
                cond["If-None-Match"] = meta["etag"]
            if meta.get("last_modified"):
                cond["If-Modified-Since"] = meta["last_modified"]
        try:
            resp = self.http.get(url, cond, max_bytes=max_bytes)
        except LawError as err:
            if data is not None and err.kind in ("network", "timeout", "upstream", "rate_limited"):
                return Cached(data, "stale", meta.get("fetched_at", 0), meta)
            raise
        if resp.status == 304 and data is not None:
            meta.update(fetched_at=now, used_at=now)
            self.cache.write_meta(key, meta)
            return Cached(data, "revalidated", now, meta)
        if resp.status != 200:
            if data is not None and resp.status >= 500:
                return Cached(data, "stale", meta.get("fetched_at", 0), meta)
            raise _status_error(resp)
        parsed = parse(resp.body, resp)
        new_meta = {"url": resp.url, "fetched_at": now, "used_at": now,
                    "etag": resp.headers.get("etag", ""),
                    "last_modified": resp.headers.get("last-modified", "")}
        self.cache.write(key, parsed, new_meta)
        self._maybe_prune(key)
        return Cached(parsed, "fresh", now, new_meta)

    def _touch(self, key: str, meta: Dict[str, Any], now: float) -> None:
        if now - meta.get("used_at", 0) > 3600:
            meta["used_at"] = now
            try:
                self.cache.write_meta(key, meta)
            except OSError:
                pass

    def _maybe_prune(self, key: str) -> None:
        try:
            limit_mb = float(self.setting("cache_max_mb", 200))
        except (TypeError, ValueError):
            limit_mb = 200.0
        try:
            self.cache.prune(int(max(limit_mb, 5) * 1024 * 1024), keep=(key,))
        except OSError:
            pass


def _status_error(resp: Response) -> LawError:
    host = urllib.parse.urlsplit(resp.url).hostname
    if resp.status in (401, 403):
        return LawError("unauthorized",
                        f"{host} refused the request (HTTP {resp.status}): the API key is missing, wrong or out of quota.",
                        "Set the lsource.net API key in this plugin's settings (free key at "
                        "https://lsource.net/developers), then retry.")
    if resp.status == 404:
        return LawError("not_found", f"{host} has no document at {resp.url} (HTTP 404).")
    if resp.status == 409:
        return LawError("withheld",
                        f"{host} withheld this text (HTTP 409): the corpus audit flagged it as unreliable.",
                        "Do not quote it. Give the user the act's own page and say the machine-readable copy "
                        "is withheld.")
    if resp.status == 429:
        return LawError("rate_limited", f"{host} answered HTTP 429 (rate limit).",
                        "Wait a minute, then retry once.")
    if resp.status == 202:
        return LawError("upstream", f"{host} answered HTTP 202 (request queued or bot check).",
                        "Try again later.")
    if resp.status >= 500:
        return LawError("upstream", f"{host} answered HTTP {resp.status}.", "Try again later.")
    return LawError("bad_request", f"{host} answered HTTP {resp.status}.")
