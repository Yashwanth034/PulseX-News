import html
import re

from src.emergency import is_verified_major_disaster

POST_LIMIT = 270

DANGLING_END_WORDS = {
    "a", "an", "the", "and", "or", "but", "because", "while",
    "during", "after", "before", "with", "without", "from", "to",
    "of", "in", "on", "at", "for", "as", "than", "that", "which",
    "who", "where", "when",
}

INCOMPLETE_FINAL_WORDS = {
    "got", "get", "gets", "getting", "seek", "seeks", "seeking",
    "save", "saves", "saving",
}

SUSPICIOUS_INTERNAL_STARTERS = (
    "Scientists", "Officials", "Authorities", "Researchers", "Police",
    "Meanwhile", "However",
)


def clean(text):
    """Decode entities, remove markup, and normalize public-facing text."""
    text = html.unescape(text or "")
    text = text.replace("\xa0", " ").replace("\u200b", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_sentence(text):
    """Clean text and ensure it ends as a complete sentence."""
    text = clean(text)

    if not text:
        return ""

    if text.endswith((".", "!", "?")):
        return text

    return text + "."


def split_sentences(text):
    """Return only source fragments that already end as real sentences."""
    text = clean(text)

    if not text:
        return []

    # Do not mistake common abbreviated month names for sentence endings in
    # dates such as "Aug. 11". Protect only the period immediately before a
    # numeric day, then restore it after splitting so public text is unchanged.
    month_period = "\ue000"
    protected = re.sub(
        r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.(?=\s+\d{1,2}\b)",
        lambda match: match.group(1) + month_period,
        text,
        flags=re.IGNORECASE,
    )

    parts = re.split(
        r"(?<=[.!?])\s+",
        protected
    )

    complete = []

    for part in parts:
        part = clean(part.replace(month_period, "."))

        if not part:
            continue

        if re.search(r"[.!?][\"')\]]?$", part):
            complete.append(part)

    return complete


def label(item, breaking_min_score=75):
    score = item.get("score", 0)
    confidence = item.get("confidence", "low")
    category = item.get("category", "")
    primary = item.get("primary_source", False)
    corroboration = item.get("strong_corroboration", 0)
    status = item.get("event_status", "NEW")

    # Never overstate low-confidence information.
    if confidence == "low":
        return "⚠️ UNCONFIRMED"

    # A meaningful development to an existing event should be
    # visibly identified as an update instead of collapsing back
    # into the generic NEWS label.
    if status == "UPDATE":
        return "🔴 UPDATE"

    verified = (
        primary
        or corroboration >= 1
    )

    # A verified major disaster is breaking even when a terse feed summary
    # keeps its generic article score below the normal breaking threshold.
    if is_verified_major_disaster(item):
        return "🚨 BREAKING"

    # Priority intelligence is stricter than the generic article score. Once
    # an event is independently verified as IMMEDIATE, do not downgrade its
    # public label merely because the generic score is under 75.
    if (
        item.get("priority_level") == "IMMEDIATE"
        and confidence in {"high", "medium"}
        and verified
    ):
        return "🚨 BREAKING"

    urgent_categories = {
        "conflict",
        "disaster",
        "politics",
        "finance",
        "health",
        "cybersecurity",
        "technology",
        "world",
    }

    urgent = bool(item.get("urgency_terms"))

    # One strong independent corroborating source plus the original
    # report is sufficient verification for a breaking label. Primary
    # sources remain independently eligible.
    if (
        score >= breaking_min_score
        and confidence in {"high", "medium"}
        and category in urgent_categories
        and urgent
        and verified
    ):
        return "🚨 BREAKING"

    if score >= 65:
        return "📰 NEWS"

    return "📰 DEVELOPING"


def make_headline_sentence(label_text, title):
    title = clean(title)

    if not title:
        return ""

    return clean_sentence(
        f"{label_text}: {title}"
    )


def make_source_sentence(source):
    source = clean(
        source or "Unknown"
    )

    return clean_sentence(
        f"Source: {source}"
    )


def strip_feed_boilerplate(text):
    """Remove known feed-navigation/promotional fragments from context."""
    text = clean(text)

    # Prefixes that describe a feed UI rather than the story itself.
    text = re.sub(
        r"^(?:View\s+CSAF\s+Summary\s+)+",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Some broadcast feeds prepend a presenter/guest introduction before the
    # actual report. Remove only a proper-name-led "is pleased to welcome"
    # sentence and keep the substantive sentences that follow.
    text = re.sub(
        r"^(?:[A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){0,3})\s+"
        r"is\s+pleased\s+to\s+welcome\b[^.!?]*[.!?]\s*",
        "",
        text,
    )

    # NASA APOD feeds can expose a navigation-card summary rather than
    # article prose. Publishing that metadata is worse than holding the
    # story for lack of usable context.
    if re.match(
        r"^(?:APOD\s+Science\s+)?APOD(?:\s+APOD)?\s*:",
        text,
        flags=re.IGNORECASE,
    ):
        return ""

    # Once these navigation/promotional fragments begin, the remainder
    # is not useful story context and should never reach a public post.
    tail_markers = [
        r"\bToday[’']s\s+APOD\b",
        r"\bArchive\s+Submissions\s+Index\s+Search\s+Calendar\s+RSS\b",
        r"\bAstronomy\s+Picture\s+of\s+the\s+Day\s+Discover\s+the\s+cosmos\b",
        r"\bContinue\s+reading\b",
        r"\bSign\s+up\s+to\s+our\s+newsletter\b",
        r"\bSubscribe\s+to\s+our\s+newsletter\b",
        r"\bFollow\s+our\s+liveblog\s+for\s+the\s+latest\s+updates\b",
        r"\b(?:UN\s+News\s+)?app\s+users\s+can\s+follow\s+here\b",
        r"\bWe\s+are\s+aiming,?\s+of\s+course,?\s+to\s+inform\s+public\s+policy\s+debate\b",
        r"\b(?:(?-i:[A-Z])[A-Za-z]+(?:\s+[A-Za-z]+){0,4})\s+live\s+[–-]\s+latest\s+updates\b",
    ]

    for marker in tail_markers:
        text = re.split(
            marker,
            text,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]

    return clean(text)


def normalize_context_prose(text):
    """Apply only conservative grammar repairs before sentence selection."""
    text = clean(text)

    # Common malformed RSS wording seen in otherwise useful prose.
    text = re.sub(
        r"\bin\s+form\s+of\b",
        "in the form of",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"([,;])\s+company\b",
        r"\1 the company",
        text,
        flags=re.IGNORECASE,
    )

    # Some feeds concatenate a new sentence without punctuation.
    starters = "|".join(SUSPICIOUS_INTERNAL_STARTERS)
    text = re.sub(
        rf"(?<=[a-z0-9])\s+(?=(?:{starters})\b)",
        ". ",
        text,
    )

    # A capitalized date transition embedded after prose is also a
    # frequent sign that two feed fragments were concatenated.
    months = (
        "January|February|March|April|May|June|July|August|"
        "September|October|November|December"
    )
    text = re.sub(
        rf"(?<=[a-z0-9])\s+(?=In\s+(?:{months}|20\d{{2}})\b)",
        ". ",
        text,
    )

    return clean(text)


def is_usable_context_sentence(sentence):
    """Reject source fragments that are structurally unsafe to publish."""
    sentence = clean(sentence)

    if not sentence or not re.search(r"[.!?][\"')\]]?$", sentence):
        return False

    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", sentence)

    # Tiny fragments such as "In July 2025." or "Europe." are feed
    # metadata/context stubs, not explanatory news sentences.
    if len(words) < 5:
        return False

    stripped = re.sub(r"[.!?\"')\]]+$", "", sentence).rstrip()
    final_word = re.findall(r"[A-Za-z]+", stripped.lower())

    if final_word and final_word[-1] in (
        DANGLING_END_WORDS | INCOMPLETE_FINAL_WORDS
    ):
        return False

    # If a source still contains an obvious capitalized sentence starter
    # in the middle, do not publish the run-on fragment.
    starters = "|".join(SUSPICIOUS_INTERNAL_STARTERS)
    if re.search(
        rf"[a-z0-9]\s+(?:{starters})\b",
        sentence,
    ):
        return False

    if re.search(
        r"\blive\s+[–-]\s+latest\s+updates\b",
        sentence,
        flags=re.IGNORECASE,
    ):
        return False

    return True


def extract_context_sentences(summary, max_sentences=2):
    """
    Extract useful context without inventing facts.

    Normal RSS summaries are split into sentences.

    Some feeds provide badly formatted summaries with no sentence
    punctuation. For those, we use safe clause boundaries instead
    of returning an empty post.
    """
    summary = normalize_context_prose(
        strip_feed_boilerplate(summary)
    )

    if not summary:
        return []

    # First try normal sentence splitting and keep only context that is
    # structurally safe to publish.
    sentences = [
        sentence
        for sentence in split_sentences(summary)
        if is_usable_context_sentence(sentence)
    ]

    if sentences:
        return sentences[:max_sentences]

    # ---------------------------------------------------------
    # Handle badly punctuated RSS summaries.
    #
    # Common patterns:
    #   "... subversive: to encourage users ..."
    #   "... birding On a brilliantly bright afternoon ..."
    #
    # We split at safe textual boundaries.
    # ---------------------------------------------------------

    clauses = []

    # Split on colon when the text before/after it is useful.
    colon_parts = re.split(
        r":\s+",
        summary,
        maxsplit=1
    )

    if len(colon_parts) == 2:
        first = clean_sentence(
            colon_parts[0]
        )
        second = clean_sentence(
            colon_parts[1]
        )

        if first and is_usable_context_sentence(first):
            clauses.append(first)

        if second and is_usable_context_sentence(second):
            clauses.append(second)

    # If we still don't have enough context, look for a clear
    # transition such as " On a..." / " With..." / " The..."
    if len(clauses) < max_sentences:
        transition_parts = re.split(
            r"\s+(?=(?:On|With|The|In|As|After|Before)\s+[A-Z])",
            summary
        )

        for part in transition_parts:
            part = clean_sentence(part)

            if (
                part
                and part not in clauses
                and is_usable_context_sentence(part)
            ):
                clauses.append(part)

    # Do not turn an arbitrary unpunctuated feed fragment into a public
    # sentence merely by adding a period. If no trustworthy context was
    # found, return no context and let the quality gate reject the story.
    return clauses[:max_sentences]


def shorten_to_words(text, limit):
    """
    Shorten text at a word boundary and avoid dangling function words.

    Never cuts a word in half, and it will not deliberately finish a
    shortened public sentence with fragments such as "the", "of", or
    "with".
    """
    text = clean(text)

    if len(text) <= limit:
        return text

    words = text.split()
    result = []

    for word in words:
        candidate = (
            word
            if not result
            else " ".join(result + [word])
        )

        if len(candidate) > limit:
            break

        result.append(word)

    if not result:
        return ""

    while (
        len(result) > 4
        and result[-1].strip(".,!?;:'\"()[]").lower()
        in DANGLING_END_WORDS
    ):
        result.pop()

    return " ".join(result).rstrip(" ,;:-")


def build_single_post(
    item,
    breaking_min_score=75
):
    """
    Build a safe single X post.

    Target:
        headline
        + useful context
        + optional second context
        + source

    Maximum:
        POST_LIMIT characters.

    The function MUST return a non-empty post whenever
    a title exists.
    """

    title = clean(
        item.get("title", "")
    )

    if not title:
        return ""

    source = clean(
        item.get(
            "source",
            "Unknown"
        )
    )

    lab = label(
        item,
        breaking_min_score
    )

    headline = make_headline_sentence(
        lab,
        title
    )

    source_sentence = make_source_sentence(
        source
    )

    summary = clean(
        item.get(
            "summary",
            ""
        )
    )

    context = extract_context_sentences(
        summary,
        4
    )

    # ---------------------------------------------------------
    # Candidate 1:
    #
    # Headline + 2 COMPLETE context sentences + source.
    # Try later sentences when the first sentence is unusually long.
    # ---------------------------------------------------------

    if len(context) >= 2:
        for first_index in range(len(context) - 1):
            for second_index in range(first_index + 1, len(context)):
                post = " ".join([
                    headline,
                    context[first_index],
                    context[second_index],
                    source_sentence,
                ])

                if len(post) <= POST_LIMIT:
                    return post

    # ---------------------------------------------------------
    # Candidate 2:
    #
    # Headline + 1 COMPLETE context sentence + source.
    # Do not manufacture a sentence by cutting prose at an arbitrary
    # word boundary; choose another complete source sentence instead.
    # ---------------------------------------------------------

    for context_text in context:
        post = " ".join([
            headline,
            context_text,
            source_sentence,
        ])

        if len(post) <= POST_LIMIT:
            return post

    # ---------------------------------------------------------
    # Candidate 3:
    #
    # Headline + source.
    #
    # This guarantees that a valid title never produces
    # an empty post.
    # ---------------------------------------------------------

    post = " ".join([
        headline,
        source_sentence,
    ])

    if len(post) <= POST_LIMIT:
        return post

    # ---------------------------------------------------------
    # Candidate 4:
    #
    # Extremely long headline.
    #
    # Keep the source and shorten the headline at a
    # word boundary.
    # ---------------------------------------------------------

    available = (
        POST_LIMIT
        - len(source_sentence)
        - 1
    )

    shortened_headline = shorten_to_words(
        headline,
        available
    )

    if shortened_headline:

        shortened_headline = clean_sentence(
            shortened_headline
        )

        post = " ".join([
            shortened_headline,
            source_sentence,
        ])

        if len(post) <= POST_LIMIT:
            return post

    # ---------------------------------------------------------
    # Final emergency fallback.
    #
    # A source/title story must never become an empty post.
    # ---------------------------------------------------------

    return shorten_to_words(
        headline,
        POST_LIMIT
    )


def choose_format(item):
    """
    Use a thread only when there is enough information
    to justify one.
    """

    summary_length = len(
        item.get(
            "summary",
            ""
        )
    )

    score = item.get(
        "score",
        0
    )

    corroboration = item.get(
        "strong_corroboration",
        0
    )

    status = item.get(
        "event_status",
        "NEW"
    )

    if (
        status == "UPDATE"
        and score >= 85
        and summary_length > 500
    ):
        return "thread"

    if (
        score >= 92
        and corroboration >= 2
        and summary_length > 500
    ):
        return "thread"

    return "single"


def build_thread(
    item,
    breaking_min_score=75
):
    """
    Build a small thread.

    Every post:
        - is non-empty
        - stays within POST_LIMIT
        - contains complete words
    """

    title = clean(
        item.get("title", "")
    )

    if not title:
        return []

    source = clean(
        item.get(
            "source",
            "Unknown"
        )
    )

    lab = label(
        item,
        breaking_min_score
    )

    first = make_headline_sentence(
        lab,
        title
    )

    context = extract_context_sentences(
        item.get("summary", ""),
        5
    )

    posts = [first]
    current = ""

    for sentence in context:

        sentence = clean_sentence(
            sentence
        )

        if not sentence:
            continue

        if len(sentence) > POST_LIMIT:
            sentence = shorten_to_words(
                sentence,
                POST_LIMIT
            )
            sentence = clean_sentence(
                sentence
            )

        candidate = (
            sentence
            if not current
            else f"{current} {sentence}"
        )

        if len(candidate) <= POST_LIMIT:
            current = candidate
        else:
            if current:
                posts.append(
                    current
                )

            current = sentence

    if current:
        posts.append(
            current
        )

    # ---------------------------------------------------------
    # Add source to final post.
    # ---------------------------------------------------------

    source_line = make_source_sentence(
        source
    )

    if posts:

        candidate = (
            f"{posts[-1]} "
            f"{source_line}"
        )

        if len(candidate) <= POST_LIMIT:
            posts[-1] = candidate
        else:
            posts.append(
                source_line
            )

    # Remove empty posts.
    posts = [
        clean(post)
        for post in posts
        if clean(post)
    ]

    # Absolute fallback.
    if not posts:
        posts = [
            shorten_to_words(
                first,
                POST_LIMIT
            )
        ]

    return posts


def format_story(
    item,
    breaking_min_score=75
):
    """
    Main formatter entry point.
    """

    chosen_format = choose_format(
        item
    )

    if chosen_format == "thread":

        thread = build_thread(
            item,
            breaking_min_score
        )

        # Never create an empty thread.
        if thread:
            return {
                "format": "thread",
                "thread": thread,
            }

        # Fall back to single post if thread
        # construction fails.
        return {
            "format": "single",
            "post": build_single_post(
                item,
                breaking_min_score
            ),
        }

    return {
        "format": "single",
        "post": build_single_post(
            item,
            breaking_min_score
        ),
    }
