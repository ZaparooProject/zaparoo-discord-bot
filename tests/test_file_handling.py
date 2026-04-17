"""Tests for file handling, extension validation, and security measures."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt", ".log"}


class TestSaveFileLocally:
    """Tests for the save_file_locally function."""

    @pytest.mark.asyncio
    async def test_saves_allowed_image_extension(self, temp_images_dir):
        """Allowed image extensions should be saved."""
        from bot import save_file_locally

        with (
            patch("bot.IMAGES_DIR", temp_images_dir),
            patch("bot.IMAGES_URL", "https://test.example.com/images"),
            patch("bot.ALLOWED_FILE_EXTENSIONS", _ALLOWED_EXTS),
        ):
            data = b"fake image data"
            result = await save_file_locally(data, "screenshot.png")

            assert result is not None
            assert result.startswith("https://test.example.com/images/")
            assert result.endswith(".png")

            # Verify file was actually written
            files = list(temp_images_dir.iterdir())
            assert len(files) == 1
            assert files[0].read_bytes() == data

    @pytest.mark.asyncio
    async def test_saves_txt_extension(self, temp_images_dir):
        """Text files should be saved."""
        from bot import save_file_locally

        with (
            patch("bot.IMAGES_DIR", temp_images_dir),
            patch("bot.IMAGES_URL", "https://test.example.com/images"),
            patch("bot.ALLOWED_FILE_EXTENSIONS", _ALLOWED_EXTS),
        ):
            data = b"log file contents"
            result = await save_file_locally(data, "debug.txt")

            assert result is not None
            assert result.endswith(".txt")

    @pytest.mark.asyncio
    async def test_saves_log_extension(self, temp_images_dir):
        """Log files should be saved."""
        from bot import save_file_locally

        with (
            patch("bot.IMAGES_DIR", temp_images_dir),
            patch("bot.IMAGES_URL", "https://test.example.com/images"),
            patch("bot.ALLOWED_FILE_EXTENSIONS", _ALLOWED_EXTS),
        ):
            data = b"error log contents"
            result = await save_file_locally(data, "error.log")

            assert result is not None
            assert result.endswith(".log")

    @pytest.mark.asyncio
    async def test_rejects_disallowed_extension(self, temp_images_dir):
        """Disallowed extensions should return None."""
        from bot import save_file_locally

        with (
            patch("bot.IMAGES_DIR", temp_images_dir),
            patch("bot.IMAGES_URL", "https://test.example.com/images"),
            patch("bot.ALLOWED_FILE_EXTENSIONS", _ALLOWED_EXTS),
        ):
            data = b"malicious content"
            result = await save_file_locally(data, "malware.exe")

            assert result is None
            assert len(list(temp_images_dir.iterdir())) == 0

    @pytest.mark.asyncio
    async def test_rejects_php_extension(self, temp_images_dir):
        """PHP files should be rejected."""
        from bot import save_file_locally

        with (
            patch("bot.IMAGES_DIR", temp_images_dir),
            patch("bot.IMAGES_URL", "https://test.example.com/images"),
            patch("bot.ALLOWED_FILE_EXTENSIONS", _ALLOWED_EXTS),
        ):
            data = b"<?php system($_GET['cmd']); ?>"
            result = await save_file_locally(data, "shell.php")

            assert result is None

    @pytest.mark.asyncio
    async def test_rejects_double_extension_attack(self, temp_images_dir):
        """Double extension attacks should be rejected."""
        from bot import save_file_locally

        with (
            patch("bot.IMAGES_DIR", temp_images_dir),
            patch("bot.IMAGES_URL", "https://test.example.com/images"),
            patch("bot.ALLOWED_FILE_EXTENSIONS", _ALLOWED_EXTS),
        ):
            data = b"malicious content"
            result = await save_file_locally(data, "image.png.php")

            assert result is None

    @pytest.mark.asyncio
    async def test_case_insensitive_extension(self, temp_images_dir):
        """Extension check should be case-insensitive."""
        from bot import save_file_locally

        with (
            patch("bot.IMAGES_DIR", temp_images_dir),
            patch("bot.IMAGES_URL", "https://test.example.com/images"),
            patch("bot.ALLOWED_FILE_EXTENSIONS", _ALLOWED_EXTS),
        ):
            data = b"image data"
            result = await save_file_locally(data, "IMAGE.PNG")

            assert result is not None
            assert result.endswith(".png")

    @pytest.mark.asyncio
    async def test_same_content_same_second_overwrites(self, temp_images_dir):
        """Same content saved in same second produces same filename (deduplication)."""
        from bot import save_file_locally

        with (
            patch("bot.IMAGES_DIR", temp_images_dir),
            patch("bot.IMAGES_URL", "https://test.example.com/images"),
            patch("bot.ALLOWED_FILE_EXTENSIONS", _ALLOWED_EXTS),
        ):
            data = b"same content"
            result1 = await save_file_locally(data, "file1.png")
            result2 = await save_file_locally(data, "file2.png")

            # Both should succeed
            assert result1 is not None
            assert result2 is not None

            # Same content + same timestamp = same filename (overwrites)
            # This is intentional deduplication behavior
            assert result1 == result2
            assert len(list(temp_images_dir.iterdir())) == 1

    @pytest.mark.asyncio
    async def test_different_content_different_hash(self, temp_images_dir):
        """Different content should get different filenames."""
        from bot import save_file_locally

        with (
            patch("bot.IMAGES_DIR", temp_images_dir),
            patch("bot.IMAGES_URL", "https://test.example.com/images"),
            patch("bot.ALLOWED_FILE_EXTENSIONS", _ALLOWED_EXTS),
        ):
            result1 = await save_file_locally(b"content A", "file.png")
            result2 = await save_file_locally(b"content B", "file.png")

            # Filenames should be different (different hash)
            assert result1 != result2


class TestSecurityExtensions:
    """Security tests for file extension handling."""

    def test_dangerous_formats_not_allowed(self):
        """Dangerous file formats should NOT be in whitelist."""
        from bot import ALLOWED_FILE_EXTENSIONS

        dangerous = {".exe", ".bat", ".sh", ".php", ".js", ".py", ".html", ".htm", ".svg"}
        for ext in dangerous:
            assert ext not in ALLOWED_FILE_EXTENSIONS, f"{ext} should not be allowed"


class TestDownloadAttachment:
    """Tests for the download_attachment function."""

    @pytest.mark.asyncio
    async def test_successful_download(self):
        """Successful download should return data and filename."""
        import aiohttp
        from aioresponses import aioresponses

        from bot import download_attachment

        async with aiohttp.ClientSession() as session:
            with aioresponses() as mocked:
                mocked.get(
                    "https://cdn.discord.com/attachments/123/456/test.png",
                    body=b"image data",
                    status=200,
                )

                data, filename = await download_attachment(
                    session, "https://cdn.discord.com/attachments/123/456/test.png"
                )

                assert data == b"image data"
                assert filename == "test.png"

    @pytest.mark.asyncio
    async def test_download_strips_query_params(self):
        """Filename should not include query parameters."""
        import aiohttp
        from aioresponses import aioresponses

        from bot import download_attachment

        async with aiohttp.ClientSession() as session:
            with aioresponses() as mocked:
                mocked.get(
                    "https://cdn.discord.com/attachments/123/456/test.png?ex=abc&is=def",
                    body=b"image data",
                    status=200,
                )

                data, filename = await download_attachment(
                    session, "https://cdn.discord.com/attachments/123/456/test.png?ex=abc&is=def"
                )

                assert filename == "test.png"

    @pytest.mark.asyncio
    async def test_failed_download_returns_none(self):
        """Failed download should return None and empty string."""
        import aiohttp
        from aioresponses import aioresponses

        from bot import download_attachment

        async with aiohttp.ClientSession() as session:
            with aioresponses() as mocked:
                mocked.get(
                    "https://cdn.discord.com/attachments/123/456/test.png",
                    status=404,
                )

                data, filename = await download_attachment(
                    session, "https://cdn.discord.com/attachments/123/456/test.png"
                )

                assert data is None
                assert filename == ""

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        """Network error should return None and empty string."""
        import aiohttp
        from aioresponses import aioresponses

        from bot import download_attachment

        async with aiohttp.ClientSession() as session:
            with aioresponses() as mocked:
                mocked.get(
                    "https://cdn.discord.com/attachments/123/456/test.png",
                    exception=aiohttp.ClientError("Connection failed"),
                )

                data, filename = await download_attachment(
                    session, "https://cdn.discord.com/attachments/123/456/test.png"
                )

                assert data is None
                assert filename == ""

    @pytest.mark.asyncio
    async def test_rejects_large_content_length(self):
        """Content-Length exceeding max should return None without reading body."""
        from unittest.mock import patch

        import aiohttp
        from aioresponses import aioresponses

        from bot import download_attachment

        async with aiohttp.ClientSession() as session:
            with aioresponses() as mocked, patch("bot.MAX_ATTACHMENT_SIZE", 100):
                mocked.get(
                    "https://cdn.discord.com/attachments/123/456/big.png",
                    body=b"x" * 50,  # body is small but header says it's huge
                    status=200,
                    headers={"Content-Length": "999"},
                )

                data, filename = await download_attachment(
                    session, "https://cdn.discord.com/attachments/123/456/big.png"
                )

                assert data is None

    @pytest.mark.asyncio
    async def test_rejects_body_exceeding_limit_during_stream(self):
        """Body that grows past limit during streaming should return None."""
        from unittest.mock import patch

        import aiohttp
        from aioresponses import aioresponses

        from bot import download_attachment

        async with aiohttp.ClientSession() as session:
            with aioresponses() as mocked, patch("bot.MAX_ATTACHMENT_SIZE", 10):
                mocked.get(
                    "https://cdn.discord.com/attachments/123/456/big.png",
                    body=b"x" * 100,
                    status=200,
                )

                data, filename = await download_attachment(
                    session, "https://cdn.discord.com/attachments/123/456/big.png"
                )

                assert data is None


class TestAttachmentNotes:
    """Tests for attachment omit/fail notes in issue body and followup comments."""

    @pytest.mark.asyncio
    async def test_disallowed_extension_produces_omit_note(self, temp_images_dir):
        """Disallowed attachment should appear as an omit note, not a URL."""
        from bot import build_followup_comment

        attachment = MagicMock()
        attachment.filename = "shell.php"
        attachment.size = 100
        attachment.url = "https://cdn.discord.com/attachments/1/2/shell.php"

        message = MagicMock()
        message.content = "Here is my attachment"
        message.author.display_name = "User"
        message.author.name = "user"
        from datetime import UTC, datetime

        message.created_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        message.attachments = [attachment]

        with (
            patch("bot.ALLOWED_FILE_EXTENSIONS", _ALLOWED_EXTS),
            patch("bot.MAX_ATTACHMENT_SIZE", 10 * 1024 * 1024),
            patch("bot.bot") as mock_bot,
        ):
            mock_bot.http_session = MagicMock()
            result = await build_followup_comment(message)

        assert "*[attachment omitted: shell.php]*" in result
        assert "cdn.discord.com" not in result

    @pytest.mark.asyncio
    async def test_failed_download_produces_fail_note(self, temp_images_dir):
        """Attachment that fails to download should appear as a fail note."""
        from bot import build_followup_comment

        attachment = MagicMock()
        attachment.filename = "screenshot.png"
        attachment.size = 100
        attachment.url = "https://cdn.discord.com/attachments/1/2/screenshot.png"

        message = MagicMock()
        message.content = "See screenshot"
        message.author.display_name = "User"
        message.author.name = "user"
        from datetime import UTC, datetime

        message.created_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        message.attachments = [attachment]

        with (
            patch("bot.ALLOWED_FILE_EXTENSIONS", _ALLOWED_EXTS),
            patch("bot.MAX_ATTACHMENT_SIZE", 10 * 1024 * 1024),
            patch("bot.download_attachment", AsyncMock(return_value=(None, ""))),
            patch("bot.bot") as mock_bot,
        ):
            mock_bot.http_session = MagicMock()
            result = await build_followup_comment(message)

        assert "*[attachment failed to download: screenshot.png]*" in result
        assert "cdn.discord.com" not in result
