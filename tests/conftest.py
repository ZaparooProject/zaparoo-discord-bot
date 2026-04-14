"""Fixtures and mocks for testing the Discord Issue Bot."""

import logging
import sys
from pathlib import Path

# Add parent directory to path so 'bot' module can be imported
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class ErrorLogDetector(logging.Handler):
    """Logging handler that captures ERROR and above logs."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.errors: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord):
        self.errors.append(record)

    def assert_no_errors(self, allowed_messages: list[str] | None = None):
        """Assert no unexpected errors were logged."""
        allowed = allowed_messages or []
        unexpected = [
            e
            for e in self.errors
            if not any(allowed_msg in e.getMessage() for allowed_msg in allowed)
        ]
        if unexpected:
            error_msgs = "\n".join(f"  - {e.getMessage()}" for e in unexpected)
            raise AssertionError(f"Unexpected error logs detected:\n{error_msgs}")


@pytest.fixture
def error_log_detector():
    """Fixture to detect unexpected error logs during tests.

    Usage:
        def test_something(error_log_detector):
            # ... test code ...
            error_log_detector.assert_no_errors()

        # Or allow specific expected errors:
        def test_error_handling(error_log_detector):
            # ... test code that logs an expected error ...
            error_log_detector.assert_no_errors(allowed_messages=["API rate limit"])
    """
    detector = ErrorLogDetector()
    root_logger = logging.getLogger()
    root_logger.addHandler(detector)
    yield detector
    root_logger.removeHandler(detector)


@pytest.fixture
def mock_role():
    """Create a mock Discord role."""

    def _make_role(role_id: int, name: str = "TestRole"):
        role = MagicMock()
        role.id = role_id
        role.name = name
        return role

    return _make_role


@pytest.fixture
def mock_member(mock_role):
    """Create a mock Discord member with roles."""

    def _make_member(
        user_id: int,
        roles: list[int] | None = None,
        display_name: str = "TestUser",
        name: str = "testuser",
    ):
        member = MagicMock()
        member.id = user_id
        member.display_name = display_name
        member.name = name
        member.roles = [mock_role(rid) for rid in (roles or [])]
        return member

    return _make_member


@pytest.fixture
def mock_author():
    """Create a mock Discord message author."""

    def _make_author(user_id: int = 12345, display_name: str = "TestUser", name: str = "testuser"):
        author = MagicMock()
        author.id = user_id
        author.display_name = display_name
        author.name = name
        return author

    return _make_author


@pytest.fixture
def mock_message(mock_author):
    """Create a mock Discord message."""

    def _make_message(
        content: str = "Test message content",
        author_id: int = 12345,
        display_name: str = "TestUser",
        name: str = "testuser",
        created_at: datetime | None = None,
        attachments: list | None = None,
    ):
        message = MagicMock()
        message.content = content
        message.author = mock_author(author_id, display_name, name)
        message.created_at = created_at or datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        message.attachments = attachments or []
        message.reply = AsyncMock()
        message.add_reaction = AsyncMock()
        message.remove_reaction = AsyncMock()
        return message

    return _make_message


@pytest.fixture
def mock_attachment():
    """Create a mock Discord attachment."""

    def _make_attachment(
        filename: str,
        size: int = 1024,
        url: str = "https://cdn.discord.com/attachments/test.png",
    ):
        attachment = MagicMock()
        attachment.filename = filename
        attachment.size = size
        attachment.url = url
        return attachment

    return _make_attachment


@pytest.fixture
def mock_channel():
    """Create a mock Discord channel."""

    def _make_channel(
        channel_id: int = 111,
        name: str = "test-channel",
        guild_name: str = "Test Server",
    ):
        channel = MagicMock()
        channel.id = channel_id
        channel.name = name
        channel.guild = MagicMock()
        channel.guild.name = guild_name
        channel.fetch_message = AsyncMock()
        channel.history = MagicMock()
        return channel

    return _make_channel


@pytest.fixture
def mock_reaction_payload(mock_member):
    """Create a mock RawReactionActionEvent payload."""

    def _make_payload(
        emoji: str = "🐛",
        message_id: int = 999,
        channel_id: int = 111,
        guild_id: int = 222,
        user_id: int = 12345,
        member_roles: list[int] | None = None,
    ):
        payload = MagicMock()
        payload.emoji = MagicMock()
        payload.emoji.__str__ = MagicMock(return_value=emoji)
        payload.message_id = message_id
        payload.channel_id = channel_id
        payload.guild_id = guild_id
        payload.user_id = user_id
        payload.member = mock_member(user_id, member_roles) if member_roles is not None else None
        return payload

    return _make_payload


@pytest.fixture
def temp_images_dir(tmp_path):
    """Create a temporary images directory."""
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    return images_dir


@pytest.fixture
def patch_bot_config(temp_images_dir):
    """Patch bot configuration for testing."""
    with patch.multiple(
        "bot",
        IMAGES_DIR=temp_images_dir,
        IMAGES_URL="https://test.example.com/images",
        AUTHORIZED_ROLE_ID=99999,
        CONTEXT_MESSAGES=5,
        PENDING_TIMEOUT=60,
        MAX_ATTACHMENT_SIZE=10 * 1024 * 1024,
        ALLOWED_FILE_EXTENSIONS={".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt", ".log"},
        IMAGE_EXTENSIONS={".png", ".jpg", ".jpeg", ".gif", ".webp"},
    ):
        yield
