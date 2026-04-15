"""Tests for functions that call external services (with mocks)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGenerateIssueTitle:
    """Tests for the generate_issue_title function."""

    @pytest.mark.asyncio
    async def test_successful_title_generation(self):
        """Successful API call should return generated title."""
        from bot import generate_issue_title

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "  User reports app crash on startup  "

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("bot.openai_client", mock_client):
            title = await generate_issue_title("The app crashes when I open it")

            assert title == "User reports app crash on startup"
            mock_client.chat.completions.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_truncates_long_body(self):
        """Long body should be truncated to 4000 chars."""
        from bot import generate_issue_title

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Generated title"

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        long_body = "x" * 5000

        with patch("bot.openai_client", mock_client):
            await generate_issue_title(long_body)

            call_args = mock_client.chat.completions.create.call_args
            user_message = call_args.kwargs["messages"][1]["content"]
            assert len(user_message) == 4000

    @pytest.mark.asyncio
    async def test_api_failure_returns_fallback(self):
        """API failure should return fallback title."""
        from bot import generate_issue_title

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API Error"))

        with patch("bot.openai_client", mock_client):
            title = await generate_issue_title("Some issue body")

            assert title == "Issue from Discord"

    @pytest.mark.asyncio
    async def test_uses_configured_model(self):
        """Should use the configured OPENAI_MODEL."""
        from bot import generate_issue_title

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Title"

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("bot.openai_client", mock_client), patch("bot.OPENAI_MODEL", "gpt-4o-mini"):
            await generate_issue_title("Body")

            call_args = mock_client.chat.completions.create.call_args
            assert call_args.kwargs["model"] == "gpt-4o-mini"


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
        import asyncio

        from bot import pending_projects, process_reaction

        pending_projects.clear()

        # Pre-populate pending_projects
        pending_projects[12345] = (
            "pending/repo",
            "PendingProject",
            asyncio.get_event_loop().time(),
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

            mock_openai_response = MagicMock()
            mock_openai_response.choices = [MagicMock()]
            mock_openai_response.choices[0].message.content = "Test title"

            mock_openai = AsyncMock()
            mock_openai.chat.completions.create = AsyncMock(return_value=mock_openai_response)

            with (
                patch("bot.bot") as mock_bot,
                patch("bot.github_client", mock_github),
                patch("bot.openai_client", mock_openai),
            ):
                mock_bot.get_guild.return_value = mock_guild
                mock_bot.get_channel.return_value = channel
                mock_bot.user = MagicMock()
                mock_bot.http_session = MagicMock()

                await process_reaction(payload)

                # Verify pending/repo was used, not default/repo
                mock_github.get_repo.assert_called_with("pending/repo")

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

            mock_openai_response = MagicMock()
            mock_openai_response.choices = [MagicMock()]
            mock_openai_response.choices[0].message.content = "Title"

            mock_openai = AsyncMock()
            mock_openai.chat.completions.create = AsyncMock(return_value=mock_openai_response)

            with (
                patch("bot.bot") as mock_bot,
                patch("bot.github_client", mock_github),
                patch("bot.openai_client", mock_openai),
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
