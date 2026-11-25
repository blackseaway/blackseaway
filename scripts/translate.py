"""DeepL API wrapper with a monthly character budget.

Without DEEPL_API_KEY every call returns None and the site shows the original text.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from common import DATA, USER_AGENT, env, load_json, now_utc, save_json, site_config

TARGET = {"en": "EN-GB", "ro": "RO", "bg": "BG"}
SOURCE = {"en": "EN", "ro": "RO", "bg": "BG"}
USAGE_FILE = DATA / "deepl_usage.json"


class Translator:
    def __init__(self):
        cfg = site_config()["news"]
        self.key = env("DEEPL_API_KEY")
        self.limit = int(cfg.get("deepl_monthly_limit", 450000))
        self.stop_share = float(cfg.get("deepl_stop_at_share", 0.85))
        host = "api-free.deepl.com" if self.key.endswith(":fx") or not self.key else "api.deepl.com"
        self.base = f"https://{host}/v2"
        month = now_utc().strftime("%Y-%m")
        usage = load_json(USAGE_FILE, {}) or {}
        if usage.get("month") != month:
            usage = {"month": month, "chars": 0}
        self.usage = usage
        self.remote_count = None
        self.disabled_reason = None if self.key else "no DEEPL_API_KEY"
        if self.key:
            self._check_remote_usage()

    # -- budget -------------------------------------------------------
    def _check_remote_usage(self):
        try:
            data = self._call("/usage", None, method="GET")
            self.remote_count = int(data.get("character_count", 0))
            remote_limit = int(data.get("character_limit", self.limit) or self.limit)
            self.limit = min(self.limit, remote_limit)
        except Exception as e:  # noqa: BLE001
            print(f"[deepl] usage check failed: {e}")

    def used(self) -> int:
        return max(self.usage["chars"], self.remote_count or 0)

    def can_spend(self, chars: int) -> bool:
        if self.disabled_reason:
            return False
        if self.used() + chars > self.limit * self.stop_share:
            self.disabled_reason = f"monthly budget reached ({self.used()}/{self.limit})"
            print(f"[deepl] {self.disabled_reason}")
            return False
        return True

    def save(self):
        save_json(USAGE_FILE, self.usage)

    # -- api ------------------------------------------------------------
    def _call(self, path: str, payload: dict | None, method: str = "POST") -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.base + path, data=body, method=method,
            headers={"Authorization": f"DeepL-Auth-Key {self.key}",
                     "Content-Type": "application/json", "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"DeepL HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from None

    def translate(self, texts: list[str], target: str, source: str | None = None,
                  html: bool = False) -> list[str] | None:
        """Translate a batch into `target` ('en'|'ro'|'bg'). Returns None if skipped."""
        texts = [t or "" for t in texts]
        chars = sum(len(t) for t in texts)
        if not texts or not self.can_spend(chars):
            return None
        payload = {"text": texts, "target_lang": TARGET[target]}
        if source in SOURCE:
            payload["source_lang"] = SOURCE[source]
        if html:
            payload["tag_handling"] = "html"
        try:
            data = self._call("/translate", payload)
        except Exception as e:  # noqa: BLE001
            print(f"[deepl] translate failed: {e}")
            if "456" in str(e):  # quota exceeded
                self.disabled_reason = "quota exceeded"
            return None
        self.usage["chars"] += chars
        if self.remote_count is not None:
            self.remote_count += chars
        return [t["text"] for t in data.get("translations", [])]
