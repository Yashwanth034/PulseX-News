import json
import os
from pathlib import Path
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]

HEALTH = ROOT / "data" / "health.json"
QUEUE = ROOT / "data" / "queue.json"
STATE = ROOT / "data" / "production_state.json"


# =========================================================
# FILE HELPERS
# =========================================================

def load(path, default):
    """
    Safely load JSON.

    If the file does not exist or contains invalid JSON,
    return the supplied default.
    """

    if not path.exists():
        return default

    try:
        return json.loads(
            path.read_text()
        )

    except Exception:
        return default


def save(path, data):
    """
    Safely save JSON.
    """

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    path.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False
        )
    )


# =========================================================
# DATETIME HELPERS
# =========================================================

def parse_timestamp(value):
    """
    Convert an ISO timestamp into an aware UTC datetime.

    Invalid timestamps return None.
    """

    if not value:
        return None

    try:
        timestamp = datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00"
            )
        )

        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(
                tzinfo=timezone.utc
            )

        return timestamp.astimezone(
            timezone.utc
        )

    except Exception:
        return None


# =========================================================
# CONTROLLER
# =========================================================

def controller():

    # -----------------------------------------------------
    # LOAD HEALTH
    # -----------------------------------------------------

    health = load(
        HEALTH,
        {
            "status": "RED"
        }
    )

    # -----------------------------------------------------
    # LOAD QUEUE
    # -----------------------------------------------------

    queue = load(
        QUEUE,
        {
            "stories": [],
            "count": 0
        }
    )

    # -----------------------------------------------------
    # LOAD PRODUCTION STATE
    # -----------------------------------------------------

    state = load(
        STATE,
        {
            "live_enabled": False,
            "kill_switch": True,
            "daily_post_count": 0,
            "last_reset_date": None,
            "recent_post_times": []
        }
    )

    now = datetime.now(
        timezone.utc
    )

    today = now.date().isoformat()

    # =====================================================
    # CONFIGURATION
    # =====================================================

    # Maximum number of individual X posts per UTC day.
    #
    # IMPORTANT:
    # A thread containing 3 X posts consumes 3 posts,
    # not 1.
    # =====================================================

    daily_limit = int(
        os.getenv(
            "X_DAILY_POST_LIMIT",
            "48"
        )
    )

    # Maximum number of individual X posts during
    # any rolling 30-minute window.
    #
    # Default:
    # 1 post / 30 minutes.
    # =====================================================

    half_hour_limit = int(
        os.getenv(
            "X_HALF_HOUR_POST_LIMIT",
            "1"
        )
    )

    # Maximum number of individual X posts during
    # any rolling 1-hour window.
    #
    # Default:
    # 2 posts / 1 hour.
    # =====================================================

    hourly_limit = int(
        os.getenv(
            "X_HOURLY_POST_LIMIT",
            "2"
        )
    )

    # =====================================================
    # DAILY RESET
    # =====================================================

    if (
        state.get(
            "last_reset_date"
        )
        != today
    ):

        state["daily_post_count"] = 0

        state["last_reset_date"] = today

        # Keep recent_post_times. Rolling 30-minute and
        # 1-hour limits must remain valid across midnight.

    # =====================================================
    # NORMALIZE DAILY COUNT
    # =====================================================

    try:
        state["daily_post_count"] = max(
            0,
            int(
                state.get(
                    "daily_post_count",
                    0
                )
            )
        )

    except Exception:

        state["daily_post_count"] = 0

    # =====================================================
    # CLEAN POST TIMESTAMPS
    #
    # Keep timestamps from the previous hour so the same
    # durable state can enforce both 30-minute and 1-hour
    # rolling limits.
    # =====================================================

    recent_post_times = []

    for value in state.get(
        "recent_post_times",
        []
    ):

        timestamp = parse_timestamp(
            value
        )

        if timestamp is None:
            continue

        age_seconds = (
            now - timestamp
        ).total_seconds()

        # Ignore future timestamps.
        if age_seconds < 0:
            continue

        # Keep posts from the rolling 1-hour window.
        if age_seconds < 3600:

            recent_post_times.append(
                timestamp.isoformat()
            )

    state["recent_post_times"] = (
        recent_post_times
    )

    # =====================================================
    # CURRENT COUNTERS
    # =====================================================

    daily_post_count = state.get(
        "daily_post_count",
        0
    )

    hourly_post_count = len(
        state.get(
            "recent_post_times",
            []
        )
    )

    half_hour_post_count = 0

    for value in state.get(
        "recent_post_times",
        []
    ):

        timestamp = parse_timestamp(value)

        if timestamp is None:
            continue

        age_seconds = (
            now - timestamp
        ).total_seconds()

        if 0 <= age_seconds < 1800:
            half_hour_post_count += 1

    ready_count = int(
        queue.get(
            "count",
            len(
                queue.get(
                    "stories",
                    []
                )
            )
        )
        or 0
    )

    # =====================================================
    # REMAINING CAPACITY
    # =====================================================

    daily_remaining = max(
        0,
        daily_limit - daily_post_count
    )

    half_hour_remaining = max(
        0,
        half_hour_limit - half_hour_post_count
    )

    hourly_remaining = max(
        0,
        hourly_limit - hourly_post_count
    )

    # The publisher must never publish more than
    # the smallest available capacity.
    publish_capacity = min(
        daily_remaining,
        half_hour_remaining,
        hourly_remaining,
        ready_count
    )

    # =====================================================
    # ENVIRONMENT CONTROLS
    # =====================================================

    live_requested = (
        os.getenv(
            "X_PUBLISH_ENABLED",
            "false"
        ).lower()
        == "true"
    )

    kill_switch = (
        os.getenv(
            "X_KILL_SWITCH",
            "true"
        ).lower()
        == "true"
    )

    # =====================================================
    # SAFETY REASONS
    # =====================================================

    reasons = []

    # -----------------------------------------------------
    # Live publishing must be explicitly enabled.
    # -----------------------------------------------------

    if not live_requested:

        reasons.append(
            "live publishing is disabled"
        )

    # -----------------------------------------------------
    # Kill switch always blocks publishing.
    # -----------------------------------------------------

    if kill_switch:

        reasons.append(
            "kill switch is active"
        )

    # -----------------------------------------------------
    # RED health blocks publishing.
    #
    # YELLOW is allowed.
    # -----------------------------------------------------

    if health.get(
        "status"
    ) == "RED":

        reasons.append(
            "health gate is RED"
        )

    # -----------------------------------------------------
    # Invalid daily limit.
    # -----------------------------------------------------

    if daily_limit <= 0:

        reasons.append(
            "daily post limit is zero"
        )

    # -----------------------------------------------------
    # Daily limit reached.
    # -----------------------------------------------------

    if (
        daily_post_count
        >= daily_limit
    ):

        reasons.append(
            "daily post limit reached"
        )

    # -----------------------------------------------------
    # Invalid 30-minute limit.
    # -----------------------------------------------------

    if half_hour_limit <= 0:

        reasons.append(
            "30-minute post limit is zero"
        )

    # -----------------------------------------------------
    # 30-minute limit reached.
    # -----------------------------------------------------

    if (
        half_hour_post_count
        >= half_hour_limit
    ):

        reasons.append(
            "30-minute post limit reached"
        )

    # -----------------------------------------------------
    # Invalid 1-hour limit.
    # -----------------------------------------------------

    if hourly_limit <= 0:

        reasons.append(
            "1-hour post limit is zero"
        )

    # -----------------------------------------------------
    # 1-hour limit reached.
    # -----------------------------------------------------

    if (
        hourly_post_count
        >= hourly_limit
    ):

        reasons.append(
            "1-hour post limit reached"
        )

    # -----------------------------------------------------
    # No stories available.
    # -----------------------------------------------------

    if ready_count <= 0:

        reasons.append(
            "no stories are ready"
        )

    # =====================================================
    # FINAL DECISION
    # =====================================================

    allowed = not reasons

    # Even if allowed is true, the publisher must
    # respect the calculated capacity.
    if allowed and publish_capacity <= 0:

        allowed = False

        reasons.append(
            "no publishing capacity available"
        )

    # =====================================================
    # RESULT
    # =====================================================

    result = {
        "checked_at": now.isoformat(),

        "live_requested": (
            live_requested
        ),

        "kill_switch": (
            kill_switch
        ),

        "health": (
            health.get(
                "status"
            )
        ),

        "daily_limit": (
            daily_limit
        ),

        "daily_post_count": (
            daily_post_count
        ),

        "daily_remaining": (
            daily_remaining
        ),

        "half_hour_limit": (
            half_hour_limit
        ),

        "half_hour_post_count": (
            half_hour_post_count
        ),

        "half_hour_remaining": (
            half_hour_remaining
        ),

        "hourly_limit": (
            hourly_limit
        ),

        "hourly_post_count": (
            hourly_post_count
        ),

        "hourly_remaining": (
            hourly_remaining
        ),

        "publish_capacity": (
            publish_capacity
        ),

        "ready_count": (
            ready_count
        ),

        "allowed": (
            allowed
        ),

        "reasons": (
            reasons
        )
    }

    # =====================================================
    # STORE CONTROLLER STATE
    # =====================================================

    state["live_enabled"] = allowed

    state["kill_switch"] = kill_switch

    save(
        STATE,
        state
    )

    # =====================================================
    # DISPLAY
    # =====================================================

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False
        )
    )

    return result


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    controller()
