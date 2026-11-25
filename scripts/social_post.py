"""Autoposting to Facebook Page, Instagram and Threads (posts in English).

python scripts/social_post.py prepare   choose the next item, write data/social_queue.json
                                         (the build step renders its image card into _site/cards/)
python scripts/social_post.py publish   post the queued item once the card is live on the site
python scripts/social_post.py preview   print what would be posted, do nothing

Secrets (environment): FB_PAGE_ID, FB_PAGE_TOKEN, IG_USER_ID, THREADS_USER_ID, THREADS_TOKEN.
Any platform without its secrets is skipped.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
import time
import urllib.error
import urllib.request

from common import (CONFIG, CONTENT, DATA, USER_AGENT, env, http_json, iso, load_json, now_utc,
                    parse_iso, save_json, site_config)

QUEUE = DATA / "social_queue.json"
STATE = DATA / "social_state.json"
GRAPH = f"https://graph.facebook.com/{os.environ.get('GRAPH_VERSION', 'v23.0')}"
THREADS = "https://graph.threads.net/v1.0"
PRIORITY = {"attacks": 5, "mines": 4, "insurance": 4, "prices": 3, "shipping": 2, "grain": 2,
            "energy": 2, "ports": 1}


def platforms() -> dict:
    return {
        "facebook": bool(env("FB_PAGE_ID") and env("FB_PAGE_TOKEN")),
        "instagram": bool(env("IG_USER_ID") and env("FB_PAGE_TOKEN")),
        "threads": bool(env("THREADS_USER_ID") and env("THREADS_TOKEN")),
    }


def load_state() -> dict:
    return load_json(STATE, {"posted": []}) or {"posted": []}


def fmt_day(d: dt.datetime) -> str:
    return d.strftime("%-d %b %Y")


def editorial_candidates(cfg, posted_ids) -> list[dict]:
    import yaml
    out = []
    for f in sorted((CONTENT / "posts").glob("*.md")):
        if re.search(r"\.(en|ro|bg)$", f.stem):
            continue
        text = f.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        meta = yaml.safe_load(text.split("---", 2)[1]) or {}
        slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", f.stem)
        pid = f"post:{slug}"
        if meta.get("draft") or pid in posted_ids or meta.get("social") is False:
            continue
        date = meta.get("date")
        if isinstance(date, str):
            date = dt.date.fromisoformat(date[:10])
        if date and (now_utc().date() - date).days > 14:
            continue
        link = f"{cfg['base_url']}/en/articles/{slug}.html"
        title = str(meta.get("title", slug))
        summary = str(meta.get("summary", ""))
        out.append({
            "kind": "article", "id": pid, "card_id": re.sub(r"[^a-z0-9-]", "-", slug.lower())[:60],
            "headline": title, "label": "Analysis", "alert": False,
            "footer": f"Black Sea Way · {fmt_day(now_utc())}",
            "link": link,
            "caption": f"{title}\n\n{summary}\n\nRead the full analysis: {link}",
            "threads_text": f"{title}\n\n{summary}",
        })
    return out


def news_candidates(cfg, posted_ids) -> list[dict]:
    items = (load_json(DATA / "news.json", {}) or {}).get("items", [])
    i18n = load_json(CONFIG / "i18n.json")["en"]["topics"]
    now = now_utc()
    scored = []
    for i in items:
        nid = f"news:{i['id']}"
        title = i["titles"].get("en") or (i["title"] if i["lang"] == "en" else None)
        if nid in posted_ids or not title:
            continue
        age_h = (now - parse_iso(i["published"])).total_seconds() / 3600
        if age_h > 18:
            continue
        score = max([PRIORITY.get(t, 0) for t in i["topics"]] or [0]) + 0.5 * len(i["places"]) - age_h / 12
        scored.append((score, i, title))
    scored.sort(key=lambda s: s[0], reverse=True)
    out = []
    for score, i, title in scored[:5]:
        top = max(i["topics"], key=lambda t: PRIORITY.get(t, 0)) if i["topics"] else "shipping"
        translated = i["lang"] != "en"
        src = i["source"] + (f" ({i['lang'].upper()}, headline translated)" if translated else "")
        site = f"{cfg['base_url']}/en/news.html"
        tags = cfg["social_posting"]["hashtags"]
        out.append({
            "kind": "news", "id": f"news:{i['id']}", "card_id": i["id"],
            "headline": title, "label": i18n.get(top, top), "alert": top == "attacks",
            "footer": f"Source: {i['source']} · {fmt_day(parse_iso(i['published']))}",
            "link": i["url"],
            "caption": f"{title}\n\nSource: {src}\n{i['url']}\n\nMore Black Sea shipping news in EN, RO and BG: {site}\n\n{tags}",
            "threads_text": f"{title}\n\nSource: {src}",
        })
    return out


def prepare():
    cfg = site_config()
    gh_out = os.environ.get("GITHUB_OUTPUT")

    def done(has_post: bool, msg: str):
        print(f"[social] {msg}")
        if gh_out:
            with open(gh_out, "a") as f:
                f.write(f"has_post={'true' if has_post else 'false'}\n")

    if not any(platforms().values()) and "--force" not in sys.argv:
        save_json(QUEUE, {})
        return done(False, "no social media secrets configured, skipping")
    state = load_state()
    now = now_utc()
    recent = [p for p in state["posted"] if now - parse_iso(p["at"]) < dt.timedelta(hours=24)]
    rules = cfg["social_posting"]
    if len(recent) >= rules["max_posts_per_day"]:
        save_json(QUEUE, {})
        return done(False, f"daily limit reached ({len(recent)})")
    if recent and now - max(parse_iso(p["at"]) for p in recent) < dt.timedelta(hours=rules["min_hours_between_posts"] - 0.25):
        save_json(QUEUE, {})
        return done(False, "too soon after the previous post")
    posted_ids = {p["id"] for p in state["posted"]}
    cands = editorial_candidates(cfg, posted_ids) or news_candidates(cfg, posted_ids)
    if not cands:
        save_json(QUEUE, {})
        return done(False, "nothing new worth posting")
    c = cands[0]
    c["image_url"] = f"{cfg['base_url']}/cards/{c['card_id']}.jpg"
    save_json(QUEUE, {"created": iso(now), "item": c,
                      "cards": [{"id": c["card_id"], "headline": c["headline"], "label": c["label"],
                                 "footer": c["footer"], "alert": c["alert"]}]})
    done(True, f"queued {c['id']}: {c['headline'][:80]}")


# ------------------------------------------------------------------ publishing
def wait_for_url(url: str, tries: int = 12, delay: int = 15) -> bool:
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status == 200:
                    return True
        except urllib.error.URLError:
            pass
        time.sleep(delay)
    return False


def post_facebook(item) -> str:
    message = item["caption"]
    res = http_json(f"{GRAPH}/{env('FB_PAGE_ID')}/feed",
                    data={"message": message, "link": item["link"], "access_token": env("FB_PAGE_TOKEN")})
    return res.get("id", "")


def post_instagram(item) -> str:
    ig, token = env("IG_USER_ID"), env("FB_PAGE_TOKEN")
    caption = item["caption"][:2100]
    res = http_json(f"{GRAPH}/{ig}/media", data={"image_url": item["image_url"], "caption": caption,
                                                  "access_token": token})
    cid = res["id"]
    for _ in range(20):
        st = http_json(f"{GRAPH}/{cid}?fields=status_code&access_token={token}")
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram container error: {st}")
        time.sleep(5)
    res = http_json(f"{GRAPH}/{ig}/media_publish", data={"creation_id": cid, "access_token": token})
    return res.get("id", "")


def post_threads(item) -> str:
    uid, token = env("THREADS_USER_ID"), env("THREADS_TOKEN")
    text = item["threads_text"]
    if len(text) > 480:
        text = text[:477].rsplit(" ", 1)[0] + "…"
    res = http_json(f"{THREADS}/{uid}/threads", data={"media_type": "TEXT", "text": text,
                                                      "link_attachment": item["link"], "access_token": token})
    time.sleep(20)  # Meta recommends waiting before publishing a container
    res = http_json(f"{THREADS}/{uid}/threads_publish", data={"creation_id": res["id"], "access_token": token})
    return res.get("id", "")


def publish():
    queue = load_json(QUEUE, {}) or {}
    item = queue.get("item")
    if not item:
        print("[social] queue is empty")
        return
    enabled = platforms()
    if enabled["instagram"] and not wait_for_url(item["image_url"]):
        print(f"[social] image card not reachable: {item['image_url']} – Instagram will be skipped")
        enabled["instagram"] = False
    results, errors = {}, {}
    for name, fn in (("facebook", post_facebook), ("instagram", post_instagram), ("threads", post_threads)):
        if not enabled[name]:
            continue
        try:
            results[name] = fn(item)
            print(f"[social] {name}: posted {results[name]}")
        except Exception as e:  # noqa: BLE001
            errors[name] = str(e)[:300]
            print(f"[social] {name}: FAILED {e}")
    state = load_state()
    state["posted"].append({"id": item["id"], "at": iso(now_utc()), "results": results, "errors": errors})
    state["posted"] = state["posted"][-500:]
    save_json(STATE, state)
    save_json(QUEUE, {})
    if errors and not results:
        sys.exit(1)


def preview():
    cfg = site_config()
    posted = {p["id"] for p in load_state()["posted"]}
    for c in (editorial_candidates(cfg, posted) + news_candidates(cfg, posted))[:5]:
        print("—" * 60)
        print(c["caption"])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "preview"
    {"prepare": prepare, "publish": publish, "preview": preview}[cmd]()
