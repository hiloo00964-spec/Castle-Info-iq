import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import app


class AppTests(unittest.TestCase):
    def test_html_cleaning(self):
        self.assertEqual(app.norm("<p>Hello <b>world</b></p>"), "Hello world")

    def test_image_selection_from_page_metadata(self):
        entry = {"summary": "<p>summary</p>"}
        page = '<meta property="og:image" content="https://example.com/image.jpg">'
        with patch.object(app, "fetch", return_value=page):
            self.assertEqual(
                app.image_of(entry, "https://example.com/article"),
                "https://example.com/image.jpg",
            )

    def test_local_text_cutting(self):
        text = "جملة طويلة. " * 500
        self.assertLessEqual(len(app.compact_text(text, 3600)), 3600)

    def test_html_escaping_and_source_link(self):
        result = app.build(
            "<عنوان>\n\nنص & تفاصيل\n\n#علوم",
            "Science & Daily",
            "https://example.com/?a=1&b=2",
        )
        self.assertIn("&lt;عنوان&gt;", result)
        self.assertIn("Science &amp; Daily", result)
        self.assertIn("https://example.com/?a=1&amp;b=2", result)

    def test_duplicate_prevention_and_local_newest_selection(self):
        old = {
            "link": "https://example.com/old",
            "title": "Old news",
            "published_parsed": (2026, 8, 20, 0, 0, 0, 0, 0, 0),
        }
        non_news = {
            "link": "https://example.com/event",
            "title": "Science Event",
            "published_parsed": (2026, 8, 24, 0, 0, 0, 0, 0, 0),
        }
        newest = {
            "link": "https://example.com/new",
            "title": "Newest news",
            "published_parsed": (2026, 8, 23, 0, 0, 0, 0, 0, 0),
        }
        source_results = [[old, non_news], [newest], [], [], [], []]
        with patch.object(app, "rss_entries", side_effect=source_results), \
             patch.object(app, "article_text", return_value="مادة كافية " * 50), \
             patch.object(app, "image_of", return_value=None):
            result = app.candidate({"posted_links": [old["link"]], "posted_titles": []})
        self.assertEqual(result["link"], newest["link"])
        self.assertEqual(result["source"], app.RSS[1]["name"])

    def test_empty_rss_returns_no_candidate(self):
        with patch.object(app, "rss_entries", return_value=[]):
            self.assertIsNone(app.candidate({"posted_links": [], "posted_titles": []}))

    def test_image_download_failure_returns_none(self):
        with patch.object(app.requests, "get", side_effect=requests.RequestException("offline")):
            self.assertIsNone(app.download("https://example.com/image.jpg"))

    def test_corrupt_json_uses_valid_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            primary = Path(directory) / "data.json"
            backup = Path(directory) / "data.backup.json"
            primary.write_text("{broken", encoding="utf8")
            backup.write_text(json.dumps({"posted_links": ["saved"]}), encoding="utf8")
            with patch.object(app, "DATA_FILE", primary), patch.object(app, "BACKUP_FILE", backup):
                result = app.load()
        self.assertEqual(result["posted_links"], ["saved"])
        self.assertIn("active_gemini_model", result)

    def test_telegram_failure_is_attempted_once(self):
        state = {"posted_links": [], "posted_titles": []}
        generated = Mock(return_value=("عنوان صالح\nنص عربي كافٍ للنشر دون أرقام أو أسماء جديدة، وهذه جملة إضافية لضمان تجاوز الحد الأدنى للنص الناتج.\n#علوم #فضاء #معرفة", "model"))
        with patch.object(app, "load", return_value=state), \
             patch.object(app, "candidate", return_value=None), \
             patch.object(app, "generate_with_auto_model", generated), \
             patch.object(app, "telegram", side_effect=RuntimeError("telegram unavailable")) as telegram, \
             patch.object(app, "save"):
            with self.assertRaises(RuntimeError):
                app.run()
        self.assertEqual(telegram.call_count, 1)

    def test_git_retry_is_explicitly_bounded(self):
        workflow = (Path(__file__).parent / ".github" / "workflows" / "bot.yml").read_text(encoding="utf8")
        self.assertIn('for attempt in 1 2 3; do', workflow)
        self.assertIn('git push origin "HEAD:${branch}"', workflow)
        self.assertIn('exit 1', workflow)
        self.assertNotIn("git push || true", workflow)
        self.assertNotIn("while", workflow)
        self.assertNotIn("workflow_run", workflow)
        self.assertNotIn("repository_dispatch", workflow)

    def test_telegram_failure_is_not_silenced(self):
        me = SimpleNamespace(username="castle_bot", id=7)
        chat = SimpleNamespace(id=-100123, title="Castle")
        membership = SimpleNamespace(
            status=app.enums.ChatMemberStatus.ADMINISTRATOR,
            privileges=SimpleNamespace(can_post_messages=True),
        )
        fake_client = Mock()
        fake_client.__enter__ = Mock(return_value=fake_client)
        fake_client.__exit__ = Mock(return_value=False)
        fake_client.get_me.return_value = me
        fake_client.get_chat.return_value = chat
        fake_client.get_chat_member.return_value = membership
        fake_client.send_message.side_effect = RuntimeError("telegram unavailable")
        with patch.object(app, "download", return_value=None), \
             patch.object(app, "Client", return_value=fake_client), \
             patch.object(app, "TELEGRAM_CHANNEL_ID", "-100123"):
            with self.assertRaises(RuntimeError):
                app.telegram("<b>test</b>")


if __name__ == "__main__":
    unittest.main()
