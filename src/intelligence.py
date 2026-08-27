import re
from collections import Counter
from src.source_reliability import reliability_bonus, get_tier

URGENT_TERMS = {
    "earthquake","tsunami","hurricane","cyclone","tornado","flood","wildfire",
    "volcano","eruption","landslide","evacuation","missile","airstrike","invasion",
    "explosion","plane crash","train crash","bridge collapse","coup",
    "market crash","bank failure","default","state of emergency",
    "data breach","cyberattack","terror attack"
}

CATEGORY_TERMS = {
    "finance": {
        "bank", "stocks", "stock market", "bond", "inflation",
        "interest rate", "central bank", "economy", "economic",
        "tariff", "trade", "earnings", "revenue", "ipo",
        "debt", "default", "economic collapse", "economic crisis",
        "energy crisis", "financial crisis", "sanctions"
    },

    "politics": {
        "president", "presidential", "prime minister",
        "government", "administration", "election",
        "parliament", "senate", "congress",
        "minister", "vote", "coalition",
        "sanctions", "diplomatic", "diplomacy",
        "political", "politics",
        "court", "courts", "appeals court",
        "federal court", "supreme court",
        "judge", "judges", "ruling",
        "legislation", "law", "bill",
        "white house", "presidency",
        "president trump", "appeal", "appeals"
    },

    "disaster": {
        "earthquake", "tsunami", "hurricane", "cyclone",
        "tornado", "flood", "wildfire", "wildfires",
        "volcano", "eruption", "landslide", "evacuation",
        "disaster"
    },

    "conflict": {
        "war", "attack", "airstrike", "missile", "invasion",
        "ceasefire", "coup", "military"
    },

    "cybersecurity": {
        "cybersecurity", "cyberattack", "data breach", "ransomware",
        "malware", "vulnerability", "vulnerabilities", "hacker", "hackers",
        "hacking", "phishing", "zero-day"
    },

    "technology": {
        "technology", "ai", "artificial intelligence", "chip",
        "semiconductor", "software", "robot", "robotics", "app", "apps",
        "startup", "startups", "platform", "algorithm", "game", "gaming",
        "gamer", "console", "playstation", "xbox", "digital media"
    },

    "science": {
        "science", "research", "study", "scientist",
        "astronomy", "biology", "physics", "chemistry"
    },

    "space": {
        "space", "nasa", "esa", "jpl", "moon", "mars",
        "rocket", "satellite", "astronaut", "orbit",
        "spacecraft", "launch"
    },

    "health": {
        "health", "disease", "virus", "outbreak", "hospital", "hospitals",
        "who", "vaccine", "pandemic", "malnutrition", "nutrition", "medical",
        "medicine", "patient", "patients", "cancer", "malaria", "cholera",
        "measles", "mpox", "mental health", "public health"
    },

    "crime": {
        "police", "rape", "raped", "murder", "murdered", "homicide",
        "kidnap", "kidnapped", "kidnapping", "robbery", "suspect", "suspects",
        "arrested", "charged", "gang", "gangs"
    },

    "environment": {
        "environment", "climate", "climate change", "pollution",
        "emissions", "deforestation", "biodiversity",
        "conservation", "wildlife", "crocodile", "crocodiles",
        "extreme rainfall", "rainfall"
    },

    "industry": {
        "company", "factory", "manufacturing", "oil", "gas",
        "energy", "automotive", "aviation", "shipping",
        "industry", "production"
    },

    "sports": {
        "football", "soccer", "cricket", "tennis", "basketball",
        "baseball", "golf", "formula 1", "f1", "olympics", "athlete",
        "athletes", "championship", "tournament", "league", "player",
        "players", "coach", "club", "match", "goal", "goals", "season",
        "cup", "pfa", "uefa", "fifa", "premier league", "golden boot",
        "referee", "transfer"
    },

    "entertainment": {
        "film", "movie", "movies", "cinema", "actor", "actors", "actress",
        "singer", "music", "album", "television", "tv series", "streaming",
        "box office", "director", "directors", "art", "contemporary art", "artist",
        "artists", "artwork", "museum", "gallery", "exhibition"
    },
}

def _words(text):
    return set(re.findall(r"[a-z0-9][a-z0-9'-]*", (text or "").lower()))


def _term_present(text, term):
    """Match a topic term as a word/phrase, never as an arbitrary substring.

    Single-word topic terms accept ordinary plural forms, so `flood` also
    recognizes `floods` without returning to unsafe substring matching.
    """
    escaped = re.escape(term.lower())
    suffix = "" if term.endswith("s") or " " in term else r"(?:s|es)?"
    return bool(
        re.search(
            r"(?<![a-z0-9])" + escaped + suffix + r"(?![a-z0-9])",
            (text or "").lower(),
        )
    )


def _category(text, source_category):
    raw = (source_category or "").lower().strip()

    # These are geographic/source labels, NOT article topics.
    regional_categories = {
        "world",
        "africa",
        "india",
        "japan",
        "china",
        "south-korea",
        "southeast-asia",
        "europe",
        "middle-east",
        "latin-america",
        "canada",
        "australia",
        "pacific",
        "south-asia",
        "east-asia",
        "oceania",
    }

    lower = (text or "").lower()

    scores = {}

    for category, terms in CATEGORY_TERMS.items():
        scores[category] = sum(
            1 for term in terms
            if _term_present(lower, term)
        )

    best_category = max(scores, key=scores.get)
    best_score = scores[best_category]

    # A regional/source label should never become the article topic. One
    # boundary-matched topical signal is enough; requiring two pushed obvious
    # sports/health/crime stories into the generic world bucket.
    if raw in regional_categories:
        if best_score >= 1:
            return best_category
        return "world"

    # If the source already supplies a specific topic category,
    # preserve it unless the article strongly indicates another topic.
    known_topics = set(CATEGORY_TERMS.keys())

    if raw in known_topics:
        if best_score >= 2 and best_category != raw:
            return best_category
        return raw

    if best_score >= 2:
        return best_category

    return "world"

def classify(title, summary, source_category, item=None):
    item = item or {}
    text = f"{title} {summary}".lower()
    category = _category(text, source_category)
    urgency_hits = [term for term in URGENT_TERMS if _term_present(text, term)]
    base = 35 + min(25, len(urgency_hits) * 8)
    base += reliability_bonus(item)
    if item.get("primary_source"):
        base += 10
    if len(summary or "") >= 180:
        base += 5
    score = max(0, min(100, base))
    confidence = "high" if item.get("primary_source") else ("medium" if get_tier(item) <= 2 else "low")
    return {
        "category": category,
        "score": score,
        "confidence": confidence,
        "urgency_terms": urgency_hits,
    }

def _tokens(text):
    return _words(text)

def _similarity(a, b):
    aa, bb = _tokens(a), _tokens(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, len(aa | bb))


DISASTER_EVENT_TERMS = {
    "earthquake", "tsunami", "hurricane", "cyclone", "tornado", "flood",
    "wildfire", "volcano", "eruption", "landslide", "evacuation",
}

DISASTER_GENERIC_TOKENS = {
    "after", "before", "dead", "deadly", "death", "deaths", "kill", "killed",
    "missing", "injured", "people", "person", "rescue", "rescues", "teams",
    "flood", "floods", "wildfire", "wildfires", "earthquake", "earthquakes",
    "flash", "major", "severe", "catastrophic", "disaster", "disasters",
    "border", "region", "area", "city", "town", "village", "country",
    "cause", "caused", "causes", "damage", "destruction", "reported",
    "reports", "report", "latest", "update", "updates", "least", "nearly",
    "more", "than", "over", "hundreds", "thousands", "scores", "the",
    "and", "for", "from", "with", "into", "that", "this", "what", "why",
    "how", "are", "was", "were", "has", "have", "had", "its", "their",
}


def _plain_tokens(text):
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


DIRECTION_GROUPS = (
    ({"north", "northern"}, {"south", "southern"}),
    ({"east", "eastern"}, {"west", "western"}),
)


def _opposing_geography(a, b):
    a_tokens = _plain_tokens(a)
    b_tokens = _plain_tokens(b)
    return any(
        (a_tokens & left and b_tokens & right)
        or (a_tokens & right and b_tokens & left)
        for left, right in DIRECTION_GROUPS
    )


def _disaster_corroboration_anchor(item, other):
    """Match differently worded reports of the same disaster conservatively.

    Generic Jaccard title similarity misses headlines such as two outlets both
    covering the Nepal-Tibet floods with very different wording. For an item
    already classified as a disaster, require the same concrete disaster type
    plus at least two shared non-generic title tokens (normally place names).
    """
    if (item.get("category") or "").lower() != "disaster":
        return False

    item_text = f"{item.get('title', '')} {item.get('summary', '')}"
    other_text = f"{other.get('title', '')} {other.get('summary', '')}"
    shared_events = {
        term for term in DISASTER_EVENT_TERMS
        if _term_present(item_text, term) and _term_present(other_text, term)
    }
    if not shared_events:
        return False

    a = _plain_tokens(item.get("title", ""))
    b = _plain_tokens(other.get("title", ""))
    distinctive = (a & b) - DISASTER_GENERIC_TOKENS
    distinctive -= {token for term in DISASTER_EVENT_TERMS for token in term.split()}
    distinctive = {
        token for token in distinctive
        if len(token) >= 4 and not token.isdigit()
    }

    return len(distinctive) >= 2


def verify(item, all_items):
    title = item.get("title", "")
    matches = []

    for other in all_items:
        if other.get("id") == item.get("id"):
            continue

        # A source cannot independently corroborate itself.
        if other.get("source") == item.get("source"):
            continue

        other_title = other.get("title", "")
        sim = _similarity(title, other_title)
        disaster_anchor = _disaster_corroboration_anchor(item, other)
        geographic_conflict = _opposing_geography(title, other_title)

        if not geographic_conflict and (disaster_anchor or sim >= 0.38):
            matches.append((max(sim, 0.39 if disaster_anchor else sim), other))

    matches.sort(reverse=True, key=lambda x: x[0])

    corroborating = []
    strong = []
    seen_sources = set()

    for sim, other in matches:
        source = other.get("source")

        if not source or source in seen_sources:
            continue

        seen_sources.add(source)
        corroborating.append(other)

        if other.get("tier", 4) <= 2:
            strong.append(other)

    return {
        "corroborating_sources": len(corroborating),
        "strong_corroboration": len(strong),
        "corroborating_source_names": [
            x.get("source") for x in strong[:5]
        ],
        "verified_match_count": len(matches),
    }
