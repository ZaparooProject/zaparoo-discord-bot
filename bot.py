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
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from github import Auth, Github, GithubException, RateLimitExceededException
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
IMAGES_DIR = Path(os.getenv("IMAGES_DIR", "./images"))
STATE_DIR = Path(os.getenv("STATE_DIR", "./state"))

# Hardcoded config
GEMINI_MODEL = "gemini-2.5-flash"
MAX_ATTACHMENT_SIZE = 10_485_760
CONTEXT_MESSAGES = 5
CONTEXT_GAP_SECONDS = 600
PENDING_TIMEOUT = 60
ISSUE_RATE_LIMIT_SECONDS = 10
RECENT_ISSUE_TTL = 86400  # 24 hours
MAX_RECENT_PER_CHANNEL = 50
RECENT_ISSUES_FILE = STATE_DIR / "recent_issues.json"
ISSUE_JOBS_FILE = STATE_DIR / "issue_jobs.json"
ISSUE_JOB_RETRY_BASE_SECONDS = 30
ISSUE_JOB_MAX_ATTEMPTS = 5
ISSUE_JOB_TTL = 86400  # 24 hours

PROJECTS = {
    "🖥️": ("ZaparooProject/zaparoo-core", "Core"),
    "📱": ("ZaparooProject/zaparoo-app", "App"),
    "🎨": ("ZaparooProject/zaparoo-designer", "Designer"),
    "🚀": ("ZaparooProject/zaparoo-frontend", "Frontend"),
}
DEFAULT_PROJECT = list(PROJECTS.values())[0]

ISSUE_TYPES = {
    "🐛": "bug",
    "💡": "enhancement",
    "📋": None,
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
        "name": "Request Troubleshooting",
        "title": "Troubleshooting Info Needed",
        "message": (
            "To help us investigate, please send a log file.\n"
            "\n"
            "**In the App**: go to **Settings > Advanced > View logs** "
            "and tap **Upload** for a shareable link.\n"
            "\n"
            "**In Frontend**: go to "
            "**Settings > Support > Upload log file** for a shareable link.\n"
            "\n"
            "**In the TUI**: go to **Settings > Logs** and select "
            "**Upload** for a shareable link.\n"
            "\n"
            "Paste the link here.\n"
            "\n"
            "**On MiSTer**: logs are wiped on power-off. Collect them "
            "before shutting down.\n"
            "\n"
            "If you can trigger the issue on demand, enable "
            "**Settings > Advanced > Debug Logging** first, reproduce it, "
            "then collect the log. This gives us much more detail."
        ),
        "buttons": [
            {"label": "Docs", "url": "https://zaparoo.org/docs/"},
            {
                "label": "Known Issues",
                "url": "https://github.com/ZaparooProject/zaparoo-core/issues",
            },
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


@dataclass
class IssueJob:
    kind: Literal["create_issue", "followup_comment"]
    user_id: int
    guild_id: int
    channel_id: int
    message_id: int
    repo_name: str
    project_name: str = ""
    label: str | None = None
    issue_number: int | None = None
    attempts: int = 0
    next_run: float = 0
    created_at: float = 0
    id: str = ""


# Track recently created issues for follow-up attachment (channel_id -> entries)
recent_issues: dict[int, list[RecentIssue]] = {}

# Queued issue/comment jobs waiting on local or GitHub rate limits
issue_jobs: list[IssueJob] = []

# Per-user issue creation timestamps for rate limiting
_user_issue_timestamps: dict[int, float] = {}

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.messages = True
intents.members = True


async def send_private_message(
    interaction: discord.Interaction, content: str, *, followup: bool = False
) -> None:
    """Send interaction-scoped private feedback."""
    if followup:
        await interaction.followup.send(content, ephemeral=True)
        return

    is_done = False
    is_done_func = getattr(interaction.response, "is_done", None)
    if callable(is_done_func):
        try:
            response_done = is_done_func()
            is_done = response_done if isinstance(response_done, bool) else False
        except Exception:
            is_done = False

    if is_done:
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)


async def send_private_error(
    interaction: discord.Interaction,
    content: str = "Failed to create issue. Check bot logs for details.",
    *,
    followup: bool = False,
) -> None:
    """Send generic private error feedback, swallowing Discord send failures."""
    try:
        await send_private_message(interaction, content, followup=followup)
    except Exception:
        logging.exception("Failed to send private error")


async def delete_original_response_if_present(interaction: discord.Interaction) -> None:
    """Best-effort cleanup for ephemeral interaction placeholders."""
    try:
        await interaction.delete_original_response()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        logging.debug("Failed to delete interaction response", exc_info=True)


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

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await message.reply(embed=embed, view=view, mention_author=False)
            await delete_original_response_if_present(interaction)
        except Exception:
            logging.exception("Failed to send support response")
            await send_private_error(
                interaction,
                "Failed to send support response. Check bot logs for details.",
                followup=True,
            )

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
        repo_name = self.project.values[0]
        project_name = next(n for _, (r, n) in PROJECTS.items() if r == repo_name)
        label = self.issue_type.values[0]
        if label == "__none__":
            label = None

        channel = self.target_message.channel
        guild = self.target_message.guild
        remaining = _check_rate_limit(interaction.user.id)
        if remaining is not None:
            enqueue_issue_job(
                make_create_issue_job(
                    user_id=interaction.user.id,
                    guild_id=guild.id,
                    channel_id=channel.id,
                    message_id=self.target_message.id,
                    repo_name=repo_name,
                    project_name=project_name,
                    label=label,
                    delay=remaining,
                )
            )
            try:
                await self.target_message.add_reaction("⏳")
            except Exception:
                pass
            await send_private_message(
                interaction,
                "Queued. I'll create the issue when the cooldown clears.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            issue_number, issue_url = await create_issue_and_respond(
                target_message=self.target_message,
                channel=channel,
                guild=guild,
                repo_name=repo_name,
                project_name=project_name,
                label=label,
                user_id=interaction.user.id,
            )
            await interaction.followup.send(
                f"Created {project_name} issue #{issue_number}: <{issue_url}>",
                ephemeral=True,
            )
        except Exception as exc:
            if is_github_rate_limit(exc):
                enqueue_issue_job(
                    make_create_issue_job(
                        user_id=interaction.user.id,
                        guild_id=guild.id,
                        channel_id=channel.id,
                        message_id=self.target_message.id,
                        repo_name=repo_name,
                        project_name=project_name,
                        label=label,
                        delay=retry_delay_for_exception(exc, 1),
                        attempts=1,
                    )
                )
                try:
                    await self.target_message.add_reaction("⏳")
                except Exception:
                    pass
                await send_private_message(
                    interaction,
                    "Queued. I'll create the issue when GitHub accepts it.",
                    followup=True,
                )
                return
            logging.exception("Failed to create issue via context menu")
            await send_private_error(interaction, followup=True)


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
        self._issue_job_worker_task: asyncio.Task | None = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        load_issue_jobs()
        self._issue_job_worker_task = asyncio.create_task(issue_job_worker())
        for response_config in SUPPORT_RESPONSES:
            name = response_config.get("name", "Support")
            callback = make_support_callback(response_config)
            cmd = discord.app_commands.ContextMenu(name=name, callback=callback)
            self.tree.add_command(cmd)

        cmd = discord.app_commands.ContextMenu(name="Create Issue", callback=create_issue_callback)
        self.tree.add_command(cmd)

    async def close(self):
        if self._issue_job_worker_task:
            self._issue_job_worker_task.cancel()
            try:
                await self._issue_job_worker_task
            except asyncio.CancelledError:
                pass
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
    load_recent_issues()


FALLBACK_ISSUE_TITLE = "Issue from Discord"
MAX_ISSUE_TITLE_LENGTH = 100
_AUTHOR_LINE_RE = re.compile(r"^\*\*.+\*\* - \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$")
_ATTACHMENT_LINK_RE = re.compile(r"!?\[([^\]]+)\]\([^)]+\)")
_ATTACHMENT_NOTE_RE = re.compile(r"\*\[attachment (?:omitted|failed to download): ([^\]]+)\]\*")
_MARKDOWN_ESCAPE_RE = re.compile(r"\\([\\`*_\[\]()])")


def _normalize_issue_title(text: str, max_length: int = MAX_ISSUE_TITLE_LENGTH) -> str:
    """Clean and truncate generated or fallback issue titles."""
    title = _MARKDOWN_ESCAPE_RE.sub(r"\1", text)
    title = re.sub(r"\s+", " ", title).strip().strip("\"'")
    if not title:
        return ""
    if len(title) <= max_length:
        return title

    suffix = "…"
    cut = max_length - len(suffix)
    truncated = title[: cut + 1].rsplit(" ", 1)[0].rstrip(".,;:- ")
    if not truncated:
        truncated = title[:cut].rstrip(".,;:- ")
    return f"{truncated}{suffix}"


def _section_lines(body: str, heading: str) -> list[str]:
    """Return lines below a markdown heading until next heading."""
    lines = body.splitlines()
    try:
        start = lines.index(heading) + 1
    except ValueError:
        return []

    section = []
    for line in lines[start:]:
        if line.startswith("### ") or line == "---":
            break
        section.append(line)
    return section


def _extract_reported_message_title(body: str) -> str:
    """Build a fallback title from Reported Message text."""
    message_lines = []
    for line in _section_lines(body, "### Reported Message"):
        if not line.startswith(">"):
            continue
        text = line[1:].strip()
        if not text or text == "*[no text content]*" or _AUTHOR_LINE_RE.match(text):
            continue
        message_lines.append(text)
    return _normalize_issue_title(" ".join(message_lines))


def _extract_attachment_names(body: str) -> list[str]:
    """Find attachment filenames from issue body markdown."""
    names = []
    for line in _section_lines(body, "### Attachments"):
        names.extend(match.group(1) for match in _ATTACHMENT_LINK_RE.finditer(line))
    for line in body.splitlines():
        match = _ATTACHMENT_NOTE_RE.search(line)
        if match:
            names.append(match.group(1))
    return names


def fallback_issue_title(body: str) -> str:
    """Generate a deterministic issue title when Gemini is unavailable."""
    reported_title = _extract_reported_message_title(body)
    if reported_title:
        return reported_title

    attachment_names = _extract_attachment_names(body)
    if attachment_names:
        return _normalize_issue_title(f"Attachment report: {', '.join(attachment_names[:3])}")

    return FALLBACK_ISSUE_TITLE


async def generate_issue_title(body: str) -> str:
    """Generate a concise issue title using Gemini."""
    if not gemini_client:
        logging.warning("Gemini client unavailable, using fallback issue title")
        return fallback_issue_title(body)

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
                    "Focus on the Reported Message. If the reported message has no text "
                    "content, derive the title from the attachment filename(s) instead "
                    "(e.g. 'Crash log report: zaparoo-core-2024-01-15.log')."
                ),
                max_output_tokens=60,
                temperature=0.3,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        title = _normalize_issue_title(response.text or "")
        return title or fallback_issue_title(body)
    except Exception:
        logging.exception("Failed to generate title, using fallback issue title")
        return fallback_issue_title(body)


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
            thinking_config=types.ThinkingConfig(thinking_budget=0),
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


def save_recent_issues() -> None:
    """Atomically write recent_issues to disk."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            str(cid): [list(entry) for entry in entries] for cid, entries in recent_issues.items()
        }
        tmp = RECENT_ISSUES_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(RECENT_ISSUES_FILE)
    except Exception:
        logging.exception("Failed to save recent_issues")


def load_recent_issues() -> None:
    """Load recent_issues from disk. Starts fresh silently on any error."""
    if not RECENT_ISSUES_FILE.exists():
        return
    try:
        raw = json.loads(RECENT_ISSUES_FILE.read_text())
        for cid, entries in raw.items():
            recent_issues[int(cid)] = [RecentIssue(*e) for e in entries]
        cleanup_pending()
        count = sum(len(v) for v in recent_issues.values())
        logging.info(f"Loaded {count} recent issue(s) from state")
    except Exception:
        logging.exception("Failed to load recent_issues, starting fresh")
        recent_issues.clear()


def record_recent_issue(
    channel_id: int,
    bot_reply_msg_id: int,
    repo_name: str,
    issue_number: int,
    target_author_id: int,
):
    """Record a recently created issue for follow-up attachment."""
    cleanup_pending()
    entries = recent_issues.setdefault(channel_id, [])
    entries.append(
        RecentIssue(
            bot_reply_msg_id,
            repo_name,
            issue_number,
            target_author_id,
            time.time(),
        )
    )
    if len(entries) > MAX_RECENT_PER_CHANNEL:
        del entries[:-MAX_RECENT_PER_CHANNEL]
    save_recent_issues()


def _new_issue_job_id(job: IssueJob) -> str:
    parts = [
        job.kind,
        str(job.user_id),
        str(job.guild_id),
        str(job.channel_id),
        str(job.message_id),
        job.repo_name,
        str(job.issue_number or ""),
        str(int(time.time() * 1000)),
    ]
    return hashlib.sha256(":".join(parts).encode()).hexdigest()[:16]


def _issue_job_key(job: IssueJob) -> tuple:
    return (
        job.kind,
        job.user_id,
        job.guild_id,
        job.channel_id,
        job.message_id,
        job.repo_name,
        job.project_name,
        job.label,
        job.issue_number,
    )


def save_issue_jobs() -> None:
    """Atomically write queued issue jobs to disk."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = ISSUE_JOBS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps([asdict(job) for job in issue_jobs]))
        tmp.replace(ISSUE_JOBS_FILE)
    except Exception:
        logging.exception("Failed to save issue_jobs")


def load_issue_jobs() -> None:
    """Load queued issue jobs from disk, skipping stale or invalid entries."""
    if not ISSUE_JOBS_FILE.exists():
        return
    try:
        now = time.time()
        raw = json.loads(ISSUE_JOBS_FILE.read_text())
        issue_jobs.clear()
        for item in raw:
            job = IssueJob(**item)
            if now - job.created_at <= ISSUE_JOB_TTL:
                issue_jobs.append(job)
        logging.info(f"Loaded {len(issue_jobs)} queued issue job(s)")
    except Exception:
        logging.exception("Failed to load issue_jobs, starting fresh")
        issue_jobs.clear()


def enqueue_issue_job(job: IssueJob) -> IssueJob:
    """Queue issue work, deduplicating repeated quick reactions."""
    now = time.time()
    if not job.created_at:
        job.created_at = now
    if not job.next_run:
        job.next_run = now
    if not job.id:
        job.id = _new_issue_job_id(job)

    job_key = _issue_job_key(job)
    for existing in issue_jobs:
        if _issue_job_key(existing) == job_key:
            existing.next_run = max(existing.next_run, job.next_run)
            save_issue_jobs()
            return existing

    issue_jobs.append(job)
    save_issue_jobs()
    logging.info(f"Queued {job.kind} job {job.id}")
    return job


def make_create_issue_job(
    *,
    user_id: int,
    guild_id: int,
    channel_id: int,
    message_id: int,
    repo_name: str,
    project_name: str,
    label: str | None,
    delay: float = 0,
    attempts: int = 0,
) -> IssueJob:
    now = time.time()
    return IssueJob(
        kind="create_issue",
        user_id=user_id,
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
        repo_name=repo_name,
        project_name=project_name,
        label=label,
        attempts=attempts,
        next_run=now + delay,
        created_at=now,
    )


def make_followup_job(
    *,
    user_id: int,
    guild_id: int,
    channel_id: int,
    message_id: int,
    repo_name: str,
    issue_number: int,
    delay: float = 0,
    attempts: int = 0,
) -> IssueJob:
    now = time.time()
    return IssueJob(
        kind="followup_comment",
        user_id=user_id,
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
        repo_name=repo_name,
        issue_number=issue_number,
        attempts=attempts,
        next_run=now + delay,
        created_at=now,
    )


def retry_delay_for_exception(exc: Exception, attempts: int) -> float:
    """Return retry delay for GitHub rate limits, preferring reset headers."""
    headers = getattr(exc, "headers", None) or {}
    reset_at = headers.get("x-ratelimit-reset") or headers.get("X-RateLimit-Reset")
    if reset_at:
        try:
            return max(float(reset_at) - time.time(), ISSUE_JOB_RETRY_BASE_SECONDS)
        except (TypeError, ValueError):
            pass
    return min(ISSUE_JOB_RETRY_BASE_SECONDS * (2 ** max(attempts - 1, 0)), 900)


def is_github_rate_limit(exc: Exception) -> bool:
    """Detect retryable GitHub rate-limit failures from PyGithub."""
    if isinstance(exc, RateLimitExceededException):
        return True
    if isinstance(exc, GithubException):
        status = getattr(exc, "status", None)
        message = str(exc).lower()
        return status in {403, 429} and "rate limit" in message
    return False


def mark_job_retry(job: IssueJob, exc: Exception) -> bool:
    """Schedule retry. Return False when job exhausted retries."""
    job.attempts += 1
    if job.attempts >= ISSUE_JOB_MAX_ATTEMPTS or time.time() - job.created_at > ISSUE_JOB_TTL:
        return False
    job.next_run = time.time() + retry_delay_for_exception(exc, job.attempts)
    save_issue_jobs()
    logging.warning(f"Retrying {job.kind} job {job.id} after rate limit")
    return True


def reschedule_job_for_cooldown(job: IssueJob, remaining: float) -> None:
    """Delay job for live per-user cooldown without consuming retry attempts."""
    job.next_run = time.time() + remaining
    save_issue_jobs()
    logging.info(f"Rescheduled {job.kind} job {job.id} for cooldown")


async def fetch_job_context(job: IssueJob):
    guild = bot.get_guild(job.guild_id)
    if not guild:
        raise RuntimeError("Queued job guild not found")

    channel = bot.get_channel(job.channel_id)
    if not channel:
        channel = await bot.fetch_channel(job.channel_id)

    message = await channel.fetch_message(job.message_id)
    return guild, channel, message


async def best_effort_message_reply(
    message: discord.Message, content: str
) -> discord.Message | None:
    try:
        return await message.reply(content, mention_author=False)
    except discord.DiscordException:
        logging.warning("Failed to send Discord completion reply", exc_info=True)
        return None


async def best_effort_remove_reaction(
    message: discord.Message, emoji: str, member: discord.abc.User
) -> None:
    try:
        await message.remove_reaction(emoji, member)
    except discord.DiscordException:
        logging.warning("Failed to remove Discord status reaction", exc_info=True)


async def best_effort_add_reaction(message: discord.Message, emoji: str) -> None:
    try:
        await message.add_reaction(emoji)
    except discord.DiscordException:
        logging.warning("Failed to add Discord status reaction", exc_info=True)


async def create_issue_and_respond(
    *,
    target_message: discord.Message,
    channel: discord.abc.Messageable,
    guild: discord.Guild,
    repo_name: str,
    project_name: str,
    label: str | None,
    user_id: int,
) -> tuple[int, str]:
    issue_number, issue_url = await create_issue_from_message(
        target_message,
        channel,
        guild,
        repo_name,
        project_name,
        label,
    )
    _record_issue_for_rate_limit(user_id)
    reply_msg = await best_effort_message_reply(
        target_message,
        f"Created {project_name} issue #{issue_number}: <{issue_url}>",
    )
    await best_effort_remove_reaction(target_message, "⏳", bot.user)
    await best_effort_add_reaction(target_message, "✅")
    if reply_msg:
        record_recent_issue(
            channel.id,
            reply_msg.id,
            repo_name,
            issue_number,
            target_message.author.id,
        )
    return issue_number, issue_url


async def attach_followup_and_respond(
    *,
    target_message: discord.Message,
    repo_name: str,
    issue_number: int,
    user_id: int,
) -> str:
    comment_body = await build_followup_comment(target_message)
    comment_url = await asyncio.to_thread(
        add_comment_to_issue, repo_name, issue_number, comment_body
    )
    _record_issue_for_rate_limit(user_id)
    await best_effort_remove_reaction(target_message, "⏳", bot.user)
    await best_effort_add_reaction(target_message, "✅")
    logging.info(f"Attached follow-up to issue #{issue_number}")
    return comment_url


async def fail_job_reaction(job: IssueJob) -> None:
    try:
        _, _, message = await fetch_job_context(job)
        try:
            await message.remove_reaction("⏳", bot.user)
        except Exception:
            pass
        await message.add_reaction("❌")
    except Exception:
        logging.exception(f"Failed to mark queued job {job.id} as failed")


async def process_issue_job(job: IssueJob) -> bool:
    """Process one queued job. Return True when job is finished."""
    try:
        guild, channel, target_message = await fetch_job_context(job)
        remaining = _check_rate_limit(job.user_id)
        if remaining is not None:
            reschedule_job_for_cooldown(job, remaining)
            return False
        if job.kind == "create_issue":
            await create_issue_and_respond(
                target_message=target_message,
                channel=channel,
                guild=guild,
                repo_name=job.repo_name,
                project_name=job.project_name,
                label=job.label,
                user_id=job.user_id,
            )
        else:
            if job.issue_number is None:
                raise RuntimeError("Queued follow-up job missing issue number")
            await attach_followup_and_respond(
                target_message=target_message,
                repo_name=job.repo_name,
                issue_number=job.issue_number,
                user_id=job.user_id,
            )
        return True
    except Exception as exc:
        if is_github_rate_limit(exc) and mark_job_retry(job, exc):
            return False
        logging.exception(f"Queued {job.kind} job {job.id} failed")
        await fail_job_reaction(job)
        return True


async def process_due_issue_jobs() -> None:
    now = time.time()
    for job in list(issue_jobs):
        if job.next_run > now:
            continue
        finished = await process_issue_job(job)
        if finished and job in issue_jobs:
            issue_jobs.remove(job)
            save_issue_jobs()


async def issue_job_worker() -> None:
    """Background worker for queued issue/comment jobs."""
    await bot.wait_until_ready()
    while True:
        await process_due_issue_jobs()
        now = time.time()
        due_times = [job.next_run for job in issue_jobs]
        sleep_for = min(max(min(due_times) - now, 1), 30) if due_times else 5
        await asyncio.sleep(sleep_for)


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
    now = time.time()
    expired = [
        msg_id
        for msg_id, (_, _, timestamp) in pending_projects.items()
        if now - timestamp > PENDING_TIMEOUT
    ]
    for msg_id in expired:
        del pending_projects[msg_id]

    before = sum(len(v) for v in recent_issues.values())
    for channel_id in list(recent_issues):
        recent_issues[channel_id] = [
            entry
            for entry in recent_issues[channel_id]
            if now - entry.timestamp <= RECENT_ISSUE_TTL
        ]
        if not recent_issues[channel_id]:
            del recent_issues[channel_id]
    if sum(len(v) for v in recent_issues.values()) != before:
        save_recent_issues()

    stale_users = [
        uid for uid, ts in _user_issue_timestamps.items() if now - ts > ISSUE_RATE_LIMIT_SECONDS * 2
    ]
    for uid in stale_users:
        del _user_issue_timestamps[uid]

    before_jobs = len(issue_jobs)
    issue_jobs[:] = [job for job in issue_jobs if now - job.created_at <= ISSUE_JOB_TTL]
    if len(issue_jobs) != before_jobs:
        save_issue_jobs()


def _check_rate_limit(user_id: int) -> float | None:
    """Return seconds remaining in cooldown if rate-limited, else None."""
    last = _user_issue_timestamps.get(user_id)
    if last is not None:
        elapsed = time.time() - last
        if elapsed < ISSUE_RATE_LIMIT_SECONDS:
            return ISSUE_RATE_LIMIT_SECONDS - elapsed
    return None


def _record_issue_for_rate_limit(user_id: int) -> None:
    _user_issue_timestamps[user_id] = time.time()


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
        pending_projects[message_id] = (repo, name, time.time())
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
                await target_message.remove_reaction("📎", payload.member)
            except Exception:
                pass
            enqueue_issue_job(
                make_followup_job(
                    user_id=payload.user_id,
                    guild_id=payload.guild_id,
                    channel_id=payload.channel_id,
                    message_id=message_id,
                    repo_name=match.repo_name,
                    issue_number=match.issue_number,
                    delay=remaining,
                )
            )
            try:
                await target_message.add_reaction("⏳")
            except Exception:
                pass
            return

        try:
            await target_message.remove_reaction("📎", payload.member)
            await target_message.add_reaction("⏳")
        except Exception:
            pass
        try:
            await attach_followup_and_respond(
                target_message=target_message,
                repo_name=match.repo_name,
                issue_number=match.issue_number,
                user_id=payload.user_id,
            )
        except Exception as exc:
            if is_github_rate_limit(exc):
                enqueue_issue_job(
                    make_followup_job(
                        user_id=payload.user_id,
                        guild_id=payload.guild_id,
                        channel_id=payload.channel_id,
                        message_id=message_id,
                        repo_name=match.repo_name,
                        issue_number=match.issue_number,
                        delay=retry_delay_for_exception(exc, 1),
                        attempts=1,
                    )
                )
                return
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

    # Get project from pending selection, otherwise use default
    if message_id in pending_projects:
        repo_name, project_name = pending_projects.pop(message_id)[:2]
    else:
        repo_name, project_name = DEFAULT_PROJECT
        logging.info(f"No project selected; using default project: {project_name}")

    # Remove pending indicator if present
    try:
        await target_message.remove_reaction("⏳", bot.user)
    except Exception:
        pass

    # Rate limit check
    remaining = _check_rate_limit(payload.user_id)
    if remaining is not None:
        enqueue_issue_job(
            make_create_issue_job(
                user_id=payload.user_id,
                guild_id=payload.guild_id,
                channel_id=payload.channel_id,
                message_id=message_id,
                repo_name=repo_name,
                project_name=project_name,
                label=label,
                delay=remaining,
            )
        )
        try:
            await target_message.add_reaction("⏳")
        except Exception:
            pass
        return

    try:
        await target_message.add_reaction("⏳")
    except Exception:
        pass

    # Create issue using shared logic
    try:
        await create_issue_and_respond(
            target_message=target_message,
            channel=channel,
            guild=guild,
            repo_name=repo_name,
            project_name=project_name,
            label=label,
            user_id=payload.user_id,
        )

    except Exception as exc:
        if is_github_rate_limit(exc):
            enqueue_issue_job(
                make_create_issue_job(
                    user_id=payload.user_id,
                    guild_id=payload.guild_id,
                    channel_id=payload.channel_id,
                    message_id=message_id,
                    repo_name=repo_name,
                    project_name=project_name,
                    label=label,
                    delay=retry_delay_for_exception(exc, 1),
                    attempts=1,
                )
            )
            return
        logging.exception("Failed to create issue")
        try:
            await target_message.remove_reaction("⏳", bot.user)
        except Exception:
            pass
        await target_message.add_reaction("❌")


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

    remaining = _check_rate_limit(message.author.id)
    if remaining is not None:
        enqueue_issue_job(
            make_followup_job(
                user_id=message.author.id,
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                message_id=message.id,
                repo_name=repo_name,
                issue_number=issue_number,
                delay=remaining,
            )
        )
        try:
            await message.add_reaction("⏳")
        except Exception:
            pass
        return

    try:
        comment_url = await attach_followup_and_respond(
            target_message=message,
            repo_name=repo_name,
            issue_number=issue_number,
            user_id=message.author.id,
        )
        await message.add_reaction("📎")
        await message.reply(
            f"Attached to issue #{issue_number}: <{comment_url}>",
            mention_author=False,
        )
        logging.info(f"Attached reply follow-up to issue #{issue_number}")
    except Exception as exc:
        if is_github_rate_limit(exc):
            enqueue_issue_job(
                make_followup_job(
                    user_id=message.author.id,
                    guild_id=message.guild.id,
                    channel_id=message.channel.id,
                    message_id=message.id,
                    repo_name=repo_name,
                    issue_number=issue_number,
                    delay=retry_delay_for_exception(exc, 1),
                    attempts=1,
                )
            )
            return
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
- Just react with issue type → creates issue in {default_name}

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
- React 🐛 only → Bug on {default_name}
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
