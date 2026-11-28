"""Collect Black Sea news from RSS sources, tag them and translate headlines.

Usage:  python scripts/fetch_news.py            (network + optional DeepL)
        python scripts/fetch_news.py --no-fetch (only translate / prune existing items)
"""
from __future__ import annotations

import datetime as dt
import email.utils
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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


def parse_feed_lenient(raw: bytes):
    """Regex fallback for feeds that are not well-formed XML (stray '&' etc.)."""
    text = raw.decode("utf-8", "replace")

    def tag(block, name):
        m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, re.S | re.I)
        if not m:
            return ""
        v = m.group(1).strip()
        v = re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", v, flags=re.S)
        return html.unescape(v).strip()
    for block in re.findall(r"<item[\s>].*?</item>", text, re.S | re.I):
        img = re.search(r'<(?:enclosure|media:content|media:thumbnail)[^>]+url="([^"]+)"', block)
        yield {"title": strip_tags(tag(block, "title")), "url": tag(block, "link"),
               "published": parse_date(tag(block, "pubDate") or tag(block, "dc:date")),
               "summary": strip_tags(tag(block, "description")), "source": "",
               "image": html.unescape(img.group(1)) if img else ""}


def parse_feed(raw: bytes):
    """Yield dicts (title, url, published, summary, source, image) from RSS 2.0 or Atom."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        yield from parse_feed_lenient(raw)
        return
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
        extra_src = extra_img = ""
        for child in it:  # Bing News: <News:Source>, <News:Image>
            low = child.tag.lower()
            if low.endswith("}source") and child.text:
                extra_src = child.text.strip()
            elif low.endswith("}image") and child.text:
                extra_img = child.text.strip()
        link = text_of(it, "link")
        if "bing.com/news/apiclick" in link:
            real = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("url", [""])[0]
            link = real or link
        yield {
            "title": text_of(it, "title"),
            "url": link,
            "published": parse_date(text_of(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date")),
            "summary": strip_tags(text_of(it, "description")),
            "source": ((src.text or "").strip() if src is not None and src.text else "") or extra_src,
            "image": feed_image(it),
            "fallback_image": extra_img,
        }


def google_news_url(q: str, lang: str) -> str:
    hl, gl, ceid = GN[lang]
    query = urllib.parse.quote(f"{q} when:7d")
    return f"https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"


def priority_ok(text: str, topics: list, sources: dict) -> bool:
    """Items from priority sites skip the Black Sea place check, but the headline must still be
    about ships/the sea and match one of our topics (these sites also cover other regions)."""
    title_norm = normalize(text.split(" \x1f ")[0])
    norm = normalize(text)
    in_region = has_any(norm, sources["anchors"]) or any(has_any(norm, p["match"]) for p in sources["places"]) \
        or has_any(title_norm, sources.get("region_words", []))
    return bool(topics) and in_region and has_any(title_norm, sources.get("maritime", [])) \
        and not has_any(norm, sources["exclude"])


RO_WORDS = {"si", "din", "pentru", "care", "cu", "este", "sunt", "dupa", "prin", "despre", "ale", "unei",
            "unui", "catre", "noi", "nou", "pe", "lui", "fost", "va", "sa", "mai", "intr", "doua", "se", "cere"}


def detect_lang(text: str, default: str) -> str:
    """Headline language by script and common words (search markets also return other languages)."""
    if re.search(r"[а-яА-Я]", text):
        return "bg"
    if re.search(r"[ăâîșşțţĂÂÎȘŞȚŢ]", text):
        return "ro"
    words = set(re.findall(r"[a-z]+", normalize(text)))
    if len(words & RO_WORDS) >= 2:
        return "ro"
    if default == "ro" and not words & {"the", "and", "of", "to", "in", "on", "for", "with"}:
        return "ro"
    return "en" if default != "bg" else "en"


def classify(text: str, sources: dict):
    norm = normalize(text)
    topics = [t for t, kws in sources["topics"].items() if has_any(norm, kws)]
    places = [p["id"] for p in sources["places"] if has_any(norm, p["match"])]
    # the headline itself must be about the sea (ships, ports, the Black Sea...), not only the summary
    title_norm = normalize(text.split(" \x1f ")[0])
    maritime = has_any(title_norm, sources.get("maritime", [])) if sources.get("maritime") else True
    sea_topics = [t for t in topics if t != "policy"] or (
        topics and has_any(norm, ["security", "securitate", "сигурност", "maritime", "maritim", "морск",
                                  "navigation", "navigatie", "корабоплаване"]))
    relevant = (has_any(norm, sources["anchors"]) or places) and maritime and sea_topics \
        and not has_any(norm, sources["exclude"])
    return bool(relevant), topics, places


GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"


def gdelt_entries(q: str) -> list[dict]:
    """GDELT DOC 2.0 API, artlist mode: direct article URLs with their preview image."""
    params = urllib.parse.urlencode({"query": q, "mode": "artlist", "format": "json",
                                     "maxrecords": 75, "timespan": "3d", "sort": "datedesc"})
    try:
        raw = http_request(f"{GDELT}?{params}", timeout=30)
    except urllib.error.HTTPError as e:
        if e.code != 429:
            raise
        time.sleep(30)
        raw = http_request(f"{GDELT}?{params}", timeout=30)
    raw = raw.decode("utf-8", "replace").strip()
    if not raw.startswith("{"):
        raise RuntimeError(raw[:120])
    out = []
    for a in json.loads(raw).get("articles", []):
        seen = a.get("seendate", "")
        try:
            pub = dt.datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pub = None
        out.append({"title": html.unescape(a.get("title", "")).strip(), "url": a.get("url", ""),
                    "published": pub, "summary": "", "source": a.get("domain", ""),
                    "image": a.get("socialimage", "") or ""})
    return out


def bing_url(q: str, mkt: str) -> str:
    # qft=sortbydate="1": newest first (otherwise Bing returns "best match", often days old)
    return "https://www.bing.com/news/search?" + urllib.parse.urlencode(
        {"q": q, "format": "rss", "mkt": mkt, "qft": 'sortbydate="1"'})


def is_blocked(item: dict, sources: dict) -> bool:
    """Russian and pro-Russian outlets (by domain or by publisher name)."""
    host = urllib.parse.urlparse(item.get("url", "")).hostname or ""
    host = host.lower().removeprefix("www.")
    if any(host == d or host.endswith("." + d) for d in sources.get("blocked_domains", [])):
        return True
    if any(host.endswith(sfx) for sfx in sources.get("blocked_domain_suffixes", [])):
        return True
    src = normalize(item.get("source", ""))
    return any(src == normalize(b) or src.startswith(normalize(b) + " ") for b in sources.get("blocked_sources", []))


def collect(sources: dict) -> list[dict]:
    jobs = [(google_news_url(g["q"], g["lang"]), g["lang"], None, True) for g in sources["google_news"]]
    # url of a site: query -> the site's domain
    trusted_urls = {google_news_url(g["q"], g["lang"]): g["q"].split()[0][5:]
                    for g in sources["google_news"] if g["q"].startswith("site:")}
    trusted_urls |= {bing_url(b["q"], b["mkt"]): b["q"].split()[0][5:]
                     for b in sources.get("bing", []) if b["q"].startswith("site:")}
    jobs += [(f["url"], f["lang"], f["name"], False) for f in sources["feeds"]]
    jobs += [(bing_url(b["q"], b["mkt"]), b["lang"], None, False) for b in sources.get("bing", [])]
    jobs += [("gdelt:" + g["q"], g["lang"], None, False) for g in sources.get("gdelt", [])]
    out, now = [], now_utc()
    for url, lang, name, is_gn in jobs:
        try:
            if "bing.com" in url:
                time.sleep(2)
            if url.startswith("gdelt:"):
                time.sleep(6)  # GDELT asks for at most one request every few seconds
                entries = gdelt_entries(url[6:])
            else:
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
            if is_blocked({"url": e["url"], "source": source}, sources):
                continue
            pub = e["published"] or now
            if now - pub > MAX_FETCH_AGE or pub > now + dt.timedelta(hours=2):
                continue
            if re.search(r"[ыэё]", (title + " " + summary).lower()):
                continue  # Russian-language text (Bulgarian has no ы, э, ё)
            ok, topics, places = classify(f"{title} \x1f {summary}", sources)
            # priority sites, searched with "Black Sea" in the query; Bing may ignore site:, so check the link
            site = trusted_urls.get(url)
            host = (urllib.parse.urlparse(e["url"]).hostname or "").lower()
            trusted = bool(site) and (is_gn or host == site or host.endswith("." + site)) \
                and priority_ok(f"{title} \x1f {summary}", topics, sources)
            if not ok and not trusted:
                continue
            tlang = detect_lang(f"{title} {summary}", lang)
            out.append({
                "id": short_hash(normalize(title)[:90]),
                "lang": tlang,
                "title": title,
                "titles": {tlang: title},
                "summary": summary[:300].rsplit(" ", 1)[0] + "…" if len(summary) > 300 else summary,
                "url": e["url"],
                "source": source[:80],
                "published": iso(pub),
                "fetched": iso(now),
                "image": "" if is_gn else (e.get("image") or ""),
                "fallback_image": e.get("fallback_image", ""),
                "topics": topics,
                "places": places,
                **({"trusted": True} if trusted else {}),
            })
            kept += 1
        label = name or ("GDELT " + lang if url.startswith("gdelt:") else
                         "Bing News " + lang if "bing.com" in url else "Google News " + lang)
        print(f"[fetch] {kept:3d}/{len(entries):3d} kept  {label}")
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
                if "news.google.com" in k["url"] and "news.google.com" not in item["url"]:
                    # prefer the publisher's own link (it has a picture and a real URL)
                    also.append({"source": k["source"], "url": k["url"]})
                    for f in ("url", "source", "image", "img_tried", "summary", "title", "titles", "lang",
                              "fallback_image", "img_checked"):
                        k[f] = item.get(f, k.get(f))
                elif not k.get("image") and item.get("image"):
                    k["image"] = item["image"]
                known = {k["url"]} | {a["url"] for a in also}
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
PAGE_UA = "Mozilla/5.0 (compatible; BlackSeaWayBot/1.0; +https://blackseaway.github.io/)"
IMAGES_PER_RUN = 60
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


def image_usable(url: str) -> str:
    """Return a URL the browser can actually show on our https site, or '' if it is not usable.

    Checks: https (http is upgraded), the server answers with an image, and it does not forbid
    embedding on other sites (Cross-Origin-Resource-Policy: same-origin/same-site)."""
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("http://"):
        url = "https://" + url[7:]
    req = urllib.request.Request(url, headers={"User-Agent": PAGE_UA, "Range": "bytes=0-4095",
                                               "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            corp = (r.headers.get("Cross-Origin-Resource-Policy") or "").lower()
            head = r.read(16)
    except Exception:  # noqa: BLE001
        return ""
    is_img = ctype.startswith("image/") or head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n" \
        or head[:4] == b"RIFF" or head[:4] == b"GIF8"
    if not is_img or corp in ("same-origin", "same-site"):
        return ""
    return url


def verify_images(items: list[dict], limit: int = 200) -> None:
    todo = [i for i in items if not i.get("img_checked")][:limit]
    ok = 0
    for i in todo:
        i["img_checked"] = True
        good = image_usable(i.get("image", ""))
        if not good and i.get("fallback_image") and i.get("fallback_image") != i.get("image"):
            good = image_usable(i["fallback_image"])
        i["image"] = good
        ok += bool(good)
    print(f"[images] verified {len(todo)}: {ok} usable on the site")


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
        if not i.get("image") and i.get("fallback_image"):
            i["image"] = i["fallback_image"]
    print(f"[images] {got}/{len(todo)} pictures found")


def translate_missing(items: list[dict], langs: list[str], per_run: int) -> int:
    tr = Translator()
    if tr.disabled_reason:
        print(f"[deepl] translation skipped: {tr.disabled_reason}")
        return 0
    # stories with pictures go to the featured block and sections, so translate them first
    todo = [i for i in sorted(items, key=lambda i: (bool(i.get("image")), i["published"]), reverse=True)
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
    for i in items:  # fix items whose language was taken from the search market, not the text
        real = detect_lang(f"{i['title']} {i.get('summary', '')}", i["lang"])
        if real != i["lang"]:
            i["lang"], i["titles"] = real, {real: i["title"]}
    kept = []
    for i in items:
        ok, i["topics"], i["places"] = classify(f"{i['title']} \x1f {i.get('summary', '')}", sources)
        i["also"] = [a for a in i.get("also", []) if not is_blocked(a, sources)]
        trusted_ok = i.get("trusted") and priority_ok(f"{i['title']} \x1f {i.get('summary', '')}", i["topics"], sources)
        if (ok or trusted_ok) and not is_blocked(i, sources):
            kept.append(i)
    items = kept
    cutoff = now_utc() - dt.timedelta(days=cfg["news"]["max_age_days"])
    items = [i for i in items if parse_iso(i["published"]) >= cutoff]
    items.sort(key=lambda i: i["published"], reverse=True)
    items = items[: cfg["news"]["max_items"]]
    if "--no-images" not in sys.argv:
        enrich_images(items)
        verify_images(items)
    translate_missing(items, cfg["languages"], cfg["news"]["translate_per_run"])
    store = {"updated": iso(now_utc()), "items": items}
    save_json(NEWS_FILE, store)
    last24 = sum(1 for i in items if now_utc() - parse_iso(i["published"]) <= dt.timedelta(hours=24))
    print(f"[news] +{added} new, {len(items)} total, {last24} from the last 24 h, newest {items[0]['published'] if items else '-'}")


if __name__ == "__main__":
    main()
