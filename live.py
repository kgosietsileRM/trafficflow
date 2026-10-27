"""Resolve live stream links into media URLs OpenCV can read."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

LIVE_CACHE_SECONDS = 300.0
INVIDIOUS_DEFAULT_URL = "https://yt.chocolatemoo53.com"
INVIDIOUS_TIMEOUT = 25.0
_SEGMENT_CACHE_SECONDS = 300.0
_SEGMENT_CACHE_LIMIT = 512
_ITAG_HEIGHTS = {
    160: 144,
    133: 240,
    134: 360,
    135: 480,
    136: 720,
    137: 1080,
    138: 1440,
    2160: 2160,
}
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
_LIVE_PROTOCOLS = ("rtsp://", "rtmp://", "rtmps://", "udp://", "http://", "https://")
_CACHE: dict[str, tuple[float, str, str]] = {}
_LOCK = threading.Lock()


def ca_bundle_path() -> Path | None:
    """Return the configured CA bundle or certifi's public root bundle."""
    configured = os.environ.get("SPE_CA_BUNDLE")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise RuntimeError("SPE_CA_BUNDLE must point to a readable PEM CA bundle file.")
        return path
    try:
        import certifi
    except ImportError:
        return None
    return Path(certifi.where())


def _yt_dlp_options() -> dict[str, Any]:
    """Build YouTube resolver options from deployment environment settings."""
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
    }
    cookie_file = os.environ.get("SPE_YTDLP_COOKIE_FILE")
    if cookie_file:
        path = Path(cookie_file).expanduser()
        if not path.is_file():
            raise RuntimeError("SPE_YTDLP_COOKIE_FILE must point to a readable cookies.txt file.")
        options["cookiefile"] = str(path)

    custom_ca = os.environ.get("SPE_CA_BUNDLE")
    if custom_ca:
        bundle = ca_bundle_path()
        assert bundle is not None
        # yt-dlp's urllib-backed TLS contexts honor these standard variables.
        os.environ["SSL_CERT_FILE"] = str(bundle)
        os.environ["REQUESTS_CA_BUNDLE"] = str(bundle)
    return options


def _friendly_youtube_error(error: Exception) -> RuntimeError:
    """Explain common deployment-side YouTube extraction failures."""
    message = str(error)
    lowered = message.lower()
    if "certificate_verify_failed" in lowered or "certificate verify failed" in lowered:
        return RuntimeError(
            "YouTube TLS certificate validation failed. Install the host's CA certificates; "
            "for a custom proxy CA, set SPE_CA_BUNDLE to its PEM bundle."
        )
    if any(
        marker in lowered
        for marker in (
            "sign in to confirm",
            "confirm you're not a bot",
            "cookies are required",
            "cookie",
        )
    ):
        return RuntimeError(
            "YouTube rejected the request or could not load its cookies. If this feed needs "
            "authentication, export a Netscape-format cookies.txt file and set "
            "SPE_YTDLP_COOKIE_FILE to its deployment secret path."
        )
    if "javascript runtime" in lowered or "yt-dlp-ejs" in lowered:
        return RuntimeError(
            "YouTube requires yt-dlp's JavaScript challenge support. Install the "
            "yt-dlp[default] requirements and provide a supported runtime such as Deno or Node."
        )
    if "http error 403" in lowered or "403 forbidden" in lowered:
        return RuntimeError(
            "YouTube rejected the resolved stream (HTTP 403). The feed may be restricted, "
            "geo-blocked, or its stream URL may have expired; refresh and try again."
        )
    return RuntimeError(f"Could not resolve the YouTube feed: {message}")


def _clean_title(raw: Any) -> str:
    title = re.sub(r"[^\x20-\x7E]+", " ", str(raw or "Live feed"))
    title = re.sub(r"\s+", " ", title).strip() or "Live feed"
    return title[:72]


def _youtube_id(value: str) -> str | None:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parts[0] if parts else None
    elif parts and parts[0] in {"live", "embed", "shorts", "v"} and len(parts) > 1:
        candidate = parts[1]
    else:
        candidate = urllib.parse.parse_qs(parsed.query).get("v", [None])[0]
    if candidate and re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
        return candidate
    return None


def _invidious_bases() -> list[str]:
    raw = os.environ.get("SPE_INVIDIOUS_URL", INVIDIOUS_DEFAULT_URL)
    if not raw.strip() or raw.strip().lower() in {"0", "off", "none", "disabled"}:
        return []
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


def _fetch(url: str, *, headers: dict[str, str] | None = None) -> tuple[bytes, dict[str, str]]:
    request_headers = {"User-Agent": "Mozilla/5.0 (compatible; FlowTraffic/1.0)"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=INVIDIOUS_TIMEOUT) as response:
            return response.read(), {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Stream resolver returned HTTP {error.code}.") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"Stream resolver request failed: {error}") from error


def _fetch_text(url: str) -> str:
    body, _headers = _fetch(url)
    return body.decode("utf-8", "replace")


def _instance_path(base: str, url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{base}{parsed.path or '/'}{query}"


def _segment_proxy_url(base: str, url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path
    if path.startswith("/videoplayback/"):
        path = path[len("/videoplayback/") :].rstrip("/")
        path = re.sub(r"mime/(\w+)/(\w+)", r"mime/\1%2F\2", path)
        parts = path.split("/")
        params = [
            (parts[index], urllib.parse.unquote_plus(parts[index + 1]))
            for index in range(0, len(parts) - 1, 2)
        ]
        params.append(("host", parsed.hostname or ""))
        return f"{base}/videoplayback?{urllib.parse.urlencode(params)}"
    return _instance_path(base, url)


def _select_media_playlist(variant: str) -> str | None:
    lines = [line.strip() for line in variant.splitlines()]
    items: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attributes = line.split(":", 1)[1]
        resolution = re.search(r"RESOLUTION=(\d+)x(\d+)", attributes)
        bandwidth = re.search(r"BANDWIDTH=(\d+)", attributes)
        height = int(resolution.group(2)) if resolution else 0
        bitrate = int(bandwidth.group(1)) if bandwidth else 0
        uri = next(
            (candidate for candidate in lines[index + 1 :] if candidate and not candidate.startswith("#")),
            None,
        )
        if uri:
            items.append((height, bitrate, uri))
    if not items:
        return next((line for line in lines if line and not line.startswith("#")), None)
    capped = [item for item in items if item[0] <= 720] or items
    return max(capped, key=lambda item: (item[0], item[1]))[2]


class _InvidiousStream:
    def __init__(self, origin: str, video_id: str, title: str) -> None:
        self.origin = origin
        self.video_id = video_id
        self.title = title
        self.mode = "hls"
        self.variant_url = ""
        self.media_url = ""
        self.segments: dict[str, tuple[str, float]] = {}
        self.chunk_itags: list[int] = []
        self.chunk_itag = 0
        self.chunks: dict[str, tuple[bytes, int, float]] = {}
        self.chunk_order: list[str] = []
        self.next_sequence = 1
        self.last_poll = 0.0


def _chunk_itags(payload: dict[str, Any]) -> list[int]:
    formats = [
        item
        for item in payload.get("adaptiveFormats") or []
        if isinstance(item, dict)
        and isinstance(item.get("itag"), (int, str))
        and str(item.get("type", "")).startswith("video/mp4")
    ]
    if not formats:
        formats = [
            item
            for item in payload.get("adaptiveFormats") or []
            if isinstance(item, dict)
            and isinstance(item.get("itag"), (int, str))
            and str(item.get("type", "")).startswith("video/")
        ]
    scored: list[tuple[int, int, int]] = []
    for item in formats:
        try:
            itag = int(item["itag"])
        except (TypeError, ValueError):
            continue
        height = _ITAG_HEIGHTS.get(itag, 0)
        bitrate = int(item.get("bitrate") or 0)
        scored.append((height, bitrate, itag))
    capped = [item for item in scored if 0 < item[0] <= 720] or scored
    capped.sort(reverse=True)
    return [itag for _height, _bitrate, itag in capped]


def _latest_chunk_body(stream: _InvidiousStream, itag: int) -> bytes:
    url = (
        f"{stream.origin}/latest_version?id={urllib.parse.quote(stream.video_id)}"
        f"&itag={itag}&local=true"
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; FlowTraffic/1.0)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=INVIDIOUS_TIMEOUT) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Stream resolver returned HTTP {error.code}.") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"Stream resolver request failed: {error}") from error
    if len(body) < 1024 or body[4:8] != b"ftyp":
        raise RuntimeError("Invidious did not return a readable video chunk.")
    return body


def _poll_chunk(stream: _InvidiousStream) -> None:
    now = time.time()
    with _HLS_LOCK:
        if stream.last_poll and now - stream.last_poll < 2.0:
            return
        stream.last_poll = now
    last_error: Exception | None = None
    for itag in list(stream.chunk_itags):
        try:
            body = _latest_chunk_body(stream, itag)
        except Exception as error:
            last_error = error
            continue
        digest = hashlib.sha1(body).hexdigest()[:24]
        with _HLS_LOCK:
            if digest not in stream.chunks:
                sequence = stream.next_sequence
                stream.next_sequence += 1
                stream.chunks[digest] = (body, sequence, now)
                stream.chunk_order.append(digest)
                while len(stream.chunk_order) > 8:
                    stale = stream.chunk_order.pop(0)
                    stream.chunks.pop(stale, None)
            stream.chunk_itag = itag
        return
    with _HLS_LOCK:
        has_chunks = bool(stream.chunks)
    if not has_chunks and last_error is not None:
        raise RuntimeError(f"Could not fetch a live video chunk: {last_error}") from last_error


_HLS_LOCK = threading.RLock()
_HLS_SERVER: ThreadingHTTPServer | None = None
_HLS_PORT = 0
_HLS_STREAMS: dict[str, _InvidiousStream] = {}


def _refresh_stream(stream: _InvidiousStream) -> None:
    api_url = (
        f"{stream.origin}/api/v1/videos/{urllib.parse.quote(stream.video_id)}?local=true"
    )
    payload = json.loads(_fetch_text(api_url))
    if not isinstance(payload, dict):
        raise RuntimeError("Invidious returned an invalid video payload.")
    hls_url = payload.get("hlsUrl")
    if isinstance(hls_url, str) and hls_url:
        hls_url = urllib.parse.urljoin(f"{stream.origin}/", hls_url)
        media = _select_media_playlist(_fetch_text(hls_url))
        if not media:
            raise RuntimeError("The Invidious HLS manifest did not contain a video stream.")
        stream.mode = "hls"
        stream.variant_url = hls_url
        stream.media_url = urllib.parse.urljoin(hls_url, media)
    else:
        itags = _chunk_itags(payload)
        if not itags:
            raise RuntimeError("Invidious did not return a readable video stream for this feed.")
        stream.mode = "chunks"
        stream.chunk_itags = itags
        stream.chunk_itag = itags[0]
        stream.chunks.clear()
        stream.chunk_order.clear()
        stream.next_sequence = 1
        stream.last_poll = 0.0
        _poll_chunk(stream)
    if payload.get("title"):
        stream.title = _clean_title(payload["title"])


def _stream_token(origin: str, video_id: str) -> str:
    return hashlib.sha1(f"{origin}\0{video_id}".encode("utf-8")).hexdigest()[:16]


def _ensure_local_stream(origin: str, video_id: str) -> tuple[str, str]:
    token = _stream_token(origin, video_id)
    with _HLS_LOCK:
        stream = _HLS_STREAMS.get(token)
    if stream is None:
        stream = _InvidiousStream(origin, video_id, "Live feed")
        _refresh_stream(stream)
        with _HLS_LOCK:
            stream = _HLS_STREAMS.setdefault(token, stream)
    return token, stream.title


def _ensure_hls_server() -> int:
    global _HLS_SERVER, _HLS_PORT
    with _HLS_LOCK:
        if _HLS_SERVER is None:
            server = ThreadingHTTPServer(("127.0.0.1", 0), _HLSHandler)
            server.daemon_threads = True
            thread = threading.Thread(
                target=server.serve_forever,
                name="live-hls-server",
                daemon=True,
            )
            thread.start()
            _HLS_SERVER = server
            _HLS_PORT = int(server.server_address[1])
        return _HLS_PORT


def _proxy_segment(stream: _InvidiousStream, token: str, url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:24]
    now = time.time()
    with _HLS_LOCK:
        stream.segments[digest] = (url, now)
        if len(stream.segments) > _SEGMENT_CACHE_LIMIT:
            cutoff = now - _SEGMENT_CACHE_SECONDS
            for key, (_value, created) in list(stream.segments.items()):
                if created < cutoff:
                    stream.segments.pop(key, None)
            if len(stream.segments) > _SEGMENT_CACHE_LIMIT:
                oldest = sorted(stream.segments, key=lambda key: stream.segments[key][1])
                for key in oldest[: len(stream.segments) - _SEGMENT_CACHE_LIMIT]:
                    stream.segments.pop(key, None)
    return f"/seg/{token}/{digest}.ts"


def _rewrite_media_playlist(text: str, stream: _InvidiousStream, token: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            line = re.sub(
                r'URI="([^"]+)"',
                lambda match: (
                    f'URI="{_proxy_segment(stream, token, urllib.parse.urljoin(stream.media_url, match.group(1)))}"'
                ),
                line,
            )
        else:
            line = _proxy_segment(stream, token, urllib.parse.urljoin(stream.media_url, line))
        lines.append(line)
    return "\n".join(lines) + "\n"


def _chunk_playlist(stream: _InvidiousStream, token: str) -> str:
    with _HLS_LOCK:
        order = list(stream.chunk_order)[-4:]
        sequences = [stream.chunks[digest][1] for digest in order if digest in stream.chunks]
    if not sequences:
        raise RuntimeError("No live video chunks are available yet.")
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        "#EXT-X-TARGETDURATION:5",
        f"#EXT-X-MEDIA-SEQUENCE:{min(sequences)}",
    ]
    for digest in order:
        lines.extend(["#EXTINF:5.0,", f"/seg/{token}/{digest}.m4s"])
    return "\n".join(lines) + "\n"


def _media_playlist_body(token: str) -> str:
    with _HLS_LOCK:
        stream = _HLS_STREAMS.get(token)
    if stream is None:
        raise KeyError(token)
    if stream.mode == "chunks":
        try:
            _poll_chunk(stream)
        except Exception:
            with _HLS_LOCK:
                has_chunks = bool(stream.chunks)
            if not has_chunks:
                raise
        return _chunk_playlist(stream, token)
    try:
        text = _fetch_text(stream.media_url)
    except Exception:
        _refresh_stream(stream)
        text = _fetch_text(stream.media_url)
    return _rewrite_media_playlist(text, stream, token)


class _HLSHandler(BaseHTTPRequestHandler):
    server_version = "FlowTrafficLive/1.0"

    def do_GET(self) -> None:
        self._handle()

    def do_HEAD(self) -> None:
        self._handle()

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _respond(
        self,
        status: int,
        body: bytes = b"",
        content_type: str = "text/plain",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _handle(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/live/") and path.endswith(".m3u8"):
            token = path[len("/live/") : -len(".m3u8")]
            try:
                body = _media_playlist_body(token).encode("utf-8")
            except Exception:
                self._respond(502, b"Live playlist unavailable")
                return
            self._respond(
                200,
                body,
                "application/x-mpegURL",
                {"Cache-Control": "no-cache"},
            )
            return
        if path.startswith("/seg/"):
            token, _, name = path[len("/seg/") :].partition("/")
            if name.endswith(".ts"):
                name = name[:-3]
            elif name.endswith(".m4s"):
                name = name[:-4]
            with _HLS_LOCK:
                stream = _HLS_STREAMS.get(token)
                chunk = stream.chunks.get(name) if stream else None
                segment = stream.segments.get(name) if stream else None
            if stream is None or (chunk is None and segment is None):
                self._respond(404, b"Unknown segment")
                return
            if chunk is not None:
                body = chunk[0]
                content_type = "video/mp4"
            else:
                assert segment is not None
                try:
                    body, upstream = _fetch(_segment_proxy_url(stream.origin, segment[0]))
                except Exception:
                    self._respond(502, b"Segment unavailable")
                    return
                content_type = upstream.get("content-type", "video/mp2t")
            response_headers = {"Cache-Control": "no-store", "Accept-Ranges": "bytes"}
            status = 200
            range_header = self.headers.get("Range")
            if range_header:
                match = re.match(r"bytes=(\d*)-(\d*)", range_header)
                if match:
                    total = len(body)
                    start = int(match.group(1) or 0)
                    end = int(match.group(2)) if match.group(2) else total - 1
                    end = min(end, total - 1)
                    if start <= end:
                        body = body[start : end + 1]
                        status = 206
                        response_headers["Content-Range"] = f"bytes {start}-{end}/{total}"
            self._respond(status, body, content_type, response_headers)
            return
        self._respond(404, b"Not found")


def _resolve_with_invidious(value: str) -> tuple[str, str]:
    bases = _invidious_bases()
    if not bases:
        raise RuntimeError("The Invidious fallback is disabled.")
    video_id = _youtube_id(value)
    if video_id is None:
        raise RuntimeError("Could not read the YouTube video id from this feed.")
    failures: list[str] = []
    for base in bases:
        try:
            token, title = _ensure_local_stream(base, video_id)
            port = _ensure_hls_server()
            return f"http://127.0.0.1:{port}/live/{token}.m3u8", title
        except Exception as error:
            failures.append(f"{base}: {error}")
    raise RuntimeError("No Invidious instance could resolve this feed (" + "; ".join(failures) + ")")


def is_live_source(value: str) -> bool:
    """Return True for live-capable URLs rather than local files."""
    lowered = value.strip().lower()
    if lowered.startswith(("rtsp://", "rtmp://", "rtmps://", "udp://")):
        return True
    if not lowered.startswith(_LIVE_PROTOCOLS):
        return False
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host in _YOUTUBE_HOSTS:
        return True
    path = parsed.path.lower()
    return path.endswith((".m3u8", ".mpd")) or "live" in host or "stream" in host


def is_youtube(value: str) -> bool:
    host = (urlparse(value).hostname or "").lower()
    return host in _YOUTUBE_HOSTS


def _resolve_with_ytdlp(value: str) -> tuple[str, str]:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as error:
        raise RuntimeError(
            "Live YouTube feeds require yt-dlp. Run bootstrap.ps1 to install requirements."
        ) from error

    with YoutubeDL(_yt_dlp_options()) as downloader:
        info = downloader.extract_info(value, download=False)
    if not isinstance(info, dict):
        raise RuntimeError("Could not resolve the live feed.")

    direct = info.get("url")
    if isinstance(direct, str) and direct.startswith(_LIVE_PROTOCOLS):
        url = direct
    else:
        formats = [
            item
            for item in info.get("formats") or []
            if isinstance(item, dict)
            and isinstance(item.get("url"), str)
            and item.get("vcodec") not in (None, "none")
        ]
        if not formats:
            raise RuntimeError("The live feed has no readable video stream.")
        capped = [item for item in formats if (item.get("height") or 0) <= 720]
        candidates = capped or formats
        candidates.sort(key=lambda item: (item.get("height") or 0, item.get("tbr") or 0))
        url = str(candidates[-1]["url"])
    return url, _clean_title(info.get("title"))


def resolve_live(value: str) -> tuple[str, str]:
    """Resolve a live page or stream URL to a direct media URL and title."""
    with _LOCK:
        cached = _CACHE.get(value)
    if cached is not None and cached[0] > time.time():
        return cached[1], cached[2]

    if not is_youtube(value):
        title = urlparse(value).path.rsplit("/", 1)[-1] or "Live feed"
        with _LOCK:
            _CACHE[value] = (time.time() + LIVE_CACHE_SECONDS, value, title)
        return value, title

    try:
        url, title = _resolve_with_ytdlp(value)
    except Exception as error:
        youtube_error = _friendly_youtube_error(error)
        if not _invidious_bases():
            raise youtube_error from error
        try:
            url, title = _resolve_with_invidious(value)
        except Exception as fallback_error:
            raise RuntimeError(
                f"{youtube_error} The fallback stream resolver also failed: {fallback_error}"
            ) from fallback_error
    with _LOCK:
        _CACHE[value] = (time.time() + LIVE_CACHE_SECONDS, url, title)
    return url, title
