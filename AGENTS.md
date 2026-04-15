# AGENTS.md

Discord bot (Python, single-file) that creates GitHub issues from Discord message reactions or the right-click context menu. Uses discord.py, PyGithub, Google Gemini, and aiohttp.

## Commands

```bash
uv run bot.py                      # Run the bot (requires .env)
uv run --extra test pytest         # Run all tests
uv run --extra test pytest -x      # Stop on first failure
uv run --extra test pytest -k "test_name"  # Run specific test
uv run --extra dev ruff check .    # Lint
uv run --extra dev ruff format .   # Format
```

No build step. Ruff handles both linting and formatting.

## Architecture

Single-file bot: all logic lives in `bot.py` (~1080 lines). No packages, no modules.

Key sections in `bot.py`:
- **Lines 1-100**: Config (secrets from .env, everything else hardcoded), project descriptions for auto-detection
- **`make_support_callback()`**: Factory that creates context menu callbacks for support responses. Each callback builds an embed + URL buttons from config and replies to the target message.
- **`CreateIssueModal`**: Modal with project and issue type Select dropdowns. Shown when user picks "Create Issue" from the right-click Apps menu. On submit, calls `create_issue_from_message()`.
- **`IssueBot.setup_hook()`**: Creates HTTP session and registers context menu commands: support responses from `SUPPORT_RESPONSES` config + the "Create Issue" command. Tree sync happens in `on_ready`.
- **`init()`**: Creates Gemini/GitHub clients. Called only from `__main__`, not at import time. This is intentional -- importing `bot` must be side-effect-free for tests.
- **`detect_project()`**: Uses Gemini to classify which project a Discord message relates to. Falls back to `DEFAULT_PROJECT` if unclear.
- **`gather_context()`**: Smart context gathering pipeline: reply chain walking → time-gap segmentation → LLM relevance filtering. Replaces the old blind N-message grab.
- **`create_issue_from_message()`**: Shared issue creation logic used by both the reaction flow and context menu flow. Uses `gather_context()` for smart context, processes attachments, builds issue body, generates title via Gemini, creates GitHub issue.
- **`process_reaction()`**: Handles the reaction -> issue creation flow. Supports project emoji, issue type emoji, and 📎 for follow-up attachment. Auto-detects project when no project emoji specified.
- **`create_github_issue()`**: Synchronous (PyGithub). Always called via `asyncio.to_thread()`.
- **`add_comment_to_issue()`**: Adds a comment to an existing GitHub issue. Synchronous (PyGithub), called via `asyncio.to_thread()`.
- **`on_message()`**: Detects replies to bot issue-creation messages for follow-up attachment.

Config:
- `.env` -- Secrets and deployment-specific: `DISCORD_TOKEN`, `GEMINI_API_KEY`, `GITHUB_TOKEN`, `AUTHORIZED_ROLE_ID`, `GITHUB_APP_*`, `IMAGES_URL` (gitignored)
- `.env.example` -- Template shipped with the repo
- Everything else (projects, issue types, support responses, model, timeouts) is hardcoded in `bot.py`

Support responses are hardcoded in `SUPPORT_RESPONSES` in `bot.py`. Each entry becomes a right-click (Apps) context menu command. Max 4 entries (1 of 5 Discord context menu slots is reserved for "Create Issue"). Each has a `name`, `title`, `message`, and optional `buttons` list with `label`/`url` pairs.

The "Create Issue" context menu command is always registered. It opens a Modal with two Select dropdowns (project and issue type) populated from the `PROJECTS` and `ISSUE_TYPES` dicts.

## Testing

Tests use `pytest-asyncio` and mock everything external (Discord API, GitHub API, OpenAI API). Integration tests use `dpytest` to simulate Discord interactions.

- `tests/test_pure_functions.py` -- Unit tests for `has_authorized_role`, `format_message_for_issue`, `cleanup_pending`
- `tests/test_external_services.py` -- Tests with mocked API clients
- `tests/test_file_handling.py` -- File save/download, extension security
- `tests/test_integration.py` -- Full flows via dpytest

When writing tests, import from `bot` inside the test function (not at module level) and use `unittest.mock.patch` to override config values. See existing tests for the pattern.

## Things That Will Bite You

- **`bot.py` import must be side-effect-free.** The `init()` function exists specifically so tests can import `bot` without reading key files or creating API clients. Never move initialization back to module level.
- **`create_github_issue()` and `add_comment_to_issue()` are synchronous.** PyGithub is blocking. They must always be called through `asyncio.to_thread()` to avoid blocking the Discord event loop.
- **`dpytest` is pinned to a git commit**, not the PyPI release. The PyPI 0.7.0 release has a bug (`colors` vs `color` in `make_role()`). Don't switch to `dpytest>=0.7.0` until a new PyPI release fixes this.
- **File extension whitelist is a security boundary.** `ALLOWED_FILE_EXTENSIONS` controls what gets saved to disk and served by nginx. Never add executable extensions (.php, .js, .py, .html, .svg, etc.).
- **Error messages to Discord are intentionally generic.** The bot replies "Failed to create issue. Check bot logs for details." -- never expose Python exceptions to Discord users.

## Boundaries

- **Never commit**: `.env`, `images/`, private keys
- **Never add to file whitelist**: executable or web-servable extensions (.php, .js, .html, .svg, .py, .sh, .exe, .bat)
- **Always run tests and linter** before considering work complete
