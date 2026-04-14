"""Tests for pure functions that don't require external service mocking."""

from datetime import UTC, datetime
from unittest.mock import patch


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
