import html
import ipaddress
import re
import socket
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests


HTML_LIMIT = 1_000_000
IMAGE_LIMIT = 8 * 1024 * 1024
VIDEO_LIMIT = 20 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_VIDEO_TYPES = {"video/mp4"}


def _public_http_url(url):
    try:
        parsed = urlparse((url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False

        host = parsed.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith(".localhost"):
            return False

        # Literal private IPs are always rejected. DNS names are resolved to
        # prevent feeds/pages from pointing media at private/metadata networks.
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_global
        except ValueError:
            pass

        try:
            addresses = {row[4][0] for row in socket.getaddrinfo(host, None)}
        except OSError:
            return False

        if not addresses:
            return False
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                return False
        return True
    except Exception:
        return False


def _kind_from_type(content_type, url=""):
    base = (content_type or "").split(";", 1)[0].strip().lower()
    if base in ALLOWED_IMAGE_TYPES:
        return "image"
    if base in ALLOWED_VIDEO_TYPES:
        return "video"

    path = urlparse(url).path.lower()
    if path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return "image"
    if path.endswith(".mp4"):
        return "video"
    return None


def _candidate(url, content_type="", origin="rss"):
    if not _public_http_url(url):
        return None
    kind = _kind_from_type(content_type, url)
    if not kind:
        return None
    return {
        "url": url,
        "kind": kind,
        "content_type": (content_type or "").split(";", 1)[0].lower(),
        "origin": origin,
    }


def _extract_meta(html_text, base_url):
    # Bounded HTML is enough for OpenGraph/Twitter metadata, which is normally
    # in <head>. Avoid a heavyweight parser/dependency for this small task.
    tags = re.findall(r"<meta\b[^>]*>", html_text, flags=re.IGNORECASE)
    values = {}
    for tag in tags:
        attrs = dict(
            (name.lower(), html.unescape(value))
            for name, _, value in re.findall(
                r'''([:\w-]+)\s*=\s*(["'])(.*?)\2''',
                tag,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        content = (attrs.get("content") or "").strip()
        if key and content and key not in values:
            values[key] = content

    ordered = [
        ("og:video:secure_url", "video/mp4"),
        ("og:video", "video/mp4"),
        ("twitter:player:stream", "video/mp4"),
        ("og:image:secure_url", "image/jpeg"),
        ("og:image", "image/jpeg"),
        ("twitter:image", "image/jpeg"),
    ]
    for key, guessed_type in ordered:
        raw = values.get(key)
        if not raw:
            continue
        url = urljoin(base_url, raw)
        candidate = _candidate(url, guessed_type, "opengraph")
        if candidate:
            return candidate
    return None


def discover_media(item, timeout=8):
    """Find one related publisher-provided image/video, otherwise return None."""
    for raw in item.get("media_candidates") or []:
        if isinstance(raw, str):
            candidate = _candidate(raw)
        else:
            candidate = _candidate(
                raw.get("url", ""),
                raw.get("type", ""),
                "rss",
            )
        if candidate:
            return candidate

    article_url = item.get("url", "")
    if not _public_http_url(article_url):
        return None

    try:
        with requests.get(
            article_url,
            timeout=timeout,
            stream=True,
            allow_redirects=True,
            headers={"User-Agent": "PulseX-NewsBot/1.0 (+source-media-preview)"},
        ) as response:
            response.raise_for_status()
            if not _public_http_url(response.url):
                return None
            content_type = response.headers.get("Content-Type", "").lower()
            if "html" not in content_type and "xhtml" not in content_type:
                return None
            chunks = []
            size = 0
            for chunk in response.iter_content(16384):
                if not chunk:
                    continue
                size += len(chunk)
                if size > HTML_LIMIT:
                    break
                chunks.append(chunk)
            text = b"".join(chunks).decode(response.encoding or "utf-8", "replace")
            return _extract_meta(text, response.url)
    except Exception:
        return None


def download_media(media, directory=None, timeout=15):
    """Download one bounded media attachment. Failure is safe: caller can post text."""
    if not media or not _public_http_url(media.get("url", "")):
        return None

    url = media["url"]
    kind = media.get("kind")
    limit = VIDEO_LIMIT if kind == "video" else IMAGE_LIMIT

    target_dir = Path(directory) if directory else Path(tempfile.mkdtemp(prefix="pulsex-media-"))
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        with requests.get(
            url,
            timeout=timeout,
            stream=True,
            allow_redirects=True,
            headers={"User-Agent": "PulseX-NewsBot/1.0 (+media-attachment)"},
        ) as response:
            response.raise_for_status()
            if not _public_http_url(response.url):
                return None

            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            resolved_kind = _kind_from_type(content_type, response.url)
            if not resolved_kind or (kind and resolved_kind != kind):
                return None

            length = response.headers.get("Content-Length")
            if length and int(length) > limit:
                return None

            suffixes = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
                "image/gif": ".gif",
                "video/mp4": ".mp4",
            }
            suffix = suffixes.get(content_type)

            # Some reputable publishers/CDNs serve media with a generic
            # Content-Type even though the final validated URL clearly ends in
            # a supported extension. `_kind_from_type` has already accepted the
            # resource as image/video, so preserve only that small allowlisted
            # URL suffix rather than writing `.bin` (which X may reject).
            if not suffix:
                url_suffix = Path(urlparse(response.url).path).suffix.lower()
                safe_url_suffixes = {
                    ".jpg": ".jpg",
                    ".jpeg": ".jpg",
                    ".png": ".png",
                    ".webp": ".webp",
                    ".gif": ".gif",
                    ".mp4": ".mp4",
                }
                suffix = safe_url_suffixes.get(url_suffix)

            if not suffix:
                return None

            path = target_dir / ("attachment" + suffix)
            total = 0
            with path.open("wb") as handle:
                for chunk in response.iter_content(65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > limit:
                        handle.close()
                        path.unlink(missing_ok=True)
                        return None
                    handle.write(chunk)

            if total == 0:
                path.unlink(missing_ok=True)
                return None
            return str(path)
    except Exception:
        return None
