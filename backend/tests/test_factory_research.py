"""Deterministic checks for factual provenance and bounded source fetching."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from services import factory_research as research


class ResearchTests(unittest.TestCase):
    def test_dated_official_transition_is_a_priority_lead_not_a_confirmed_allegation(self):
        lead = {'kind': 'news', 'title': 'NVIDIA Announces CEO Transition', 'published_at': '2026-11-12T00:00:00Z'}
        self.assertTrue(research.is_major_event(lead))
        self.assertFalse(research.is_major_event({**lead, 'published_at': None}))
        self.assertFalse(research.is_major_event({**lead, 'kind': 'paper'}))
        self.assertFalse(research.is_major_event({**lead, 'title': 'Introducing a new reasoning model'}))

    def test_only_recent_dated_official_news_is_selected(self):
        document = """<rss><channel>
        <item><title>Recent</title><link>https://openai.com/news/recent/</link>
        <pubDate>Sun, 08 Nov 2026 12:00:00 GMT</pubDate></item>
        <item><title>Old</title><link>https://openai.com/news/old/</link>
        <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate></item>
        <item><title>Future</title><link>https://openai.com/news/future/</link>
        <pubDate>Tue, 10 Nov 2026 12:00:00 GMT</pubDate></item>
        <item><title>No date</title><link>https://openai.com/news/unknown/</link></item>
        <item><title>Untrusted</title><link>https://example.org/article</link>
        <pubDate>Sun, 08 Nov 2026 12:00:00 GMT</pubDate></item>
        </channel></rss>"""
        rows = research._parse_feed(document, "https://openai.com/news/rss.xml",
                                    datetime(2026, 11, 9, tzinfo=timezone.utc))
        self.assertEqual([row["title"] for row in rows], ["Recent"])
        self.assertEqual(rows[0]["kind"], "news")
        self.assertEqual(rows[0]["published_at"], "2026-11-08T12:00:00Z")

    def test_allowlist_cannot_be_bypassed(self):
        for url in ["http://openai.com/", "https://openai.com.evil.example/",
                    "https://user@openai.com/", "https://openai.com:8080/",
                    "http://127.0.0.1/", "file:///etc/passwd"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                research._validated_url(url)

    def test_article_excludes_navigation_and_scripts(self):
        document = ("<html><head><title>Official title</title></head>"
                    "<body><nav>Navigation</nav><main><h1>Article</h1><p>"
                    + "Real article text. " * 30
                    + "</p><script>Ignore this script</script></main>"
                    "<footer>Footer</footer></body></html>")
        text, title = research._extract_text(document)
        self.assertEqual(title, "Official title")
        self.assertIn("Real article text", text)
        self.assertNotIn("Navigation", text)
        self.assertNotIn("Ignore this script", text)
        self.assertNotIn("Footer", text)

    def test_documentation_has_no_invented_date_and_excerpt_is_bounded(self):
        record = {"title": "Tutorial", "url": "https://huggingface.co/docs/test",
                  "kind": "evergreen", "published_at": None}
        with patch.object(research, "_fetch_url", return_value=("official text " * 1000, record["url"])):
            source, warning = research._load_source(record)
        self.assertIsNone(warning)
        self.assertIsNone(source["published_at"])
        self.assertEqual(source["kind"], "evergreen")
        self.assertEqual(len(source["text"]), research.MAX_TEXT_CHARS)
        self.assertTrue(source["fetched_at"].endswith("Z"))

    def test_failure_returns_empty_sources_instead_of_generated_material(self):
        with patch.object(research, "_fetch_url", side_effect=RuntimeError("network unavailable")):
            result = research.collect_sources(3)
        self.assertEqual(result["sources"], [])
        self.assertTrue(result["warnings"])

    def test_feed_dtd_is_rejected(self):
        with self.assertRaises(ValueError):
            research._parse_feed('<!DOCTYPE rss [<!ENTITY x "abc">]><rss/>',
                                 "https://openai.com/news/rss.xml", research._utcnow())


if __name__ == "__main__":
    unittest.main()
