import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests


class XPublisherError(Exception):
    pass


class XPublisher:
    """
    Official X API v2 publisher.

    Safety rules:

        - Live publishing is disabled by default.
        - Kill switch blocks publishing.
        - Production controller must explicitly allow publishing.
        - Maximum 48 successful posts per UTC day by default.
        - Maximum 1 successful post in a rolling 30-minute window by default.
        - Maximum 2 successful posts in a rolling 1-hour window by default.
        - A thread consumes one post for every tweet in the thread.
        - Failed API requests are NOT counted.
        - A thread is checked for sufficient capacity BEFORE it starts.
        - Dry-run mode never contacts X.
    """

    def __init__(self):

        self.enabled = (
            os.getenv(
                "X_PUBLISH_ENABLED",
                "false"
            ).lower()
            == "true"
        )

        self.token = os.getenv(
            "X_USER_ACCESS_TOKEN",
            ""
        ).strip()

        self.base = (
            "https://api.x.com/2/tweets"
        )

        self.root = (
            Path(__file__)
            .resolve()
            .parents[1]
        )

        self.state_file = (
            self.root
            / "data"
            / "production_state.json"
        )

        # Keep these synchronized with
        # production_controller.py.
        self.daily_limit = int(
            os.getenv(
                "X_DAILY_POST_LIMIT",
                "48"
            )
        )

        self.half_hour_limit = int(
            os.getenv(
                "X_HALF_HOUR_POST_LIMIT",
                "1"
            )
        )

        self.hourly_limit = int(
            os.getenv(
                "X_HOURLY_POST_LIMIT",
                "2"
            )
        )

    # =====================================================
    # STATE
    # =====================================================

    def _load_state(self):

        if not self.state_file.exists():

            return {
                "live_enabled": False,
                "kill_switch": True,
                "daily_post_count": 0,
                "last_reset_date": None,
                "recent_post_times": [],
            }

        try:

            return json.loads(
                self.state_file.read_text()
            )

        except Exception as exc:

            raise XPublisherError(
                "Unable to read production state: "
                f"{exc}"
            )

    def _save_state(self, state):

        self.state_file.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        self.state_file.write_text(
            json.dumps(
                state,
                indent=2,
                ensure_ascii=False
            )
        )

    # =====================================================
    # TIME
    # =====================================================

    def _prepare_state(self, state):

        now = datetime.now(
            timezone.utc
        )

        today = now.date().isoformat()

        # -------------------------------------------------
        # Automatic UTC daily reset
        # -------------------------------------------------

        if (
            state.get(
                "last_reset_date"
            )
            != today
        ):

            state["daily_post_count"] = 0

            state["last_reset_date"] = today

            # Do not clear recent_post_times here. Rolling
            # limits must remain valid across UTC midnight.

        # -------------------------------------------------
        # Normalize daily count
        # -------------------------------------------------

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

        # -------------------------------------------------
        # Clean rolling 1-hour window.
        #
        # We retain one hour of timestamps so both the
        # 30-minute and 1-hour limits can be enforced from
        # the same durable state.
        # -------------------------------------------------

        recent = []

        for value in state.get(
            "recent_post_times",
            []
        ):

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

                timestamp = timestamp.astimezone(
                    timezone.utc
                )

                age = (
                    now - timestamp
                ).total_seconds()

                if (
                    0 <= age < 3600
                ):

                    recent.append(
                        timestamp.isoformat()
                    )

            except Exception:

                continue

        state["recent_post_times"] = recent

        return state

    # =====================================================
    # SAFETY CAPACITY
    # =====================================================

    def _available_capacity(self, state):

        state = self._prepare_state(
            state
        )

        daily_count = int(
            state.get(
                "daily_post_count",
                0
            )
        )

        now = datetime.now(
            timezone.utc
        )

        hour_count = 0
        half_hour_count = 0

        for value in state.get(
            "recent_post_times",
            []
        ):

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

                age = (
                    now
                    - timestamp.astimezone(timezone.utc)
                ).total_seconds()

            except Exception:
                continue

            if 0 <= age < 3600:
                hour_count += 1

            if 0 <= age < 1800:
                half_hour_count += 1

        daily_remaining = max(
            0,
            self.daily_limit
            - daily_count
        )

        half_hour_remaining = max(
            0,
            self.half_hour_limit
            - half_hour_count
        )

        hourly_remaining = max(
            0,
            self.hourly_limit
            - hour_count
        )

        return min(
            daily_remaining,
            half_hour_remaining,
            hourly_remaining
        )

    # =====================================================
    # SAFETY GATE
    # =====================================================

    def _check_allowed(
        self,
        required_posts=1
    ):

        if not self.enabled:

            raise XPublisherError(
                "Publishing blocked: "
                "X_PUBLISH_ENABLED is false"
            )

        state = self._prepare_state(
            self._load_state()
        )

        # Keep state normalized.
        self._save_state(
            state
        )

        # -------------------------------------------------
        # Kill switch
        # -------------------------------------------------

        if state.get(
            "kill_switch",
            True
        ):

            raise XPublisherError(
                "Publishing blocked: "
                "kill switch is active"
            )

        # -------------------------------------------------
        # Production controller
        # -------------------------------------------------

        if not state.get(
            "live_enabled",
            False
        ):

            raise XPublisherError(
                "Publishing blocked: "
                "production controller has not "
                "allowed live publishing"
            )

        # -------------------------------------------------
        # Validate requested amount
        # -------------------------------------------------

        if required_posts <= 0:

            raise XPublisherError(
                "Invalid publishing request"
            )

        # -------------------------------------------------
        # Capacity check
        #
        # IMPORTANT:
        # For a thread, required_posts can be > 1.
        # We check the COMPLETE thread before
        # publishing its first tweet.
        # -------------------------------------------------

        capacity = self._available_capacity(
            state
        )

        if required_posts > capacity:

            raise XPublisherError(
                "Publishing blocked: "
                f"need {required_posts} post(s), "
                f"but only {capacity} post(s) "
                "are currently available"
            )

        return state

    # =====================================================
    # HTTP HEADERS
    # =====================================================

    def _headers(self):

        if not self.token:

            raise XPublisherError(
                "Missing X_USER_ACCESS_TOKEN"
            )

        return {
            "Authorization": (
                f"Bearer {self.token}"
            ),
            "Content-Type": (
                "application/json"
            ),
        }

    # =====================================================
    # RECORD SUCCESS
    # =====================================================

    def _record_success(self):

        state = self._prepare_state(
            self._load_state()
        )

        now = datetime.now(
            timezone.utc
        )

        state["daily_post_count"] = (
            int(
                state.get(
                    "daily_post_count",
                    0
                )
            )
            + 1
        )

        state.setdefault(
            "recent_post_times",
            []
        ).append(
            now.isoformat()
        )

        self._save_state(
            state
        )

    # =====================================================
    # CREATE ONE POST
    # =====================================================

    def create_post(
        self,
        text,
        reply_to=None,
        prechecked=False
    ):

        text = (
            text or ""
        ).strip()

        if not text:

            raise XPublisherError(
                "Cannot publish empty post"
            )

        # -------------------------------------------------
        # Dry run
        #
        # Absolutely no API request.
        # -------------------------------------------------

        if not self.enabled:

            return {
                "mode": "dry_run",
                "text": text,
            }

        # -------------------------------------------------
        # Production safety gate
        # -------------------------------------------------

        if not prechecked:

            self._check_allowed(
                required_posts=1
            )

        # -------------------------------------------------
        # Build payload
        # -------------------------------------------------

        payload = {
            "text": text
        }

        if reply_to:

            payload["reply"] = {
                "in_reply_to_tweet_id": (
                    reply_to
                )
            }

        # -------------------------------------------------
        # Request
        # -------------------------------------------------

        try:

            response = requests.post(
                self.base,
                headers=self._headers(),
                json=payload,
                timeout=20
            )

        except requests.RequestException as exc:

            raise XPublisherError(
                "X API request failed: "
                f"{exc}"
            )

        # -------------------------------------------------
        # Rate limit
        # -------------------------------------------------

        if response.status_code in (
            420,
            429,
        ):

            raise XPublisherError(
                "X API rate limit reached. "
                "Publishing stopped without retrying."
            )

        # -------------------------------------------------
        # Other API errors
        # -------------------------------------------------

        if response.status_code >= 300:

            raise XPublisherError(
                "X API HTTP "
                f"{response.status_code}: "
                f"{response.text[:500]}"
            )

        # -------------------------------------------------
        # Parse response
        # -------------------------------------------------

        try:

            result = response.json()

        except ValueError as exc:

            raise XPublisherError(
                "Invalid X API response: "
                f"{exc}"
            )

        # -------------------------------------------------
        # ONLY successful responses count.
        # -------------------------------------------------

        self._record_success()

        return result

    # =====================================================
    # PUBLISH STORY
    # =====================================================

    def publish(self, item):

        fmt = item.get(
            "format"
        )

        # =================================================
        # DRY RUN
        # =================================================

        if not self.enabled:

            if fmt == "single":

                return [
                    {
                        "mode": "dry_run",
                        "text": item.get(
                            "post",
                            ""
                        )
                    }
                ]

            return [
                {
                    "mode": "dry_run",
                    "text": text,
                }
                for text in item.get(
                    "thread",
                    []
                )
            ]

        # =================================================
        # SINGLE POST
        # =================================================

        if fmt == "single":

            text = item.get(
                "post",
                ""
            )

            # Check capacity before sending.
            self._check_allowed(
                required_posts=1
            )

            return [
                self.create_post(
                    text,
                    prechecked=True
                )
            ]

        # =================================================
        # THREAD
        # =================================================

        if fmt != "thread":

            raise XPublisherError(
                f"Unknown format: {fmt}"
            )

        thread = [
            (text or "").strip()
            for text in item.get(
                "thread",
                []
            )
            if (text or "").strip()
        ]

        if not thread:

            raise XPublisherError(
                "Cannot publish empty thread"
            )

        # -------------------------------------------------
        # IMPORTANT SAFETY CHECK
        #
        # Check the COMPLETE thread before sending
        # its first tweet.
        # -------------------------------------------------

        required_posts = len(
            thread
        )

        self._check_allowed(
            required_posts=required_posts
        )

        # -------------------------------------------------
        # Publish thread
        # -------------------------------------------------

        results = []

        previous = None

        for text in thread:

            result = self.create_post(
                text,
                reply_to=previous,
                prechecked=True
            )

            results.append(
                result
            )

            previous = (
                result.get(
                    "data"
                ) or {}
            ).get(
                "id"
            )

        return results
