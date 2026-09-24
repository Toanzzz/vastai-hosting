# Project rules

- Headless Python 3.12 service. Use uv for dependency management, commands, and the committed `uv.lock`; `pyproject.toml` is the source of truth for dependencies and tools.
- Use a `src/` package layout with small one-directional modules (aim for 300 lines or less). Use Ruff for formatting and linting, ty for type checking, and pytest for tests.
- Pricing proposals come from Gemini through `google-genai`; validate the response and all pricing bounds locally before any listing write. The pricing cycle does not call `list_machine`. A listing write happens only when an allowed Telegram subscriber taps the suggested price, and that price is checked against the configured bounds again first. Skip writes when market data or Gemini is unavailable.
- Never include API keys in command arguments or logs. Never commit `.env`.
- After edits run, in order: `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`, `uv run pytest`, `uv build`.
