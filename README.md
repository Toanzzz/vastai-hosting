# Vast.ai host pricing

A headless Python service that reads on-demand GPU offers and host market metrics through the official Vast.ai Python SDK, asks Gemini to recommend a GPU price through `google-genai`, and sends that status to subscribed Telegram chats. The listing changes only when a subscriber taps the price button in that message. It runs one cycle immediately, then repeats at `POLL_SECONDS`. The container has no web server or exposed ports; the bot uses Telegram long polling.

## Configure

`VAST_API_KEY`, `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_SUBSCRIBE_SECRET` are required. They live in Doppler project `vastai-hosting`, config `prd`, and this directory is linked to that config. The Vast key must be a host key (market metrics reject client keys) with permission to view offers and manage host machines. `GEMINI_API_KEY` is a [Google AI Studio API key](https://aistudio.google.com/apikey). `TELEGRAM_BOT_TOKEN` is a [@BotFather](https://t.me/BotFather) token. `TELEGRAM_SUBSCRIBE_SECRET` is the code a private chat sends to `/start`.

Every other setting uses the default in `src/vastai_hosting/config.py` when the variable is unset or blank. These defaults describe machine `51673` (one RTX 5090). Set a variable only to point the service at another host or listing.

| Variable | Default |
| --- | --- |
| `GEMINI_MODEL` | `gemini-flash-latest` |
| `GEMINI_THINKING_BUDGET` | `1024` |
| `MACHINE_ID` | `51673` |
| `POLL_SECONDS` | `86400` |
| `TELEGRAM_STATE_PATH` | `data/subscribers.json` |
| `MIN_PEERS` | `5` |
| `OFFER_LIMIT` | `200` |
| `MIN_PRICE` | `0.40` |
| `MAX_PRICE` | `1.00` |
| `MIN_CHANGE` | `0.01` |
| `GPU_RUNNING_COST` | `0` |
| `DISK_PRICE` | `0.15` |
| `UPLOAD_PRICE` | `0.001953125` |
| `DOWNLOAD_PRICE` | `0.001953125` |
| `MIN_BID_PRICE` | `0.40` |
| `DISCOUNT_RATE` | `0.10` |
| `MIN_CHUNK` | `1` |
| `VOLUME_SIZE_GB` | `209` |
| `VOLUME_PRICE` | `0.15` |
| `DURATION_DAYS` | `7` |

`GPU_RUNNING_COST` is extra USD per GPU-hour while a GPU is rented, such as power. At `0` the service maximizes revenue. Gemini proposes the GPU price; disk, internet, bid floor, discount, minimum GPU chunk, volume size, volume price, and rolling `DURATION_DAYS` come from these settings. The machine must already be listed.

The market query matches **GPU model and exact GPU count**, verified and rentable on-demand offers. Vast returns a different sample of about 55 offers per search, so the service repeats the search until a call adds no new machine (at most 12 calls) and merges the results, excluding the host machine and duplicate machine IDs.

Offer prices (`dph_base`) are what renters pay; the host's `listed_gpu_cost` is what it earns. Each cycle derives the host share from the host's own public offer (`listed_gpu_cost / (dph_base / num_gpus)`, currently 0.75) and converts peer prices to host earnings. The cycle is skipped when the host's offer is not visible or gives a share outside 0.5–1.

Market demand comes from Vast's host market metrics for verified machines of the same GPU model and GPU-count bucket (1, 2, 4 or 8, else all): current and 30-day usage percent, rented and available GPUs, and a seven-day trend at six-hour steps. Their price percentiles are not used, because they match neither host earnings nor renter prices.

Each cycle records whether an on-demand rental is running on the host (`current_rentals_running_on_demand`; `current_rentals_on_demand` also counts stopped rentals) at its listed price. From these observations the service keeps an occupancy history in memory: idle hours, occupied hours and rental starts per listed price, over a rolling seven-day window that restarts with the process. Observations are one per cycle, so rentals that start and end between cycles are not seen.

Pricing maximizes expected hourly profit, `occupancy × (price − GPU_RUNNING_COST)`, where occupancy is the expected fraction of GPU-hours rented on demand over the next 24 hours. Gemini receives the host's current price, hardware and running rentals, the occupancy history, the market demand, the host share, each peer's GPU-only price in host earnings and available hardware/reliability/location data, the peer median, and the configured price bounds. It works through market demand, competition at each price, the host's own demand evidence and the effect on active rentals, then returns 3–8 candidate prices, each with an occupancy estimate and a brief rationale. The service rounds candidates to cents and **rejects** the answer when a candidate falls outside `MIN_PRICE`–`MAX_PRICE` or below `MIN_BID_PRICE` (USD per GPU-hour), when occupancy leaves 0–1 or rises with price, or when the current price is within bounds but missing. It then picks the candidate with the highest expected profit, preferring the price closest to the current one on ties. Missing or invalid Gemini answers do not trigger listing updates.

Cycles are skipped with fewer than `MIN_PEERS` usable offers, when any search hits `OFFER_LIMIT` (to avoid a truncated sample), or when market metrics are unavailable. The service logs when the GPU price differs by at least `MIN_CHANGE` or any configured listing field differs. It does not filter by geography, RAM, network, or reliability; these are provided to Gemini for comparison. A successful cycle sends the status to every subscribed chat, including when the suggestion equals the listed price. A failed cycle only logs and retries; it sends no button.

The message has one button, labeled with the suggested GPU price. Tapping it calls `list_machine` with that price and the configured disk, internet, bid floor, discount, minimum GPU chunk, volume, and duration. That tap is the only listing write, and it refreshes the listing horizon. The button keeps working on older messages: the price on that message is checked against the configured bounds again, then written. A price increase for an existing rental can require the client's acceptance; see the [Vast host list-machine reference](https://docs.vast.ai/host/sdk/list-machine). `POLL_SECONDS` must be at least 60; the example is 86400 (daily), which fits the Gemini free tier of 20 requests per model per day. Gemini overload errors (5xx) are retried up to five attempts with backoff, and every attempt counts against that quota; quota errors (429) are not retried. After a failed cycle the service retries in one hour, or after `POLL_SECONDS` if that is shorter.

## Telegram

Create a bot with [@BotFather](https://t.me/BotFather). The subscribe code is 1–64 characters from `A–Z`, `a–z`, `0–9`, `_`, and `-`. In a private chat, send `/start` followed by that code, or open `https://t.me/<bot>?start=<code>`. `/stop` unsubscribes. A new subscriber receives the latest successful status immediately when the process still has one. Subscribed chat ids are stored at `TELEGRAM_STATE_PATH` on the `telegram-state` volume, so recreating the container keeps them. Group chats cannot subscribe or apply a price. Only one process may poll a given bot token.

The service logs the bot's `@username` at startup. It omits the token, the subscribe code, and incoming message text from logs.

## Run

Deploy to `darkhorn` builds the `linux/amd64` image on this machine, loads it on the host, and starts it from `~/apps/vastai-hosting`. The host receives the image, `compose.yml`, and an env file that contains the four Doppler secrets:

```sh
./scripts/deploy.sh
ssh darkhorn 'cd ~/apps/vastai-hosting && docker compose logs -f'
```

For a local cycle, with [uv](https://docs.astral.sh/uv/) installed:

```sh
doppler run -- uv run python -m vastai_hosting.main
```

The same image runs on any host with outbound access to Vast.ai, Gemini, and `api.telegram.org`. No GPU or Docker socket access is required inside the container. An env file for that path needs `VAST_API_KEY`, `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_SUBSCRIBE_SECRET`:

```sh
docker build --platform linux/amd64 -t vastai-hosting .
docker run -d --name vastai-hosting --restart unless-stopped \
  --env-file .env -v telegram-state:/app/data vastai-hosting
```

Validate with `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`, `uv run pytest`, and `uv build`.

Docs: [search offers](https://docs.vast.ai/sdk/python/quickstart), [show machines](https://docs.vast.ai/host/sdk/show-machines), [list machine](https://docs.vast.ai/host/sdk/list-machine), [Gemini structured output](https://ai.google.dev/gemini-api/docs/generate-content/structured-output).
