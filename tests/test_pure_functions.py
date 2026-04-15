"""Tests for pure functions that don't require external service mocking."""

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
        """Basic message should be formatted correctly."""
        from bot import format_message_for_issue

        msg = mock_message(
            content="Hello, this is a test message",
            display_name="John Doe",
            name="johndoe",
            created_at=datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC),
        )

        result = format_message_for_issue(msg)

        assert "**John Doe** (@johndoe)" in result
        assert "2024-01-15 10:30 UTC" in result
        assert "> Hello, this is a test message" in result
        assert ">>> Target Message" not in result

    def test_target_message_has_prefix(self, mock_message):
        """Target message should have the special prefix."""
        from bot import format_message_for_issue

        msg = mock_message(content="Bug report here")
        result = format_message_for_issue(msg, is_target=True)

        assert "**>>> Target Message:**" in result

    def test_multiline_message(self, mock_message):
        """Multi-line message should have each line quoted."""
        from bot import format_message_for_issue

        msg = mock_message(content="Line 1\nLine 2\nLine 3")
        result = format_message_for_issue(msg)

        assert "> Line 1" in result
        assert "> Line 2" in result
        assert "> Line 3" in result

    def test_empty_content(self, mock_message):
        """Empty content should show placeholder."""
        from bot import format_message_for_issue

        msg = mock_message(content="")
        result = format_message_for_issue(msg)

        assert "*[no text content]*" in result

    def test_none_content(self, mock_message):
        """None content should show placeholder."""
        from bot import format_message_for_issue

        msg = mock_message(content=None)
        msg.content = None
        result = format_message_for_issue(msg)

        assert "*[no text content]*" in result


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


class TestEscapeMarkdownFilename:
    """Tests for the _escape_markdown_filename function."""

    def test_escapes_brackets_and_parens(self):
        """Should escape markdown-injecting characters."""
        from bot import _escape_markdown_filename

        assert _escape_markdown_filename("normal.png") == "normal.png"
        assert _escape_markdown_filename("file[1].png") == r"file\[1\].png"
        assert _escape_markdown_filename("test](http://evil.com)[x") == (
            r"test\]\(http://evil.com\)\[x"
        )

    def test_escapes_parentheses(self):
        """Should escape parentheses."""
        from bot import _escape_markdown_filename

        assert _escape_markdown_filename("file(1).txt") == r"file\(1\).txt"


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
