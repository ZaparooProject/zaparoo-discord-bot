# Discord Issue Bot

A Discord bot that creates GitHub issues from Discord messages when you react with specific emoji. Supports multiple projects with an optional two-step reaction flow.

## Features

- **Multi-project support**: Configure multiple GitHub repos with different emoji
- **Two-step flow**: React with project emoji, then issue type (optional)
- **Single-step flow**: Just react with issue type to use default project
- **Role-based authorization**: Only users with specified role can trigger issue creation
- **Context included**: Captures previous messages for context
- **File hosting**: Downloads attachments and serves them locally
- **Auto-reply**: Bot replies with the issue number and link

## How It Works

### Single-Step (Quick)
React with an issue type emoji -> Creates issue on **default project**

```
User message: "The app crashes when I tap a card"
You react: bug emoji
Bot creates: Bug issue on default project (e.g., Core)
```

### Two-Step (Specific Project)
1. React with project emoji (bot adds hourglass to show it's waiting)
2. React with issue type emoji -> Creates issue on that project

```
User message: "The app crashes when I tap a card"
You react: app emoji (selects App project)
Bot reacts: hourglass (waiting for issue type)
You react: bug emoji
Bot creates: Bug issue on App
```

## Configuration

Projects, issue types, and support responses are hardcoded in `bot.py`. Edit them directly to customize for your Discord server.

Secrets and deployment-specific settings go in `.env` (see below).

## Quick Start

### 1. Create a Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" -> name it -> Create
3. Go to "Bot" tab -> Click "Reset Token" -> Copy the token
4. Enable these **Privileged Gateway Intents**:
   - Message Content Intent
5. Go to "OAuth2" -> "URL Generator"
   - Scopes: `bot`
   - Bot Permissions: `Read Messages/View Channels`, `Send Messages`, `Add Reactions`, `Read Message History`
6. Copy the generated URL and open it to invite the bot to your server

### 2. Get Your Discord Role ID

1. In Discord, go to Settings -> Advanced -> Enable "Developer Mode"
2. Create a role in your server for bot authorization (Server Settings -> Roles)
3. Right-click the role -> "Copy Role ID"
4. Assign this role to users who should be able to create issues

### 3. Create a GitHub Token

1. Go to [GitHub Settings -> Fine-grained tokens](https://github.com/settings/tokens?type=beta)
2. Click "Generate new token"
3. Select repositories you want to create issues on
4. Permissions: `Issues` -> Read and Write
5. Generate and copy the token

### 4. Configure and Run

```bash
cp .env.example .env
# Edit .env with your tokens and settings

uv run bot.py
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes | Discord bot token |
| `GEMINI_API_KEY` | Yes | Google Gemini API key for AI features |
| `AUTHORIZED_ROLE_ID` | Yes | Discord role ID for authorized users |
| `GITHUB_TOKEN` | Yes* | GitHub personal access token |
| `GITHUB_APP_ID` | Yes* | GitHub App ID (alternative to token) |
| `GITHUB_APP_PRIVATE_KEY_PATH` | Yes* | Path to GitHub App private key PEM file |
| `GITHUB_APP_INSTALLATION_ID` | Yes* | GitHub App installation ID |
| `IMAGES_URL` | No | Public URL for files served by nginx |

*GitHub auth: set either `GITHUB_TOKEN` **or** all three `GITHUB_APP_*` variables.

## File Hosting

The bot saves downloaded attachments (images, .txt, .log files) locally. You need to serve them via nginx or similar.

### Example nginx config

```nginx
location /discord-files/ {
    alias /opt/discord-issue-bot/images/;
    add_header X-Content-Type-Options nosniff always;
    default_type application/octet-stream;
    types {
        image/png  png;
        image/jpeg jpg jpeg;
        image/gif  gif;
        image/webp webp;
        text/plain txt log;
    }
    autoindex off;
}
```

Then set `IMAGES_URL=https://your-domain.com/discord-files`

## Deployment

Copy files to `/opt/discord-issue-bot/` and use the included systemd service:

```bash
sudo cp discord-issue-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable discord-issue-bot
sudo systemctl start discord-issue-bot
```

## Example Issue Output

```markdown
*Issue created from Discord*

**Source:** [Zaparoo / #support](https://discord.com/channels/...)

---

### Reported Message

**>>> Target Message:**
> **UserB** (@userb) - 2024-01-15 10:32 UTC
> Yeah I get it too when I tap a card right after boot.
> Happens every time on my MiSTer.

### Context (previous messages)

> **UserA** (@usera) - 2024-01-15 10:30 UTC
> Has anyone else seen this crash?

### Attachments

![screenshot.png](https://your-server.com/images/20240115_103245_abc123.png)
```

## Commands

| Command | Description |
|---------|-------------|
| `!help` | Show help (only visible to authorized role) |

## License

MIT
