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
from datetime import datetime
from pathlib import Path
from typing import TypedDict

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

# Secrets and deployment-specific settings from .env
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
AUTHORIZED_ROLE_ID = int(os.getenv("AUTHORIZED_ROLE_ID", "0"))
_app_id = os.getenv("GITHUB_APP_ID")
GITHUB_APP_ID = int(_app_id) if _app_id else None
GITHUB_APP_PRIVATE_KEY_PATH = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
_app_install = os.getenv("GITHUB_APP_INSTALLATION_ID")
GITHUB_APP_INSTALLATION_ID = int(_app_install) if _app_install else None
IMAGES_URL = os.getenv("IMAGES_URL", "")

# Hardcoded config
OPENAI_MODEL = "gpt-4o"
IMAGES_DIR = Path("./images")
MAX_ATTACHMENT_SIZE = 10_485_760
CONTEXT_MESSAGES = 5
PENDING_TIMEOUT = 60

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
                self.target_message, channel, guild, repo_name, project_name, label,
            )
            await interaction.followup.send(
                f"Created {project_name} issue #{issue_number}: <{issue_url}>",
                ephemeral=True,
            )
            await self.target_message.reply(
                f"Created {project_name} issue #{issue_number}: <{issue_url}>",
                mention_author=False,
            )
            await self.target_message.add_reaction("✅")
        except Exception:
            logging.exception("Failed to create issue via context menu")
            try:
                await interaction.followup.send(
                    "Failed to create issue. Check bot logs for details.",
                    ephemeral=True,
                )
            except Exception:
                pass


async def create_issue_callback(
    interaction: discord.Interaction, message: discord.Message
):
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

        cmd = discord.app_commands.ContextMenu(
            name="Create Issue", callback=create_issue_callback
        )
        self.tree.add_command(cmd)

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
    # Fetch context messages (only from target author or users with authorized role)
    target_author_id = target_message.author.id
    context_messages = []
    try:
        async for msg in channel.history(limit=10, before=target_message):
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
    guild_name = guild.name
    discord_url = (
        f"https://discord.com/channels/{guild.id}/{channel.id}/{target_message.id}"
    )

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
    issue_number, issue_url = await asyncio.to_thread(
        create_github_issue, repo_name, title, body, labels
    )

    logging.info(f"Created {project_name} issue #{issue_number}: {title}")
    return issue_number, issue_url


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

    # Create issue using shared logic
    try:
        issue_number, issue_url = await create_issue_from_message(
            target_message, channel, guild, repo_name, project_name, label,
        )

        await target_message.reply(
            f"Created {project_name} issue #{issue_number}: <{issue_url}>",
            mention_author=False,
        )
        await target_message.add_reaction("✅")

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
        print("Error: AUTHORIZED_ROLE_ID not set in .env")
        exit(1)
    if not OPENAI_API_KEY:
        print("Error: OPENAI_API_KEY not set")
        exit(1)

    init()

    if not github_client:
        print("Error: Set GITHUB_TOKEN or GITHUB_APP_* variables in .env")
        exit(1)

    asyncio.run(main())
