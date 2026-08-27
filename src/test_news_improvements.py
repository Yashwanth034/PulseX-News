import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.collector import extract_entry_media
from src.formatter import strip_feed_boilerplate
from src.event_memory import decide, init_events
from src.intelligence import _category, classify
from src.media import _extract_meta, _public_http_url
from src.priority import _term_present as priority_term_present, priority
from src.quality import has_rss_junk
from src.selection import select_balanced_queue
from src.x_web_publisher import _WebComposer, XWebPublisher
import src.main as news_main


class BalancedSelectionTests(unittest.TestCase):
    def test_first_slots_cover_distinct_categories(self):
        candidates = [
            {
                "id": "conflict-1",
                "category": "conflict",
                "source": "Source A",
                "priority_level": "URGENT",
                "priority_score": 100,
                "confidence": "high",
                "score": 95,
            },
            {
                "id": "conflict-2",
                "category": "conflict",
                "source": "Source B",
                "priority_level": "URGENT",
                "priority_score": 99,
                "confidence": "high",
                "score": 94,
            },
            {
                "id": "science-1",
                "category": "science",
                "source": "Source C",
                "priority_level": "HIGH",
                "priority_score": 80,
                "confidence": "high",
                "score": 80,
            },
            {
                "id": "tech-1",
                "category": "technology",
                "source": "Source D",
                "priority_level": "HIGH",
                "priority_score": 70,
                "confidence": "high",
                "score": 70,
            },
        ]

        selected = select_balanced_queue(candidates, 3)

        self.assertEqual(
            {item["category"] for item in selected},
            {"conflict", "science", "technology"},
        )

    def test_immediate_story_is_never_blocked_by_diversity_cap(self):
        candidates = [
            {
                "id": f"emergency-{index}",
                "category": "disaster",
                "source": f"Source {index}",
                "priority_level": "IMMEDIATE",
                "priority_score": 100 - index,
                "confidence": "high",
                "score": 100 - index,
            }
            for index in range(3)
        ]

        selected = select_balanced_queue(
            candidates,
            3,
            max_per_category=1,
        )

        self.assertEqual(len(selected), 3)


class PriorityBoundaryTests(unittest.TestCase):
    def test_war_does_not_match_howard_or_software(self):
        self.assertFalse(priority_term_present("Jeret Howard joins NASA", "war"))
        self.assertFalse(priority_term_present("critical software update", "war"))

    def test_real_war_and_plural_attack_still_match(self):
        self.assertTrue(priority_term_present("war enters a new phase", "war"))
        self.assertTrue(priority_term_present("drone attacks hit the region", "attack"))

    def test_routine_nasa_personnel_story_is_not_immediate(self):
        result = priority({
            "title": "Contractor to Civil Servant: NASA Welcomes Jeret Howard",
            "summary": (
                "At 17, Jeret Howard served as a United States Army combat medic. "
                "He later joined NASA as a civil servant."
            ),
            "score": 55,
            "confidence": "high",
            "primary_source": True,
            "strong_corroboration": 0,
        })
        self.assertEqual(result["priority_level"], "HIGH")

    def test_actual_verified_attack_can_be_immediate(self):
        result = priority({
            "title": "Missile attack hits major city",
            "summary": "Authorities confirmed the attack and emergency response.",
            "score": 60,
            "confidence": "high",
            "primary_source": True,
            "strong_corroboration": 1,
        })
        self.assertEqual(result["priority_level"], "IMMEDIATE")


class TopicClassificationTests(unittest.TestCase):
    def test_pfa_players_story_is_sports_not_world(self):
        text = (
            "Man United’s Fernandes, City’s Shaw named PFA Players of the Year. "
            "Fernandes had a record number of assists in the league and Shaw won the Golden Boot."
        )
        self.assertEqual(_category(text, "world"), "sports")

    def test_afghan_child_malnutrition_story_is_health(self):
        text = (
            "One million Afghan children suffer life-threatening malnutrition. "
            "The United Nations warned of a worsening nutrition crisis."
        )
        self.assertEqual(_category(text, "world"), "health")

    def test_ai_letters_inside_unrelated_word_do_not_trigger_technology(self):
        self.assertEqual(
            _category("Maid service expands into three cities", "world"),
            "world",
        )

    def test_single_real_disaster_signal_can_leave_world_bucket(self):
        self.assertEqual(
            _category("Earthquake reported near coastal town", "world"),
            "disaster",
        )

    def test_cybersecurity_source_keeps_specific_topic(self):
        result = classify(
            "Critical vulnerability patched",
            "Researchers recommend installing the update.",
            "cybersecurity",
            {"tier": 2, "primary_source": False},
        )
        self.assertEqual(result["category"], "cybersecurity")

    def test_rape_story_is_crime_not_generic_world(self):
        self.assertEqual(
            _category(
                "Teenage girl allegedly raped at residential care home",
                "world",
            ),
            "crime",
        )


class FeedCleanupAndMediaTests(unittest.TestCase):
    def test_guardian_public_policy_boilerplate_is_removed(self):
        text = (
            "We are aiming, of course, to inform public policy debate, "
            "which can often get heated."
        )
        self.assertEqual(strip_feed_boilerplate(text), "")
        self.assertTrue(has_rss_junk(text))

    def test_liveblog_boilerplate_is_removed_without_losing_story_text(self):
        text = (
            "Iran says the Hormuz strait remains closed. "
            "Follow our liveblog for the latest updates."
        )
        self.assertEqual(
            strip_feed_boilerplate(text),
            "Iran says the Hormuz strait remains closed.",
        )
        self.assertTrue(has_rss_junk(text))

    def test_presenter_intro_is_removed_but_report_context_is_kept(self):
        text = (
            "Mark Owen is pleased to welcome Peter Zalmayev, Director of the "
            "Eurasia Democracy Initiative. He argues that Ukraine’s expanding "
            "long-range strike campaign has raised the costs of Russia’s invasion."
        )
        cleaned = strip_feed_boilerplate(text)
        self.assertNotIn("pleased to welcome", cleaned.lower())
        self.assertTrue(cleaned.startswith("He argues that Ukraine"))
        self.assertTrue(has_rss_junk(text))

    def test_rss_media_candidates_are_collected_and_deduplicated(self):
        entry = {
            "media_content": [
                {
                    "url": "https://example.com/photo.jpg",
                    "type": "image/jpeg",
                }
            ],
            "media_thumbnail": [
                {"url": "https://example.com/photo.jpg"},
            ],
            "enclosures": [
                {
                    "href": "https://example.com/video.mp4",
                    "type": "video/mp4",
                }
            ],
        }

        media = extract_entry_media(entry)

        self.assertEqual(
            media,
            [
                {
                    "url": "https://example.com/photo.jpg",
                    "type": "image/jpeg",
                },
                {
                    "url": "https://example.com/video.mp4",
                    "type": "video/mp4",
                },
            ],
        )

    def test_media_url_guard_rejects_internal_networks(self):
        self.assertFalse(_public_http_url("http://127.0.0.1/private.jpg"))
        self.assertFalse(_public_http_url("http://169.254.169.254/meta"))
        self.assertTrue(_public_http_url("https://8.8.8.8/image.jpg"))

    def test_opengraph_image_is_discovered_without_file_extension(self):
        html = (
            '<html><head><meta property="og:image" '
            'content="https://8.8.8.8/media?id=42"></head></html>'
        )

        media = _extract_meta(html, "https://8.8.8.8/article")

        self.assertEqual(media["kind"], "image")
        self.assertEqual(media["origin"], "opengraph")
        self.assertEqual(media["url"], "https://8.8.8.8/media?id=42")


class WebMediaAttachmentTests(unittest.TestCase):
    def test_composer_single_attaches_media_before_posting(self):
        composer = object.__new__(_WebComposer)
        events = []
        composer._open_composer = lambda: events.append("open")
        composer._type_text = lambda text: events.append(("text", text))
        composer._attach_media = lambda path: events.append(("media", path)) or True
        composer._click_post = lambda: events.append("post")
        composer._tweet_id_from_toast = lambda: "123"

        result = composer.post_single("hello", media_path="/tmp/photo.jpg")

        self.assertEqual(
            events,
            ["open", ("text", "hello"), ("media", "/tmp/photo.jpg"), "post"],
        )
        self.assertTrue(result["media_attached"])
        self.assertEqual(result["tweet_id"], "123")

    def _publisher(self):
        publisher = object.__new__(XWebPublisher)
        publisher.enabled = True
        publisher.username = ""
        publisher.password = ""
        publisher.otp = ""
        publisher.headless = True
        publisher.session_file = Path("unused-session.json")
        publisher._check_allowed = lambda required_posts=1: None
        publisher._record_success = lambda: None
        return publisher

    def test_live_publish_passes_downloaded_media_to_composer(self):
        received = []

        class FakeComposer:
            def __init__(self, *args, **kwargs):
                pass

            def ensure_logged_in(self):
                pass

            def post_single(self, text, media_path=None):
                received.append(media_path)
                return {
                    "web": True,
                    "text": text,
                    "tweet_id": "123",
                    "media_attached": bool(media_path),
                }

            def close(self):
                pass

        publisher = self._publisher()
        item = {
            "format": "single",
            "post": "hello",
            "media": {
                "url": "https://example.com/photo.jpg",
                "kind": "image",
            },
        }

        with patch("src.x_web_publisher._WebComposer", FakeComposer), patch(
            "src.x_web_publisher.download_media",
            return_value="/tmp/downloaded.jpg",
        ):
            result = publisher.publish(item)

        self.assertEqual(received, ["/tmp/downloaded.jpg"])
        self.assertTrue(result[0]["media_attached"])

    def test_media_download_failure_falls_back_to_text(self):
        received = []

        class FakeComposer:
            def __init__(self, *args, **kwargs):
                pass

            def ensure_logged_in(self):
                pass

            def post_single(self, text, media_path=None):
                received.append((text, media_path))
                return {
                    "web": True,
                    "text": text,
                    "tweet_id": "123",
                    "media_attached": False,
                }

            def close(self):
                pass

        publisher = self._publisher()
        item = {
            "format": "single",
            "post": "text survives",
            "media": {
                "url": "https://example.com/missing.jpg",
                "kind": "image",
            },
        }

        with patch("src.x_web_publisher._WebComposer", FakeComposer), patch(
            "src.x_web_publisher.download_media",
            return_value=None,
        ):
            result = publisher.publish(item)

        self.assertEqual(received, [("text survives", None)])
        self.assertFalse(result[0]["media_attached"])

    def test_thread_attaches_media_only_to_first_post(self):
        composer = object.__new__(_WebComposer)
        composer._open_composer = lambda: None
        composer._click_post = lambda: None
        composer._type_text = lambda text: None
        composer._attach_media = lambda path: True

        class EmptyLocator:
            def count(self):
                return 0

        class Page:
            def locator(self, selector):
                return EmptyLocator()

            def wait_for_timeout(self, milliseconds):
                pass

        # A single-item thread avoids exercising X's unrelated add-button DOM
        # while still proving the attachment contract and result metadata.
        composer.page = Page()
        results = composer.post_thread(["first"], media_path="/tmp/video.mp4")

        self.assertTrue(results[0]["media_attached"])


class SemanticDuplicateTests(unittest.TestCase):
    def _conn(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE stories(id TEXT PRIMARY KEY, source TEXT, event_id TEXT)"
        )
        init_events(conn)
        return conn

    def _item(self, title, source):
        return {
            "title": title,
            "summary": "",
            "source": source,
            "category": "world",
            "priority_score": 70,
            "score": 70,
        }

    def _seed(self, conn, title, source="Source A"):
        status, event_id, _ = decide(conn, self._item(title, source))
        self.assertEqual(status, "NEW")
        conn.execute(
            "INSERT INTO stories(id, source, event_id) VALUES(?,?,?)",
            ("seed", source, event_id),
        )
        conn.commit()
        return event_id

    def test_algeria_cross_source_parallel_headline_is_duplicate(self):
        conn = self._conn()
        try:
            event_id = self._seed(
                conn,
                "At least 12 dead, 54 injured as wildfires ravage northeastern Algeria",
            )
            status, matched_event, _ = decide(
                conn,
                self._item(
                    "At least 12 dead as wildfires sweep through northern Algeria",
                    "Source B",
                ),
            )
            self.assertEqual(status, "DUPLICATE")
            self.assertEqual(matched_event, event_id)
        finally:
            conn.close()

    def test_wildberries_different_wording_is_same_event_duplicate(self):
        conn = self._conn()
        try:
            event_id = self._seed(
                conn,
                "Ukrainian drone attacks kill 3 as fire destroys Wildberries warehouse",
            )
            status, matched_event, _ = decide(
                conn,
                self._item(
                    "Russia’s ‘Amazon’ Wildberries comes under Ukraine attack again",
                    "Source B",
                ),
            )
            self.assertEqual(status, "DUPLICATE")
            self.assertEqual(matched_event, event_id)
        finally:
            conn.close()

    def test_same_country_and_attack_words_do_not_merge_unrelated_events(self):
        conn = self._conn()
        try:
            first_event = self._seed(
                conn,
                "Ukrainian drone attack hits Wildberries warehouse",
            )
            status, second_event, _ = decide(
                conn,
                self._item(
                    "Ukrainian drone attack hits Kyiv airport terminal",
                    "Source B",
                ),
            )
            self.assertEqual(status, "NEW")
            self.assertNotEqual(second_event, first_event)
        finally:
            conn.close()


class QueueMemoryOrderingTests(unittest.TestCase):
    def _candidate(self, story_id, title, category, source, priority_score):
        return {
            "id": story_id,
            "title": title,
            "url": f"https://example.com/{story_id}",
            "source": source,
            "source_category": category,
            "primary_source": True,
            "tier": 1,
            "region": "global",
            "discovery": False,
            "summary": "A complete explanatory sentence about this report.",
            "published_at": None,
            "updated_at": None,
            "effective_at": None,
            "media_candidates": [],
            "_test_category": category,
            "_test_priority": priority_score,
        }

    def test_deferred_good_candidates_are_not_written_to_story_memory(self):
        candidates = [
            self._candidate(
                "story-a",
                "Major diplomacy agreement announced",
                "politics",
                "Source A",
                90,
            ),
            self._candidate(
                "story-b",
                "New space telescope result released",
                "space",
                "Source B",
                80,
            ),
            self._candidate(
                "story-c",
                "Technology company launches new processor",
                "technology",
                "Source C",
                70,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_config = news_main.CONFIG
            original_db = news_main.DB
            original_queue = news_main.QUEUE
            original_health = news_main.SOURCE_HEALTH

            news_main.CONFIG = copy.deepcopy(original_config)
            news_main.CONFIG.update({
                "max_stories_per_run": 1,
                "min_score_to_queue": 0,
                "discovery_min_score": 0,
            })
            news_main.DB = root / "news.db"
            news_main.QUEUE = root / "queue.json"
            news_main.SOURCE_HEALTH = root / "source_health.json"

            def fake_classify(title, summary, source_category, item):
                return {
                    "category": item["_test_category"],
                    "score": 70,
                    "urgency_terms": [],
                }

            def fake_verify(item, all_items):
                return {
                    "confidence": "high",
                    "strong_corroboration": 1,
                }

            def fake_priority(item):
                return {
                    "priority_score": item["_test_priority"],
                    "priority_level": "URGENT",
                    "max_delay_minutes": 15,
                }

            def fake_format(item, breaking_min_score):
                return {
                    "format": "single",
                    "post": (
                        f"📰 NEWS: {item['title']}. "
                        "A complete explanatory sentence. "
                        f"Source: {item['source']}."
                    ),
                }

            try:
                with (
                    patch.object(news_main, "fetch", return_value=candidates),
                    patch.object(news_main, "check_item", return_value="ENGLISH"),
                    patch.object(news_main, "classify", side_effect=fake_classify),
                    patch.object(news_main, "verify", side_effect=fake_verify),
                    patch.object(news_main, "priority", side_effect=fake_priority),
                    patch.object(news_main, "format_story", side_effect=fake_format),
                    patch.object(
                        news_main,
                        "quality_check",
                        return_value={
                            "quality_pass": True,
                            "quality_errors": [],
                            "quality_warnings": [],
                        },
                    ),
                    patch.object(news_main, "discover_media", return_value=None),
                ):
                    news_main.main()

                with sqlite3.connect(news_main.DB) as conn:
                    stored_story_count = conn.execute(
                        "SELECT COUNT(*) FROM stories"
                    ).fetchone()[0]
                    stored_event_count = conn.execute(
                        "SELECT COUNT(*) FROM events"
                    ).fetchone()[0]

                self.assertEqual(stored_story_count, 1)
                self.assertEqual(stored_event_count, 1)
            finally:
                news_main.CONFIG = original_config
                news_main.DB = original_db
                news_main.QUEUE = original_queue
                news_main.SOURCE_HEALTH = original_health


if __name__ == "__main__":
    unittest.main()
