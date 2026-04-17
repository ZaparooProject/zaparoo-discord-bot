"""Tests for pure functions that don't require external service mocking."""

import time
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch


class TestHasAuthorizedRole:
    """Tests for the has_authorized_role function."""

    def test_member_with_authorized_role(self, mock_member):
        """Member with the authorized role should return True."""
        from bot import has_authorized_role

        with patch("bot.AUTHORIZED_ROLE_ID", 99999):
            member = mock_member(user_id=1, roles=[99999])
            assert has_authorized_role(member) is True

    def test_member_without_authorized_role(self, mock_member):
        """Member without the authorized role should return False."""
        from bot import has_authorized_role

        with patch("bot.AUTHORIZED_ROLE_ID", 99999):
            member = mock_member(user_id=1, roles=[11111, 22222])
            assert has_authorized_role(member) is False

    def test_member_with_no_roles(self, mock_member):
        """Member with no roles should return False."""
        from bot import has_authorized_role

        with patch("bot.AUTHORIZED_ROLE_ID", 99999):
            member = mock_member(user_id=1, roles=[])
            assert has_authorized_role(member) is False

    def test_member_with_multiple_roles_including_authorized(self, mock_member):
        """Member with multiple roles including authorized should return True."""
        from bot import has_authorized_role

        with patch("bot.AUTHORIZED_ROLE_ID", 99999):
            member = mock_member(user_id=1, roles=[11111, 99999, 33333])
            assert has_authorized_role(member) is True


class TestFormatMessageForIssue:
    """Tests for the format_message_for_issue function."""

    def test_basic_message_formatting(self, mock_message):
        """Basic message should use the supplied author label."""
        from bot import format_message_for_issue

        msg = mock_message(
            content="Hello, this is a test message",
            display_name="John Doe",
            name="johndoe",
            created_at=datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC),
        )

        result = format_message_for_issue(msg, "Reporter")

        assert "**Reporter**" in result
        assert "johndoe" not in result
        assert "2024-01-15 10:30 UTC" in result
        assert "> Hello, this is a test message" in result
        assert ">>> Target Message" not in result

    def test_target_message_has_prefix(self, mock_message):
        """Target message should have the special prefix."""
        from bot import format_message_for_issue

        msg = mock_message(content="Bug report here")
        result = format_message_for_issue(msg, "Reporter", is_target=True)

        assert "**>>> Target Message:**" in result

    def test_multiline_message(self, mock_message):
        """Multi-line message should have each line quoted."""
        from bot import format_message_for_issue

        msg = mock_message(content="Line 1\nLine 2\nLine 3")
        result = format_message_for_issue(msg, "Reporter")

        assert "> Line 1" in result
        assert "> Line 2" in result
        assert "> Line 3" in result

    def test_empty_content(self, mock_message):
        """Empty content should show placeholder."""
        from bot import format_message_for_issue

        msg = mock_message(content="")
        result = format_message_for_issue(msg, "Reporter")

        assert "*[no text content]*" in result

    def test_none_content(self, mock_message):
        """None content should show placeholder."""
        from bot import format_message_for_issue

        msg = mock_message(content=None)
        msg.content = None
        result = format_message_for_issue(msg, "Reporter")

        assert "*[no text content]*" in result

    def test_user_mention_sanitized(self, mock_message):
        """User mentions in content should be replaced with [user]."""
        from bot import format_message_for_issue

        msg = mock_message(content="Thanks <@123456> and <@!789>")
        result = format_message_for_issue(msg, "Reporter")

        assert "<@" not in result
        assert "[user]" in result

    def test_role_mention_sanitized(self, mock_message):
        """Role mentions in content should be replaced with [role]."""
        from bot import format_message_for_issue

        msg = mock_message(content="Hey <@&111222>")
        result = format_message_for_issue(msg, "Reporter")

        assert "<@&" not in result
        assert "[role]" in result

    def test_channel_mention_preserved(self, mock_message):
        """Channel mentions should be left intact."""
        from bot import format_message_for_issue

        msg = mock_message(content="See <#999888>")
        result = format_message_for_issue(msg, "Reporter")

        assert "<#999888>" in result

    def test_author_label_markdown_escaped(self, mock_message):
        """Author label with markdown characters should be escaped."""
        from bot import format_message_for_issue

        msg = mock_message(content="Hello")
        result = format_message_for_issue(msg, "User [A]")

        assert "**User \\[A\\]**" in result


class TestCleanupPending:
    """Tests for the cleanup_pending function."""

    def test_removes_expired_entries(self):
        """Expired entries should be removed."""
        from bot import cleanup_pending, pending_projects

        with patch("bot.PENDING_TIMEOUT", 60), patch("bot.time") as mock_time:
            mock_time.monotonic.return_value = 1000.0

            # Add an expired entry (timestamp 900, current 1000, timeout 60 = expired)
            pending_projects[123] = ("repo/name", "Project", 900.0)
            # Add a valid entry (timestamp 950, current 1000, timeout 60 = valid)
            pending_projects[456] = ("repo/other", "Other", 950.0)

            cleanup_pending()

            assert 123 not in pending_projects
            assert 456 in pending_projects

            # Clean up
            pending_projects.clear()

    def test_keeps_valid_entries(self):
        """Valid entries should not be removed."""
        from bot import cleanup_pending, pending_projects

        with patch("bot.PENDING_TIMEOUT", 60), patch("bot.time") as mock_time:
            mock_time.monotonic.return_value = 1000.0

            pending_projects[789] = ("repo/name", "Project", 980.0)

            cleanup_pending()

            assert 789 in pending_projects

            pending_projects.clear()

    def test_handles_empty_pending(self):
        """Should handle empty pending dict without error."""
        from bot import cleanup_pending, pending_projects

        with patch("bot.time") as mock_time:
            mock_time.monotonic.return_value = 1000.0

            pending_projects.clear()
            cleanup_pending()
            assert len(pending_projects) == 0

    def test_cleans_expired_recent_issues(self):
        """Should remove expired recent_issues entries."""
        from bot import RecentIssue, cleanup_pending, recent_issues

        with (
            patch("bot.PENDING_TIMEOUT", 60),
            patch("bot.RECENT_ISSUE_TTL", 3600),
            patch("bot.time") as mock_time,
        ):
            mock_time.monotonic.return_value = 5000.0

            recent_issues[111] = [
                RecentIssue(1, "repo/a", 1, 100, 1000.0),  # expired (4000s ago)
                RecentIssue(2, "repo/b", 2, 200, 4500.0),  # valid (500s ago)
            ]

            cleanup_pending()

            assert len(recent_issues[111]) == 1
            assert recent_issues[111][0].issue_number == 2

            recent_issues.clear()

    def test_cleans_stale_rate_limit_entries(self):
        """Should remove stale _user_issue_timestamps entries."""
        from bot import _user_issue_timestamps, cleanup_pending

        with (
            patch("bot.ISSUE_RATE_LIMIT_SECONDS", 10),
            patch("bot.time") as mock_time,
        ):
            mock_time.monotonic.return_value = 1000.0

            _user_issue_timestamps.clear()
            _user_issue_timestamps[1] = 900.0  # stale: 100s ago, limit*2=20s
            _user_issue_timestamps[2] = 995.0  # fresh: 5s ago

            cleanup_pending()

            assert 1 not in _user_issue_timestamps
            assert 2 in _user_issue_timestamps

            _user_issue_timestamps.clear()


class TestEscapeMarkdownText:
    """Tests for the _escape_markdown_text function."""

    def test_escapes_brackets_and_parens(self):
        """Should escape markdown link characters."""
        from bot import _escape_markdown_text

        assert _escape_markdown_text("normal.png") == "normal.png"
        assert _escape_markdown_text("file[1].png") == r"file\[1\].png"
        assert _escape_markdown_text("test](http://evil.com)[x") == (
            r"test\]\(http://evil.com\)\[x"
        )

    def test_escapes_parentheses(self):
        """Should escape parentheses."""
        from bot import _escape_markdown_text

        assert _escape_markdown_text("file(1).txt") == r"file\(1\).txt"

    def test_escapes_backticks(self):
        """Should escape backticks."""
        from bot import _escape_markdown_text

        assert _escape_markdown_text("foo`bar`baz") == r"foo\`bar\`baz"

    def test_escapes_asterisks(self):
        """Should escape asterisks."""
        from bot import _escape_markdown_text

        assert _escape_markdown_text("**bold**") == r"\*\*bold\*\*"

    def test_escapes_underscores(self):
        """Should escape underscores."""
        from bot import _escape_markdown_text

        assert _escape_markdown_text("_italic_") == r"\_italic\_"

    def test_escapes_backslash(self):
        """Should escape backslash first to avoid double-escaping."""
        from bot import _escape_markdown_text

        assert _escape_markdown_text("a\\b") == r"a\\b"

    def test_plain_text_unchanged(self):
        """Plain text without markdown characters should be returned as-is."""
        from bot import _escape_markdown_text

        assert _escape_markdown_text("Hello World") == "Hello World"


class TestSanitizeMentions:
    """Tests for the _sanitize_mentions function."""

    def test_user_mention_replaced(self):
        """<@id> should become [user]."""
        from bot import _sanitize_mentions

        assert _sanitize_mentions("hey <@123456>") == "hey [user]"

    def test_user_mention_with_bang_replaced(self):
        """<@!id> should become [user]."""
        from bot import _sanitize_mentions

        assert _sanitize_mentions("hey <@!123456>") == "hey [user]"

    def test_role_mention_replaced(self):
        """<@&id> should become [role]."""
        from bot import _sanitize_mentions

        assert _sanitize_mentions("hey <@&999>") == "hey [role]"

    def test_channel_mention_preserved(self):
        """<#id> should be left intact."""
        from bot import _sanitize_mentions

        assert _sanitize_mentions("go to <#777>") == "go to <#777>"

    def test_multiple_mentions(self):
        """Multiple mixed mentions should all be handled."""
        from bot import _sanitize_mentions

        result = _sanitize_mentions("<@1> <@!2> <@&3> <#4>")
        assert result == "[user] [user] [role] <#4>"

    def test_no_mentions_unchanged(self):
        """Text without mentions should be unchanged."""
        from bot import _sanitize_mentions

        assert _sanitize_mentions("no mentions here") == "no mentions here"


class TestRateLimit:
    """Tests for _check_rate_limit and _record_issue_for_rate_limit."""

    def test_not_limited_when_no_prior_action(self):
        """User with no prior issue creation should not be rate-limited."""
        from bot import _check_rate_limit, _user_issue_timestamps

        _user_issue_timestamps.clear()
        assert _check_rate_limit(999) is None
        _user_issue_timestamps.clear()

    def test_limited_immediately_after_action(self):
        """User should be rate-limited right after creating an issue."""
        from bot import _check_rate_limit, _record_issue_for_rate_limit, _user_issue_timestamps

        _user_issue_timestamps.clear()
        with patch("bot.ISSUE_RATE_LIMIT_SECONDS", 10):
            _record_issue_for_rate_limit(42)
            remaining = _check_rate_limit(42)
            assert remaining is not None
            assert 0 < remaining <= 10
        _user_issue_timestamps.clear()

    def test_not_limited_after_cooldown_expires(self):
        """User should not be rate-limited once the cooldown has passed."""
        from bot import _check_rate_limit, _user_issue_timestamps

        _user_issue_timestamps.clear()
        with patch("bot.ISSUE_RATE_LIMIT_SECONDS", 10):
            _user_issue_timestamps[42] = time.monotonic() - 11
            assert _check_rate_limit(42) is None
        _user_issue_timestamps.clear()

    def test_different_users_independent(self):
        """Rate limit for one user should not affect another."""
        from bot import _check_rate_limit, _record_issue_for_rate_limit, _user_issue_timestamps

        _user_issue_timestamps.clear()
        with patch("bot.ISSUE_RATE_LIMIT_SECONDS", 10):
            _record_issue_for_rate_limit(1)
            assert _check_rate_limit(1) is not None
            assert _check_rate_limit(2) is None
        _user_issue_timestamps.clear()


class TestSegmentByTimeGap:
    """Tests for the segment_by_time_gap function."""

    def test_cuts_at_time_gap(self):
        """Should return only messages after the last gap."""
        from datetime import UTC, datetime

        from bot import segment_by_time_gap

        target = MagicMock()
        target.created_at = datetime(2024, 1, 1, 12, 30, 0, tzinfo=UTC)

        msgs = []
        for minute in [10, 11, 12, 25, 26]:  # gap between 12 and 25
            m = MagicMock()
            m.created_at = datetime(2024, 1, 1, 12, minute, 0, tzinfo=UTC)
            msgs.append(m)

        result = segment_by_time_gap(msgs, target, gap_seconds=600)
        # Should only include messages at :25 and :26 (after the gap)
        assert len(result) == 2

    def test_returns_empty_for_isolated_target(self):
        """Should return empty if most recent candidate is too far from target."""
        from datetime import UTC, datetime

        from bot import segment_by_time_gap

        target = MagicMock()
        target.created_at = datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC)

        msg = MagicMock()
        msg.created_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)  # 1 hour ago

        result = segment_by_time_gap([msg], target, gap_seconds=600)
        assert result == []

    def test_returns_empty_for_no_candidates(self):
        """Should return empty for empty candidate list."""
        from bot import segment_by_time_gap

        result = segment_by_time_gap([], MagicMock(), gap_seconds=600)
        assert result == []


class TestRecordRecentIssue:
    """Tests for the record_recent_issue function."""

    def test_records_issue(self):
        """Should add a RecentIssue entry to the channel's list."""
        from bot import recent_issues, record_recent_issue

        recent_issues.clear()
        record_recent_issue(111, 999, "org/repo", 42, 12345)

        assert 111 in recent_issues
        assert len(recent_issues[111]) == 1
        entry = recent_issues[111][0]
        assert entry.bot_reply_msg_id == 999
        assert entry.repo_name == "org/repo"
        assert entry.issue_number == 42
        assert entry.target_author_id == 12345

        recent_issues.clear()
