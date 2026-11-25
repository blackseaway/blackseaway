"""Collect Black Sea news from RSS sources, tag them and translate headlines.

Usage:  python scripts/fetch_news.py            (network + optional DeepL)
        python scripts/fetch_news.py --no-fetch (only translate / prune existing items)
"""
from __future__ import annotations

import datetime as dt
import email.utils
import html
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET

from common import (CONFIG, DATA, has_any, http_request, iso, load_json, normalize, now_utc,
                    parse_iso, save_json, short_hash, site_config)
from translate import Translator

NEWS_FILE = DATA / "news.json"
GN = {"en": ("en-US", "US", "US:en"), "ro": ("ro", "RO", "RO:ro"), "bg": ("bg", "BG", "BG:bg")}
MAX_FETCH_AGE = dt.timedelta(days=14)


def strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_date(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def text_of(el, *names):
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


MEDIA_NS = "{http://search.yahoo.com/mrss/}"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"


def feed_image(el) -> str:
    """Image URL from media:content / media:thumbnail / enclosure / first <img> in the HTML body."""
    for tag in (f"{MEDIA_NS}content", f"{MEDIA_NS}thumbnail"):
        for m in el.iter(tag):
            url = m.get("url", "")
            if url and (m.get("medium") in (None, "image") or re.search(r"\.(jpe?g|png|webp)", url, re.I)):
                return url
    enc = el.find("enclosure")
    if enc is not None and (enc.get("type") or "").startswith("image") and enc.get("url"):
        return enc.get("url")
    for tag in (f"{CONTENT_NS}encoded", "description"):
        node = el.find(tag)
        if node is not None and node.text:
            m = re.search(r"<img[^>]+src=[\"']([^\"']+)", node.text)
            if m:
                return html.unescape(m.group(1))
    return ""


def parse_feed(raw: bytes):
    """Yield dicts (title, url, published, summary, source) from RSS 2.0 or Atom."""
    root = ET.fromstring(raw)
    atom = "{http://www.w3.org/2005/Atom}"
    if root.tag == f"{atom}feed":
        for e in root.iter(f"{atom}entry"):
            link = e.find(f"{atom}link")
            yield {
                "title": text_of(e, f"{atom}title"),
                "url": link.get("href") if link is not None else "",
                "published": parse_date(text_of(e, f"{atom}published", f"{atom}updated")),
                "summary": strip_tags(text_of(e, f"{atom}summary", f"{atom}content")),
                "source": "",
            }
        return
    for it in root.iter("item"):
        src = it.find("source")
        yield {
            "title": text_of(it, "title"),
            "url": text_of(it, "link"),
            "published": parse_date(text_of(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date")),
            "summary": strip_tags(text_of(it, "description")),
            "source": (src.text or "").strip() if src is not None and src.text else "",
            "image": feed_image(it),
        }


def google_news_url(q: str, lang: str) -> str:
    hl, gl, ceid = GN[lang]
    query = urllib.parse.quote(f"{q} when:7d")
    return f"https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"


def classify(text: str, sources: dict):
    norm = normalize(text)
    topics = [t for t, kws in sources["topics"].items() if has_any(norm, kws)]
    places = [p["id"] for p in sources["places"] if has_any(norm, p["match"])]
    relevant = (has_any(norm, sources["anchors"]) or places) and topics \
        and not has_any(norm, sources["exclude"])
    return bool(relevant), topics, places


def collect(sources: dict) -> list[dict]:
    jobs = [(google_news_url(g["q"], g["lang"]), g["lang"], None, True) for g in sources["google_news"]]
    jobs += [(f["url"], f["lang"], f["name"], False) for f in sources["feeds"]]
    out, now = [], now_utc()
    for url, lang, name, is_gn in jobs:
        try:
            entries = list(parse_feed(http_request(url, timeout=25)))
        except Exception as e:  # noqa: BLE001
            print(f"[fetch] FAIL {url[:90]}: {e}")
            continue
        kept = 0
        for e in entries:
            title, source = e["title"], e["source"] or name or ""
            if is_gn:
                # Google News titles look like "Headline - Publisher"
                m = re.match(r"^(.*)\s+[-–—]\s+([^-–—]{2,60})$", title)
                if m:
                    title, source = m.group(1).strip(), source or m.group(2).strip()
                summary = ""  # Google's description only repeats the headline
            else:
                summary = e["summary"][:400]
            if not title or not e["url"]:
                continue
            pub = e["published"] or now
            if now - pub > MAX_FETCH_AGE or pub > now + dt.timedelta(hours=2):
                continue
            ok, topics, places = classify(f"{title} {summary}", sources)
            if not ok:
                continue
            out.append({
                "id": short_hash(normalize(title)[:90]),
                "lang": lang,
                "title": title,
                "titles": {lang: title},
                "summary": summary[:300].rsplit(" ", 1)[0] + "…" if len(summary) > 300 else summary,
                "url": e["url"],
                "source": source[:80],
                "published": iso(pub),
                "fetched": iso(now),
                "image": "" if is_gn else (e.get("image") or ""),
                "topics": topics,
                "places": places,
            })
            kept += 1
        print(f"[fetch] {kept:3d}/{len(entries):3d} kept  {name or 'Google News ' + lang}")
    return out


# --- near-duplicate detection: the same story from several publishers ---------------
STOPWORDS = set("""the a an of in on at to for and or with from by as is are was be after over into new
said will its it this that than un una o cu de la pe din si ar care dupa ce sa mai fost este sunt al ale lui
unei unui pentru prin despre на и в с от за по се да са че при към след как ще до или
black sea seas marea neagra черно море черноморски ukraine ukrainian ucraina ucrainei rusia russia russian
rusiei украйна русия""".split())
GENERIC_STEMS = {"attac", "drone", "drona", "ship", "ships", "shipp", "strik", "vesse", "nave", "atacu",
                 "atac", "кораб", "атака", "атаки", "дрон", "дрони", "дроно", "port", "ports", "portu", "war",
                 "razbo", "война", "says", "warn", "warns", "urges", "calls"}
DUP_WINDOW = dt.timedelta(hours=48)
DUP_MIN_SHARED = 3
DUP_MIN_OVERLAP = 0.6


def title_tokens(item: dict) -> set[str]:
    out = set()
    for t in {item["title"], item["titles"].get("en", "")}:
        for w in re.findall(r"\w+", normalize(t)):
            if len(w) < 3 or w in STOPWORDS or w.isdigit():
                continue
            stem = w[:5]
            if stem not in GENERIC_STEMS:
                out.add(stem)
    return out


def is_duplicate(a: dict, ta: set, b: dict, tb: set) -> bool:
    if not ta or not tb:
        return False
    if abs(parse_iso(a["published"]) - parse_iso(b["published"])) > DUP_WINDOW:
        return False
    shared = len(ta & tb)
    overlap = shared / min(len(ta), len(tb))
    return (shared >= DUP_MIN_SHARED and overlap >= DUP_MIN_OVERLAP) or (shared >= 4 and overlap >= 0.45)


def dedupe(items: list[dict]) -> list[dict]:
    """Keep the earliest report of each story; later ones go into its `also` list."""
    kept: list[tuple[dict, set]] = []
    for item in sorted(items, key=lambda i: i["published"]):
        tok = title_tokens(item)
        for k, ktok in reversed(kept):
            if is_duplicate(k, ktok, item, tok):
                also = k.setdefault("also", [])
                known = {k["url"]} | {a["url"] for a in also}
                if not k.get("image") and item.get("image"):
                    k["image"] = item["image"]
                for extra in [{"source": item["source"], "url": item["url"]}] + item.get("also", []):
                    if extra["url"] not in known and len(also) < 8:
                        also.append(extra)
                        known.add(extra["url"])
                break
        else:
            kept.append((item, tok))
    return [k for k, _ in kept]


def merge(existing: list[dict], fresh: list[dict]) -> tuple[list[dict], int]:
    by_id = {i["id"]: i for i in existing}
    urls = {i["url"] for i in existing} | {a["url"] for i in existing for a in i.get("also", [])}
    for item in fresh:
        if item["id"] in by_id or item["url"] in urls:
            continue
        by_id[item["id"]] = item
        urls.add(item["url"])
    before = {i["id"] for i in existing}
    merged = dedupe(list(by_id.values()))
    return merged, sum(1 for i in merged if i["id"] not in before)


# --- pictures: the publisher's own preview image (og:image), as social networks show it ----
PAGE_UA = "Mozilla/5.0 (compatible; BlackSeaWayBot/1.0; +https://blackseaway.github.io/blackseaway/)"
IMAGES_PER_RUN = 30
META_RE = re.compile(r"<meta\s[^>]*>", re.I)
ATTR_RE = re.compile(r'([a-zA-Z:_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')


def og_image(url: str) -> str:
    page = http_request(url, headers={"User-Agent": PAGE_UA, "Accept": "text/html"},
                        timeout=12)[:600_000].decode("utf-8", "replace")
    found = {}
    for tag in META_RE.findall(page):
        attrs = {k.lower(): (v1 or v2) for k, v1, v2 in ATTR_RE.findall(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if attrs.get("content"):
            found.setdefault(key, html.unescape(attrs["content"]).strip())
    for key in ("og:image:secure_url", "og:image", "og:image:url", "twitter:image", "twitter:image:src"):
        img = found.get(key)
        if img:
            img = urllib.parse.urljoin(url, img)
            if img.startswith("http"):
                return img
    return ""


def enrich_images(items: list[dict], limit: int = IMAGES_PER_RUN) -> None:
    """Only direct publisher links; Google News links get the site's placeholder picture."""
    todo = [i for i in items if not i.get("image") and not i.get("img_tried")
            and "news.google.com" not in i["url"]][:limit]
    got = 0
    for i in todo:
        i["img_tried"] = True
        try:
            img = og_image(i["url"])
            if img:
                i["image"] = img
                got += 1
        except Exception as e:  # noqa: BLE001
            print(f"[images] {i['source']}: {str(e)[:120]}")
    print(f"[images] {got}/{len(todo)} pictures found")


def translate_missing(items: list[dict], langs: list[str], per_run: int) -> int:
    tr = Translator()
    if tr.disabled_reason:
        print(f"[deepl] translation skipped: {tr.disabled_reason}")
        return 0
    todo = [i for i in sorted(items, key=lambda i: i["published"], reverse=True)
            if any(lang not in i["titles"] for lang in langs)][:per_run]
    done = 0
    for target in langs:
        batch = [i for i in todo if target not in i["titles"]]
        for src in {i["lang"] for i in batch}:
            group = [i for i in batch if i["lang"] == src]
            res = tr.translate([i["title"] for i in group], target, source=src)
            if res is None:
                continue
            for i, t in zip(group, res):
                i["titles"][target] = t
                done += 1
    tr.save()
    print(f"[deepl] translated {done} headlines, used this month ≈ {tr.used()} chars")
    return done


def main():
    cfg = site_config()
    sources = load_json(CONFIG / "sources.json")
    store = load_json(NEWS_FILE, {"updated": None, "items": []})
    items = store["items"]
    added = 0
    if "--no-fetch" not in sys.argv:
        items, added = merge(items, collect(sources))
    else:
        items = dedupe(items)
    # re-tag old items when keyword lists change
    for i in items:
        _, i["topics"], i["places"] = classify(f"{i['title']} {i.get('summary', '')}", sources)
    cutoff = now_utc() - dt.timedelta(days=cfg["news"]["max_age_days"])
    items = [i for i in items if parse_iso(i["published"]) >= cutoff]
    items.sort(key=lambda i: i["published"], reverse=True)
    items = items[: cfg["news"]["max_items"]]
    if "--no-images" not in sys.argv:
        enrich_images(items)
    translate_missing(items, cfg["languages"], cfg["news"]["translate_per_run"])
    store = {"updated": iso(now_utc()), "items": items}
    save_json(NEWS_FILE, store)
    print(f"[news] +{added} new, {len(items)} total")


if __name__ == "__main__":
    main()
