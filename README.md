# Vast.ai host pricing

A headless Python service that reads on-demand GPU offers through the official Vast.ai Python SDK, asks Gemini to recommend a GPU price through `google-genai`, and maintains the host's full listing. It runs one cycle immediately, then repeats at `POLL_SECONDS`. The container has no web server or exposed ports.

## Configure

```sh
cp .env.example .env
# Provide VAST_API_KEY and GEMINI_API_KEY; review every listing setting.
```

`.env.example` includes an example based on machine `51673` (RTX 5090); change it for another host. Obtain a Vast API key with permission to view offers and manage host machines, and a [Google AI Studio API key](https://aistudio.google.com/apikey) for `GEMINI_API_KEY`. `GEMINI_MODEL` defaults to `gemini-flash-latest`. Set `GEMINI_THINKING_BUDGET` explicitly. All listing fields are explicit: Gemini proposes the GPU price; disk, internet, bid floor, discount, minimum GPU chunk, volume size, volume price, and rolling `DURATION_DAYS` come from configuration. The machine must already be listed.

The market query matches **GPU model and exact GPU count**, verified and rentable on-demand offers. It excludes the host machine and duplicate machine IDs. Gemini receives the host's current price and hardware, each peer's GPU-only `dph_base / num_gpus` and available hardware/reliability/location data, the peer median, and configured price bounds. Its structured answer contains a target price and brief rationale. The service rounds to cents and **rejects** prices outside `MIN_PRICE`–`MAX_PRICE` or below `MIN_BID_PRICE` (USD per GPU-hour). Missing or invalid Gemini answers do not trigger listing updates.

Cycles are skipped with fewer than `MIN_PEERS` usable offers or when a search hits `OFFER_LIMIT` (to avoid a truncated cheapest-first sample). The service updates when the GPU price differs by at least `MIN_CHANGE` or any configured listing field differs. It does not filter by geography, RAM, network, or reliability; these are provided to Gemini for comparison.

`DRY_RUN=true` by default: review proposed prices in logs before setting `DRY_RUN=false`. A price increase for an existing rental can require the client's acceptance; see the [Vast host list-machine reference](https://docs.vast.ai/host/sdk/list-machine). `POLL_SECONDS` must be at least 60; 1800 is the example interval. The listing duration is refreshed when less than one day of its configured horizon is available.

## Run

```sh
docker build --platform linux/amd64 -t vastai-hosting .
docker run -d --name vastai-hosting --restart unless-stopped --env-file .env vastai-hosting
docker logs -f vastai-hosting
```

Run on any host with outbound access to Vast.ai and Gemini. No GPU or Docker socket access is required inside the container. For local development, install [uv](https://docs.astral.sh/uv/), then run `uv sync` and `uv run --env-file .env python -m vastai_hosting.main`. Validate with `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`, `uv run pytest`, and `uv build`.

Docs: [search offers](https://docs.vast.ai/sdk/python/quickstart), [show machines](https://docs.vast.ai/host/sdk/show-machines), [list machine](https://docs.vast.ai/host/sdk/list-machine), [Gemini structured output](https://ai.google.dev/gemini-api/docs/generate-content/structured-output).
