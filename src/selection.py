from collections import Counter


def _rank_key(item):
    return (
        item.get("priority_level") == "IMMEDIATE",
        item.get("priority_score", 0),
        item.get("event_status") == "UPDATE",
        item.get("confidence") == "high",
        item.get("score", 0),
    )


def select_balanced_queue(
    candidates,
    limit,
    max_per_category=2,
    max_per_source=2,
):
    """Select the strongest stories while preventing one sector/source takeover.

    IMMEDIATE stories are never blocked by diversity caps. Remaining slots use
    two passes: first one story per category, then additional stories while
    respecting configurable category/source caps.
    """
    if limit <= 0:
        return []

    ranked = sorted(candidates, key=_rank_key, reverse=True)
    selected = []
    selected_ids = set()
    category_counts = Counter()
    source_counts = Counter()

    def add(item, ignore_caps=False):
        story_id = item.get("id") or id(item)
        if story_id in selected_ids or len(selected) >= limit:
            return False

        category = item.get("category") or "world"
        source = item.get("source") or "Unknown"

        if not ignore_caps:
            if category_counts[category] >= max_per_category:
                return False
            if source_counts[source] >= max_per_source:
                return False

        selected.append(item)
        selected_ids.add(story_id)
        category_counts[category] += 1
        source_counts[source] += 1
        return True

    # Safety-critical events retain top priority.
    for item in ranked:
        if item.get("priority_level") == "IMMEDIATE":
            add(item, ignore_caps=True)

    if len(selected) >= limit:
        return selected[:limit]

    # Diversity pass: prefer a new topic category for each remaining slot.
    used_categories = set(category_counts)
    for item in ranked:
        category = item.get("category") or "world"
        if category in used_categories:
            continue
        if add(item):
            used_categories.add(category)
        if len(selected) >= limit:
            return selected

    # Strength pass: fill remaining capacity without allowing domination.
    for item in ranked:
        if add(item):
            if len(selected) >= limit:
                return selected

    # Last-resort fill. If the candidate pool is narrow, do not leave slots
    # empty merely because diversity caps cannot be satisfied.
    for item in ranked:
        if add(item, ignore_caps=True):
            if len(selected) >= limit:
                break

    return selected
