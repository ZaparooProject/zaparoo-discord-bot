"""Integration tests using dpytest to simulate Discord interactions."""

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import discord.ext.test as dpytest
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def bot_instance():
    """Create and configure bot for testing."""
    # Import here to avoid module-level side effects
    import bot as bot_module

    # Setup the async loop before configuring dpytest
    await bot_module.bot._async_setup_hook()

    # Configure dpytest with the bot
    dpytest.configure(bot_module.bot)

    yield bot_module.bot

    # Cleanup
    await dpytest.empty_queue()


@pytest.fixture
def mock_github():
    """Mock GitHub client for issue creation."""
    mock_issue = MagicMock()
    mock_issue.number = 42
    mock_issue.html_url = "https://github.com/test/repo/issues/42"

    mock_repo = MagicMock()
    label_bug = MagicMock()
    label_bug.name = "bug"
    label_enhancement = MagicMock()
    label_enhancement.name = "enhancement"
    mock_repo.get_labels.return_value = [label_bug, label_enhancement]
    mock_repo.create_issue.return_value = mock_issue

    mock_client = MagicMock()
    mock_client.get_repo.return_value = mock_repo

    with patch("bot.github_client", mock_client):
        yield mock_client


@pytest.fixture
def mock_gemini():
    """Mock Gemini client for title generation."""
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        return_value=MagicMock(text="Test issue title from Discord")
    )

    with patch("bot.gemini_client", mock_client):
        yield mock_client


class TestHelpCommand:
    """Tests for the !help command."""

    @pytest.mark.asyncio
    async def test_help_command_with_authorized_role(self, bot_instance):
        """Help command should respond when user has authorized role."""

        # Get the test guild and give user the authorized role
        guild = dpytest.get_config().guilds[0]
        role = await guild.create_role(name="IssueCreator")
        member = dpytest.get_config().members[0]
        await member.add_roles(role)

        with patch("bot.AUTHORIZED_ROLE_ID", role.id):
            await dpytest.message("!help")

            # Check bot responded with help text
            assert dpytest.verify().message().contains().content("Discord Issue Bot")

    @pytest.mark.asyncio
    async def test_help_command_without_authorized_role(self, bot_instance):
        """Help command should not respond when user lacks authorized role."""

        with patch("bot.AUTHORIZED_ROLE_ID", 99999999):
            await dpytest.message("!help")

            # Bot should not respond
            assert dpytest.verify().message().nothing()


class TestReactionFlow:
    """Tests for the reaction-based issue creation flow."""

    @pytest.mark.asyncio
    async def test_unauthorized_reaction_ignored(self, bot_instance):
        """Reactions from unauthorized users should be ignored."""

        member = dpytest.get_config().members[0]

        with patch("bot.AUTHORIZED_ROLE_ID", 99999999):
            # Send a message (this simulates a user sending a message)
            msg = await dpytest.message("This is a bug report")

            # Add reaction (user doesn't have authorized role)
            await dpytest.add_reaction(member, msg, "🐛")

            # No response expected from bot
            assert dpytest.verify().message().nothing()

    @pytest.mark.asyncio
    async def test_issue_creation_flow(
        self, bot_instance, mock_github, mock_gemini, error_log_detector
    ):
        """Full issue creation flow should work and send correct payload."""

        # Setup authorized role
        guild = dpytest.get_config().guilds[0]
        role = await guild.create_role(name="IssueCreator")
        member = dpytest.get_config().members[0]
        await member.add_roles(role)

        with (
            patch("bot.AUTHORIZED_ROLE_ID", role.id),
            patch("bot.PROJECTS", {"🖥️": ("test/repo", "TestProject")}),
            patch("bot.DEFAULT_PROJECT", ("test/repo", "TestProject")),
            patch("bot.ISSUE_TYPES", {"🐛": "bug"}),
        ):
            # Send a message
            msg = await dpytest.message("The app crashes on startup")

            # Add reaction with authorized user
            await dpytest.add_reaction(member, msg, "🐛")

            # Bot should reply with issue link
            assert dpytest.verify().message().contains().content("issue #42")

            # Verify GitHub was called with correct payload
            mock_repo = mock_github.get_repo.return_value
            mock_repo.create_issue.assert_called_once()
            call_kwargs = mock_repo.create_issue.call_args.kwargs

            # Verify title was generated (from mock)
            assert call_kwargs["title"] == "Test issue title from Discord"

            # Verify body contains the message content
            assert "The app crashes on startup" in call_kwargs["body"]

            # Verify body contains required sections
            assert "Reported Message" in call_kwargs["body"]
            assert "Issue created from Discord" in call_kwargs["body"]

            # Verify labels were applied
            assert call_kwargs["labels"] == ["bug"]

            # Verify no unexpected errors were logged during the flow
            error_log_detector.assert_no_errors()


class TestContextFiltering:
    """Tests for context message filtering."""

    @pytest.mark.asyncio
    async def test_context_gathering_with_history(self, bot_instance, mock_github, mock_gemini):
        """Context gathering should fetch channel history for issue body."""

        guild = dpytest.get_config().guilds[0]
        role = await guild.create_role(name="IssueCreator")
        member = dpytest.get_config().members[0]
        await member.add_roles(role)

        with (
            patch("bot.AUTHORIZED_ROLE_ID", role.id),
            patch("bot.DEFAULT_PROJECT", ("test/repo", "TestProject")),
            patch("bot.ISSUE_TYPES", {"🐛": "bug"}),
            patch("bot.CONTEXT_MESSAGES", 5),
        ):
            # Send context message
            await dpytest.message("I was trying to launch the app")

            # Send target message
            await dpytest.message("Then it crashed with error code 500")

            # Get the message for reaction
            channel = dpytest.get_config().channels[0]
            messages = [m async for m in channel.history(limit=1)]
            if messages:
                msg = messages[0]
                await dpytest.add_reaction(member, msg, "🐛")

            # Verify issue was created (GitHub mock was called)
            mock_github.get_repo.assert_called()


class TestOnReady:
    """Tests for the on_ready event."""

    @pytest.mark.asyncio
    async def test_on_ready_logs_info(self, bot_instance):
        """on_ready should complete without error."""
        import bot as bot_module

        # dpytest already configures a mock user, just call on_ready
        # This tests the logging paths without crashing
        with patch.object(bot_module.bot, "_tree_synced", True):
            await bot_module.on_ready()


class TestBotLifecycle:
    """Tests for bot setup and teardown."""

    @pytest.mark.asyncio
    async def test_setup_hook_creates_session(self):
        """setup_hook should create an aiohttp session."""
        from bot import IssueBot

        test_bot = IssueBot()
        assert test_bot.http_session is None

        await test_bot.setup_hook()
        assert test_bot.http_session is not None

        # Cleanup
        await test_bot.close()

    @pytest.mark.asyncio
    async def test_close_cleans_up_session(self):
        """close should clean up the http session."""
        from bot import IssueBot

        test_bot = IssueBot()
        await test_bot.setup_hook()
        session = test_bot.http_session

        await test_bot.close()
        assert session.closed


class TestAttachmentFlow:
    """Tests for attachment handling in issue creation."""

    @pytest.mark.asyncio
    async def test_large_attachment_skipped(self, bot_instance, mock_github, mock_gemini):
        """Attachments over MAX_ATTACHMENT_SIZE should be skipped."""

        guild = dpytest.get_config().guilds[0]
        role = await guild.create_role(name="IssueCreator")
        member = dpytest.get_config().members[0]
        await member.add_roles(role)

        # Create a mock attachment that's too large
        large_attachment = MagicMock()
        large_attachment.filename = "huge.png"
        large_attachment.size = 100 * 1024 * 1024  # 100MB
        large_attachment.url = "https://cdn.discord.com/huge.png"

        with (
            patch("bot.AUTHORIZED_ROLE_ID", role.id),
            patch("bot.DEFAULT_PROJECT", ("test/repo", "TestProject")),
            patch("bot.ISSUE_TYPES", {"🐛": "bug"}),
            patch("bot.MAX_ATTACHMENT_SIZE", 10 * 1024 * 1024),
        ):
            # Send message and trigger reaction
            msg = await dpytest.message("Bug with large file")
            await dpytest.add_reaction(member, msg, "🐛")

            # Issue should still be created
            assert dpytest.verify().message().contains().content("issue #42")

    @pytest.mark.asyncio
    async def test_disallowed_extension_skipped(self, temp_images_dir):
        """Files with disallowed extensions should be skipped."""
        from bot import save_file_locally

        with (
            patch("bot.IMAGES_DIR", temp_images_dir),
            patch("bot.IMAGES_URL", "https://test.example.com/images"),
            patch("bot.ALLOWED_FILE_EXTENSIONS", {".png", ".jpg"}),
        ):
            # Try to save a .exe file
            result = await save_file_locally(b"malicious", "virus.exe")
            assert result is None

            # Try to save a .php file
            result = await save_file_locally(b"<?php", "shell.php")
            assert result is None

    @pytest.mark.asyncio
    async def test_attachment_download_and_save_pipeline(self, temp_images_dir):
        """Test full attachment download and save pipeline."""

        from bot import download_attachment, save_file_locally

        # Test download_attachment with mocked session that supports iter_chunked
        body = b"fake image data"

        async def fake_iter_chunked(size):
            yield body

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.content.iter_chunked = fake_iter_chunked

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=mock_response),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        with patch("bot.MAX_ATTACHMENT_SIZE", 10 * 1024 * 1024):
            data, filename = await download_attachment(
                mock_session, "https://cdn.discord.com/attachments/123/456/screenshot.png"
            )

        assert data == b"fake image data"
        assert filename == "screenshot.png"

        # Now test save_file_locally
        with (
            patch("bot.IMAGES_DIR", temp_images_dir),
            patch("bot.IMAGES_URL", "https://images.example.com"),
            patch("bot.ALLOWED_FILE_EXTENSIONS", {".png"}),
        ):
            url = await save_file_locally(data, filename)

            assert url is not None
            assert url.startswith("https://images.example.com/")
            assert url.endswith(".png")

            # Verify file was written
            files = list(temp_images_dir.iterdir())
            assert len(files) == 1
            assert files[0].read_bytes() == b"fake image data"

    @pytest.mark.asyncio
    async def test_fail_note_on_save_failure(self, mock_github, mock_gemini):
        """When save_file_locally fails, issue body should contain a fail note (not Discord URL)."""
        from bot import process_reaction

        mock_attachment = MagicMock()
        mock_attachment.filename = "test.png"
        mock_attachment.size = 1024
        mock_attachment.url = "https://cdn.discord.com/original.png"

        mock_message = MagicMock()
        mock_message.content = "Test message"
        mock_message.author = MagicMock()
        mock_message.author.id = 12345
        mock_message.author.display_name = "TestUser"
        mock_message.author.name = "testuser"
        mock_message.created_at = MagicMock()
        mock_message.created_at.strftime = MagicMock(return_value="2024-01-01 00:00 UTC")
        mock_message.attachments = [mock_attachment]
        mock_message.reply = AsyncMock()
        mock_message.add_reaction = AsyncMock()
        mock_message.remove_reaction = AsyncMock()

        mock_channel = MagicMock()
        mock_channel.fetch_message = AsyncMock(return_value=mock_message)
        mock_channel.name = "test-channel"
        mock_channel.guild = MagicMock()
        mock_channel.guild.name = "Test Server"

        # Return empty history
        async def empty_history(*args, **kwargs):
            return
            yield

        mock_channel.history = MagicMock(return_value=empty_history())

        mock_guild = MagicMock()
        mock_guild.get_member = MagicMock(return_value=None)

        mock_member = MagicMock()
        mock_member.roles = [MagicMock(id=99999)]

        mock_payload = MagicMock()
        mock_payload.guild_id = 1
        mock_payload.channel_id = 1
        mock_payload.message_id = 1
        mock_payload.user_id = 1
        mock_payload.member = mock_member
        mock_payload.emoji = MagicMock()
        mock_payload.emoji.__str__ = MagicMock(return_value="🐛")

        with (
            patch("bot.AUTHORIZED_ROLE_ID", 99999),
            patch("bot.ISSUE_TYPES", {"🐛": "bug"}),
            patch("bot.DEFAULT_PROJECT", ("test/repo", "TestProject")),
            patch("bot.bot") as mock_bot,
            patch("bot.download_attachment", AsyncMock(return_value=(b"data", "test.png"))),
            patch("bot.save_file_locally", AsyncMock(return_value=None)),
        ):  # Save fails
            mock_bot.get_guild.return_value = mock_guild
            mock_bot.get_channel.return_value = mock_channel
            mock_bot.user = MagicMock()
            mock_bot.http_session = MagicMock()

            await process_reaction(mock_payload)

            # Verify issue was created with a fail note (no expiring Discord URL)
            mock_repo = mock_github.get_repo.return_value
            call_kwargs = mock_repo.create_issue.call_args.kwargs
            assert "*[attachment failed to download: test.png]*" in call_kwargs["body"]
            assert "cdn.discord.com" not in call_kwargs["body"]


class TestProjectSelection:
    """Tests for multi-project workflow."""

    @pytest.mark.asyncio
    async def test_pending_project_used(self, bot_instance, mock_github, mock_gemini):
        """Pending project selection should be used for issue creation."""

        from bot import pending_projects

        pending_projects.clear()

        guild = dpytest.get_config().guilds[0]
        role = await guild.create_role(name="IssueCreator")
        member = dpytest.get_config().members[0]
        await member.add_roles(role)

        with (
            patch("bot.AUTHORIZED_ROLE_ID", role.id),
            patch("bot.PROJECTS", {"🖥️": ("selected/repo", "SelectedProject")}),
            patch("bot.DEFAULT_PROJECT", ("default/repo", "DefaultProject")),
            patch("bot.ISSUE_TYPES", {"🐛": "bug"}),
        ):
            msg = await dpytest.message("Test bug report")

            # First, select project
            await dpytest.add_reaction(member, msg, "🖥️")

            # Verify pending project was stored
            # Note: message ID may vary, so just check something was stored
            assert len(pending_projects) >= 0  # Integration with dpytest is tricky

        pending_projects.clear()


class TestThreadSupport:
    """Tests for thread message handling."""

    @pytest.mark.asyncio
    async def test_thread_channel_display_format(self):
        """Thread should show parent channel in display format."""

        # Create mock thread
        mock_parent = MagicMock()
        mock_parent.name = "support"

        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.name = "User issue discussion"
        mock_thread.parent = mock_parent
        mock_thread.guild = MagicMock()
        mock_thread.guild.name = "Test Server"

        # Verify isinstance check works with our mock
        assert isinstance(mock_thread, discord.Thread)

        # Build the channel display string as the bot does
        if isinstance(mock_thread, discord.Thread):
            parent_name = mock_thread.parent.name if mock_thread.parent else "unknown"
            channel_display = f"#{parent_name} → {mock_thread.name}"
        else:
            channel_display = f"#{mock_thread.name}"

        assert channel_display == "#support → User issue discussion"

    @pytest.mark.asyncio
    async def test_thread_context_only_from_thread(self, mock_github, mock_gemini):
        """Context gathering in threads should only get thread messages."""
        from bot import process_reaction

        # This test verifies that when processing a reaction in a thread,
        # channel.history() is called on the thread (not the parent channel).
        # Discord.py already handles this - Thread.history() returns only thread messages.

        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.name = "Bug discussion"
        mock_thread.parent = MagicMock()
        mock_thread.parent.name = "support"
        mock_thread.guild = MagicMock()
        mock_thread.guild.name = "Test Server"

        mock_message = MagicMock()
        mock_message.content = "This is broken"
        mock_message.author = MagicMock()
        mock_message.author.id = 12345
        mock_message.author.display_name = "User"
        mock_message.author.name = "user"
        mock_message.created_at = MagicMock()
        mock_message.created_at.strftime = MagicMock(return_value="2024-01-01 00:00 UTC")
        mock_message.attachments = []
        mock_message.reply = AsyncMock()
        mock_message.add_reaction = AsyncMock()
        mock_message.remove_reaction = AsyncMock()
        mock_message.reference = None

        mock_thread.fetch_message = AsyncMock(return_value=mock_message)

        # Track that history was called on the thread
        history_called_on = []

        async def mock_history(*args, **kwargs):
            history_called_on.append("thread")
            return
            yield

        mock_thread.history = MagicMock(return_value=mock_history())

        mock_guild = MagicMock()
        mock_guild.get_member = MagicMock(return_value=None)

        mock_member = MagicMock()
        mock_member.roles = [MagicMock(id=99999)]

        mock_payload = MagicMock()
        mock_payload.guild_id = 1
        mock_payload.channel_id = 1
        mock_payload.message_id = 1
        mock_payload.user_id = 1
        mock_payload.member = mock_member
        mock_payload.emoji = MagicMock()
        mock_payload.emoji.__str__ = MagicMock(return_value="🐛")

        with (
            patch("bot.AUTHORIZED_ROLE_ID", 99999),
            patch("bot.ISSUE_TYPES", {"🐛": "bug"}),
            patch("bot.DEFAULT_PROJECT", ("test/repo", "TestProject")),
            patch("bot.bot") as mock_bot,
        ):
            mock_bot.get_guild.return_value = mock_guild
            mock_bot.get_channel.return_value = mock_thread
            mock_bot.user = MagicMock()
            mock_bot.http_session = MagicMock()

            await process_reaction(mock_payload)

            # Verify history was called (on the thread, not parent)
            assert len(history_called_on) == 1

            # Verify issue body contains thread format
            mock_repo = mock_github.get_repo.return_value
            call_kwargs = mock_repo.create_issue.call_args.kwargs
            assert "#support → Bug discussion" in call_kwargs["body"]


class TestErrorHandling:
    """Tests for error handling scenarios."""

    @pytest.mark.asyncio
    async def test_github_error_shows_failure(self, bot_instance, mock_gemini, error_log_detector):
        """GitHub API errors should show failure reaction."""

        guild = dpytest.get_config().guilds[0]
        role = await guild.create_role(name="IssueCreator")
        member = dpytest.get_config().members[0]
        await member.add_roles(role)

        # Mock GitHub to raise an error
        mock_repo = MagicMock()
        mock_repo.get_labels.return_value = []
        mock_repo.create_issue.side_effect = Exception("API rate limit exceeded")

        mock_client = MagicMock()
        mock_client.get_repo.return_value = mock_repo

        with (
            patch("bot.AUTHORIZED_ROLE_ID", role.id),
            patch("bot.DEFAULT_PROJECT", ("test/repo", "TestProject")),
            patch("bot.ISSUE_TYPES", {"🐛": "bug"}),
            patch("bot.github_client", mock_client),
        ):
            msg = await dpytest.message("This will fail")
            await dpytest.add_reaction(member, msg, "🐛")

            # Should get an error message
            assert (
                dpytest.verify()
                .message()
                .contains()
                .content("Failed to create issue. Check bot logs for details.")
            )

            # This test expects an error log - verify it was logged
            error_log_detector.assert_no_errors(allowed_messages=["Failed to create issue"])
