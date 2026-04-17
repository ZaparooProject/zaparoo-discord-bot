#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "discord.py>=2.3.0",
#     "aiohttp>=3.9.0",
#     "python-dotenv>=1.0.0",
#     "PyGithub>=2.1.0",
#     "google-genai>=1.73.1",
# ]
# ///
"""
Discord Issue Bot

React to Discord messages with emoji to create GitHub issues.
Only responds to reactions from users with the authorized role.
"""

import asyncio
import hashlib
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, TypedDict

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from github import Auth, Github
from google import genai
from google.genai import types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

load_dotenv()

# Secrets and deployment-specific settings from .env
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
AUTHORIZED_ROLE_ID = int(os.getenv("AUTHORIZED_ROLE_ID", "0"))
_app_id = os.getenv("GITHUB_APP_ID")
GITHUB_APP_ID = int(_app_id) if _app_id else None
GITHUB_APP_PRIVATE_KEY_PATH = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
_app_install = os.getenv("GITHUB_APP_INSTALLATION_ID")
GITHUB_APP_INSTALLATION_ID = int(_app_install) if _app_install else None
IMAGES_URL = os.getenv("IMAGES_URL", "")

# Hardcoded config
GEMINI_MODEL = "gemini-2.5-flash"
IMAGES_DIR = Path("./images")
MAX_ATTACHMENT_SIZE = 10_485_760
CONTEXT_MESSAGES = 5
CONTEXT_GAP_SECONDS = 600
PENDING_TIMEOUT = 60
ISSUE_RATE_LIMIT_SECONDS = 10

PROJECTS = {
    "🖥️": ("ZaparooProject/zaparoo-core", "Core"),
    "📱": ("ZaparooProject/zaparoo-app", "App"),
    "🎨": ("ZaparooProject/zaparoo-designer", "Designer"),
}
DEFAULT_PROJECT = list(PROJECTS.values())[0]

ISSUE_TYPES = {
    "🐛": "bug",
    "💡": "enhancement",
    "📋": None,
}

PROJECT_DESCRIPTIONS = {
    "ZaparooProject/zaparoo-core": (
        "Core: The main service that runs on hardware (MiSTer, Steam Deck, "
        "Raspberry Pi, etc). Handles NFC tag reading/writing, launching games/media, "
        "REST API server, hardware integration, platform readers, and system service."
    ),
    "ZaparooProject/zaparoo-app": (
        "App: The mobile/desktop companion app. UI for browsing media libraries, "
        "managing NFC cards, searching games, and configuring the core service remotely."
    ),
    "ZaparooProject/zaparoo-designer": (
        "Designer: Web-based card and label design tool. Templates, custom artwork, "
        "printing layouts, and label generation for NFC cards."
    ),
}


class SupportButton(TypedDict, total=False):
    label: str
    url: str


class SupportResponse(TypedDict, total=False):
    name: str
    title: str
    message: str
    buttons: list[SupportButton]


SUPPORT_RESPONSES: list[SupportResponse] = [
    {
        "name": "Request Logs",
        "title": "📋 Log File Needed",
        "message": (
            "To help troubleshoot, please send us your log file:\n"
            "\n"
            "1. Open the **Zaparoo App** or **TUI**\n"
            "2. Go to **Settings > Advanced > View logs**\n"
            "3. Tap **Upload** to get a shareable link\n"
            "4. Paste the link here"
        ),
        "buttons": [
            {"label": "📄 Log Guide", "url": "https://zaparoo.org/support/#collecting-logs"},
            {"label": "Full Support Page", "url": "https://zaparoo.org/support/"},
        ],
    },
    {
        "name": "Enable Debug Mode",
        "title": "🔍 Debug Logging Needed",
        "message": (
            "Please enable debug logging and reproduce the issue:\n"
            "\n"
            "1. Open the **Zaparoo App** or **TUI**\n"
            "2. Go to **Settings > Advanced**\n"
            "3. Enable **Debug Logging**\n"
            "4. Reproduce the issue\n"
            "5. Upload the log file (**Settings > Advanced > View logs > Upload**)\n"
            "6. Paste the shareable link here"
        ),
        "buttons": [
            {"label": "🔍 Debug Guide", "url": "https://zaparoo.org/support/#debug-logging"},
            {"label": "📄 Log Guide", "url": "https://zaparoo.org/support/#collecting-logs"},
        ],
    },
]

# Allowed file extensions (security whitelist)
ALLOWED_FILE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt", ".log"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Pending project selections: message_id -> (project_repo, project_name, timestamp)
pending_projects: dict[int, tuple[str, str, float]] = {}


class RecentIssue(NamedTuple):
    bot_reply_msg_id: int
    repo_name: str
    issue_number: int
    target_author_id: int
    timestamp: float


# Track recently created issues for follow-up attachment (channel_id -> entries)
recent_issues: dict[int, list[RecentIssue]] = {}
RECENT_ISSUE_TTL = 86400  # 24 hours

# Per-user issue creation timestamps for rate limiting
_user_issue_timestamps: dict[int, float] = {}

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.messages = True
intents.members = True


def make_support_callback(response_config: dict):
    """Create a context menu callback for a support response."""
    title = response_config.get("title", "Support")
    message_text = response_config.get("message", "")
    buttons_config = response_config.get("buttons", [])

    async def callback(interaction: discord.Interaction, message: discord.Message):
        if not isinstance(interaction.user, discord.Member) or not has_authorized_role(
            interaction.user
        ):
            await interaction.response.send_message(
                "You don't have permission to use this.", ephemeral=True
            )
            return

        embed = discord.Embed(title=title, description=message_text, color=0x2B2D31)

        view = discord.ui.View()
        for btn in buttons_config:
            url = btn.get("url", "")
            if not url:
                continue
            view.add_item(
                discord.ui.Button(
                    style=discord.ButtonStyle.link,
                    label=btn.get("label", "Link"),
                    url=url,
                )
            )

        await interaction.response.send_message("Sent!", ephemeral=True)
        try:
            await message.reply(embed=embed, view=view, mention_author=False)
        except Exception:
            logging.exception("Failed to send support response")

    return callback


class CreateIssueModal(discord.ui.Modal, title="Create Issue"):
    """Modal with project and issue type selects for creating GitHub issues."""

    project = discord.ui.Select(
        placeholder="Select a project...",
        options=[
            discord.SelectOption(label=name, value=repo, emoji=emoji)
            for emoji, (repo, name) in PROJECTS.items()
        ],
        row=0,
    )
    issue_type = discord.ui.Select(
        placeholder="Select issue type...",
        options=[
            discord.SelectOption(
                label=label.replace("-", " ").title() if label else "General Issue",
                value=label if label else "__none__",
                emoji=emoji,
            )
            for emoji, label in ISSUE_TYPES.items()
        ],
        row=1,
    )

    def __init__(self, target_message: discord.Message):
        super().__init__()
        self.target_message = target_message

    async def on_submit(self, interaction: discord.Interaction):
        remaining = _check_rate_limit(interaction.user.id)
        if remaining is not None:
            await interaction.response.send_message(
                f"Rate-limited. Try again in {remaining:.0f}s.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        repo_name = self.project.values[0]
        project_name = next(n for _, (r, n) in PROJECTS.items() if r == repo_name)
        label = self.issue_type.values[0]
        if label == "__none__":
            label = None

        channel = self.target_message.channel
        guild = self.target_message.guild

        try:
            issue_number, issue_url = await create_issue_from_message(
                self.target_message,
                channel,
                guild,
                repo_name,
                project_name,
                label,
            )
            _record_issue_for_rate_limit(interaction.user.id)
            await interaction.followup.send(
                f"Created {project_name} issue #{issue_number}: <{issue_url}>",
                ephemeral=True,
            )
            reply_msg = await self.target_message.reply(
                f"Created {project_name} issue #{issue_number}: <{issue_url}>",
                mention_author=False,
            )
            await self.target_message.add_reaction("✅")

            record_recent_issue(
                channel.id,
                reply_msg.id,
                repo_name,
                issue_number,
                self.target_message.author.id,
            )
        except Exception:
            logging.exception("Failed to create issue via context menu")
            try:
                await interaction.followup.send(
                    "Failed to create issue. Check bot logs for details.",
                    ephemeral=True,
                )
            except Exception:
                pass


async def create_issue_callback(interaction: discord.Interaction, message: discord.Message):
    """Context menu callback for the Create Issue command."""
    if not isinstance(interaction.user, discord.Member) or not has_authorized_role(
        interaction.user
    ):
        await interaction.response.send_message(
            "You don't have permission to use this.", ephemeral=True
        )
        return
    modal = CreateIssueModal(target_message=message)
    await interaction.response.send_modal(modal)


class IssueBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.http_session: aiohttp.ClientSession | None = None
        self._tree_synced: bool = False

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        for response_config in SUPPORT_RESPONSES:
            name = response_config.get("name", "Support")
            callback = make_support_callback(response_config)
            cmd = discord.app_commands.ContextMenu(name=name, callback=callback)
            self.tree.add_command(cmd)

        cmd = discord.app_commands.ContextMenu(name="Create Issue", callback=create_issue_callback)
        self.tree.add_command(cmd)

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()


bot = IssueBot()
gemini_client: genai.Client | None = None
github_client: Github | None = None


def init():
    """Initialize API clients and create directories. Called at startup."""
    global gemini_client, github_client

    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

    if GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY_PATH and GITHUB_APP_INSTALLATION_ID:
        private_key = Path(GITHUB_APP_PRIVATE_KEY_PATH).read_text()
        app_auth = Auth.AppAuth(int(GITHUB_APP_ID), private_key)
        github_auth = Auth.AppInstallationAuth(app_auth, int(GITHUB_APP_INSTALLATION_ID))
        github_client = Github(auth=github_auth)
        logging.info("Using GitHub App authentication")
    elif GITHUB_TOKEN:
        github_client = Github(auth=Auth.Token(GITHUB_TOKEN))
        logging.info("Using GitHub token authentication")

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)


async def generate_issue_title(body: str) -> str:
    """Generate a concise issue title using Gemini."""
    truncated_body = body[:4000] if len(body) > 4000 else body
    try:
        response = await gemini_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=truncated_body,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "Generate a GitHub issue title from the provided issue body. "
                    "Write a short sentence (8-15 words) that describes the specific "
                    "problem or request. Be descriptive and include relevant details. "
                    "Output only the title, no quotes or prefixes. Use sentence case. "
                    "Do not mention if there are attachments or not."
                ),
                max_output_tokens=60,
                temperature=0.3,
            ),
        )
        return response.text.strip()
    except Exception:
        logging.exception("Failed to generate title")
        return "Issue from Discord"


async def detect_project(message_content: str) -> tuple[str, str] | None:
    """Use Gemini to detect which project a message relates to.

    Returns (repo_name, project_name) or None if unclear.
    """
    descriptions = "\n".join(f"- {repo}: {desc}" for repo, desc in PROJECT_DESCRIPTIONS.items())
    try:
        response = await gemini_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=message_content,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You classify Discord support messages to the correct project.\n\n"
                    f"Projects:\n{descriptions}\n\n"
                    "Based on the message, determine which project it relates to. "
                    "Reply with ONLY the repository name (e.g., 'ZaparooProject/zaparoo-core'). "
                    "If the message could apply to multiple projects or is unclear, "
                    "reply 'unknown'."
                ),
                temperature=0.0,
                max_output_tokens=30,
            ),
        )
        result = response.text.strip()
        for _, (repo, name) in PROJECTS.items():
            if repo in result:
                return repo, name
    except Exception:
        logging.exception("Failed to detect project")
    return None


async def walk_reply_chain(
    message: discord.Message,
    channel: discord.abc.Messageable,
    max_depth: int = 10,
) -> list[discord.Message]:
    """Walk the reply chain from a message upward."""
    chain = []
    current = message
    for _ in range(max_depth):
        if not current.reference or not current.reference.message_id:
            break
        try:
            parent = await channel.fetch_message(current.reference.message_id)
            chain.append(parent)
            current = parent
        except Exception:
            break
    chain.reverse()
    return chain


def segment_by_time_gap(
    candidates: list[discord.Message],
    target_message: discord.Message,
    gap_seconds: int = 600,
) -> list[discord.Message]:
    """Find messages in the same conversation as the target by walking backward."""
    if not candidates:
        return []

    # Check if most recent candidate is close to target
    most_recent = candidates[-1]
    if (target_message.created_at - most_recent.created_at).total_seconds() > gap_seconds:
        return []

    segment = [most_recent]
    for i in range(len(candidates) - 2, -1, -1):
        gap = (candidates[i + 1].created_at - candidates[i].created_at).total_seconds()
        if gap > gap_seconds:
            break
        segment.append(candidates[i])

    segment.reverse()
    return segment


async def filter_context_with_llm(
    target_message: discord.Message,
    candidates: list[discord.Message],
) -> list[discord.Message]:
    """Use Gemini to filter context messages for relevance to the target."""
    if not candidates:
        return []

    messages_text = "\n".join(
        f"[{i}] {msg.author.display_name}: {msg.content}" for i, msg in enumerate(candidates)
    )

    prompt = (
        f"Target message (the one being reported as an issue):\n"
        f"{target_message.author.display_name}: {target_message.content}\n\n"
        f"Candidate context messages:\n{messages_text}\n\n"
        f"Which of these messages are about the same topic/issue as the target? "
        f"Return ONLY the indices as comma-separated numbers (e.g., '0,2,5'). "
        f"If none are relevant, return 'none'."
    )

    response = await gemini_client.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=(
                "You filter Discord messages for relevance to a specific issue report. "
                "Include messages that discuss the same problem, provide additional context, "
                "share related experiences, or contain relevant error messages/logs. "
                "Exclude unrelated conversations, greetings, and off-topic messages."
            ),
            temperature=0.0,
            max_output_tokens=100,
        ),
    )

    if not response.text:
        return candidates[:CONTEXT_MESSAGES]

    result_text = response.text.strip().lower()
    if result_text == "none":
        return []

    try:
        indices = [int(x.strip()) for x in result_text.split(",")]
        return [candidates[i] for i in indices if 0 <= i < len(candidates)]
    except (ValueError, IndexError):
        logging.warning(f"Could not parse LLM context filter response: {result_text}")
        return candidates[:CONTEXT_MESSAGES]


async def gather_context(
    target_message: discord.Message,
    channel: discord.abc.Messageable,
) -> list[discord.Message]:
    """Gather relevant context using reply chains, time gaps, and LLM filtering."""
    # Step 1: Walk reply chain
    reply_chain = await walk_reply_chain(target_message, channel)
    if len(reply_chain) >= 2:
        return reply_chain

    # Step 2: Fetch larger window and segment by time gaps
    candidates = []
    try:
        async for msg in channel.history(limit=50, before=target_message):
            candidates.append(msg)
        candidates.reverse()
    except Exception:
        logging.exception("Could not fetch history")
        return reply_chain

    segmented = segment_by_time_gap(candidates, target_message, CONTEXT_GAP_SECONDS)
    if not segmented:
        return reply_chain

    # Step 3: LLM filtering
    try:
        filtered = await filter_context_with_llm(target_message, segmented)
        return filtered if filtered else reply_chain
    except Exception:
        logging.exception("LLM context filtering failed, using time-segmented results")
        return segmented[:CONTEXT_MESSAGES]


async def download_attachment(session: aiohttp.ClientSession, url: str) -> tuple[bytes | None, str]:
    """Download an attachment and return (data, filename)."""
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return None, ""
            try:
                content_length = int(response.headers.get("Content-Length", 0))
            except ValueError:
                content_length = 0
            if content_length > MAX_ATTACHMENT_SIZE:
                logging.warning(
                    f"Skipping attachment: Content-Length {content_length} exceeds limit"
                )
                return None, ""
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.content.iter_chunked(65536):
                total += len(chunk)
                if total > MAX_ATTACHMENT_SIZE:
                    logging.warning(f"Attachment exceeded size limit during download: {url}")
                    return None, ""
                chunks.append(chunk)
            data = b"".join(chunks)
            filename = url.split("/")[-1].split("?")[0]
            return data, filename
    except Exception:
        logging.exception("Failed to download attachment")
    return None, ""


async def save_file_locally(data: bytes, original_filename: str) -> str | None:
    """Save file to local directory and return the public URL, or None if not allowed."""
    ext = Path(original_filename).suffix.lower()
    # Reject non-whitelisted extensions
    if ext not in ALLOWED_FILE_EXTENSIONS:
        logging.debug(f"Skipping disallowed extension: {original_filename}")
        return None

    # Create unique filename with hash to avoid collisions
    file_hash = hashlib.sha256(data).hexdigest()[:12]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{file_hash}{ext}"

    filepath = IMAGES_DIR / filename
    # Use async I/O to avoid blocking the event loop
    await asyncio.to_thread(filepath.write_bytes, data)

    return f"{IMAGES_URL.rstrip('/')}/{filename}"


def _escape_markdown_text(text: str) -> str:
    """Escape characters that could inject markdown in prose and link label contexts."""
    return (
        text.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


_USER_MENTION_RE = re.compile(r"<@!?\d+>")
_ROLE_MENTION_RE = re.compile(r"<@&\d+>")


def _sanitize_mentions(text: str) -> str:
    """Replace user/role Discord mentions with placeholders; keep channel mentions."""
    text = _USER_MENTION_RE.sub("[user]", text)
    return _ROLE_MENTION_RE.sub("[role]", text)


def format_message_for_issue(
    message: discord.Message, author_label: str, is_target: bool = False
) -> str:
    """Format a Discord message for inclusion in GitHub issue."""
    prefix = "**>>> Target Message:**" if is_target else ""

    timestamp = message.created_at.strftime("%Y-%m-%d %H:%M UTC")
    author = f"**{_escape_markdown_text(author_label)}**"

    raw_content = message.content or "*[no text content]*"
    content = _sanitize_mentions(raw_content)

    lines = [
        prefix,
        f"> {author} - {timestamp}",
        "> ",
    ]

    for line in content.split("\n"):
        lines.append(f"> {line}")

    return "\n".join(lines)


def create_github_issue(
    repo_name: str, title: str, body: str, labels: list[str]
) -> tuple[int, str]:
    """Create a GitHub issue and return (number, url)."""
    repo = github_client.get_repo(repo_name)

    existing_labels = [lbl.name for lbl in repo.get_labels()]
    valid_labels = [lbl for lbl in labels if lbl in existing_labels]

    if valid_labels:
        issue = repo.create_issue(title=title, body=body, labels=valid_labels)
    else:
        issue = repo.create_issue(title=title, body=body)

    return issue.number, issue.html_url


def add_comment_to_issue(repo_name: str, issue_number: int, body: str) -> str:
    """Add a comment to a GitHub issue. Returns comment URL. Synchronous (PyGithub)."""
    repo = github_client.get_repo(repo_name)
    issue = repo.get_issue(number=issue_number)
    comment = issue.create_comment(body)
    return comment.html_url


async def build_followup_comment(message: discord.Message) -> str:
    """Build a markdown comment from a follow-up Discord message, including attachments."""
    parts = ["*Follow-up from Discord*", ""]
    parts.append(format_message_for_issue(message, "Reporter"))
    parts.append("")

    attachment_urls = []
    attachment_notes = []
    for attachment in message.attachments:
        ext = Path(attachment.filename).suffix.lower()
        if ext not in ALLOWED_FILE_EXTENSIONS:
            attachment_notes.append(
                f"*[attachment omitted: {_escape_markdown_text(attachment.filename)}]*"
            )
            continue
        if attachment.size > MAX_ATTACHMENT_SIZE:
            continue
        data, filename = await download_attachment(bot.http_session, attachment.url)
        if data:
            local_url = await save_file_locally(data, filename)
            if local_url:
                attachment_urls.append((attachment.filename, local_url))
                continue
        attachment_notes.append(
            f"*[attachment failed to download: {_escape_markdown_text(attachment.filename)}]*"
        )

    if attachment_notes:
        parts.extend(attachment_notes)
        parts.append("")

    if attachment_urls:
        parts.append("### Attachments")
        parts.append("")
        for filename, url in attachment_urls:
            safe_name = _escape_markdown_text(filename)
            ext = Path(filename).suffix.lower()
            if ext in IMAGE_EXTENSIONS:
                parts.append(f"![{safe_name}]({url})")
            else:
                parts.append(f"[{safe_name}]({url})")
        parts.append("")

    return "\n".join(parts)


def record_recent_issue(
    channel_id: int,
    bot_reply_msg_id: int,
    repo_name: str,
    issue_number: int,
    target_author_id: int,
):
    """Record a recently created issue for follow-up attachment."""
    cleanup_pending()
    if channel_id not in recent_issues:
        recent_issues[channel_id] = []
    recent_issues[channel_id].append(
        RecentIssue(
            bot_reply_msg_id,
            repo_name,
            issue_number,
            target_author_id,
            time.monotonic(),
        )
    )


async def create_issue_from_message(
    target_message: discord.Message,
    channel: discord.abc.Messageable,
    guild: discord.Guild,
    repo_name: str,
    project_name: str,
    label: str | None,
) -> tuple[int, str]:
    """Gather context, build issue body, generate title, and create a GitHub issue.

    Returns (issue_number, issue_url). Raises on failure.
    """
    # Gather relevant context using smart filtering
    context_messages = await gather_context(target_message, channel)

    # Build anonymized author map: target becomes "Reporter", others "User A/B/C..."
    author_map: dict[int, str] = {}
    label_counter = 0
    for msg in context_messages:
        if msg.author.id not in author_map:
            author_map[msg.author.id] = f"User {chr(ord('A') + min(label_counter, 25))}"
            label_counter += 1
    author_map[target_message.author.id] = "Reporter"

    # Process attachments
    attachment_urls: list[tuple[str, str]] = []
    attachment_notes: list[str] = []
    for msg in context_messages + [target_message]:
        for attachment in msg.attachments:
            ext = Path(attachment.filename).suffix.lower()
            if ext not in ALLOWED_FILE_EXTENSIONS:
                attachment_notes.append(
                    f"*[attachment omitted: {_escape_markdown_text(attachment.filename)}]*"
                )
                continue
            if attachment.size > MAX_ATTACHMENT_SIZE:
                logging.warning(
                    f"Skipping large attachment: {attachment.filename} ({attachment.size} bytes)"
                )
                continue
            data, filename = await download_attachment(bot.http_session, attachment.url)
            if data:
                local_url = await save_file_locally(data, filename)
                if local_url:
                    attachment_urls.append((attachment.filename, local_url))
                    continue
            attachment_notes.append(
                f"*[attachment failed to download: {_escape_markdown_text(attachment.filename)}]*"
            )

    # Build issue body
    guild_name = _escape_markdown_text(guild.name)
    discord_url = f"https://discord.com/channels/{guild.id}/{channel.id}/{target_message.id}"

    if isinstance(channel, discord.Thread):
        parent_name = _escape_markdown_text(channel.parent.name if channel.parent else "unknown")
        channel_display = f"#{parent_name} → {_escape_markdown_text(channel.name)}"
    else:
        channel_display = (
            f"#{_escape_markdown_text(channel.name)}"
            if hasattr(channel, "name")
            else "Direct Message"
        )

    body_parts = [
        "*Issue created from Discord*",
        "",
        f"**Source:** [{guild_name} / {channel_display}]({discord_url})",
        "",
        "---",
        "",
    ]

    body_parts.append("### Reported Message")
    body_parts.append("")
    body_parts.append(format_message_for_issue(target_message, "Reporter", is_target=True))
    body_parts.append("")

    if context_messages:
        body_parts.append("### Context (previous messages)")
        body_parts.append("")
        for msg in context_messages:
            body_parts.append(format_message_for_issue(msg, author_map.get(msg.author.id, "User")))
            body_parts.append("")

    if attachment_notes:
        body_parts.extend(attachment_notes)
        body_parts.append("")

    if attachment_urls:
        body_parts.append("### Attachments")
        body_parts.append("")
        for filename, url in attachment_urls:
            safe_name = _escape_markdown_text(filename)
            ext = Path(filename).suffix.lower()
            if ext in IMAGE_EXTENSIONS:
                body_parts.append(f"![{safe_name}]({url})")
            else:
                body_parts.append(f"[{safe_name}]({url})")
        body_parts.append("")

    body = "\n".join(body_parts)

    # Generate issue title using LLM
    title = await generate_issue_title(body)

    # Build labels
    labels = []
    if label:
        labels.append(label)

    # Create issue
    issue_number, issue_url = await asyncio.to_thread(
        create_github_issue, repo_name, title, body, labels
    )

    logging.info(f"Created {project_name} issue #{issue_number}: {title}")
    return issue_number, issue_url


def cleanup_pending():
    """Remove expired pending selections, old recent issues, and stale rate limit entries."""
    now = time.monotonic()
    expired = [
        msg_id
        for msg_id, (_, _, timestamp) in pending_projects.items()
        if now - timestamp > PENDING_TIMEOUT
    ]
    for msg_id in expired:
        del pending_projects[msg_id]

    for channel_id in list(recent_issues):
        recent_issues[channel_id] = [
            entry
            for entry in recent_issues[channel_id]
            if now - entry.timestamp <= RECENT_ISSUE_TTL
        ]
        if not recent_issues[channel_id]:
            del recent_issues[channel_id]

    stale_users = [
        uid for uid, ts in _user_issue_timestamps.items() if now - ts > ISSUE_RATE_LIMIT_SECONDS * 2
    ]
    for uid in stale_users:
        del _user_issue_timestamps[uid]


def _check_rate_limit(user_id: int) -> float | None:
    """Return seconds remaining in cooldown if rate-limited, else None."""
    last = _user_issue_timestamps.get(user_id)
    if last is not None:
        elapsed = time.monotonic() - last
        if elapsed < ISSUE_RATE_LIMIT_SECONDS:
            return ISSUE_RATE_LIMIT_SECONDS - elapsed
    return None


def _record_issue_for_rate_limit(user_id: int) -> None:
    _user_issue_timestamps[user_id] = time.monotonic()


def has_authorized_role(member: discord.Member) -> bool:
    """Check if a member has the authorized role."""
    return any(role.id == AUTHORIZED_ROLE_ID for role in member.roles)


async def process_reaction(payload: discord.RawReactionActionEvent):
    """Process a reaction and potentially create a GitHub issue."""
    # Only works in guilds (not DMs)
    if not payload.guild_id:
        return

    # Check if user has the authorized role
    if not payload.member or not has_authorized_role(payload.member):
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    emoji = str(payload.emoji)
    message_id = payload.message_id

    # Clean up old pending selections
    cleanup_pending()

    # Check if this is a project selection
    if emoji in PROJECTS:
        repo, name = PROJECTS[emoji]
        pending_projects[message_id] = (repo, name, time.monotonic())
        logging.info(f"Project selected: {name} ({repo}) for message {message_id}")

        # Add a visual indicator
        channel = bot.get_channel(payload.channel_id)
        if channel:
            try:
                message = await channel.fetch_message(message_id)
                await message.add_reaction("⏳")
            except Exception:
                pass
        return

    # Fetch channel (needed for all paths below)
    channel = bot.get_channel(payload.channel_id)
    if not channel:
        try:
            channel = await bot.fetch_channel(payload.channel_id)
        except Exception:
            logging.exception("Could not fetch channel")
            return

    # Handle 📎 follow-up attachment
    if emoji == "📎":
        entries = recent_issues.get(payload.channel_id, [])
        if not entries:
            return
        try:
            target_message = await channel.fetch_message(message_id)
        except Exception:
            return
        # Find most recent issue from this message's author
        author_id = target_message.author.id
        match = None
        for entry in reversed(entries):
            if entry.target_author_id == author_id:
                match = entry
                break
        if not match:
            return

        remaining = _check_rate_limit(payload.user_id)
        if remaining is not None:
            try:
                await target_message.reply(
                    f"Rate-limited. Try again in {remaining:.0f}s.",
                    mention_author=False,
                )
            except Exception:
                pass
            return

        try:
            await target_message.remove_reaction("📎", payload.member)
            await target_message.add_reaction("⏳")
        except Exception:
            pass
        try:
            comment_body = await build_followup_comment(target_message)
            comment_url = await asyncio.to_thread(
                add_comment_to_issue, match.repo_name, match.issue_number, comment_body
            )
            _record_issue_for_rate_limit(payload.user_id)
            await target_message.remove_reaction("⏳", bot.user)
            await target_message.add_reaction("✅")
            await target_message.reply(
                f"Attached to issue #{match.issue_number}: <{comment_url}>",
                mention_author=False,
            )
            logging.info(f"Attached follow-up to issue #{match.issue_number}")
        except Exception:
            logging.exception("Failed to attach follow-up")
            try:
                await target_message.remove_reaction("⏳", bot.user)
                await target_message.add_reaction("❌")
            except Exception:
                pass
        return

    # Check if this is an issue type
    if emoji not in ISSUE_TYPES:
        return

    label = ISSUE_TYPES[emoji]

    # Fetch target message
    try:
        target_message = await channel.fetch_message(message_id)
    except Exception:
        logging.exception("Could not fetch message")
        return

    # Get project (from pending, auto-detect, or default)
    if message_id in pending_projects:
        repo_name, project_name = pending_projects.pop(message_id)[:2]
    else:
        detected = await detect_project(target_message.content)
        if detected:
            repo_name, project_name = detected
            logging.info(f"Auto-detected project: {project_name}")
        else:
            repo_name, project_name = DEFAULT_PROJECT

    # Remove pending indicator if present
    try:
        await target_message.remove_reaction("⏳", bot.user)
    except Exception:
        pass

    # Rate limit check
    remaining = _check_rate_limit(payload.user_id)
    if remaining is not None:
        try:
            await target_message.reply(
                f"Rate-limited. Try again in {remaining:.0f}s.",
                mention_author=False,
            )
        except Exception:
            pass
        return

    # Create issue using shared logic
    try:
        issue_number, issue_url = await create_issue_from_message(
            target_message,
            channel,
            guild,
            repo_name,
            project_name,
            label,
        )

        _record_issue_for_rate_limit(payload.user_id)
        reply_msg = await target_message.reply(
            f"Created {project_name} issue #{issue_number}: <{issue_url}>",
            mention_author=False,
        )
        await target_message.add_reaction("✅")

        record_recent_issue(
            channel.id,
            reply_msg.id,
            repo_name,
            issue_number,
            target_message.author.id,
        )

    except Exception:
        logging.exception("Failed to create issue")
        await target_message.add_reaction("❌")

        try:
            await target_message.reply(
                "Failed to create issue. Check bot logs for details.",
                mention_author=False,
            )
        except Exception:
            pass


@bot.event
async def on_ready():
    if not bot._tree_synced:
        await bot.tree.sync()
        bot._tree_synced = True
        cmd_count = len(SUPPORT_RESPONSES) + 1
        logging.info(f"Synced {cmd_count} context menu command(s)")
    logging.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logging.info(f"Authorized role ID: {AUTHORIZED_ROLE_ID}")
    logging.info(f"Images: {IMAGES_DIR.absolute()} -> {IMAGES_URL}")
    for emoji, (repo, name) in PROJECTS.items():
        default = " (default)" if (repo, name) == DEFAULT_PROJECT else ""
        logging.info(f"  {emoji} → {name} ({repo}){default}")
    if SUPPORT_RESPONSES:
        for sr in SUPPORT_RESPONSES:
            logging.info(f"  → {sr.get('name', 'unnamed')}")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """Handle reaction add events."""
    await process_reaction(payload)


@bot.event
async def on_message(message: discord.Message):
    """Handle messages — detect replies to bot issue confirmations for follow-up attachment."""
    await bot.process_commands(message)

    if message.author.bot:
        return
    if not message.reference or not message.reference.message_id:
        return
    if not message.guild:
        return

    # Fetch the referenced message
    try:
        ref_msg = message.reference.resolved or await message.channel.fetch_message(
            message.reference.message_id
        )
    except Exception:
        return

    # Check if replying to a bot issue-creation message (not follow-up confirmations)
    if ref_msg.author != bot.user or not ref_msg.content.startswith("Created "):
        return

    # Authorization: original issue author OR authorized role member
    member = message.guild.get_member(message.author.id)
    is_authorized = member and has_authorized_role(member)

    # Check if user is the original issue author
    entries = recent_issues.get(message.channel.id, [])
    is_original_author = any(
        entry.bot_reply_msg_id == ref_msg.id and entry.target_author_id == message.author.id
        for entry in entries
    )

    if not is_authorized and not is_original_author:
        return

    # Parse issue info from the bot message URL
    match = re.search(r"<(https://github\.com/([^/]+/[^/]+)/issues/(\d+))>", ref_msg.content)
    if not match:
        return

    repo_name = match.group(2)
    issue_number = int(match.group(3))

    # Validate repo is a known project
    if not any(repo == repo_name for _, (repo, _) in PROJECTS.items()):
        return

    try:
        comment_body = await build_followup_comment(message)
        comment_url = await asyncio.to_thread(
            add_comment_to_issue, repo_name, issue_number, comment_body
        )
        await message.add_reaction("📎")
        await message.reply(
            f"Attached to issue #{issue_number}: <{comment_url}>",
            mention_author=False,
        )
        logging.info(f"Attached reply follow-up to issue #{issue_number}")
    except Exception:
        logging.exception("Failed to attach reply follow-up")


@bot.command()
async def help(ctx):
    """Show help for the issue bot."""
    if not isinstance(ctx.author, discord.Member) or not has_authorized_role(ctx.author):
        return

    projects_list = "\n".join(
        f"{emoji} = {name} (`{repo}`)" for emoji, (repo, name) in PROJECTS.items()
    )
    default_name = DEFAULT_PROJECT[1]

    help_text = f"""**Discord Issue Bot**

**Two-step flow:**
1. React with project emoji to select project
2. React with issue type to create issue

**Single-step flow:**
- Just react with issue type → bot auto-detects project (fallback: {default_name})

**Projects:**
{projects_list}

**Issue types:**
🐛 = Bug report (`bug` label)
💡 = Feature request (`enhancement` label)
📋 = General issue (no label)

**Follow-up:**
📎 = React on a message to attach it to the most recent issue
Or reply to the bot's issue confirmation message

**Example:**
- React 📱 then 🐛 → Bug on App
- React 🐛 only → Bug on auto-detected project
"""
    await ctx.send(help_text)


async def main():
    await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("Error: DISCORD_TOKEN not set")
        exit(1)
    if not AUTHORIZED_ROLE_ID:
        print("Error: AUTHORIZED_ROLE_ID not set in .env")
        exit(1)
    if not GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY not set")
        exit(1)
    if not IMAGES_URL:
        print("Error: IMAGES_URL not set")
        exit(1)

    init()

    if not github_client:
        print("Error: Set GITHUB_TOKEN or GITHUB_APP_* variables in .env")
        exit(1)

    asyncio.run(main())
