import re

from src.formatter import clean as clean_feed_text, format_story, label
from src.quality import quality_check
from src.priority import priority


def test_normal():
    item = {
        "title": "Central bank announces new policy",
        "summary": (
            "Officials said the policy will take effect next month "
            "and explained the expected changes. "
            "The central bank said the measure is intended to support "
            "economic stability."
        ),
        "source": "Test",
        "url": "https://example.com/a",
        "confidence": "medium",
        "score": 60,
        "primary_source": True,
        "strong_corroboration": 1,
        "corroborating_sources": 1,
        "event_status": "NEW",
        "event_id": "a",
        "language_status": "ENGLISH",
    }

    item.update(
        format_story(item)
    )

    r = quality_check(item)

    assert r["quality_pass"], r
    assert item["format"] == "single"


def test_urgent():
    item = {
        "title": "Major earthquake triggers tsunami warning",
        "summary": (
            "Emergency authorities are assessing the situation."
        ),
        "source": "Test",
        "url": "https://example.com/b",
        "confidence": "high",
        "score": 70,
        "primary_source": True,
        "strong_corroboration": 1,
        "corroborating_sources": 1,
        "event_status": "NEW",
        "event_id": "b",
        "language_status": "ENGLISH",
    }

    p = priority(item)

    assert p["priority_level"] == "IMMEDIATE", p


def test_no_confirmed_low_confidence():
    """
    Low-confidence stories may use the label
    UNCONFIRMED, but must never claim that something
    is confirmed.
    """

    item = {
        "format": "single",
        "post": (
            "⚠️ UNCONFIRMED: A major event may have happened. "
            "The report is not independently verified. "
            "Officials are assessing the available information. "
            "Source: Test."
        ),
        "url": "https://example.com",
        "confidence": "low",
        "language_status": "ENGLISH",
        "event_status": "NEW",
        "event_id": "c",
    }

    r = quality_check(item)

    assert r["quality_pass"], r

    # UNCONFIRMED is allowed.
    assert "UNCONFIRMED" in item["post"]

    # But the standalone word "confirmed" is not allowed.
    assert not re.search(
        r"\bconfirmed\b",
        item["post"].lower()
    )


def test_feed_entity_cleanup():
    cleaned = clean_feed_text(
        "Moon’s surface&#160;through <b>Artemis</b> hardware."
    )

    assert "&#160;" not in cleaned
    assert "\xa0" not in cleaned
    assert "<b>" not in cleaned
    assert cleaned == "Moon’s surface through Artemis hardware."


def test_update_label_is_preserved():
    item = {
        "score": 58,
        "confidence": "medium",
        "category": "politics",
        "primary_source": False,
        "strong_corroboration": 1,
        "event_status": "UPDATE",
        "urgency_terms": [],
    }

    assert label(item) == "🔴 UPDATE"


def test_breaking_label_for_verified_urgent_story():
    item = {
        "score": 82,
        "confidence": "medium",
        "category": "disaster",
        "primary_source": False,
        "strong_corroboration": 1,
        "event_status": "NEW",
        "urgency_terms": ["earthquake"],
    }

    assert label(item) == "🚨 BREAKING"


def test_embedded_truncation_is_rejected():
    item = {
        "format": "single",
        "post": (
            "📰 NEWS: NASA prepares Artemis hardware. "
            "The hardware must survive the. "
            "Source: NASA News Releases."
        ),
        "url": "https://example.com/nasa",
        "confidence": "high",
        "language_status": "ENGLISH",
    }

    result = quality_check(item)

    assert not result["quality_pass"], result
    assert any(
        "truncated" in error
        for error in result["quality_errors"]
    ), result


def test_encoded_entity_is_rejected():
    item = {
        "format": "single",
        "post": (
            "📰 NEWS: NASA prepares Artemis hardware. "
            "The hardware is being tested&#160;before launch. "
            "Source: NASA News Releases."
        ),
        "url": "https://example.com/nasa",
        "confidence": "high",
        "language_status": "ENGLISH",
    }

    result = quality_check(item)

    assert not result["quality_pass"], result
    assert any(
        "HTML entity" in error
        for error in result["quality_errors"]
    ), result


def test_formatter_cleans_pulsex_style_nasa_text():
    item = {
        "title": (
            "NASA’s Lunar Development and Test Facility "
            "Prepares Artemis Hardware for Moon"
        ),
        "summary": (
            "Before astronauts return to the Moon’s surface&#160;through "
            "NASA’s&#160;Artemis program, the hardware they depend on "
            "must first prove it can survive harsh lunar conditions. "
            "Engineers are testing systems before the mission."
        ),
        "source": "NASA News Releases",
        "url": "https://example.com/artemis",
        "confidence": "high",
        "score": 72,
        "primary_source": True,
        "strong_corroboration": 0,
        "corroborating_sources": 0,
        "event_status": "NEW",
        "event_id": "nasa-artemis",
        "language_status": "ENGLISH",
        "urgency_terms": [],
    }

    item.update(format_story(item))
    result = quality_check(item)

    assert result["quality_pass"], result
    assert "&#160;" not in item["post"]
    assert "\xa0" not in item["post"]
    assert " the. Source:" not in item["post"].lower()


def test_apod_navigation_summary_is_not_publishable():
    item = {
        "title": "APOD: 2026 August 8 – A Messier Moment for Tempel 2",
        "summary": (
            "APOD Science APOD APOD: 2026 August 8 – A Messier Moment "
            "for Tempel 2 Today’s APOD Archive Submissions Index Search "
            "Calendar RSS Education About Discuss APOD Astronomy Picture "
            "of the Day Discover the cosmos!"
        ),
        "source": "NASA News Releases",
        "url": "https://example.com/apod",
        "confidence": "high",
        "score": 70,
        "primary_source": True,
        "strong_corroboration": 0,
        "corroborating_sources": 0,
        "event_status": "NEW",
        "event_id": "apod-test",
        "language_status": "ENGLISH",
        "urgency_terms": [],
    }

    item.update(format_story(item))
    result = quality_check(item)
    public_text = item.get("post", "")

    assert "Today’s APOD" not in public_text
    assert "Discover the cosmos" not in public_text
    assert not result["quality_pass"], result


def test_cisa_summary_prefix_is_removed():
    item = {
        "title": "CPDLC over ATN-B1 Vulnerabilities",
        "summary": (
            "View CSAF Summary ATN-B1 CPDLC relies on legacy clear text "
            "unauthenticated radio frequency links. Attackers could abuse "
            "the weakness to interfere with communications."
        ),
        "source": "CISA Alerts",
        "url": "https://example.com/cisa",
        "confidence": "high",
        "score": 72,
        "primary_source": True,
        "strong_corroboration": 0,
        "corroborating_sources": 0,
        "event_status": "NEW",
        "event_id": "cisa-test",
        "language_status": "ENGLISH",
        "urgency_terms": [],
    }

    item.update(format_story(item))
    result = quality_check(item)

    assert "View CSAF Summary" not in item["post"]
    assert result["quality_pass"], result


def _story_for_context(title, summary, source="Test Source"):
    return {
        "title": title,
        "summary": summary,
        "source": source,
        "url": "https://example.com/story",
        "confidence": "high",
        "score": 70,
        "primary_source": True,
        "strong_corroboration": 0,
        "corroborating_sources": 0,
        "event_status": "NEW",
        "event_id": "context-regression",
        "language_status": "ENGLISH",
        "urgency_terms": [],
    }


def test_meta_runon_context_is_repaired_or_omitted():
    item = _story_for_context(
        "Meta recruits influencers to promote safety features",
        (
            "As Meta faces platform regulations, company recruits "
            "influencers to promote its safety features In July 2025. "
            "The campaign focuses on explaining new safety tools."
        ),
    )
    item.update(format_story(item))
    result = quality_check(item)

    assert result["quality_pass"], result
    assert ", company recruits" not in item["post"]
    assert "features In July" not in item["post"]
    assert "In July 2025." not in item["post"]


def test_marmot_runon_context_is_repaired():
    item = _story_for_context(
        "Marmot conservation receives unusual funding boost",
        (
            "Funding boost for conservation has recently materialized "
            "in form of new marmot-themed cryptocurrency Scientists "
            "who turned to OnlyFans to help save."
        ),
    )
    item.update(format_story(item))
    result = quality_check(item)

    assert result["quality_pass"], result
    assert "in form of" not in item["post"].lower()
    assert "cryptocurrency Scientists" not in item["post"]
    assert "Scientists who turned to OnlyFans to help save." not in item["post"]


def test_haiti_incomplete_context_is_not_publishable():
    item = _story_for_context(
        "Authorities respond to worsening conditions in Haiti",
        (
            "Security operations continued across the capital, as "
            "authorities seek."
        ),
    )
    item.update(format_story(item))
    result = quality_check(item)

    assert not result["quality_pass"], result
    assert "authorities seek." not in item["post"].lower()


def test_guardian_live_navigation_tail_is_removed():
    item = _story_for_context(
        "British-made drones used in deep strike campaign",
        (
            "Officials described the use of British-made drones in "
            "‘deep strike’ campaign UK politics live – latest updates Europe."
        ),
        source="The Guardian World",
    )
    item.update(format_story(item))
    result = quality_check(item)

    assert result["quality_pass"], result
    assert "latest updates" not in item["post"].lower()
    assert "deep strike’ campaign." in item["post"]
    assert "deep strike’." not in item["post"]
    assert not item["post"].endswith("Europe.")


def test_tupac_incomplete_context_is_not_publishable():
    item = _story_for_context(
        "Tupac-related trial draws renewed attention",
        (
            "The case became one of America's most anticipated trials got."
        ),
    )
    item.update(format_story(item))
    result = quality_check(item)

    assert not result["quality_pass"], result
    assert "trials got." not in item["post"].lower()


def test_clean_west_bank_style_context_still_passes():
    item = _story_for_context(
        "Officials announce new West Bank measures",
        (
            "Officials announced the measures after a security review. "
            "The changes are expected to take effect this week."
        ),
    )
    item.update(format_story(item))
    result = quality_check(item)

    assert result["quality_pass"], result


def main():
    test_normal()
    test_urgent()
    test_no_confirmed_low_confidence()
    test_feed_entity_cleanup()
    test_update_label_is_preserved()
    test_breaking_label_for_verified_urgent_story()
    test_embedded_truncation_is_rejected()
    test_encoded_entity_is_rejected()
    test_formatter_cleans_pulsex_style_nasa_text()
    test_apod_navigation_summary_is_not_publishable()
    test_cisa_summary_prefix_is_removed()
    test_meta_runon_context_is_repaired_or_omitted()
    test_marmot_runon_context_is_repaired()
    test_haiti_incomplete_context_is_not_publishable()
    test_guardian_live_navigation_tail_is_removed()
    test_tupac_incomplete_context_is_not_publishable()
    test_clean_west_bank_style_context_still_passes()

    print("REGRESSION TESTS PASSED")


if __name__ == "__main__":
    main()
