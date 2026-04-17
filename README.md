# Discord Issue Bot

A Discord bot that creates GitHub issues from Discord messages, either via emoji reactions or the right-click Apps context menu. Built for the [Zaparoo](https://zaparoo.org) project but configurable for any GitHub repository.

## Features

- **Reaction flow**: React to any message with emoji to create a GitHub issue from it
- **Context menu**: Right-click a message and use "Create Issue" to pick project and type from dropdowns
- **Smart context**: Gathers surrounding conversation for context, using Gemini to filter relevance
- **Auto-detect project**: If you don't specify a project emoji, Gemini classifies the message automatically
- **Follow-up attachment**: React with 📎 to attach a later message to an already-created issue as a comment
- **Support responses**: Right-click context menu commands that post pre-written support replies
- **File hosting**: Saves attachments locally and embeds them in the issue body
- **Role-based authorization**: Only users with a specified role can trigger issue creation
- **State persistence**: Recently created issues are saved to disk so 📎 follow-up survives restarts

## How It Works

### Reaction Flow

React to a message with issue type emoji to create an issue on the default project:

```
User: "The app crashes when I tap a card"
You react: 🐛
Bot creates: bug issue on default project, replies with link
```

To target a specific project, react with the project emoji first:

```
You react: 📱  (selects App project, bot shows ⏳)
You react: 🐛  (creates bug issue on App)
```

The project selection expires after 60 seconds.

### Context Menu

Right-click any message -> Apps -> **Create Issue** to open a modal with project and issue type dropdowns.

### Follow-up Attachment

React with 📎 on any message to attach it as a comment to the most recently created issue in that channel (within the last 24 hours).

### Support Responses

Right-click any message -> Apps to find pre-written support reply commands (e.g. "Request Troubleshooting"). These post an embed with links directly in the channel as a reply to the message.

## Configuration

Projects, issue types, and support responses are hardcoded in `bot.py`. Edit them directly:

- `PROJECTS` — emoji to `(repo, name)` mapping
- `ISSUE_TYPES` — emoji to label mapping
- `SUPPORT_RESPONSES` — list of context menu support reply commands
- `PROJECT_DESCRIPTIONS` — text descriptions fed to Gemini for auto-detection

Secrets and deployment-specific settings go in `.env`.

## Setup

### 1. Create a Discord Bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. New Application -> name it -> Create
3. Bot tab -> Reset Token -> copy the token
4. Enable **Privileged Gateway Intents**: Message Content Intent
5. OAuth2 -> URL Generator:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Read Messages/View Channels`, `Send Messages`, `Add Reactions`, `Read Message History`
6. Open the generated URL to invite the bot to your server

### 2. Get Your Discord Role ID

1. Discord Settings -> Advanced -> enable Developer Mode
2. Create a role for authorized users (Server Settings -> Roles)
3. Right-click the role -> Copy Role ID

### 3. GitHub Auth

**Option A: Personal access token**

1. [GitHub Settings -> Fine-grained tokens](https://github.com/settings/tokens?type=beta)
2. Select the target repositories, grant Issues: Read and Write
3. Set `GITHUB_TOKEN` in `.env`

**Option B: GitHub App** (issues created as the bot account)

1. [GitHub Settings -> Developer settings -> GitHub Apps](https://github.com/settings/apps) -> New GitHub App
2. Grant Issues: Read and Write, subscribe to no events
3. Install the app on your repositories
4. Set `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_PATH`, and `GITHUB_APP_INSTALLATION_ID` in `.env`

### 4. Get a Gemini API Key

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Create an API key and set `GEMINI_API_KEY` in `.env`

Gemini is used to generate issue titles and auto-detect which project a message relates to.

### 5. Run

```bash
cp .env.example .env
# Fill in your tokens

uv run bot.py
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes | Discord bot token |
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `AUTHORIZED_ROLE_ID` | Yes | Discord role ID for authorized users |
| `GITHUB_TOKEN` | Yes* | GitHub personal access token |
| `GITHUB_APP_ID` | Yes* | GitHub App ID |
| `GITHUB_APP_PRIVATE_KEY_PATH` | Yes* | Path to GitHub App private key PEM file |
| `GITHUB_APP_INSTALLATION_ID` | Yes* | GitHub App installation ID |
| `IMAGES_DIR` | No | Directory to save attachments (default: `./images`) |
| `IMAGES_URL` | No | Public base URL for saved attachments |
| `STATE_DIR` | No | Directory to persist bot state (default: `./state`) |

\* GitHub auth: set either `GITHUB_TOKEN` **or** all three `GITHUB_APP_*` variables.

## File Hosting

The bot saves attachments (images, `.txt`, `.log` files) to `IMAGES_DIR` and embeds them in issue bodies using `IMAGES_URL`. You need to serve `IMAGES_DIR` via nginx or similar.

Example nginx config:

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

Set `IMAGES_URL=https://your-domain.com/discord-files` and `IMAGES_DIR=/path/to/images`.

## Deployment

### systemd

A `discord-issue-bot.service` file is included. Adjust paths and user, then:

```bash
sudo cp discord-issue-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now discord-issue-bot
```

Add `ReadWritePaths=/path/to/state` to the service file if using `STATE_DIR`.

### Docker

Example `docker-compose.yml` service:

```yaml
discord-bot:
  build: /opt/discord-issue-bot
  restart: unless-stopped
  env_file: /opt/discord-issue-bot/.env
  volumes:
    - /path/to/images:/app/images:rw
    - /path/to/state:/app/state:rw
    - /path/to/key.pem:/app/key.pem:ro
```

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
