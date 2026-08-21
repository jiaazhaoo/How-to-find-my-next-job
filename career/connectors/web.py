"""Fetch a URL or feed and stage it. The "just give me a link" connector.

Built for personal writing that lives at a public address: a blog, an Atom or
RSS feed, a single long post. Point `urls` at feeds where they exist -- a feed
gives you the full archive with dates and per-entry bodies, while a rendered
index page gives you whatever that page happened to show.

It will fetch anything, including a timeline URL. Whether that *works* is a
property of the site, not of this code, so the connector reports what it
actually got rather than pretending: a page that comes back as a login wall
or with no readable body is reported, not staged.
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from ..relevance import histogram, weighted_length, work_score
from .base import Connector, StagedItem

USER_AGENT = "career-input-layer/0.1 (+personal archive import)"
TIMEOUT = 30
# A coarse floor only, matching the X standalone cutoff: relevance does the
# real filtering, so this just keeps out empty shells.
MIN_ENTRY_WEIGHT = 120

FEED_LINK = re.compile(
    r"""<link[^>]+type=["']application/(?:rss|atom)\+xml["'][^>]*>""", re.I)
HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)
DROP_BLOCKS = re.compile(r"<(script|style|nav|footer|header|form)\b.*?</\1>", re.I | re.S)
BLOCK_TAG = re.compile(r"</(?:p|div|li|h[1-6]|tr|blockquote|pre|title)>", re.I)
TAG = re.compile(r"<[^>]+>")

# Pages that returned 200 but no content -- the polite version of a refusal.
WALL = re.compile(
    r"(?:log in to|sign in to|enable javascript|create an account to|"
    r"something went wrong.{0,40}try again|are you a robot|verify you are human)", re.I)


def fetch(url: str, timeout: int = TIMEOUT) -> tuple[str, str, str]:
    """Return (body, content_type, error). Never raises."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(8_000_000)
            ctype = response.headers.get("Content-Type", "")
            charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, "replace"), ctype, ""
    except urllib.error.HTTPError as exc:
        return "", "", f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return "", "", f"{type(exc).__name__}: {exc}"


def html_to_text(body: str) -> str:
    body = DROP_BLOCKS.sub(" ", body)
    body = BLOCK_TAG.sub("\n", body)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    text = html.unescape(TAG.sub("", body))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


def discover_feed(page_url: str, body: str) -> str | None:
    """A blog's index usually declares its own feed. Prefer the feed."""
    m = FEED_LINK.search(body)
    if not m:
        return None
    href = HREF.search(m.group(0))
    if not href:
        return None
    return urllib.parse.urljoin(page_url, href.group(1))


def _text_of(element, *paths: str) -> str:
    for path in paths:
        found = element.find(path)
        if found is not None:
            if found.text and found.text.strip():
                return found.text
            inner = "".join(ET.tostring(c, encoding="unicode") for c in found)
            if inner.strip():
                return inner
    return ""


def _iso(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return raw[:10]


def parse_feed(body: str) -> list[dict]:
    """RSS and Atom, without a dependency."""
    try:
        root = ET.fromstring(body.strip())
    except ET.ParseError:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom",
          "content": "http://purl.org/rss/1.0/modules/content/"}
    entries = []

    for item in root.findall(".//item"):
        link = _text_of(item, "link", "guid")
        entries.append({
            "id": link or _text_of(item, "guid"),
            "title": html.unescape(_text_of(item, "title")).strip(),
            "body": (item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded")
                     or item.findtext("description") or ""),
            "date": _iso(item.findtext("pubDate") or item.findtext("date") or ""),
            "url": link,
        })

    for entry in root.findall(".//atom:entry", ns):
        link_el = entry.find("atom:link", ns)
        link = link_el.get("href") if link_el is not None else ""
        entries.append({
            "id": entry.findtext("atom:id", "", ns) or link,
            "title": html.unescape(entry.findtext("atom:title", "", ns) or "").strip(),
            "body": (entry.findtext("atom:content", "", ns)
                     or entry.findtext("atom:summary", "", ns) or ""),
            "date": _iso(entry.findtext("atom:published", "", ns)
                         or entry.findtext("atom:updated", "", ns) or ""),
            "url": link,
        })
    return entries


class WebConnector(Connector):
    name = "web"
    source_type = "public"
    description = "Blogs, feeds and public pages (set `urls` in config)"

    def __init__(self) -> None:
        self.last_skipped = 0
        self.last_histogram: dict[str, int] = {}
        self.last_counts: dict[str, int] = {}
        self.problems: list[str] = []

    def available(self, config: dict | None = None) -> tuple[bool, str]:
        urls = (config or {}).get("urls") or []
        if not urls:
            return False, ("set connectors['web'].urls to your blog feed or post URLs "
                           "(a feed gives the full archive; a rendered page gives one screenful)")
        return True, f"{len(urls)} url(s) configured"

    def fetch(self, config: dict, limit: int | None = None) -> list[StagedItem]:
        urls = config.get("urls") or []
        threshold = float(config.get("min_relevance", 0.35))
        per_url_limit = int(config.get("limit", 200))

        raw_entries: list[dict] = []
        counts = {"urls": len(urls), "feeds": 0, "pages": 0, "entries": 0, "failed": 0,
                  "walled": 0, "thin": 0}
        self.problems = []

        for url in urls:
            body, ctype, error = fetch(url)
            if error or not body.strip():
                counts["failed"] += 1
                self.problems.append(f"{url}: {error or 'empty response'}")
                continue

            looks_feed = ("xml" in ctype.lower() or body.lstrip()[:200].lstrip().startswith("<?xml")
                          or "<rss" in body[:500].lower() or "<feed" in body[:500].lower())
            if not looks_feed:
                feed_url = discover_feed(url, body)
                if feed_url:
                    feed_body, _, feed_error = fetch(feed_url)
                    if not feed_error and feed_body.strip():
                        body, looks_feed = feed_body, True
                        self.problems.append(f"{url}: used its declared feed {feed_url}")

            if looks_feed:
                entries = parse_feed(body)[:per_url_limit]
                counts["feeds"] += 1
                counts["entries"] += len(entries)
                for e in entries:
                    e["body"] = html_to_text(e["body"])
                raw_entries.extend(entries)
                if not entries:
                    self.problems.append(f"{url}: parsed as a feed but contained no entries")
                continue

            text = html_to_text(body)
            counts["pages"] += 1
            # Keep these two diagnoses apart. A wall and a short post both
            # produce nothing, but they send you to completely different
            # fixes, and calling a real short post a "login wall" is worse
            # than saying nothing.
            if WALL.search(text[:2000]):
                counts["walled"] += 1
                self.problems.append(
                    f"{url}: served a page with no article text -- login wall or "
                    f"client-side rendering. Nothing staged; try a feed URL instead.")
                continue
            if weighted_length(text) < MIN_ENTRY_WEIGHT:
                counts["thin"] += 1
                self.problems.append(f"{url}: only {weighted_length(text)} units of text "
                                     f"(floor is {MIN_ENTRY_WEIGHT}) -- not staged")
                continue
            match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
            title = html.unescape(match.group(1)).strip() if match else url
            raw_entries.append({"id": url, "title": title, "body": text, "date": "", "url": url})
            counts["entries"] += 1

        items, scores, skipped = [], [], 0
        for e in raw_entries:
            text = e["body"].strip()
            if not text or weighted_length(text) < MIN_ENTRY_WEIGHT:
                skipped += 1
                continue
            score, signals = work_score(text)
            scores.append(score)
            if score < threshold:
                skipped += 1
                continue
            header = (f"<!-- source_type=public connector=web "
                      f"work_relevance={score} url={e.get('url') or e['id']} -->")
            items.append(StagedItem(
                native_id=re.sub(r"[^A-Za-z0-9._-]+", "-", e["id"])[-90:] or "page",
                title=e["title"] or e.get("url") or "untitled",
                text=f"{header}\n\n# {e['title']}\n\n{text}\n",
                created_at=e.get("date", ""), updated_at=e.get("date", ""),
                meta={"source_type": self.source_type, "work_relevance": score,
                      "signals": signals, "url": e.get("url", "")}))

        self.last_skipped = skipped
        self.last_histogram = histogram(scores)
        self.last_counts = counts
        items.sort(key=lambda i: i.created_at, reverse=True)
        return items[:limit] if limit else items
