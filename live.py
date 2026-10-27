"""Resolve live stream links into media URLs OpenCV can read."""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

LIVE_CACHE_SECONDS = 300.0
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
        from yt_dlp import YoutubeDL
    except ImportError as error:
        raise RuntimeError(
            "Live YouTube feeds require yt-dlp. Run bootstrap.ps1 to install requirements."
        ) from error

    try:
        with YoutubeDL(_yt_dlp_options()) as downloader:
            info = downloader.extract_info(value, download=False)
    except Exception as error:
        raise _friendly_youtube_error(error) from error
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

    raw_title = str(info.get("title") or "Live feed")
    title = re.sub(r"[^\x20-\x7E]+", " ", raw_title)
    title = re.sub(r"\s+", " ", title).strip() or "Live feed"
    title = title[:72]
    with _LOCK:
        _CACHE[value] = (time.time() + LIVE_CACHE_SECONDS, url, title)
    return url, title
