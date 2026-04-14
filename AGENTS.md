# AGENTS.md

Discord bot (Python, single-file) that creates GitHub issues from Discord message reactions. Uses discord.py, PyGithub, OpenAI, and aiohttp.

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

Single-file bot: all logic lives in `bot.py` (~540 lines). No packages, no modules.

Key sections in `bot.py`:
- **Lines 1-100**: Config loading (env vars, TOML config, constants)
- **`init()`**: Creates OpenAI/GitHub clients. Called only from `__main__`, not at import time. This is intentional -- importing `bot` must be side-effect-free for tests.
- **`process_reaction()`**: Core logic. Handles the full reaction -> issue creation flow.
- **`create_github_issue()`**: Synchronous (PyGithub). Always called via `asyncio.to_thread()`.

Config files:
- `.env` -- Secrets and env-specific settings (gitignored)
- `config.toml` -- Project/issue type emoji mapping (gitignored, loaded by `load_config()`)
- `config.example.toml` / `.env.example` -- Templates shipped with the repo

## Testing

Tests use `pytest-asyncio` and mock everything external (Discord API, GitHub API, OpenAI API). Integration tests use `dpytest` to simulate Discord interactions.

- `tests/test_pure_functions.py` -- Unit tests for `has_authorized_role`, `format_message_for_issue`, `cleanup_pending`
- `tests/test_external_services.py` -- Tests with mocked API clients
- `tests/test_file_handling.py` -- File save/download, extension security
- `tests/test_integration.py` -- Full flows via dpytest

When writing tests, import from `bot` inside the test function (not at module level) and use `unittest.mock.patch` to override config values. See existing tests for the pattern.

## Things That Will Bite You

- **`bot.py` import must be side-effect-free.** The `init()` function exists specifically so tests can import `bot` without reading key files or creating API clients. Never move initialization back to module level.
- **`create_github_issue()` is synchronous.** PyGithub is blocking. It must always be called through `asyncio.to_thread()` to avoid blocking the Discord event loop.
- **`dpytest` is pinned to a git commit**, not the PyPI release. The PyPI 0.7.0 release has a bug (`colors` vs `color` in `make_role()`). Don't switch to `dpytest>=0.7.0` until a new PyPI release fixes this.
- **File extension whitelist is a security boundary.** `ALLOWED_FILE_EXTENSIONS` controls what gets saved to disk and served by nginx. Never add executable extensions (.php, .js, .py, .html, .svg, etc.).
- **Error messages to Discord are intentionally generic.** The bot replies "Failed to create issue. Check bot logs for details." -- never expose Python exceptions to Discord users.

## Boundaries

- **Never commit**: `.env`, `config.toml`, `images/`, private keys
- **Never add to file whitelist**: executable or web-servable extensions (.php, .js, .html, .svg, .py, .sh, .exe, .bat)
- **Always run tests and linter** before considering work complete
