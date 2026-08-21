#!/usr/bin/env python
"""
Sync topics.json from the source Medium article.

Scrapes https://medium.com/@vanika.awasthi/topics-and-links-daf4858c8732, walks the
article's section headers (bold paragraphs) and their resource links, and MERGES any
resource URLs that are not already present in topics.json -- preserving the exact
schema (categories -> topics -> concepts + resources). Existing entries are never
rewritten, so hand-authored titles/concepts/concepts ordering are kept; only NEW
URLs get appended as new topics/resources. Idempotent: re-running is a no-op once
the article is fully represented.

This mirrors how keep-ai treats the Medium article as the canonical source for the
topic index. Uses only the Python standard library (no external deps).

Usage:
    python sync_topics.py            # scrape live + merge
    python sync_topics.py --dry-run  # show what would change, write nothing
"""
import os
import re
import sys
import json
import urllib.request

ARTICLE_URL = "https://medium.com/@vanika.awasthi/topics-and-links-daf4858c8732"
TOPICS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "topics.json")
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "topics_source.html")

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

# Keepword stopwords reused for concept derivation (mirrors keep-ai builder style).
STOP = set(("a an the and or but if then else for with without into from to of in on at by as is are was were be been being "
            "this that these those it its it's we you your our their there they them he she his her "
            "i me my about not no yes use used using can could would should will do does did have has had more most all any "
            "some also just very really something someone work working project status list need needs make making new time day week month year").split())
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}


def _live_fetch(url=ARTICLE_URL):
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "ignore")


def fetch_article(url=ARTICLE_URL):
    """Fetch the Medium article. Retries live, then falls back to a local cache
    (topics_source.html) so the script stays re-runnable when Medium's anti-bot
    returns 403. Writes the cache on a successful live fetch if absent."""
    last_err = None
    for _ in range(3):
        try:
            html = _live_fetch(url)
            if "Topics and links" in html or "keep.google.com" in html:
                if not os.path.exists(CACHE_FILE):
                    try:
                        with open(CACHE_FILE, "w", encoding="utf-8") as f:
                            f.write(html)
                    except Exception:
                        pass
                return html
        except Exception as e:
            last_err = e
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    raise SystemExit(f"Could not fetch article and no local cache at {CACHE_FILE}: {last_err}")


def html_text(s):
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return __import__("html").unescape(s).strip()


NAV = {"sign in", "sign up", "write", "search", "get app", "open in app"}
NAV_URL = ("/m/signin", "/new-story", "play.google.com", "medium.com/store",
           "/about", "/jobs-at-medium", "help.medium.com", "status.medium.com",
           "policy.medium.com", "blog.medium.com", "mailto:")


def parse_article(html):
    """Return list of (section, groups) where each group is a list of
    (title, url, type) that appeared together in one paragraph. Links from page
    chrome (nav) are filtered out."""
    paras = re.findall(r'<p[^>]*>(.*?)</p>', html, re.S)
    sections = []  # [(section, [ [link,...], ... ])]
    cur = None  # (section, groups)

    def new_section(name):
        sec = (name, [])
        sections.append(sec)
        return sec

    for p in paras:
        raw_links = re.findall(r'<a[^>]+href="([^">]+)"[^>]*>(.*?)</a>', p, flags=re.S | re.I)
        links = []
        for u, t in raw_links:
            u = u.strip()
            t = html_text(t)
            if not u or len(t) <= 1:
                continue
            if t.lower() in NAV:
                continue
            if any(n in u.lower() for n in NAV_URL):
                continue
            links.append((t, u, classify(u)))
        strong = re.findall(r'<strong[^>]*>(.*?)</strong>', p, flags=re.S | re.I)
        if strong:
            # A <strong> is a section heading. It may trail links on the same
            # line (e.g. "<strong>NETWORK AND SECURITY</strong><a...>HTTP1.1 rest grpc")
            # -- the heading starts a new section and its paragraph's links become the
            # first group. Heading-only paragraphs (no links) still work as before.
            name = " ".join(html_text(s) for s in strong).strip().upper()
            if name:
                cur = new_section(name)
            if links:
                cur[1].append(links)
        elif links:
            if cur is None:
                cur = new_section("INTRODUCTION")
            cur[1].append(links)  # one paragraph group
    return sections


def classify(url):
    u = url.lower()
    if "keep.google.com" in u and "/media/" in u:
        return "google_keep_media"
    if "keep.google.com" in u:
        return "google_keep"
    if "docs.google.com" in u:
        return "google_doc"
    if "medium.com" in u:
        return "medium_article"
    if "squarespace-cdn.com" in u or "lh3.googleusercontent.com" in u:
        return "image"
    if "confluent" in u:
        return "external_webpage"
    ext = os.path.splitext(u)[1]
    if ext in IMAGE_EXTS or "image" in u:
        return "image"
    return "external_webpage"


def slug(name, taken):
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    s = s[:60] or "section"
    base = s
    i = 2
    while s in taken:
        s = f"{base}_{i}"
        i += 1
    taken.add(s)
    return s


def concepts_from(title):
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    out = []
    for w in words:
        if len(w) < 3 or w in STOP:
            continue
        if w not in out:
            out.append(w)
    return out[:8]


def next_id(existing, prefix="r"):
    nums = [int(m.group(1)) for m in (re.match(rf"{prefix}(\d+)$", x) for x in existing) if m]
    n = (max(nums) + 1) if nums else 1
    while f"{prefix}{n}" in existing:
        n += 1
    return f"{prefix}{n}"


def main():
    dry = "--dry-run" in sys.argv
    with open(TOPICS_FILE, "r", encoding="utf-8") as f:
        topics = json.load(f)

    existing_urls = set()
    existing_ids = set()
    for cat in topics.get("categories", []):
        existing_ids.add(cat.get("id"))
        for t in cat.get("topics", []):
            existing_ids.add(t.get("id"))
            for r in t.get("resources", []):
                existing_urls.add(r.get("url"))
                existing_ids.add(r.get("id"))

    html = fetch_article()
    sections = parse_article(html)

    cat_taken = set(existing_ids)
    topic_taken = set(existing_ids)
    res_taken = set(existing_ids)
    rid_seq = [0]

    def new_rid():
        rid_seq[0] += 1
        return f"r{1000 + rid_seq[0]}"

    new_cats = []
    added = 0
    total_links = 0
    for section, groups in sections:
        total_links += sum(len(g) for g in groups)
        # filter to fresh, URL-resolves links, grouped by paragraph
        fresh_groups = []
        for g in groups:
            fg = [(t, u, ty) for (t, u, ty) in g if u and len(html_text(t)) > 1 and u not in existing_urls]
            fresh_groups.append(fg)
        if not any(fresh_groups):
            continue
        cat_id = slug(section.replace(" & ", " AND ").replace("+", "PLUS"), cat_taken)
        cat_taken.add(cat_id)
        cat_name = section.title().replace(" And ", " & ").replace("Plus", "+")
        cat = {"id": cat_id, "name": cat_name, "topics": []}
        for group in fresh_groups:
            if not group:
                continue
            for (t, u, ty) in group:
                existing_urls.add(u)
            title = group[0][0]
            tid = slug(title, topic_taken)
            topic_taken.add(tid)
            resources = []
            for (t, u, ty) in group:
                rid = new_rid()
                res_taken.add(rid)
                resources.append({
                    "id": rid,
                    "title": t,
                    "type": ty,
                    "relationship": ("primary_resource" if resources else "primary_resource"),
                    "url": u,
                })
                added += 1
            topic = {
                "id": tid,
                "title": title,
                "concepts": concepts_from(title),
                "resources": resources,
            }
            cat["topics"].append(topic)
        new_cats.append(cat)

    # Append any linkless section headers to unlinked_or_linkless_topics
    linkless = topics.get("unlinked_or_linkless_topics", [])
    linkless_taken = {x.get("title") for x in linkless}
    for section, groups in sections:
        if not any(groups) and section:
            if section not in linkless_taken:
                cat_id = slug(section, cat_taken)
                cat_taken.add(cat_id)
                linkless.append({"title": section, "category": cat_id, "resources": []})
                linkless_taken.add(section)

    topics["categories"].extend(new_cats)
    topics["unlinked_or_linkless_topics"] = linkless

    print(f"Scraped {total_links} links across {len([s for s,g in sections if any(g)])} sections with content.")
    print(f"New resources added: {added}")
    print(f"New categories added: {len(new_cats)}")
    for c in new_cats:
        nres = sum(len(t.get('resources', [])) for t in c['topics'])
        print(f"  + category '{c['name']}' ({len(c['topics'])} topics, {nres} resources)")

    if not dry:
        with open(TOPICS_FILE, "w", encoding="utf-8") as f:
            json.dump(topics, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"\nWrote {TOPICS_FILE} ({added} new resources)")
    else:
        print("\n--dry-run: topics.json not written")


if __name__ == "__main__":
    main()
