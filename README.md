# Vast.ai host pricing

A headless Rust service that checks on-demand GPU offers on Vast.ai and maintains a host's full listing using the official `vastai` CLI. It runs one cycle immediately, then repeats at `POLL_SECONDS`. The image contains a static Rust binary and a small Python runtime for the CLI; it has no web server or exposed ports.

## Configure

```sh
cp .env.example .env
# Edit .env: provide VAST_API_KEY; review every listing setting.
```

`.env.example` includes an example based on machine `51673` (RTX 5090); change it for another host. Obtain a Vast API key with permission to view offers and manage host machines. All listing fields must be explicit: GPU price is computed; disk, internet, bid floor, discount, minimum GPU chunk, volume size, volume price, and rolling `DURATION_DAYS` come from configuration. The machine must already be listed.

The market query matches **GPU model and exact GPU count**, verified and rentable on-demand offers. It excludes the host machine and duplicate machine IDs, takes the median GPU-only `dph_base / num_gpus`, rounds to cents, and clamps to `MIN_PRICE`–`MAX_PRICE` (USD per GPU-hour). It skips cycles with fewer than `MIN_PEERS` usable offers or when the query hits `OFFER_LIMIT` (to avoid pricing against a truncated cheapest-first sample). The service updates when the GPU price differs by at least `MIN_CHANGE` or any configured listing field differs. It does not filter by geography, RAM, network, or reliability; adjust the comparison policy in `src/main.rs` if those matter for your host.

`DRY_RUN=true` by default: review proposed prices in logs before setting `DRY_RUN=false`. A price increase for an existing rental can require the client's acceptance; see the [Vast CLI list-machine reference](https://docs.vast.ai/cli/reference/list-machine). `POLL_SECONDS` must be at least 60; 1800 is the example interval. The listing duration is refreshed with `vastai list machine -l` when less than one day of its configured horizon is available.

## Run

```sh
docker build --platform linux/amd64 -t vastai-hosting .
docker run -d --name vastai-hosting --restart unless-stopped --env-file .env vastai-hosting
docker logs -f vastai-hosting
```

Run it on the machine hosting the service or on any host with outbound access to Vast.ai. No GPU or Docker socket access is required inside the container. For local development, install the official `vastai` CLI and Rust, export the values in `.env`, then run `cargo run --release`. Validate with `cargo fmt --check` and `cargo test`.

Docs: [search offers](https://docs.vast.ai/cli/reference/search-offers), [show machines](https://docs.vast.ai/cli/reference/show-machines), [list machine](https://docs.vast.ai/cli/reference/list-machine).
