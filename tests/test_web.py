from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from career.connectors.web import WebConnector, discover_feed, html_to_text, parse_feed

ARTICLE = ("我们决定用双写加对账做灰度，因为一次性切换没有回滚路径。接口改造涉及 reconcile 模块，"
           "数据库要加索引，p99 的目标是 150ms。上线后实际降到了 120ms，对账不一致率 0.002%。")

ATOM = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Blog</title>
  <entry>
    <id>tag:blog,2026:1</id>
    <title>迁移复盘</title>
    <link href="http://127.0.0.1:{{port}}/posts/1"/>
    <published>2026-01-05T10:00:00Z</published>
    <content>&lt;p&gt;{ARTICLE}&lt;/p&gt;</content>
  </entry>
  <entry>
    <id>tag:blog,2026:2</id>
    <title>短记</title>
    <link href="http://127.0.0.1:{{port}}/posts/2"/>
    <published>2026-02-05T10:00:00Z</published>
    <content>今天天气不错</content>
  </entry>
</feed>"""

RSS = f"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title>ETL 重构</title><link>http://example.com/2</link>
  <pubDate>Mon, 05 Jan 2026 10:00:00 +0000</pubDate>
  <description>&lt;p&gt;{ARTICLE}&lt;/p&gt;</description></item>
</channel></rss>"""

INDEX_WITH_FEED = """<html><head>
<link rel="alternate" type="application/atom+xml" href="/feed.xml">
</head><body><p>index</p></body></html>"""

LOGIN_WALL = """<html><head><title>Home</title></head><body>
<div>Something went wrong. Try again.</div><div>Log in to see more.</div>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        routes = {
            "/feed.xml": ("application/atom+xml", ATOM.format(port=self.server.server_port)),
            "/rss.xml": ("application/rss+xml", RSS),
            "/": ("text/html", INDEX_WITH_FEED),
            "/walled": ("text/html", LOGIN_WALL),
            "/article": ("text/html", f"<html><head><title>Post</title></head>"
                                      f"<body><article><p>{ARTICLE}</p></article></body></html>"),
            "/short": ("text/html", "<html><head><title>Note</title></head>"
                                    "<body><p>今天改了个小 bug。</p></body></html>"),
        }
        if self.path not in routes:
            self.send_error(404)
            return
        ctype, body = routes[self.path]
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class TestWebConnector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def fetch(self, *paths, **conf):
        c = WebConnector()
        return c, c.fetch({"urls": [self.base + p for p in paths], **conf})

    def test_atom_feed_gives_dated_entries(self):
        c, items = self.fetch("/feed.xml")
        self.assertEqual(len(items), 1, "the one-line post should fall below the floor")
        self.assertEqual(items[0].title, "迁移复盘")
        self.assertTrue(items[0].created_at.startswith("2026-01-05"))
        self.assertIn("双写加对账", items[0].text)
        self.assertEqual(c.last_counts["feeds"], 1)

    def test_rss_dates_parse_too(self):
        _, items = self.fetch("/rss.xml")
        self.assertTrue(items[0].created_at.startswith("2026-01-05"))

    def test_a_page_declaring_a_feed_is_followed_to_it(self):
        """A rendered index shows one screenful; its feed has the archive."""
        c, items = self.fetch("/")
        self.assertEqual(c.last_counts["feeds"], 1)
        self.assertTrue(items)
        self.assertTrue(any("used its declared feed" in p for p in c.problems))

    def test_plain_article_pages_still_work(self):
        _, items = self.fetch("/article")
        self.assertEqual(len(items), 1)
        self.assertIn("双写加对账", items[0].text)

    def test_a_login_wall_is_reported_not_staged(self):
        """Staging a wall is worse than a missing source: it looks like success."""
        c, items = self.fetch("/walled")
        self.assertEqual(items, [])
        self.assertEqual(c.last_counts["walled"], 1)
        self.assertTrue(any("login wall" in p for p in c.problems))

    def test_a_short_post_is_not_diagnosed_as_a_login_wall(self):
        """Both produce nothing, but they send you to different fixes."""
        c, items = self.fetch("/short")
        self.assertEqual(items, [])
        self.assertEqual(c.last_counts["walled"], 0)
        self.assertEqual(c.last_counts["thin"], 1)
        self.assertTrue(any("units of text" in p for p in c.problems))

    def test_unreachable_urls_are_counted_not_fatal(self):
        c, items = self.fetch("/nope", "/feed.xml")
        self.assertEqual(c.last_counts["failed"], 1)
        self.assertTrue(items, "one bad url must not sink the rest")

    def test_relevance_gate_applies(self):
        _, items = self.fetch("/feed.xml", min_relevance=0.99)
        self.assertEqual(items, [])


class TestHtmlAndFeedParsing(unittest.TestCase):
    def test_scripts_and_tags_are_stripped(self):
        text = html_to_text("<html><script>var x=1</script><p>hello</p><p>world</p></html>")
        self.assertNotIn("var x", text)
        self.assertEqual(text.split(), ["hello", "world"])

    def test_entities_are_decoded(self):
        self.assertIn("a & b", html_to_text("<p>a &amp; b</p>"))

    def test_feed_discovery_resolves_relative_hrefs(self):
        self.assertEqual(discover_feed("https://b.com/blog/", INDEX_WITH_FEED),
                         "https://b.com/feed.xml")

    def test_malformed_feeds_return_nothing_rather_than_raising(self):
        self.assertEqual(parse_feed("<not xml"), [])


if __name__ == "__main__":
    unittest.main()
