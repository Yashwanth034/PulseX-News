import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.collector import clean_title, extract_alert_level, extract_entry_media, fetch_one
from src.formatter import format_story, label, strip_feed_boilerplate
from src.event_memory import decide, init_events
from src.intelligence import _category, classify, verify
from src.media import _extract_meta, _public_http_url
from src.emergency import is_verified_major_disaster
from src.priority import _term_present as priority_term_present, priority
from src.quality import has_rss_junk, quality_check
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

    def test_multiple_verified_major_disasters_overflow_ordinary_limit(self):
        candidates = [
            {
                "id": "nepal-flood",
                "title": "Nearly 1,000 are missing after floods in Nepal",
                "summary": "Catastrophic floods followed a glacial collapse.",
                "category": "disaster",
                "source": "Source A",
                "priority_level": "URGENT",
                "priority_score": 95,
                "confidence": "high",
                "strong_corroboration": 1,
                "score": 68,
            },
            {
                "id": "major-wildfire",
                "title": "Wildfires leave 80 dead across the region",
                "summary": "Emergency crews are responding.",
                "category": "disaster",
                "source": "Source B",
                "priority_level": "URGENT",
                "priority_score": 92,
                "confidence": "high",
                "strong_corroboration": 1,
                "score": 70,
            },
            {
                "id": "finance",
                "title": "Markets rise after policy announcement",
                "summary": "Markets gained after the decision.",
                "category": "finance",
                "source": "Source C",
                "priority_level": "URGENT",
                "priority_score": 99,
                "confidence": "high",
                "strong_corroboration": 1,
                "score": 90,
            },
        ]

        selected = select_balanced_queue(candidates, 1)
        self.assertEqual(
            [item["id"] for item in selected],
            ["nepal-flood", "major-wildfire"],
        )


class MajorDisasterTests(unittest.TestCase):
    def _nepal_item(self):
        return {
            "title": "Nearly 1,000 are missing in Nepal and Tibet after floods caused by a glacial collapse",
            "summary": "Rescue teams are searching the affected area.",
            "category": "disaster",
            "source": "BBC World",
            "primary_source": False,
            "strong_corroboration": 1,
            "confidence": "high",
            "priority_level": "URGENT",
            "priority_score": 93,
            "score": 68,
            "urgency_terms": ["flood"],
            "event_status": "NEW",
            "language_status": "ENGLISH",
            "url": "https://example.com/nepal-flood",
        }

    def test_nepal_flood_is_recognized_as_verified_major_disaster(self):
        self.assertTrue(is_verified_major_disaster(self._nepal_item()))

    def test_nepal_flood_plural_classifies_as_disaster(self):
        item = self._nepal_item()
        result = classify(
            item["title"],
            item["summary"],
            "world",
            item,
        )
        self.assertEqual(result["category"], "disaster")
        self.assertIn("flood", result["urgency_terms"])

    def test_verified_major_disaster_with_two_sentence_post_passes(self):
        item = self._nepal_item()
        item.update({
            "format": "single",
            "post": (
                "🚨 BREAKING: Nearly 1,000 are missing in Nepal and Tibet after floods caused by a glacial collapse. "
                "Source: BBC World."
            ),
        })
        result = quality_check(item)
        self.assertTrue(result["quality_pass"], result["quality_errors"])
        self.assertTrue(result["quality_warnings"])

    def test_normal_two_sentence_post_is_still_rejected(self):
        item = self._nepal_item()
        item.update({
            "category": "world",
            "format": "single",
            "post": "📰 NEWS: Routine diplomatic meeting concludes. Source: BBC World.",
        })
        result = quality_check(item)
        self.assertFalse(result["quality_pass"])
        self.assertIn("single post has fewer than 3 sentences", result["quality_errors"])

    def test_major_disaster_is_breaking_below_generic_breaking_score(self):
        self.assertEqual(label(self._nepal_item()), "🚨 BREAKING")

    def test_major_disaster_update_remains_update(self):
        item = self._nepal_item()
        item["event_status"] = "UPDATE"
        self.assertEqual(label(item), "🔴 UPDATE")

    def test_different_nepal_headlines_corroborate_same_flood(self):
        item = self._nepal_item()
        item.update({"id": "a", "tier": 2})
        other = {
            "id": "b",
            "title": "Nearly 1,400 missing after Nepal-Tibet flash flood",
            "summary": "Rescuers continue searching for survivors.",
            "source": "Other Source",
            "tier": 2,
        }
        result = verify(item, [item, other])
        self.assertGreaterEqual(result["strong_corroboration"], 1)
        self.assertIn("Other Source", result["corroborating_source_names"])

    def test_generic_same_country_floods_do_not_auto_corroborate(self):
        item = {
            "id": "a",
            "title": "Floods kill 30 in northern India",
            "summary": "Emergency crews are responding.",
            "source": "Source A",
            "category": "disaster",
            "tier": 2,
        }
        other = {
            "id": "b",
            "title": "Floods kill 40 in southern India",
            "summary": "Emergency crews are responding.",
            "source": "Source B",
            "tier": 2,
        }
        result = verify(item, [item, other])
        self.assertEqual(result["strong_corroboration"], 0)

    def test_future_disaster_rescue_technology_is_not_a_major_disaster(self):
        item = {
            "title": "Cyborg cockroaches designed for rescue teams in major disasters",
            "summary": "Researchers are testing tiny robotic systems for future emergencies.",
            "category": "disaster",
            "source": "Technology Source",
            "primary_source": False,
            "strong_corroboration": 1,
            "confidence": "high",
            "priority_level": "URGENT",
            "score": 70,
        }
        self.assertFalse(is_verified_major_disaster(item))


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

    def test_video_game_physical_media_story_stays_technology(self):
        text = (
            "With the end of physical media in sight, gamers have had enough. "
            "Sony will cease physical game disc production and games will be digital products."
        )
        self.assertEqual(_category(text, "technology"), "technology")

    def test_contemporary_artist_obituary_is_entertainment_not_industry(self):
        text = (
            "An icon of contemporary art: a look back at the career of Yayoi Kusama. "
            "The celebrated contemporary artist has died aged 97, her company said."
        )
        self.assertEqual(_category(text, "world"), "entertainment")


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

    def test_un_news_app_navigation_is_removed(self):
        text = "UN News app users can follow here."
        self.assertEqual(strip_feed_boilerplate(text), "")
        self.assertTrue(has_rss_junk(text))

    def test_npr_multi_story_title_keeps_only_matching_first_headline(self):
        self.assertEqual(
            clean_title(
                "Meta reaches $17B settlement. And, nuclear regulator to abandon radiation safety rule"
            ),
            "Meta reaches $17B settlement",
        )

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


class SourceFilterTests(unittest.TestCase):
    def test_alert_level_is_extracted_only_from_explicit_prefix(self):
        self.assertEqual(
            extract_alert_level({"title": "Orange flood alert in China"}),
            "orange",
        )
        self.assertEqual(
            extract_alert_level({"title": "RED earthquake notification"}),
            "red",
        )
        self.assertIsNone(
            extract_alert_level({"title": "UN reports major flood damage"})
        )

    def test_feed_alert_filter_and_source_scan_limit_are_applied(self):
        now = datetime.now(timezone.utc).timetuple()
        entries = [
            {
                "title": "Green minor flood notification",
                "link": "https://example.com/green",
                "summary": "Low impact event.",
                "published_parsed": now,
            },
            {
                "title": "Orange major flood notification",
                "link": "https://example.com/orange",
                "summary": "Major impact event.",
                "published_parsed": now,
            },
            {
                "title": "Red severe earthquake notification",
                "link": "https://example.com/red",
                "summary": "Severe impact event.",
                "published_parsed": now,
            },
        ]
        parsed = SimpleNamespace(
            entries=entries,
            bozo=False,
            status=200,
        )
        feed = {
            "name": "GDACS Major Alerts",
            "url": "https://example.com/feed.xml",
            "category": "disaster",
            "primary": True,
            "tier": 1,
            "allowed_alert_levels": ["orange", "red"],
            "max_entries": 2,
        }

        with patch("src.collector.feedparser.parse", return_value=parsed):
            rows, health = fetch_one(feed, limit=25, max_age_hours=48)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["alert_level"], "orange")
        self.assertEqual(rows[0]["title"], "Orange major flood notification")
        self.assertEqual(health["entries_seen"], 3)
        self.assertEqual(health["recent_entries"], 1)

    def test_transient_empty_feed_failure_is_retried_once(self):
        now = datetime.now(timezone.utc).timetuple()
        failed = SimpleNamespace(
            entries=[],
            bozo=True,
            bozo_exception=TimeoutError("temporary timeout"),
            status=None,
        )
        recovered = SimpleNamespace(
            entries=[{
                "title": "Recovered source story",
                "link": "https://example.com/recovered",
                "summary": "A useful recovered source summary.",
                "published_parsed": now,
            }],
            bozo=False,
            status=200,
        )
        feed = {
            "name": "Transient Source",
            "url": "https://example.com/feed.xml",
            "category": "world",
            "primary": False,
            "tier": 2,
        }

        with patch(
            "src.collector.feedparser.parse",
            side_effect=[failed, recovered],
        ) as parse:
            rows, health = fetch_one(feed, limit=25, max_age_hours=48)

        self.assertEqual(parse.call_count, 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Recovered source story")
        self.assertIsNone(health["error"])

    def test_config_uses_lean_high_value_source_set(self):
        root = Path(__file__).resolve().parent.parent
        config = json.loads((root / "config.json").read_text())
        feeds = config["feeds"]
        names = [feed["name"] for feed in feeds]
        urls = [feed["url"] for feed in feeds]

        self.assertEqual(len(feeds), 26)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(urls), len(set(urls)))
        self.assertFalse(any(name.startswith("Google News ") for name in names))
        self.assertNotIn("NASA News Releases", names)
        self.assertIn("NASA News", names)
        self.assertIn("UN News", names)
        self.assertIn("GDACS Major Alerts", names)

        gdacs = next(feed for feed in feeds if feed["name"] == "GDACS Major Alerts")
        self.assertEqual(gdacs["allowed_alert_levels"], ["orange", "red"])
        self.assertGreaterEqual(gdacs["max_entries"], 100)
        self.assertTrue(gdacs["primary"])


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

    def test_spanish_museum_heist_cross_source_is_duplicate(self):
        conn = self._conn()
        try:
            event_id = self._seed(
                conn,
                "Thieves steal Bronze Age artifacts from Spanish museum in four-minute heist",
                "BBC World",
            )
            status, matched_event, _ = decide(
                conn,
                self._item(
                    "Thieves raid bronze age collection in ‘super-fast’ Spain museum heist",
                    "Al Jazeera",
                ),
            )
            self.assertEqual(status, "DUPLICATE")
            self.assertEqual(matched_event, event_id)
        finally:
            conn.close()

    def test_trade_war_opinion_word_lose_is_not_a_material_update(self):
        conn = self._conn()
        try:
            event_id = self._seed(
                conn,
                "How far will the US-Canada trade war go?",
                "Al Jazeera",
            )
            status, matched_event, _ = decide(
                conn,
                self._item(
                    "How to lose Trump’s trade war: test Canada’s resolve with threats to hockey sticks",
                    "The Guardian World",
                ),
            )
            self.assertEqual(status, "DUPLICATE")
            self.assertEqual(matched_event, event_id)
        finally:
            conn.close()

    def test_nepal_flood_reports_with_different_leads_share_one_event(self):
        conn = self._conn()
        reports = [
            (
                "France 24",
                "Nepal floods: Rescue teams search for over 1,300 missing",
                "After flash floods destroyed villages in a Himalayan valley on the Nepal-Tibet border, rescuers are searching for over 1,300 missing people.",
            ),
            (
                "NPR World",
                "Nearly 1,000 are missing in Nepal and Tibet after floods caused by a glacial collapse",
                "Mudslides in Tibet caused by a glacial collapse left hundreds missing while police in Nepal reported deaths.",
            ),
            (
                "The Guardian World",
                "Nearly 1,400 missing after Nepal-Tibet flash flood kills at least 356",
                "Entire villages were swept away after a glacial collapse triggered flooding in the mountainous border region.",
            ),
            (
                "Al Jazeera",
                "Satellite images show destruction from Nepal-Tibet floods",
                "A glacier collapse high in the Himalayas triggered catastrophic flooding along the Nepal-Tibet border.",
            ),
        ]
        event_ids = []
        try:
            for index, (source, title, summary) in enumerate(reports):
                item = self._item(title, source)
                item.update({
                    "summary": summary,
                    "category": "disaster",
                    "priority_score": 93,
                })
                status, event_id, _ = decide(conn, item)
                self.assertIn(status, {"NEW", "UPDATE", "DUPLICATE"})
                conn.execute(
                    "INSERT INTO stories(id, source, event_id) VALUES(?,?,?)",
                    (f"nepal-{index}", source, event_id),
                )
                conn.commit()
                event_ids.append(event_id)

            self.assertEqual(len(set(event_ids)), 1)
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

    def test_same_event_is_queued_only_once_per_run(self):
        candidates = [
            self._candidate(
                "nepal-a",
                "Nepal floods leave 1,300 missing",
                "disaster",
                "Source A",
                95,
            ),
            self._candidate(
                "finance-a",
                "Central bank announces emergency liquidity facility",
                "finance",
                "Source C",
                90,
            ),
            self._candidate(
                "nepal-b",
                "Nepal floods leave 1,400 missing",
                "disaster",
                "Source B",
                94,
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
                "max_stories_per_run": 5,
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
                    "urgency_terms": ["flood"] if item["_test_category"] == "disaster" else [],
                }

            def fake_verify(item, all_items):
                return {
                    "confidence": "high",
                    "strong_corroboration": 1,
                }

            def fake_priority(item):
                return {
                    "priority_score": item["_test_priority"],
                    "priority_level": "URGENT" if item["_test_category"] == "disaster" else "HIGH",
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

                queue = json.loads(news_main.QUEUE.read_text())
                disaster_posts = [
                    story for story in queue["stories"]
                    if story["category"] == "disaster"
                ]
                self.assertEqual(queue["count"], 2)
                self.assertEqual(len(disaster_posts), 1)

                with sqlite3.connect(news_main.DB) as conn:
                    event_ids = conn.execute(
                        "SELECT event_id FROM stories WHERE category='disaster'"
                    ).fetchall()
                    self.assertEqual(len(event_ids), 2)
                    self.assertEqual(len({row[0] for row in event_ids}), 1)
            finally:
                news_main.CONFIG = original_config
                news_main.DB = original_db
                news_main.QUEUE = original_queue
                news_main.SOURCE_HEALTH = original_health


if __name__ == "__main__":
    unittest.main()
