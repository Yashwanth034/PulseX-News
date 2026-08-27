import re


MAJOR_DISASTER_MARKERS = (
    "catastrophic",
    "mass casualty",
    "state of emergency",
    "glacial collapse",
    "glacier collapse",
    "dam collapse",
    "red alert",
)


def _impact_number(text, labels):
    labels_re = "|".join(re.escape(label) for label in labels)
    patterns = (
        rf"\b(?:nearly\s+|about\s+|at\s+least\s+|more\s+than\s+|over\s+)?([0-9][0-9,]*)\s+(?:people\s+)?(?:are\s+|were\s+|reported\s+)?(?:{labels_re})\b",
        rf"\b(?:{labels_re})\s+(?:include\s+|rise\s+to\s+|reaches?\s+|at\s+)?(?:nearly\s+|about\s+|at\s+least\s+|more\s+than\s+|over\s+)?([0-9][0-9,]*)\b",
    )
    best = 0
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            try:
                best = max(best, int(match.group(1).replace(",", "")))
            except ValueError:
                pass
    return best


def is_verified_major_disaster(item):
    """Return True only for a severe, sufficiently verified disaster.

    This intentionally does not treat every flood/fire/earthquake story as a
    guaranteed queue item. The article must already classify as a disaster,
    have credible verification, and contain a strong severity signal.
    """
    if (item.get("category") or "").lower() != "disaster":
        return False

    confidence = (item.get("confidence") or "low").lower()
    verified = (
        confidence in {"high", "medium"}
        and (
            bool(item.get("primary_source"))
            or item.get("strong_corroboration", 0) >= 1
        )
    )
    if not verified:
        return False

    if item.get("priority_level") == "IMMEDIATE":
        return True

    alert_level = str(item.get("alert_level") or "").lower()
    severity = str(item.get("severity") or "").lower()
    if alert_level == "red" or severity in {"extreme", "severe"}:
        return True

    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    if any(marker in text for marker in MAJOR_DISASTER_MARKERS):
        return True

    dead_or_missing = _impact_number(
        text,
        ("dead", "killed", "deaths", "missing"),
    )
    injured = _impact_number(text, ("injured", "wounded"))
    affected = _impact_number(
        text,
        ("affected", "evacuated", "displaced", "homeless"),
    )

    return (
        dead_or_missing >= 25
        or injured >= 100
        or affected >= 1000
    )
