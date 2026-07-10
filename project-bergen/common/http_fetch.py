"""Polite, cached, read-only HTTP GET used by watcher --live and audit --live.

Rules enforced here, in code:
  - identifiable User-Agent (config.yaml http.user_agent)
  - robots.txt checked before every fetch (urllib.robotparser)
  - minimum delay between requests to any host
  - every response cached to out/http_cache/ and reused
  - GET only; there is no code path that can POST, PUT, or DELETE
"""
import hashlib
import time
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urlparse

from common.util import ROOT, ensure_out, load_config

_cfg = load_config()["http"]
_last_hit: dict[str, float] = {}
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}


def _cache_path(url: str):
    return ensure_out("http_cache") / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".cache")


def _robots_allows(url: str) -> bool:
    host = urlparse(url).netloc
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{urlparse(url).scheme}://{host}/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None  # robots unreachable -> be conservative below
        _robots[host] = rp
    rp = _robots[host]
    if rp is None:
        return False  # can't read robots.txt -> don't fetch
    return rp.can_fetch(_cfg["user_agent"], url)


def polite_get(url: str) -> str | None:
    cache = _cache_path(url)
    if cache.exists():
        return cache.read_text(errors="replace")
    if _cfg.get("respect_robots_txt", True) and not _robots_allows(url):
        return None
    host = urlparse(url).netloc
    wait = _cfg["min_delay_seconds"] - (time.monotonic() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": _cfg["user_agent"]})
    try:
        with urllib.request.urlopen(req, timeout=_cfg["timeout_seconds"]) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    finally:
        _last_hit[host] = time.monotonic()
    cache.write_text(body)
    return body
