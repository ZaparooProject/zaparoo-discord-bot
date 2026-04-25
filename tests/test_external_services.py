"""Tests for functions that call external services (with mocks)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGenerateIssueTitle:
    """Tests for the generate_issue_title function."""

    @pytest.mark.asyncio
    async def test_successful_title_generation(self):
        """Successful API call should return generated title."""
        from bot import generate_issue_title

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(
            return_value=MagicMock(text="  User reports app crash on startup  ")
        )

        with patch("bot.gemini_client", mock_client):
            title = await generate_issue_title("The app crashes when I open it")

            assert title == "User reports app crash on startup"
            mock_client.aio.models.generate_content.assert_called_once()

    @pytest.mark.asyncio
    async def test_truncates_long_body(self):
        """Long body should be truncated to 4000 chars."""
        from bot import generate_issue_title

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(
            return_value=MagicMock(text="Generated title")
        )

        long_body = "x" * 5000

        with patch("bot.gemini_client", mock_client):
            await generate_issue_title(long_body)

            call_args = mock_client.aio.models.generate_content.call_args
            assert len(call_args.kwargs["contents"]) == 4000

    @pytest.mark.asyncio
    async def test_api_failure_returns_fallback(self):
        """API failure should return fallback title."""
        from bot import generate_issue_title

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(side_effect=Exception("API Error"))

        with patch("bot.gemini_client", mock_client):
            title = await generate_issue_title("Some issue body")

            assert title == "Issue from Discord"

    @pytest.mark.asyncio
    async def test_uses_configured_model(self):
        """Should use the configured GEMINI_MODEL."""
        from bot import generate_issue_title

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=MagicMock(text="Title"))

        with patch("bot.gemini_client", mock_client), patch("bot.GEMINI_MODEL", "gemini-2.5-pro"):
            await generate_issue_title("Body")

            call_args = mock_client.aio.models.generate_content.call_args
            assert call_args.kwargs["model"] == "gemini-2.5-pro"


class TestCreateGithubIssue:
    """Tests for the create_github_issue function."""

    def test_creates_issue_with_labels(self):
        """Issue should be created with valid labels."""
        from bot import create_github_issue

        mock_issue = MagicMock()
        mock_issue.number = 42
        mock_issue.html_url = "https://github.com/owner/repo/issues/42"

        # Create label mocks with proper name attribute
        label_bug = MagicMock()
        label_bug.name = "bug"
        label_enhancement = MagicMock()
        label_enhancement.name = "enhancement"
        label_discord = MagicMock()
        label_discord.name = "from-discord"

        mock_repo = MagicMock()
        mock_repo.get_labels.return_value = [label_bug, label_enhancement, label_discord]
        mock_repo.create_issue.return_value = mock_issue

        mock_client = MagicMock()
        mock_client.get_repo.return_value = mock_repo

        with patch("bot.github_client", mock_client):
            number, url = create_github_issue(
                "owner/repo", "Test Issue", "Issue body", ["bug", "from-discord"]
            )

            assert number == 42
            assert url == "https://github.com/owner/repo/issues/42"
            mock_repo.create_issue.assert_called_once_with(
                title="Test Issue", body="Issue body", labels=["bug", "from-discord"]
            )

    def test_filters_invalid_labels(self):
        """Invalid labels should be filtered out."""
        from bot import create_github_issue

        mock_issue = MagicMock()
        mock_issue.number = 1
        mock_issue.html_url = "https://github.com/owner/repo/issues/1"

        label_bug = MagicMock()
        label_bug.name = "bug"

        mock_repo = MagicMock()
        mock_repo.get_labels.return_value = [label_bug]
        mock_repo.create_issue.return_value = mock_issue

        mock_client = MagicMock()
        mock_client.get_repo.return_value = mock_repo

        with patch("bot.github_client", mock_client):
            create_github_issue("owner/repo", "Test", "Body", ["bug", "nonexistent-label"])

            mock_repo.create_issue.assert_called_once_with(
                title="Test", body="Body", labels=["bug"]
            )

    def test_creates_issue_without_labels_if_none_valid(self):
        """Issue should be created without labels if none are valid."""
        from bot import create_github_issue

        mock_issue = MagicMock()
        mock_issue.number = 1
        mock_issue.html_url = "https://github.com/owner/repo/issues/1"

        mock_repo = MagicMock()
        mock_repo.get_labels.return_value = [MagicMock(name="bug")]
        mock_repo.create_issue.return_value = mock_issue

        mock_client = MagicMock()
        mock_client.get_repo.return_value = mock_repo

        with patch("bot.github_client", mock_client):
            create_github_issue("owner/repo", "Test", "Body", ["nonexistent"])

            mock_repo.create_issue.assert_called_once_with(title="Test", body="Body")

    def test_creates_issue_with_empty_labels(self):
        """Issue should be created without labels param if list is empty."""
        from bot import create_github_issue

        mock_issue = MagicMock()
        mock_issue.number = 1
        mock_issue.html_url = "https://github.com/owner/repo/issues/1"

        mock_repo = MagicMock()
        mock_repo.get_labels.return_value = []
        mock_repo.create_issue.return_value = mock_issue

        mock_client = MagicMock()
        mock_client.get_repo.return_value = mock_repo

        with patch("bot.github_client", mock_client):
            create_github_issue("owner/repo", "Test", "Body", [])

            mock_repo.create_issue.assert_called_once_with(title="Test", body="Body")


class TestProcessReaction:
    """Tests for the process_reaction function."""

    @pytest.mark.asyncio
    async def test_ignores_non_guild_reactions(self, mock_reaction_payload):
        """Reactions in DMs (no guild_id) should be ignored."""
        from bot import process_reaction

        payload = mock_reaction_payload(guild_id=None)
        payload.guild_id = None

        # Should complete without error
        await process_reaction(payload)

    @pytest.mark.asyncio
    async def test_ignores_unauthorized_user(self, mock_reaction_payload):
        """Reactions from users without authorized role should be ignored."""
        from bot import process_reaction

        with patch("bot.AUTHORIZED_ROLE_ID", 99999):
            payload = mock_reaction_payload(member_roles=[11111, 22222])

            # Should complete without error (no issue created)
            await process_reaction(payload)

    @pytest.mark.asyncio
    async def test_ignores_no_member(self, mock_reaction_payload):
        """Reactions without member data should be ignored."""
        from bot import process_reaction

        payload = mock_reaction_payload()
        payload.member = None

        await process_reaction(payload)

    @pytest.mark.asyncio
    async def test_ignores_unknown_emoji(self, mock_reaction_payload, mock_channel):
        """Unknown emoji should be ignored."""
        from bot import process_reaction

        with (
            patch("bot.AUTHORIZED_ROLE_ID", 99999),
            patch("bot.ISSUE_TYPES", {"🐛": "bug"}),
            patch("bot.PROJECTS", {}),
        ):
            payload = mock_reaction_payload(emoji="👍", member_roles=[99999])

            mock_guild = MagicMock()
            mock_guild.get_member.return_value = None

            with patch("bot.bot") as mock_bot:
                mock_bot.get_guild.return_value = mock_guild

                await process_reaction(payload)

    @pytest.mark.asyncio
    async def test_project_selection_stores_pending(
        self, mock_reaction_payload, mock_channel, mock_message
    ):
        """Project emoji should store pending selection."""
        from bot import pending_projects, process_reaction

        pending_projects.clear()

        with (
            patch("bot.AUTHORIZED_ROLE_ID", 99999),
            patch("bot.PROJECTS", {"🖥️": ("owner/repo", "Core")}),
        ):
            payload = mock_reaction_payload(emoji="🖥️", member_roles=[99999], message_id=12345)

            channel = mock_channel()
            channel.fetch_message.return_value = mock_message()

            mock_guild = MagicMock()

            with patch("bot.bot") as mock_bot:
                mock_bot.get_guild.return_value = mock_guild
                mock_bot.get_channel.return_value = channel

                await process_reaction(payload)

                assert 12345 in pending_projects
                assert pending_projects[12345][0] == "owner/repo"
                assert pending_projects[12345][1] == "Core"

        pending_projects.clear()

    @pytest.mark.asyncio
    async def test_uses_pending_project_over_default(
        self, mock_reaction_payload, mock_channel, mock_message
    ):
        """When pending project exists, it should be used instead of default."""
        import time

        from bot import pending_projects, process_reaction

        pending_projects.clear()

        # Pre-populate pending_projects
        pending_projects[12345] = (
            "pending/repo",
            "PendingProject",
            time.time(),
        )

        with (
            patch("bot.AUTHORIZED_ROLE_ID", 99999),
            patch("bot.ISSUE_TYPES", {"🐛": "bug"}),
            patch("bot.DEFAULT_PROJECT", ("default/repo", "DefaultProject")),
        ):
            payload = mock_reaction_payload(emoji="🐛", member_roles=[99999], message_id=12345)

            channel = mock_channel()
            msg = mock_message()
            channel.fetch_message.return_value = msg

            mock_guild = MagicMock()
            mock_guild.get_member.return_value = None

            # Mock the history to return empty
            async def empty_history(*args, **kwargs):
                return
                yield  # Make it an async generator

            channel.history.return_value = empty_history()

            mock_issue = MagicMock()
            mock_issue.number = 99
            mock_issue.html_url = "https://github.com/pending/repo/issues/99"

            mock_repo = MagicMock()
            mock_repo.get_labels.return_value = []
            mock_repo.create_issue.return_value = mock_issue

            mock_github = MagicMock()
            mock_github.get_repo.return_value = mock_repo

            mock_gemini = MagicMock()
            mock_gemini.aio.models.generate_content = AsyncMock(
                return_value=MagicMock(text="Test title")
            )

            with (
                patch("bot.bot") as mock_bot,
                patch("bot.github_client", mock_github),
                patch("bot.gemini_client", mock_gemini),
            ):
                mock_bot.get_guild.return_value = mock_guild
                mock_bot.get_channel.return_value = channel
                mock_bot.user = MagicMock()
                mock_bot.http_session = MagicMock()

                await process_reaction(payload)

                # Verify pending/repo was used, not default/repo
                mock_github.get_repo.assert_called_with("pending/repo")

        pending_projects.clear()

    @pytest.mark.asyncio
    async def test_uses_default_project_without_pending_selection(
        self, mock_reaction_payload, mock_channel, mock_message
    ):
        """When no project is selected, issue reactions should use the default project."""
        from bot import pending_projects, process_reaction

        pending_projects.clear()

        with (
            patch("bot.AUTHORIZED_ROLE_ID", 99999),
            patch("bot.ISSUE_TYPES", {"🐛": "bug"}),
            patch("bot.DEFAULT_PROJECT", ("default/repo", "DefaultProject")),
        ):
            payload = mock_reaction_payload(emoji="🐛", member_roles=[99999], message_id=12345)

            channel = mock_channel()
            msg = mock_message()
            channel.fetch_message.return_value = msg

            mock_guild = MagicMock()
            mock_guild.get_member.return_value = None

            async def empty_history(*args, **kwargs):
                return
                yield

            channel.history.return_value = empty_history()

            mock_issue = MagicMock()
            mock_issue.number = 99
            mock_issue.html_url = "https://github.com/default/repo/issues/99"

            mock_repo = MagicMock()
            mock_repo.get_labels.return_value = []
            mock_repo.create_issue.return_value = mock_issue

            mock_github = MagicMock()
            mock_github.get_repo.return_value = mock_repo

            mock_gemini = MagicMock()
            mock_gemini.aio.models.generate_content = AsyncMock(
                return_value=MagicMock(text="Test title")
            )

            with (
                patch("bot.bot") as mock_bot,
                patch("bot.github_client", mock_github),
                patch("bot.gemini_client", mock_gemini),
            ):
                mock_bot.get_guild.return_value = mock_guild
                mock_bot.get_channel.return_value = channel
                mock_bot.user = MagicMock()
                mock_bot.http_session = MagicMock()

                await process_reaction(payload)

                mock_github.get_repo.assert_called_with("default/repo")
                mock_gemini.aio.models.generate_content.assert_awaited_once()

        pending_projects.clear()


class TestChannelFetching:
    """Tests for channel fetching fallback."""

    @pytest.mark.asyncio
    async def test_fetches_channel_when_not_cached(
        self, mock_reaction_payload, mock_channel, mock_message
    ):
        """Should fetch channel if not in cache."""
        from bot import process_reaction

        with (
            patch("bot.AUTHORIZED_ROLE_ID", 99999),
            patch("bot.ISSUE_TYPES", {"🐛": "bug"}),
            patch("bot.DEFAULT_PROJECT", ("test/repo", "TestProject")),
        ):
            payload = mock_reaction_payload(emoji="🐛", member_roles=[99999])

            channel = mock_channel()
            msg = mock_message()
            channel.fetch_message.return_value = msg

            mock_guild = MagicMock()

            async def empty_history(*args, **kwargs):
                return
                yield

            channel.history.return_value = empty_history()

            mock_issue = MagicMock()
            mock_issue.number = 1
            mock_issue.html_url = "https://github.com/test/repo/issues/1"

            mock_repo = MagicMock()
            mock_repo.get_labels.return_value = []
            mock_repo.create_issue.return_value = mock_issue

            mock_github = MagicMock()
            mock_github.get_repo.return_value = mock_repo

            mock_gemini = MagicMock()
            mock_gemini.aio.models.generate_content = AsyncMock(
                return_value=MagicMock(text="Title")
            )

            with (
                patch("bot.bot") as mock_bot,
                patch("bot.github_client", mock_github),
                patch("bot.gemini_client", mock_gemini),
            ):
                mock_bot.get_guild.return_value = mock_guild
                # Channel not in cache
                mock_bot.get_channel.return_value = None
                # But can be fetched
                mock_bot.fetch_channel = AsyncMock(return_value=channel)
                mock_bot.user = MagicMock()
                mock_bot.http_session = MagicMock()

                await process_reaction(payload)

                # Verify fetch_channel was called
                mock_bot.fetch_channel.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_when_channel_fetch_fails(self, mock_reaction_payload):
        """Should return early if channel cannot be fetched."""
        from bot import process_reaction

        with patch("bot.AUTHORIZED_ROLE_ID", 99999), patch("bot.ISSUE_TYPES", {"🐛": "bug"}):
            payload = mock_reaction_payload(emoji="🐛", member_roles=[99999])

            mock_guild = MagicMock()

            with patch("bot.bot") as mock_bot:
                mock_bot.get_guild.return_value = mock_guild
                mock_bot.get_channel.return_value = None
                mock_bot.fetch_channel = AsyncMock(side_effect=Exception("Not found"))

                # Should complete without error
                await process_reaction(payload)

    @pytest.mark.asyncio
    async def test_returns_when_message_fetch_fails(self, mock_reaction_payload, mock_channel):
        """Should return early if message cannot be fetched."""
        from bot import process_reaction

        with patch("bot.AUTHORIZED_ROLE_ID", 99999), patch("bot.ISSUE_TYPES", {"🐛": "bug"}):
            payload = mock_reaction_payload(emoji="🐛", member_roles=[99999])

            channel = mock_channel()
            channel.fetch_message = AsyncMock(side_effect=Exception("Message deleted"))

            mock_guild = MagicMock()

            with patch("bot.bot") as mock_bot:
                mock_bot.get_guild.return_value = mock_guild
                mock_bot.get_channel.return_value = channel

                # Should complete without error
                await process_reaction(payload)


class TestSupportResponses:
    """Tests for support response context menu commands."""

    def test_make_support_callback_returns_callable(self):
        """make_support_callback should return an async callable."""
        import asyncio

        from bot import make_support_callback

        config = {
            "title": "Test Title",
            "message": "Test message",
            "buttons": [{"label": "Link", "url": "https://example.com"}],
        }
        callback = make_support_callback(config)
        assert asyncio.iscoroutinefunction(callback)

    @pytest.mark.asyncio
    async def test_authorized_user_sends_embed(self, mock_role):
        """Authorized user should get an embed reply on the target message."""
        import discord

        from bot import make_support_callback

        config = {
            "title": "📋 Log File Needed",
            "message": "Please send logs",
            "buttons": [
                {"label": "Log Guide", "url": "https://example.com/logs"},
                {"label": "Support", "url": "https://example.com/support"},
            ],
        }
        callback = make_support_callback(config)

        member = MagicMock(spec=discord.Member)
        member.roles = [mock_role(99999)]
        interaction = AsyncMock()
        interaction.user = member
        message = AsyncMock()

        with patch("bot.AUTHORIZED_ROLE_ID", 99999):
            await callback(interaction, message)

        message.reply.assert_called_once()
        call_kwargs = message.reply.call_args[1]
        assert call_kwargs["mention_author"] is False
        assert call_kwargs["embed"].title == "📋 Log File Needed"
        assert call_kwargs["embed"].description == "Please send logs"

        view = call_kwargs["view"]
        assert len(view.children) == 2
        assert view.children[0].label == "Log Guide"
        assert view.children[0].url == "https://example.com/logs"
        assert view.children[1].label == "Support"

        interaction.response.send_message.assert_called_once_with("Sent!", ephemeral=True)

    @pytest.mark.asyncio
    async def test_unauthorized_user_rejected(self, mock_role):
        """Unauthorized user should get an ephemeral rejection."""
        import discord

        from bot import make_support_callback

        config = {"title": "Test", "message": "Test", "buttons": []}
        callback = make_support_callback(config)

        member = MagicMock(spec=discord.Member)
        member.roles = [mock_role(11111)]
        interaction = AsyncMock()
        interaction.user = member
        message = AsyncMock()

        with patch("bot.AUTHORIZED_ROLE_ID", 99999):
            await callback(interaction, message)

        message.reply.assert_not_called()
        interaction.response.send_message.assert_called_once_with(
            "You don't have permission to use this.", ephemeral=True
        )

    @pytest.mark.asyncio
    async def test_no_buttons_sends_embed_with_empty_view(self, mock_role):
        """Support response with no buttons should still send an embed."""
        import discord

        from bot import make_support_callback

        config = {"title": "Info", "message": "Some info", "buttons": []}
        callback = make_support_callback(config)

        member = MagicMock(spec=discord.Member)
        member.roles = [mock_role(99999)]
        interaction = AsyncMock()
        interaction.user = member
        message = AsyncMock()

        with patch("bot.AUTHORIZED_ROLE_ID", 99999):
            await callback(interaction, message)

        message.reply.assert_called_once()
        call_kwargs = message.reply.call_args[1]
        assert len(call_kwargs["view"].children) == 0


class TestCreateIssueFromMessage:
    """Tests for the extracted create_issue_from_message function."""

    @pytest.mark.asyncio
    async def test_creates_issue_and_returns_result(self):
        """Should gather context, generate title, and create a GitHub issue."""
        import discord

        from bot import create_issue_from_message

        target_message = MagicMock(spec=discord.Message)
        target_message.author.id = 123
        target_message.author.display_name = "TestUser"
        target_message.author.name = "testuser"
        target_message.content = "App crashes on startup"
        target_message.created_at.strftime.return_value = "2024-01-01 00:00 UTC"
        target_message.attachments = []
        target_message.id = 999

        async def empty_history():
            return
            yield

        channel = MagicMock()
        channel.id = 456
        channel.name = "support"
        channel.guild.name = "Test Server"
        channel.history = MagicMock(return_value=empty_history())

        guild = MagicMock(spec=discord.Guild)
        guild.id = 789

        mock_gemini = MagicMock()
        mock_gemini.aio.models.generate_content = AsyncMock(
            return_value=MagicMock(text="App crashes on startup")
        )

        mock_github_issue = MagicMock()
        mock_github_issue.number = 42
        mock_github_issue.html_url = "https://github.com/org/repo/issues/42"
        label_bug = MagicMock()
        label_bug.name = "bug"
        mock_github_repo = MagicMock()
        mock_github_repo.get_labels.return_value = [label_bug]
        mock_github_repo.create_issue.return_value = mock_github_issue
        mock_github = MagicMock()
        mock_github.get_repo.return_value = mock_github_repo

        with (
            patch("bot.gemini_client", mock_gemini),
            patch("bot.github_client", mock_github),
            patch("bot.bot") as mock_bot,
        ):
            mock_bot.http_session = AsyncMock()
            issue_number, issue_url = await create_issue_from_message(
                target_message,
                channel,
                guild,
                "org/repo",
                "Core",
                "bug",
            )

        assert issue_number == 42
        assert issue_url == "https://github.com/org/repo/issues/42"
        mock_github_repo.create_issue.assert_called_once()
        call_kwargs = mock_github_repo.create_issue.call_args[1]
        assert call_kwargs["labels"] == ["bug"]

    @pytest.mark.asyncio
    async def test_creates_issue_without_label(self):
        """Should create issue with no labels when label is None."""
        import discord

        from bot import create_issue_from_message

        target_message = MagicMock(spec=discord.Message)
        target_message.author.id = 123
        target_message.author.display_name = "TestUser"
        target_message.author.name = "testuser"
        target_message.content = "General question"
        target_message.created_at.strftime.return_value = "2024-01-01 00:00 UTC"
        target_message.attachments = []
        target_message.id = 999

        async def empty_history():
            return
            yield

        channel = MagicMock()
        channel.id = 456
        channel.name = "support"
        channel.guild.name = "Test Server"
        channel.history = MagicMock(return_value=empty_history())

        guild = MagicMock(spec=discord.Guild)
        guild.id = 789

        mock_gemini = MagicMock()
        mock_gemini.aio.models.generate_content = AsyncMock(
            return_value=MagicMock(text="General question")
        )

        mock_github_issue = MagicMock()
        mock_github_issue.number = 10
        mock_github_issue.html_url = "https://github.com/org/repo/issues/10"
        mock_github_repo = MagicMock()
        mock_github_repo.get_labels.return_value = []
        mock_github_repo.create_issue.return_value = mock_github_issue
        mock_github = MagicMock()
        mock_github.get_repo.return_value = mock_github_repo

        with (
            patch("bot.gemini_client", mock_gemini),
            patch("bot.github_client", mock_github),
            patch("bot.bot") as mock_bot,
        ):
            mock_bot.http_session = AsyncMock()
            issue_number, issue_url = await create_issue_from_message(
                target_message,
                channel,
                guild,
                "org/repo",
                "Core",
                None,
            )

        assert issue_number == 10
        mock_github_repo.create_issue.assert_called_once()
        call_kwargs = mock_github_repo.create_issue.call_args[1]
        # No label passed, so create_issue called without labels kwarg
        assert "labels" not in call_kwargs


class TestCreateIssueModal:
    """Tests for the CreateIssueModal class."""

    @pytest.mark.asyncio
    async def test_modal_has_project_options_from_config(self):
        """Modal project select should have options matching PROJECTS config."""
        from bot import CreateIssueModal

        target_message = MagicMock()
        modal = CreateIssueModal(target_message=target_message)

        project_labels = [opt.label for opt in modal.project.options]
        assert len(project_labels) > 0
        # Options should match what's in PROJECTS config
        from bot import PROJECTS

        expected_names = [name for _, (_, name) in PROJECTS.items()]
        assert project_labels == expected_names

    @pytest.mark.asyncio
    async def test_modal_has_issue_type_options_from_config(self):
        """Modal issue type select should have options matching ISSUE_TYPES config."""
        from bot import CreateIssueModal

        target_message = MagicMock()
        modal = CreateIssueModal(target_message=target_message)

        type_labels = [opt.label for opt in modal.issue_type.options]
        assert len(type_labels) > 0
        # Should have readable display names, not raw labels
        assert "General Issue" in type_labels or all(label[0].isupper() for label in type_labels)

    @pytest.mark.asyncio
    async def test_modal_stores_target_message(self):
        """Modal should store the target message reference."""
        from bot import CreateIssueModal

        target_message = MagicMock()
        modal = CreateIssueModal(target_message=target_message)
        assert modal.target_message is target_message

    @pytest.mark.asyncio
    async def test_on_submit_creates_issue(self):
        """on_submit should defer, create issue, and send followup."""
        import discord

        from bot import PROJECTS, CreateIssueModal

        async def empty_history():
            return
            yield

        target_message = AsyncMock(spec=discord.Message)
        target_message.channel = MagicMock()
        target_message.channel.id = 456
        target_message.channel.name = "support"
        target_message.channel.guild.name = "Test Server"
        target_message.channel.history = MagicMock(return_value=empty_history())
        target_message.guild = MagicMock(spec=discord.Guild)
        target_message.guild.id = 789
        target_message.author.id = 123
        target_message.author.display_name = "TestUser"
        target_message.author.name = "testuser"
        target_message.content = "Bug report"
        target_message.created_at.strftime.return_value = "2024-01-01 00:00 UTC"
        target_message.attachments = []
        target_message.id = 999

        modal = CreateIssueModal(target_message=target_message)

        # Simulate selection values
        first_repo = list(PROJECTS.values())[0][0]
        modal.project._values = [first_repo]
        modal.issue_type._values = ["bug"]

        interaction = AsyncMock()

        mock_gemini = MagicMock()
        mock_gemini.aio.models.generate_content = AsyncMock(
            return_value=MagicMock(text="Bug report title")
        )

        mock_github_issue = MagicMock()
        mock_github_issue.number = 5
        mock_github_issue.html_url = "https://github.com/org/repo/issues/5"
        mock_github_repo = MagicMock()
        label_bug = MagicMock()
        label_bug.name = "bug"
        mock_github_repo.get_labels.return_value = [label_bug]
        mock_github_repo.create_issue.return_value = mock_github_issue
        mock_github = MagicMock()
        mock_github.get_repo.return_value = mock_github_repo

        with (
            patch("bot.gemini_client", mock_gemini),
            patch("bot.github_client", mock_github),
            patch("bot.bot") as mock_bot,
        ):
            mock_bot.http_session = AsyncMock()
            await modal.on_submit(interaction)

        interaction.response.defer.assert_called_once_with(ephemeral=True, thinking=True)
        interaction.followup.send.assert_called_once()
        followup_kwargs = interaction.followup.send.call_args
        assert "issue #5" in followup_kwargs[0][0]
        assert followup_kwargs[1]["ephemeral"] is True
        target_message.reply.assert_called_once()
        target_message.add_reaction.assert_called_once_with("✅")

    @pytest.mark.asyncio
    async def test_on_submit_none_label_conversion(self):
        """on_submit should convert __none__ sentinel back to None."""
        import discord

        from bot import PROJECTS, CreateIssueModal

        async def empty_history():
            return
            yield

        target_message = AsyncMock(spec=discord.Message)
        target_message.channel = MagicMock()
        target_message.channel.id = 456
        target_message.channel.name = "support"
        target_message.channel.guild.name = "Test Server"
        target_message.channel.history = MagicMock(return_value=empty_history())
        target_message.guild = MagicMock(spec=discord.Guild)
        target_message.guild.id = 789
        target_message.author.id = 123
        target_message.author.display_name = "TestUser"
        target_message.author.name = "testuser"
        target_message.content = "General question"
        target_message.created_at.strftime.return_value = "2024-01-01 00:00 UTC"
        target_message.attachments = []
        target_message.id = 999

        modal = CreateIssueModal(target_message=target_message)

        first_repo = list(PROJECTS.values())[0][0]
        modal.project._values = [first_repo]
        modal.issue_type._values = ["__none__"]

        interaction = AsyncMock()

        mock_gemini = MagicMock()
        mock_gemini.aio.models.generate_content = AsyncMock(
            return_value=MagicMock(text="General title")
        )

        mock_github_issue = MagicMock()
        mock_github_issue.number = 6
        mock_github_issue.html_url = "https://github.com/org/repo/issues/6"
        mock_github_repo = MagicMock()
        mock_github_repo.get_labels.return_value = []
        mock_github_repo.create_issue.return_value = mock_github_issue
        mock_github = MagicMock()
        mock_github.get_repo.return_value = mock_github_repo

        with (
            patch("bot.gemini_client", mock_gemini),
            patch("bot.github_client", mock_github),
            patch("bot.bot") as mock_bot,
        ):
            mock_bot.http_session = AsyncMock()
            await modal.on_submit(interaction)

        # Issue should be created without labels (no label for __none__)
        mock_github_repo.create_issue.assert_called_once()
        call_kwargs = mock_github_repo.create_issue.call_args[1]
        assert "labels" not in call_kwargs


class TestCreateIssueContextMenu:
    """Tests for the Create Issue context menu callback."""

    @pytest.mark.asyncio
    async def test_unauthorized_user_rejected(self, mock_role):
        """Unauthorized user should get an ephemeral rejection."""
        import discord

        from bot import create_issue_callback

        member = MagicMock(spec=discord.Member)
        member.roles = [mock_role(11111)]

        interaction = AsyncMock()
        interaction.user = member
        message = AsyncMock()

        with patch("bot.AUTHORIZED_ROLE_ID", 99999):
            await create_issue_callback(interaction, message)

        interaction.response.send_message.assert_called_once_with(
            "You don't have permission to use this.", ephemeral=True
        )
        interaction.response.send_modal.assert_not_called()

    @pytest.mark.asyncio
    async def test_authorized_user_gets_modal(self, mock_role):
        """Authorized user should get a modal sent."""
        import discord

        from bot import CreateIssueModal, create_issue_callback

        member = MagicMock(spec=discord.Member)
        member.roles = [mock_role(99999)]

        interaction = AsyncMock()
        interaction.user = member

        message = AsyncMock(spec=discord.Message)

        with patch("bot.AUTHORIZED_ROLE_ID", 99999):
            await create_issue_callback(interaction, message)

        interaction.response.send_modal.assert_called_once()
        sent_modal = interaction.response.send_modal.call_args[0][0]
        assert isinstance(sent_modal, CreateIssueModal)
        assert sent_modal.target_message is message


class TestGuildHandling:
    """Tests for guild-related edge cases."""

    @pytest.mark.asyncio
    async def test_returns_when_guild_not_found(self, mock_reaction_payload):
        """Should return early if guild cannot be found."""
        from bot import process_reaction

        with patch("bot.AUTHORIZED_ROLE_ID", 99999):
            payload = mock_reaction_payload(emoji="🐛", member_roles=[99999])

            with patch("bot.bot") as mock_bot:
                mock_bot.get_guild.return_value = None

                # Should complete without error
                await process_reaction(payload)


class TestWalkReplyChain:
    """Tests for the walk_reply_chain function."""

    @pytest.mark.asyncio
    async def test_follows_reply_references(self):
        """Should walk the reply chain upward."""
        from bot import walk_reply_chain

        msg3 = MagicMock()
        msg3.reference = None

        msg2 = MagicMock()
        msg2.reference = MagicMock()
        msg2.reference.message_id = 300

        msg1 = MagicMock()
        msg1.reference = MagicMock()
        msg1.reference.message_id = 200

        channel = MagicMock()
        channel.fetch_message = AsyncMock(side_effect=[msg2, msg3])

        chain = await walk_reply_chain(msg1, channel)
        assert len(chain) == 2
        assert chain[0] is msg3  # reversed: oldest first
        assert chain[1] is msg2

    @pytest.mark.asyncio
    async def test_stops_at_no_reference(self):
        """Should stop when message has no reference."""
        from bot import walk_reply_chain

        msg = MagicMock()
        msg.reference = None

        channel = MagicMock()
        chain = await walk_reply_chain(msg, channel)
        assert chain == []

    @pytest.mark.asyncio
    async def test_stops_on_fetch_error(self):
        """Should stop when fetch_message fails."""
        from bot import walk_reply_chain

        msg = MagicMock()
        msg.reference = MagicMock()
        msg.reference.message_id = 123

        channel = MagicMock()
        channel.fetch_message = AsyncMock(side_effect=Exception("Not found"))

        chain = await walk_reply_chain(msg, channel)
        assert chain == []


class TestFilterContextWithLlm:
    """Tests for the filter_context_with_llm function."""

    @pytest.mark.asyncio
    async def test_returns_filtered_messages(self):
        """Should return messages at the indices the LLM specifies."""
        from bot import filter_context_with_llm

        msgs = [
            MagicMock(author=MagicMock(display_name=f"User{i}"), content=f"msg{i}")
            for i in range(5)
        ]
        target = MagicMock(author=MagicMock(display_name="Reporter"), content="bug report")

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=MagicMock(text="1,3"))

        with patch("bot.gemini_client", mock_client):
            result = await filter_context_with_llm(target, msgs)

        assert result == [msgs[1], msgs[3]]

    @pytest.mark.asyncio
    async def test_returns_empty_for_none_response(self):
        """Should return empty list when LLM says none are relevant."""
        from bot import filter_context_with_llm

        msgs = [MagicMock(author=MagicMock(display_name="User"), content="hello")]
        target = MagicMock(author=MagicMock(display_name="Reporter"), content="bug")

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=MagicMock(text="none"))

        with patch("bot.gemini_client", mock_client):
            result = await filter_context_with_llm(target, msgs)

        assert result == []

    @pytest.mark.asyncio
    async def test_fallback_on_null_response(self):
        """Should fall back to first N candidates when response.text is None."""
        from bot import filter_context_with_llm

        msgs = [
            MagicMock(author=MagicMock(display_name=f"User{i}"), content=f"msg{i}")
            for i in range(10)
        ]
        target = MagicMock(author=MagicMock(display_name="Reporter"), content="bug")

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=MagicMock(text=None))

        with patch("bot.gemini_client", mock_client), patch("bot.CONTEXT_MESSAGES", 3):
            result = await filter_context_with_llm(target, msgs)

        assert result == msgs[:3]

    @pytest.mark.asyncio
    async def test_fallback_on_unparseable_response(self):
        """Should fall back when LLM response can't be parsed as indices."""
        from bot import filter_context_with_llm

        msgs = [MagicMock(author=MagicMock(display_name="User"), content="msg")]
        target = MagicMock(author=MagicMock(display_name="Reporter"), content="bug")

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(
            return_value=MagicMock(text="these messages are relevant: 0")
        )

        with patch("bot.gemini_client", mock_client), patch("bot.CONTEXT_MESSAGES", 5):
            result = await filter_context_with_llm(target, msgs)

        assert result == msgs[:5]


class TestAddCommentToIssue:
    """Tests for the add_comment_to_issue function."""

    def test_creates_comment(self):
        """Should call PyGithub to create a comment."""
        from bot import add_comment_to_issue

        mock_comment = MagicMock()
        mock_comment.html_url = "https://github.com/org/repo/issues/42#issuecomment-1"

        mock_issue = MagicMock()
        mock_issue.create_comment.return_value = mock_comment

        mock_repo = MagicMock()
        mock_repo.get_issue.return_value = mock_issue

        mock_client = MagicMock()
        mock_client.get_repo.return_value = mock_repo

        with patch("bot.github_client", mock_client):
            url = add_comment_to_issue("org/repo", 42, "Follow-up body")

        assert url == "https://github.com/org/repo/issues/42#issuecomment-1"
        mock_repo.get_issue.assert_called_once_with(number=42)
        mock_issue.create_comment.assert_called_once_with("Follow-up body")


class TestBuildFollowupComment:
    """Tests for the build_followup_comment function."""

    @pytest.mark.asyncio
    async def test_builds_comment_with_text(self):
        """Should build a markdown comment from a message."""
        from bot import build_followup_comment

        message = MagicMock()
        message.content = "Here are the logs"
        message.author.display_name = "TestUser"
        message.author.name = "testuser"
        message.created_at.strftime.return_value = "2024-01-15 10:30 UTC"
        message.attachments = []

        with patch("bot.bot") as mock_bot:
            mock_bot.http_session = MagicMock()
            result = await build_followup_comment(message)

        assert "*Follow-up from Discord*" in result
        assert "Here are the logs" in result
