import hashlib
import re
import unicodedata
from datetime import datetime, timezone, timedelta


def init_events(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS events(
        event_id TEXT PRIMARY KEY,
        canonical_title TEXT NOT NULL,
        category TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        major INTEGER DEFAULT 0,
        queued_count INTEGER DEFAULT 0,
        canonical_summary TEXT DEFAULT ''
    )
    """)

    # Upgrade existing databases created before
    # canonical_summary was added.
    columns = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(events)"
        ).fetchall()
    }

    if "canonical_summary" not in columns:
        conn.execute(
            """
            ALTER TABLE events
            ADD COLUMN canonical_summary TEXT DEFAULT ''
            """
        )

    conn.commit()


_EVENT_STOPWORDS = {
    "a", "an", "the", "to", "for", "of", "in", "on", "at", "by",
    "from", "with", "and", "or", "as", "during", "amid", "after",
    "before", "over", "under", "into", "about", "why",
}

_EVENT_ALIASES = {
    "elections": "election",
    "wartime": "war",
    "sacked": "removed",
    "ousted": "removed",
    "dismissed": "removed",
    "ukrainian": "ukraine",
    "russian": "russia",
    "algerian": "algeria",
}

_WEAK_EVENT_TOKENS = {
    "again", "latest", "least", "more", "new", "news", "report", "reported",
    "say", "says", "said", "people", "person", "official", "officials",
    "through", "come", "comes", "coming", "dead", "die", "died", "kill",
    "killed", "injure", "injured", "hit", "hits",
}

_EVENT_SIGNAL_TOKENS = {
    "attack", "airstrike", "blast", "ceasefire", "collapse", "coup", "crash",
    "drone", "earthquake", "eruption", "explosion", "fire", "flood",
    "hurricane", "invasion", "missile", "outbreak", "recall", "strike",
    "tsunami", "wildfire", "war", "heist", "robbery", "theft",
}

_MATERIAL_UPDATE_TERMS = {
    "arrest", "arrested", "charge", "charged", "confirm", "confirmed",
    "die", "died", "kill", "killed", "injure", "injured", "resign",
    "resigned", "appoint", "appointed", "approve", "approved", "reject",
    "rejected", "sign", "signed", "launch", "launched", "strike",
    "strikes", "withdraw", "withdrew", "reopen", "reopened", "close",
    "closed", "raise", "raised", "rise", "rises", "increase", "increased",
    "cut", "cuts",
}


def _tokens(text):
    """Return normalized content tokens for cross-source event matching."""
    normalized = unicodedata.normalize(
        "NFKC",
        text or ""
    ).replace("’", "'").lower()

    # Treat hyphenated place/event phrases as ordinary words for matching.
    # This lets `Nepal-Tibet floods` align with `Nepal and Tibet ... floods`
    # without weakening thresholds for unrelated events.
    normalized = re.sub(r"[-‐‑‒–—]+", " ", normalized)

    tokens = set()

    for word in re.findall(
        r"[a-z0-9][a-z0-9'-]*",
        normalized
    ):
        if word.endswith("'s") and len(word) > 2:
            word = word[:-2]

        # Very small morphology normalization keeps the matcher
        # deterministic while allowing headlines such as election/elections
        # and call/calls to compare as the same event wording.
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif (
            len(word) > 4
            and word.endswith("s")
            and not word.endswith("ss")
        ):
            word = word[:-1]

        word = _EVENT_ALIASES.get(
            word,
            word
        )

        if (
            word
            and word not in _EVENT_STOPWORDS
        ):
            tokens.add(word)

    return tokens


def _numbers(text):
    return set(
        re.findall(
            r"\b\d+(?:\.\d+)?%?\b",
            text or ""
        )
    )


def _material_title_update(new_title, canonical_title):
    """Detect a concrete event change, not merely different wording."""
    new_numbers = _numbers(new_title)
    old_numbers = _numbers(canonical_title)

    # A genuinely new figure (for example a rising death toll) is meaningful.
    # Merely omitting an older headline figure is not an update; otherwise a
    # shorter parallel headline can be reposted after a more detailed one.
    if (
        new_numbers
        and old_numbers
        and (new_numbers - old_numbers)
    ):
        return True

    new_tokens = _tokens(new_title)
    old_tokens = _tokens(canonical_title)

    return bool(
        (new_tokens - old_tokens)
        & _MATERIAL_UPDATE_TERMS
    )


def _sim(a, b):
    aa = _tokens(a)
    bb = _tokens(b)

    if not aa or not bb:
        return 0.0

    return len(aa & bb) / max(
        1,
        len(aa | bb)
    )


def _anchor_match(a, b):
    """Detect the same event when outlets use substantially different wording.

    We require at least two meaningful shared anchors and either a shared event
    signal (attack/wildfire/etc.) or an identical numeric fact. This is much
    narrower than lowering the global Jaccard threshold and avoids merging
    unrelated stories merely because they share a country or generic verb.
    """
    aa = _tokens(a)
    bb = _tokens(b)
    shared = {
        token for token in (aa & bb)
        if token not in _WEAK_EVENT_TOKENS
        and not token.isdigit()
    }

    if len(shared) < 2:
        return False

    shared_signal = bool(shared & _EVENT_SIGNAL_TOKENS)
    shared_numbers = bool(_numbers(a) & _numbers(b))

    # At least one shared token must be a reasonably distinctive anchor rather
    # than only generic incident vocabulary.
    distinctive = {
        token for token in shared
        if token not in _EVENT_SIGNAL_TOKENS
        and len(token) >= 4
    }

    return (
        (shared_numbers and bool(distinctive))
        or (shared_signal and len(distinctive) >= 2)
    )


def _combined_anchor_match(item, canonical_title, canonical_summary):
    """Compare event anchors across headline + explanatory context.

    Headline-only matching can fragment one major event when one outlet leads
    with impact figures while another leads with imagery, rescue work, or the
    cause. Context is used only through the existing conservative anchor rule.
    """
    incoming = " ".join(
        part for part in (
            item.get("title", ""),
            item.get("summary", ""),
        )
        if part
    )
    canonical = " ".join(
        part for part in (
            canonical_title,
            canonical_summary,
        )
        if part
    )
    return _anchor_match(incoming, canonical)


def _new_id(title):
    return hashlib.sha256(
        title.strip().lower().encode()
    ).hexdigest()[:24]


def _same_event_source(
    conn,
    event_id,
    source
):
    """
    Check whether this source has already produced
    a story belonging to this event.
    """
    if not source:
        return False

    row = conn.execute(
        """
        SELECT 1
        FROM stories
        WHERE event_id=? AND source=?
        LIMIT 1
        """,
        (
            event_id,
            source
        )
    ).fetchone()

    return row is not None


def _meaningful_update(
    item,
    canonical_title,
    canonical_summary,
    event_anchor=False,
):
    """
    Determine whether the incoming story contains
    meaningful new information compared with the
    existing event coverage.

    This intentionally uses a conservative rule.
    """
    new_title = item.get(
        "title",
        ""
    )

    new_summary = item.get(
        "summary",
        ""
    )

    title_similarity = _sim(
        new_title,
        canonical_title
    )

    summary_similarity = _sim(
        new_summary,
        canonical_summary
    )

    # High headline similarity or a strong entity/topic anchor across different
    # sources normally means parallel coverage of the same event, even when the
    # wording differs substantially. Publish only when the incoming headline
    # carries a concrete new fact, not merely a different phrasing.
    if (
        title_similarity >= 0.62
        or event_anchor
        or _anchor_match(new_title, canonical_title)
    ):
        return _material_title_update(
            new_title,
            canonical_title
        )

    # For less-similar headlines that still matched the same event, retain
    # the existing conservative update behavior. This preserves real
    # developments while suppressing the common cross-source duplicate case.
    if title_similarity < 0.55:
        return True

    if (
        new_summary
        and canonical_summary
        and summary_similarity < 0.55
    ):
        return True

    return False


def decide(
    conn,
    item,
    memory_hours=48,
    major_memory_hours=168
):
    now = datetime.now(
        timezone.utc
    )

    rows = conn.execute(
        """
        SELECT
            event_id,
            canonical_title,
            first_seen,
            last_seen,
            major,
            queued_count,
            canonical_summary
        FROM events
        """
    ).fetchall()

    best = None
    best_sim = 0.0
    best_anchor = False
    best_strength = 0.0

    for row in rows:
        (
            event_id,
            canonical,
            first_seen,
            last_seen,
            major,
            queued_count,
            canonical_summary,
        ) = row

        try:
            last = datetime.fromisoformat(
                last_seen.replace(
                    "Z",
                    "+00:00"
                )
            )
        except Exception:
            continue

        hours = (
            major_memory_hours
            if major
            else memory_hours
        )

        if (
            now - last
        ).total_seconds() > hours * 3600:
            continue

        incoming_title = item.get("title", "")
        sim = _sim(
            incoming_title,
            canonical
        )
        anchored = (
            _anchor_match(
                incoming_title,
                canonical
            )
            or _combined_anchor_match(
                item,
                canonical,
                canonical_summary,
            )
        )
        strength = sim + (0.35 if anchored else 0.0)

        if strength > best_strength:
            best_strength = strength
            best_sim = sim
            best_anchor = anchored
            best = row

    # ---------------------------------------------------------
    # No sufficiently similar recent event.
    # ---------------------------------------------------------
    if (
        not best
        or (
            best_sim < 0.45
            and not best_anchor
        )
    ):
        event_id = _new_id(
            item.get("title", "")
            + "|"
            + item.get("source", "")
        )

        conn.execute(
            """
            INSERT OR REPLACE INTO events(
                event_id,
                canonical_title,
                category,
                first_seen,
                last_seen,
                major,
                queued_count,
                canonical_summary
            )
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                event_id,
                item.get(
                    "title",
                    ""
                ),
                item.get(
                    "category",
                    "world"
                ),
                now.isoformat(),
                now.isoformat(),
                int(
                    item.get(
                        "priority_score",
                        item.get(
                            "score",
                            0
                        )
                    ) >= 85
                ),
                0,
                item.get(
                    "summary",
                    ""
                ),
            )
        )

        return (
            "NEW",
            event_id,
            1.0
        )

    (
        event_id,
        canonical,
        first_seen,
        last_seen,
        major,
        queued_count,
        canonical_summary,
    ) = best

    source = item.get(
        "source",
        ""
    )

    # ---------------------------------------------------------
    # Same source + same event.
    # ---------------------------------------------------------
    if _same_event_source(
        conn,
        event_id,
        source
    ):
        conn.execute(
            """
            UPDATE events
            SET last_seen=?,
                major=MAX(major,?)
            WHERE event_id=?
            """,
            (
                now.isoformat(),
                int(
                    item.get(
                        "priority_score",
                        item.get(
                            "score",
                            0
                        )
                    ) >= 85
                ),
                event_id,
            )
        )

        return (
            "DUPLICATE",
            event_id,
            best_sim
        )

    # ---------------------------------------------------------
    # Different source covering the same event.
    #
    # Only call it UPDATE if the incoming report contains
    # meaningfully different information.
    # ---------------------------------------------------------
    if _meaningful_update(
        item,
        canonical,
        canonical_summary,
        event_anchor=best_anchor,
    ):
        conn.execute(
            """
            UPDATE events
            SET
                last_seen=?,
                major=MAX(major,?),
                canonical_title=?,
                canonical_summary=?
            WHERE event_id=?
            """,
            (
                now.isoformat(),
                int(
                    item.get(
                        "priority_score",
                        item.get(
                            "score",
                            0
                        )
                    ) >= 85
                ),
                item.get(
                    "title",
                    canonical
                ),
                item.get(
                    "summary",
                    canonical_summary
                ),
                event_id,
            )
        )

        return (
            "UPDATE",
            event_id,
            best_sim
        )

    # ---------------------------------------------------------
    # Same event, different source, but no meaningful
    # new information.
    #
    # Do not repost it.
    # ---------------------------------------------------------
    conn.execute(
        """
        UPDATE events
        SET
            last_seen=?,
            major=MAX(major,?)
        WHERE event_id=?
        """,
        (
            now.isoformat(),
            int(
                item.get(
                    "priority_score",
                    item.get(
                        "score",
                        0
                    )
                ) >= 85
            ),
            event_id,
        )
    )

    return (
        "DUPLICATE",
        event_id,
        best_sim
    )


def mark_queued(
    conn,
    event_id
):
    conn.execute(
        """
        UPDATE events
        SET queued_count=queued_count+1
        WHERE event_id=?
        """,
        (event_id,)
    )


def purge_expired(
    conn,
    story_memory_hours=48,
    memory_hours=48,
    major_memory_hours=168
):
    """
    Delete only records whose retention period has elapsed.

    - Individual stories expire after story_memory_hours.
    - Normal events expire after memory_hours.
    - Major events expire after major_memory_hours.

    Timestamp-based, idempotent, and safe to run on
    every collection cycle (including every 5 minutes
    in GitHub Actions). Active records are never touched.
    """
    now = datetime.now(
        timezone.utc
    )

    story_cutoff = (
        now - timedelta(
            hours=story_memory_hours
        )
    ).isoformat()

    event_cutoff = (
        now - timedelta(
            hours=memory_hours
        )
    ).isoformat()

    major_cutoff = (
        now - timedelta(
            hours=major_memory_hours
        )
    ).isoformat()

    stories_expired = conn.execute(
        """
        DELETE FROM stories
        WHERE first_seen < ?
        """,
        (story_cutoff,)
    ).rowcount

    normal_events_expired = conn.execute(
        """
        DELETE FROM events
        WHERE major=0 AND last_seen < ?
        """,
        (event_cutoff,)
    ).rowcount

    major_events_expired = conn.execute(
        """
        DELETE FROM events
        WHERE major=1 AND last_seen < ?
        """,
        (major_cutoff,)
    ).rowcount

    conn.commit()

    return {
        "stories_expired": stories_expired,
        "normal_events_expired": normal_events_expired,
        "major_events_expired": major_events_expired,
    }
