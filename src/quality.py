import re


POST_LIMIT = 270

DANGLING_ENDINGS = (
    " and", " or", " but", " because", " while", " during",
    " after", " before", " with", " without", " from", " to",
    " of", " in", " on", " at", " for", " as", " than", " that",
    " which", " who", " where", " when", " an", " a", " the",
)

HTML_ENTITY_RE = re.compile(
    r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);",
    re.IGNORECASE,
)

INCOMPLETE_FINAL_WORDS = {
    "got", "get", "gets", "getting", "seek", "seeks", "seeking",
    "save", "saves", "saving",
}

SUSPICIOUS_INTERNAL_STARTERS = (
    "Scientists", "Officials", "Authorities", "Researchers", "Police",
    "Meanwhile", "However",
)


# ---------------------------------------------------------
# Sentence helpers
# ---------------------------------------------------------

def sentence_count(text):
    """
    Count complete sentences.

    A sentence is considered complete when it ends with
    ., !, or ?.
    """

    text = (text or "").strip()

    if not text:
        return 0

    matches = re.findall(
        r"[.!?](?:\s+|$)",
        text
    )

    return len(matches)


def ends_with_sentence_punctuation(text):
    """
    Check whether text ends naturally as a complete sentence.
    """

    text = (text or "").strip()

    if not text:
        return False

    return bool(
        re.search(
            r"[.!?][\"')\]]?$",
            text
        )
    )


def has_source(text):
    """
    Check that the public post contains a source attribution.
    """

    return "Source:" in (
        text or ""
    )


def has_rss_junk(text):
    """
    Detect common RSS/article-page fragments that should
    never appear in a public X post.
    """

    text = (text or "").lower()

    junk_patterns = [
        r"\bcontinue reading\b",
        r"\bget our breaking news email\b",
        r"\bfree app or daily news podcast\b",
        r"\bsign up to our newsletter\b",
        r"\bsubscribe to our newsletter\b",
        r"\bread more\b",
        r"\bfollow us on\b",
        r"\bdownload our app\b",
        r"\blisten to our podcast\b",
        r"\btoday[’']s apod\b",
        r"\barchive submissions index search calendar rss\b",
        r"\bdiscover the cosmos\b",
        r"\bfollow our liveblog for the latest updates\b",
        r"\bis pleased to welcome\b",
        r"\bwe are aiming,? of course,? to inform public policy debate\b",
    ]

    for pattern in junk_patterns:
        if re.search(
            pattern,
            text
        ):
            return True

    return False


def has_html_entity(text):
    """Reject encoded HTML entities that would leak into a public post."""
    return bool(HTML_ENTITY_RE.search(text or ""))


def has_unsafe_prose_fragment(text):
    """Detect malformed source prose that punctuation checks alone miss."""
    text = text or ""

    if re.search(r"\bin\s+form\s+of\b", text, flags=re.IGNORECASE):
        return True

    if re.search(r"[,;]\s+company\b", text, flags=re.IGNORECASE):
        return True

    if re.search(
        r"\blive\s+[–-]\s+latest\s+updates\b",
        text,
        flags=re.IGNORECASE,
    ):
        return True

    starters = "|".join(SUSPICIOUS_INTERNAL_STARTERS)
    if re.search(
        rf"[a-z0-9]\s+(?:{starters})\b",
        text,
    ):
        return True

    for sentence in re.split(r"(?<=[.!?])\s+", text):
        stripped = re.sub(
            r"[.!?\"')\]]+$",
            "",
            sentence.strip().lower(),
        ).rstrip()
        words = re.findall(r"[a-z]+", stripped)

        if words and words[-1] in INCOMPLETE_FINAL_WORDS:
            return True

    return False


def looks_truncated(text):
    """
    Detect obvious cut-offs in every public sentence, not just the
    final sentence of the whole post.

    This catches failures such as "must survive the. Source: NASA.",
    where the old implementation only inspected the final source line.
    """

    text = (text or "").strip()

    if not text:
        return True

    if not ends_with_sentence_punctuation(text):
        return True

    sentences = [
        part.strip()
        for part in re.split(
            r"(?<=[.!?])\s+",
            text,
        )
        if part.strip()
    ]

    for sentence in sentences:
        lowered = sentence.lower()
        stripped = re.sub(
            r"[.!?\"')\]]+$",
            "",
            lowered,
        ).rstrip()

        for ending in DANGLING_ENDINGS:
            if stripped.endswith(ending):
                return True

    return False


# ---------------------------------------------------------
# Quality check
# ---------------------------------------------------------

def quality_check(item):
    """
    Final safety and quality gate for formatted stories.

    Single:
        - 1 post
        - <= 270 characters
        - 3 to 4 complete sentences
        - source included
        - no RSS junk
        - no obvious truncation

    Thread:
        - 1 to 7 posts
        - every post <= 270 characters
        - every post is complete
        - final post contains source
        - no RSS junk
        - no obvious truncation

    Both:
        - final language must be English
        - source URL must be valid
        - low-confidence stories cannot claim confirmation
    """

    errors = []
    warnings = []

    fmt = item.get(
        "format"
    )

    # -----------------------------------------------------
    # SINGLE POST
    # -----------------------------------------------------

    if fmt == "single":

        post = (
            item.get(
                "post",
                ""
            )
            or ""
        ).strip()

        if not post:
            errors.append(
                "empty post"
            )

        else:

            # Character limit.
            if len(post) > POST_LIMIT:
                errors.append(
                    f"single post exceeds "
                    f"{POST_LIMIT} characters"
                )

            # Sentence count.
            count = sentence_count(
                post
            )

            if count < 3:
                errors.append(
                    "single post has fewer "
                    "than 3 sentences"
                )

            if count > 4:
                errors.append(
                    "single post has more "
                    "than 4 sentences"
                )

            # Source.
            if not has_source(
                post
            ):
                errors.append(
                    "single post missing source"
                )

            # RSS junk.
            if has_rss_junk(
                post
            ):
                errors.append(
                    "single post contains "
                    "RSS/article-page junk"
                )

            # Encoded HTML entities must never leak to X.
            if has_html_entity(post):
                errors.append(
                    "single post contains encoded HTML entity"
                )

            if has_unsafe_prose_fragment(post):
                errors.append(
                    "single post contains malformed or incomplete prose"
                )

            # Truncation.
            if looks_truncated(
                post
            ):
                errors.append(
                    "single post appears "
                    "truncated or incomplete"
                )

    # -----------------------------------------------------
    # THREAD
    # -----------------------------------------------------

    elif fmt == "thread":

        thread = item.get(
            "thread",
            []
        )

        if not thread:
            errors.append(
                "empty thread"
            )

        if len(thread) > 7:
            errors.append(
                "thread too long"
            )

        for i, post in enumerate(
            thread,
            1
        ):

            post = (
                post or ""
            ).strip()

            if not post:
                errors.append(
                    f"thread post {i} is empty"
                )
                continue

            # Character limit.
            if len(post) > POST_LIMIT:
                errors.append(
                    f"thread post {i} exceeds "
                    f"{POST_LIMIT} characters"
                )

            # Every thread post must contain
            # at least one complete sentence.
            if sentence_count(
                post
            ) < 1:
                errors.append(
                    f"thread post {i} has "
                    f"no complete sentence"
                )

            # Every thread post must end naturally.
            if not ends_with_sentence_punctuation(
                post
            ):
                errors.append(
                    f"thread post {i} does not "
                    f"end as a complete sentence"
                )

            # Detect obvious truncation.
            if looks_truncated(
                post
            ):
                errors.append(
                    f"thread post {i} appears "
                    f"truncated or incomplete"
                )

            # RSS junk.
            if has_rss_junk(
                post
            ):
                errors.append(
                    f"thread post {i} contains "
                    f"RSS/article-page junk"
                )

            if has_html_entity(post):
                errors.append(
                    f"thread post {i} contains encoded HTML entity"
                )

            if has_unsafe_prose_fragment(post):
                errors.append(
                    f"thread post {i} contains malformed or incomplete prose"
                )

        # Final thread post must contain source.
        if thread:

            final_post = (
                thread[-1]
                or ""
            ).strip()

            if not has_source(
                final_post
            ):
                errors.append(
                    "thread missing source"
                )

    # -----------------------------------------------------
    # UNKNOWN FORMAT
    # -----------------------------------------------------

    else:

        errors.append(
            "unknown format"
        )

    # -----------------------------------------------------
    # FINAL LANGUAGE CHECK
    # -----------------------------------------------------

    if item.get(
        "language_status"
    ) not in (
        "ENGLISH",
        "TRANSLATED_TO_ENGLISH",
    ):
        errors.append(
            "final language is not English"
        )

    # -----------------------------------------------------
    # SOURCE URL CHECK
    # -----------------------------------------------------

    url = (
        item.get(
            "url",
            ""
        )
        or ""
    ).strip()

    if not url.startswith(
        (
            "http://",
            "https://",
        )
    ):
        errors.append(
            "invalid source URL"
        )

    # -----------------------------------------------------
    # LOW-CONFIDENCE WARNING
    # -----------------------------------------------------

    if item.get(
        "confidence"
    ) == "low":

        warnings.append(
            "low-confidence story"
        )

    # -----------------------------------------------------
    # LOW-CONFIDENCE CONFIRMED CHECK
    # -----------------------------------------------------

    text_to_check = ""

    if fmt == "single":

        text_to_check = (
            item.get(
                "post",
                ""
            )
            or ""
        )

    elif fmt == "thread":

        text_to_check = " ".join(
            item.get(
                "thread",
                []
            )
        )

    if (
        re.search(
            r"\bconfirmed\b",
            text_to_check.lower()
        )
        and item.get(
            "confidence"
        ) == "low"
    ):
        errors.append(
            "low-confidence story uses "
            "confirmed wording"
        )

    # -----------------------------------------------------
    # FINAL RESULT
    # -----------------------------------------------------

    return {
        "quality_pass": not errors,
        "quality_errors": errors,
        "quality_warnings": warnings,
    }
