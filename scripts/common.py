"""Shared helpers for Black Sea Way scripts (standard library only)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"
CONTENT = ROOT / "content"

USER_AGENT = "BlackSeaWayBot/1.0 (+https://blackseaway.github.io/blackseaway/)"


def load_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")
    tmp.replace(path)


def site_config() -> dict:
    return load_json(CONFIG / "site.json")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def normalize(text: str) -> str:
    """Lowercase, strip diacritics (ș→s, ă→a, й→и), collapse spaces."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower()).strip()


_pattern_cache: dict[str, re.Pattern] = {}


def keyword_pattern(kw: str) -> re.Pattern:
    """`kw` matches at a word start; a trailing `$` means whole word only."""
    if kw not in _pattern_cache:
        whole = kw.endswith("$")
        body = re.escape(normalize(kw.rstrip("$")))
        pat = r"(?<!\w)" + body + (r"(?!\w)" if whole else "")
        _pattern_cache[kw] = re.compile(pat)
    return _pattern_cache[kw]


def has_any(norm_text: str, keywords) -> bool:
    return any(keyword_pattern(k).search(norm_text) for k in keywords)


def short_hash(*parts: str, n: int = 12) -> str:
    h = hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()
    return h[:n]


def http_request(url: str, data: dict | None = None, headers: dict | None = None,
                 method: str | None = None, timeout: int = 30) -> bytes:
    hdrs = {"User-Agent": USER_AGENT}
    hdrs.update(headers or {})
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_json(url: str, data: dict | None = None, headers: dict | None = None,
              method: str | None = None, timeout: int = 30) -> dict:
    try:
        raw = http_request(url, data=data, headers=headers, method=method, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {e.code} for {url.split('?')[0]}: {detail}") from None
    return json.loads(raw.decode("utf-8"))


def env(name: str) -> str:
    return (os.environ.get(name) or "").strip()
