"""Build the static site into _site/ (EN / RO / BG).

python scripts/build.py           build with DeepL translation of articles (if key present)
python scripts/build.py --offline build without calling DeepL
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import shutil
import sys
from collections import Counter
from email.utils import format_datetime
from pathlib import Path

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from cards import news_card, og_card
from common import CONFIG, CONTENT, DATA, ROOT, iso, load_json, now_utc, parse_iso, save_json, site_config

OUT = ROOT / "_site"
POST_TR_FILE = DATA / "post_translations.json"
OFFLINE = "--offline" in sys.argv


# ---------------------------------------------------------------- content
def read_md(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    meta, body = {}, text
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        meta = yaml.safe_load(fm) or {}
    return meta, body.strip()


def md_to_html(body: str) -> str:
    return markdown.markdown(body, extensions=["extra", "sane_lists"], output_format="html")


def load_posts(cfg: dict) -> list[dict]:
    langs = cfg["languages"]
    files = sorted((CONTENT / "posts").glob("*.md"))
    manual = {}  # (slug, lang) -> file for hand-made translations like 2026-11-25-x.ro.md
    originals = []
    for f in files:
        m = re.match(r"^(.*)\.(en|ro|bg)$", f.stem)
        if m:
            manual[(m.group(1), m.group(2))] = f
        else:
            originals.append(f)
    tr_cache = load_json(POST_TR_FILE, {}) or {}
    translator = None
    posts = []
    for f in originals:
        meta, body = read_md(f)
        if meta.get("draft"):
            continue
        slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", f.stem)
        date = meta.get("date") or dt.date.today()
        if isinstance(date, str):
            date = dt.date.fromisoformat(date[:10])
        src_lang = meta.get("lang", "en")
        base = {
            "title": str(meta.get("title", slug)),
            "summary": str(meta.get("summary", "")),
            "html": md_to_html(body),
        }
        words = len(re.findall(r"\w+", body))
        post = {"slug": slug, "date": date, "lang": src_lang, "author": meta.get("author", cfg["editor"]),
                "image": meta.get("image"), "minutes": max(1, round(words / 220)), "versions": {}}
        digest = hashlib.sha1(json.dumps(base, sort_keys=True).encode()).hexdigest()[:16]
        for lang in langs:
            if lang == src_lang:
                post["versions"][lang] = dict(base, auto=False, untranslated=False)
                continue
            if (f.stem, lang) in manual:
                mm, mb = read_md(manual[(f.stem, lang)])
                post["versions"][lang] = {"title": str(mm.get("title", base["title"])),
                                          "summary": str(mm.get("summary", base["summary"])),
                                          "html": md_to_html(mb), "auto": False, "untranslated": False}
                continue
            key = f"{slug}:{lang}"
            cached = tr_cache.get(key)
            if not cached or cached.get("hash") != digest:
                cached = None
                if not OFFLINE:
                    if translator is None:
                        from translate import Translator
                        translator = Translator()
                    res = translator.translate([base["title"], base["summary"] or " "], lang, src_lang)
                    res_html = translator.translate([base["html"]], lang, src_lang, html=True) if res else None
                    if res and res_html:
                        cached = {"hash": digest, "title": res[0], "summary": res[1].strip(), "html": res_html[0]}
                        tr_cache[key] = cached
            if cached:
                post["versions"][lang] = {"title": cached["title"], "summary": cached["summary"],
                                          "html": cached["html"], "auto": True, "untranslated": False}
            else:
                post["versions"][lang] = dict(base, auto=False, untranslated=True)
        posts.append(post)
    if translator is not None:
        translator.save()
        save_json(POST_TR_FILE, tr_cache)
    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts


def load_page(name: str, lang: str) -> dict:
    p = CONTENT / "pages" / f"{name}.{lang}.md"
    if not p.exists():
        p = CONTENT / "pages" / f"{name}.en.md"
    meta, body = read_md(p)
    return {"title": meta.get("title", name), "html": md_to_html(body)}


# ---------------------------------------------------------------- stats
def week_start(d: dt.datetime) -> dt.date:
    day = d.date()
    return day - dt.timedelta(days=day.weekday())


def compute_stats(items: list[dict], sources: dict) -> dict:
    now = now_utc()
    this_week = week_start(now)
    weeks = [this_week - dt.timedelta(weeks=k) for k in range(11, -1, -1)]
    wcount = Counter()
    last30 = [i for i in items if now - parse_iso(i["published"]) <= dt.timedelta(days=30)]
    last7 = [i for i in items if now - parse_iso(i["published"]) <= dt.timedelta(days=7)]
    for i in items:
        wcount[week_start(parse_iso(i["published"]))] += 1
    topic_counts = Counter(t for i in last30 for t in i["topics"])
    place_counts = Counter(p for i in last30 for p in i["places"])
    place_week = Counter(p for i in last7 for p in i["places"])
    places = {p["id"]: p for p in sources["places"]}
    return {
        "weeks": [{"week": w, "count": wcount.get(w, 0), "partial": w == this_week} for w in weeks],
        "topics": sorted(({"id": t, "count": c, "share": c / max(1, len(last30))}
                          for t, c in topic_counts.items()), key=lambda x: -x["count"]),
        "places": [dict(places[p], count=c) for p, c in place_counts.most_common()],
        "hotspots": [dict(places[p], count=c) for p, c in place_week.most_common(6)],
        "total30": len(last30),
        "total7": len(last7),
    }


# ---------------------------------------------------------------- render
def make_env(cfg, i18n):
    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")),
                      autoescape=select_autoescape(["html", "xml"]), trim_blocks=True, lstrip_blocks=True)
    base = cfg["base_path"].rstrip("/")

    def url(path=""):
        return f"{base}/{path.lstrip('/')}"

    def fmt_date(value, lang, time=False):
        if isinstance(value, str):
            value = parse_iso(value)
        months = i18n[lang]["months"]
        s = f"{value.day} {months[value.month - 1]} {value.year}"
        if time and isinstance(value, dt.datetime):
            s += f", {value.strftime('%H:%M')} UTC"
        return s

    env.globals.update(url=url, cfg=cfg, i18n=i18n, langs=cfg["languages"], build_time=now_utc())
    env.filters["date"] = fmt_date
    env.filters["pct"] = lambda v: f"{round(v * 100)}%"
    env.filters["tojson_safe"] = lambda v: json.dumps(v, ensure_ascii=False)
    return env


def news_view(item: dict, lang: str) -> dict:
    title = item["titles"].get(lang)
    return {
        "id": item["id"],
        "title": title or item["title"],
        "translated": bool(title) and lang != item["lang"],
        "shown_lang": lang if title else item["lang"],
        "orig_lang": item["lang"],
        "url": item["url"],
        "source": item["source"],
        "published": item["published"],
        "topics": item["topics"],
        "places": item["places"],
        "also": item.get("also", []),
        "summary": item.get("summary", "") if not title or lang == item["lang"] else "",
    }


def indicator_view(ind: dict, lang: str) -> dict:
    pick = lambda d: (d or {}).get(lang) or (d or {}).get("en", "")  # noqa: E731
    return {"label": pick(ind.get("label")), "value": ind.get("value", ""), "unit": pick(ind.get("unit")),
            "note": pick(ind.get("note")), "date": ind.get("date", ""), "source": ind.get("source", {})}


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build():
    cfg = site_config()
    i18n = load_json(CONFIG / "i18n.json")
    sources = load_json(CONFIG / "sources.json")
    news = load_json(DATA / "news.json", {"updated": None, "items": []})
    indicators = [i for i in (load_json(DATA / "indicators.json", {}) or {}).get("indicators", [])
                  if i.get("published")]
    queue = load_json(DATA / "social_queue.json", {}) or {}
    items = news["items"]
    posts = load_posts(cfg)
    stats = compute_stats(items, sources)

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    shutil.copytree(ROOT / "assets", OUT / "assets", ignore=shutil.ignore_patterns("fonts"))
    write(OUT / ".nojekyll", "")
    og_card(OUT / "assets" / "og-default.png", cfg["name"], i18n["en"]["tagline"])

    env = make_env(cfg, i18n)
    pages_for_sitemap = []

    def render(tpl, out_rel, lang, **ctx):
        t = i18n[lang]
        html_out = env.get_template(tpl).render(lang=lang, t=t, path=out_rel, **ctx)
        write(OUT / lang / out_rel, html_out)
        pages_for_sitemap.append(f"{lang}/{out_rel}".replace("index.html", ""))

    for lang in cfg["languages"]:
        nv = [news_view(i, lang) for i in items]
        common_ctx = dict(news_updated=news.get("updated"), place_names={p["id"]: p["names"][lang]
                                                                         for p in sources["places"]})
        render("home.html", "index.html", lang, news=nv[:24], posts=[p for p in posts][:3],
               indicators=[indicator_view(i, lang) for i in indicators][:4], stats=stats, **common_ctx)
        render("news.html", "news.html", lang, news=nv, topics=list(sources["topics"].keys()), **common_ctx)
        render("stats.html", "stats.html", lang, stats=stats,
               indicators=[indicator_view(i, lang) for i in indicators], **common_ctx)
        render("articles.html", "articles/index.html", lang, posts=posts, **common_ctx)
        for p in posts:
            render("article.html", f"articles/{p['slug']}.html", lang, post=p, v=p["versions"][lang],
                   og_image=(p.get("image") or None), **common_ctx)
        render("page.html", "about.html", lang, page=load_page("about", lang), **common_ctx)

    # root: language redirect + 404
    write(OUT / "index.html", env.get_template("root_index.html").render(t=i18n["en"], lang="en", path=""))
    write(OUT / "404.html", env.get_template("404.html").render(t=i18n["en"], lang="en", path=""))
    write(OUT / "robots.txt", f"User-agent: *\nAllow: /\nSitemap: {cfg['base_url']}/sitemap.xml\n")
    sm = "".join(f"<url><loc>{html.escape(cfg['base_url'] + '/' + p)}</loc></url>" for p in pages_for_sitemap)
    write(OUT / "sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n'
          f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>\n')
    write(OUT / "feed.xml", rss_feed(cfg, posts, items))

    # social cards for the queued item(s)
    for c in queue.get("cards", []):
        news_card(OUT / "cards" / f"{c['id']}.jpg", c["headline"], c["label"], c["footer"], c.get("alert", False))
    write(OUT / "data" / "stats.json", json.dumps({"updated": iso(now_utc()), "total30": stats["total30"],
                                                   "places": [{"id": p["id"], "count": p["count"]}
                                                              for p in stats["places"]]}, ensure_ascii=False))
    print(f"[build] {len(pages_for_sitemap)} pages, {len(items)} news, {len(posts)} articles → {OUT}")


def rss_feed(cfg, posts, items) -> str:
    entries = []
    for p in posts[:20]:
        v = p["versions"]["en"]
        d = dt.datetime.combine(p["date"], dt.time(9, 0), tzinfo=dt.timezone.utc)
        entries.append((d, v["title"], f"{cfg['base_url']}/en/articles/{p['slug']}.html", v["summary"]))
    for i in items[:40]:
        entries.append((parse_iso(i["published"]), i["titles"].get("en", i["title"]), i["url"],
                        f"{i['source']}"))
    entries.sort(key=lambda e: e[0], reverse=True)
    body = "".join(
        f"<item><title>{html.escape(t)}</title><link>{html.escape(u)}</link><guid>{html.escape(u)}</guid>"
        f"<pubDate>{format_datetime(d)}</pubDate><description>{html.escape(s)}</description></item>"
        for d, t, u, s in entries)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
            f"<title>{cfg['name']}</title><link>{cfg['base_url']}/en/</link>"
            "<description>Black Sea shipping, security and trade monitor</description>"
            f"{body}</channel></rss>\n")


if __name__ == "__main__":
    build()
