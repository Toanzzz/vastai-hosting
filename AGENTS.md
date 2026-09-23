# Project rules

- Headless Python 3.12 service. Use uv for dependency management, commands, and the committed `uv.lock`; `pyproject.toml` is the source of truth for dependencies and tools.
- Use a `src/` package layout with small one-directional modules (aim for 300 lines or less). Use Ruff for formatting and linting, ty for type checking, and pytest for tests.
- Pricing proposals come from Gemini through `google-genai`; validate the response and all pricing bounds locally before calling the Vast.ai Python SDK's `list_machine`. Skip writes when market data or Gemini is unavailable. Default to dry-run.
- Never include API keys in command arguments or logs. Never commit `.env`.
- After edits run, in order: `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`, `uv run pytest`, `uv build`.
