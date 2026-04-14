#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "discord.py>=2.3.0",
#     "aiohttp>=3.9.0",
#     "python-dotenv>=1.0.0",
#     "PyGithub>=2.1.0",
#     "openai>=1.0.0",
# ]
# ///
"""
Discord Issue Bot

React to Discord messages with emoji to create GitHub issues.
Only responds to reactions from the authorized user.
"""

import asyncio
import hashlib
import logging
import os
import time
import tomllib
from datetime import datetime
from pathlib import Path

import aiohttp
import discord
import openai
from discord.ext import commands
from dotenv import load_dotenv
from github import Auth, Github

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

load_dotenv()

# Required config
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
AUTHORIZED_ROLE_ID = int(os.getenv("AUTHORIZED_ROLE_ID", "0"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# GitHub auth - either use App (preferred) or personal token
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
GITHUB_APP_PRIVATE_KEY_PATH = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
GITHUB_APP_INSTALLATION_ID = os.getenv("GITHUB_APP_INSTALLATION_ID")

# File hosting (files saved locally, served by external web server like nginx)
IMAGES_DIR = Path(os.getenv("IMAGES_DIR", "./images"))
IMAGES_URL = os.getenv("IMAGES_URL", "https://example.com/discord-images")

# How many previous messages to include as context
CONTEXT_MESSAGES = int(os.getenv("CONTEXT_MESSAGES", "5"))

# How long to wait for second reaction (seconds)
PENDING_TIMEOUT = int(os.getenv("PENDING_TIMEOUT", "60"))

# Max attachment size in bytes (default 10MB)
MAX_ATTACHMENT_SIZE = int(os.getenv("MAX_ATTACHMENT_SIZE", str(10 * 1024 * 1024)))

# OpenAI model for title generation
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Configuration file path
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "./config.toml"))


def load_config(path: Path) -> tuple[dict[str, tuple[str, str]], dict[str, str | None]]:
    """Load project and issue type configuration from a TOML file."""
    with open(path, "rb") as f:
        config = tomllib.load(f)

    projects = {emoji: tuple(val) for emoji, val in config.get("projects", {}).items()}
    issue_types = {
        emoji: (label if label else None) for emoji, label in config.get("issue_types", {}).items()
    }
    return projects, issue_types


# Load config from file, fall back to defaults
if CONFIG_PATH.exists():
    PROJECTS, ISSUE_TYPES = load_config(CONFIG_PATH)
    logging.info(f"Loaded configuration from {CONFIG_PATH}")
else:
    PROJECTS = {
        "🖥️": ("ZaparooProject/zaparoo-core", "Core"),
        "📱": ("ZaparooProject/zaparoo-app", "App"),
        "🎨": ("ZaparooProject/zaparoo-designer", "Designer"),
    }
    ISSUE_TYPES = {"🐛": "bug", "💡": "enhancement", "📋": None}

# Default project (first in PROJECTS)
DEFAULT_PROJECT = list(PROJECTS.values())[0]

# Allowed file extensions (security whitelist)
ALLOWED_FILE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt", ".log"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Pending project selections: message_id -> (project_repo, project_name, timestamp)
pending_projects: dict[int, tuple[str, str, float]] = {}

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.messages = True
intents.members = True


class IssueBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()


bot = IssueBot()
openai_client: openai.AsyncOpenAI | None = None
github_client: Github | None = None


def init():
    """Initialize API clients and create directories. Called at startup."""
    global openai_client, github_client

    openai_client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)

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
    """Generate a concise issue title using OpenAI."""
    # Truncate to avoid token limits and costs
    truncated_body = body[:4000] if len(body) > 4000 else body
    try:
        response = await openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Generate a GitHub issue title from the provided issue body. "
                        "Write a short sentence (8-15 words) that describes the specific "
                        "problem or request. Be descriptive and include relevant details. "
                        "Output only the title, no quotes or prefixes. Use sentence case. "
                        "Do not mention if there are attachments or not."
                    ),
                },
                {"role": "user", "content": truncated_body},
            ],
            max_tokens=60,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        logging.exception("Failed to generate title")
        return "Issue from Discord"


async def download_attachment(session: aiohttp.ClientSession, url: str) -> tuple[bytes | None, str]:
    """Download an attachment and return (data, filename)."""
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.read()
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


def format_message_for_issue(message: discord.Message, is_target: bool = False) -> str:
    """Format a Discord message for inclusion in GitHub issue."""
    prefix = "**>>> Target Message:**" if is_target else ""

    timestamp = message.created_at.strftime("%Y-%m-%d %H:%M UTC")
    author = f"**{message.author.display_name}** (@{message.author.name})"

    content = message.content or "*[no text content]*"

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


def cleanup_pending():
    """Remove expired pending project selections."""
    now = time.monotonic()
    expired = [
        msg_id
        for msg_id, (_, _, timestamp) in pending_projects.items()
        if now - timestamp > PENDING_TIMEOUT
    ]
    for msg_id in expired:
        del pending_projects[msg_id]


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

    # Check if this is an issue type
    if emoji not in ISSUE_TYPES:
        return

    label = ISSUE_TYPES[emoji]

    # Get project (from pending or default)
    if message_id in pending_projects:
        repo_name, project_name = pending_projects.pop(message_id)[:2]
    else:
        repo_name, project_name = DEFAULT_PROJECT

    # Fetch channel
    channel = bot.get_channel(payload.channel_id)
    if not channel:
        try:
            channel = await bot.fetch_channel(payload.channel_id)
        except Exception:
            logging.exception("Could not fetch channel")
            return

    # Fetch target message
    try:
        target_message = await channel.fetch_message(message_id)
    except Exception:
        logging.exception("Could not fetch message")
        return

    # Remove pending indicator if present
    try:
        await target_message.remove_reaction("⏳", bot.user)
    except Exception:
        pass

    # Fetch context messages (only from target author or users with authorized role)
    target_author_id = target_message.author.id
    context_messages = []
    try:
        async for msg in channel.history(limit=10, before=target_message):
            # Check if message author is target author or has authorized role
            msg_member = guild.get_member(msg.author.id)
            if msg.author.id == target_author_id or (
                msg_member and has_authorized_role(msg_member)
            ):
                context_messages.append(msg)
                if len(context_messages) >= CONTEXT_MESSAGES:
                    break
        context_messages.reverse()
    except Exception:
        logging.exception("Could not fetch context")

    # Process attachments
    attachment_urls = []

    for msg in context_messages + [target_message]:
        for attachment in msg.attachments:
            ext = Path(attachment.filename).suffix.lower()
            if ext in ALLOWED_FILE_EXTENSIONS:
                if attachment.size > MAX_ATTACHMENT_SIZE:
                    logging.warning(
                        f"Skipping large attachment: {attachment.filename}"
                        f" ({attachment.size} bytes)"
                    )
                    continue
                data, filename = await download_attachment(bot.http_session, attachment.url)
                if data:
                    local_url = await save_file_locally(data, filename)
                    if local_url:
                        attachment_urls.append((attachment.filename, local_url))
                        continue
                # Fallback to Discord URL if download or save failed
                attachment_urls.append((attachment.filename, attachment.url))

    # Build issue body
    guild_name = channel.guild.name if hasattr(channel, "guild") else "DM"
    discord_url = (
        f"https://discord.com/channels/{payload.guild_id}/{payload.channel_id}/{payload.message_id}"
    )

    # Handle threads - show parent channel context
    if isinstance(channel, discord.Thread):
        parent_name = channel.parent.name if channel.parent else "unknown"
        channel_display = f"#{parent_name} → {channel.name}"
    else:
        channel_display = f"#{channel.name}" if hasattr(channel, "name") else "Direct Message"

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
    body_parts.append(format_message_for_issue(target_message, is_target=True))
    body_parts.append("")

    if context_messages:
        body_parts.append("### Context (previous messages)")
        body_parts.append("")
        for msg in context_messages:
            body_parts.append(format_message_for_issue(msg))
            body_parts.append("")

    if attachment_urls:
        body_parts.append("### Attachments")
        body_parts.append("")
        for filename, url in attachment_urls:
            ext = Path(filename).suffix.lower()
            if ext in IMAGE_EXTENSIONS:
                body_parts.append(f"![{filename}]({url})")
            else:
                body_parts.append(f"[{filename}]({url})")
        body_parts.append("")

    body = "\n".join(body_parts)

    # Generate issue title using LLM
    title = await generate_issue_title(body)

    # Build labels
    labels = []
    if label:
        labels.append(label)

    # Create issue
    try:
        issue_number, issue_url = await asyncio.to_thread(
            create_github_issue, repo_name, title, body, labels
        )

        await target_message.reply(
            f"Created {project_name} issue #{issue_number}: <{issue_url}>",
            mention_author=False,
        )

        await target_message.add_reaction("✅")

        logging.info(f"Created {project_name} issue #{issue_number}: {title}")

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
    logging.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logging.info(f"Authorized role ID: {AUTHORIZED_ROLE_ID}")
    logging.info(f"Images: {IMAGES_DIR.absolute()} -> {IMAGES_URL}")
    for emoji, (repo, name) in PROJECTS.items():
        default = " (default)" if (repo, name) == DEFAULT_PROJECT else ""
        logging.info(f"  {emoji} → {name} ({repo}){default}")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """Handle reaction add events."""
    await process_reaction(payload)


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
- Just react with issue type → uses default project ({default_name})

**Projects:**
{projects_list}

**Issue types:**
🐛 = Bug report (`bug` label)
💡 = Feature request (`enhancement` label)
📋 = General issue (no label)

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
        print("Error: AUTHORIZED_ROLE_ID not set")
        exit(1)
    if not OPENAI_API_KEY:
        print("Error: OPENAI_API_KEY not set")
        exit(1)
    if not PROJECTS:
        print("Error: No projects configured")
        exit(1)

    init()

    if not github_client:
        print(
            "Error: Set GITHUB_TOKEN or GITHUB_APP_ID"
            " + GITHUB_APP_PRIVATE_KEY_PATH + GITHUB_APP_INSTALLATION_ID"
        )
        exit(1)

    asyncio.run(main())
