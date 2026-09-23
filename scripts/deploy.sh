#!/usr/bin/env bash
# Build the linux/amd64 image locally, load it on darkhorn, and start it.
# Doppler project vastai-hosting, config prd, supplies VAST_API_KEY and GEMINI_API_KEY.
# Every other setting uses the defaults in vastai_hosting.config.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
host=darkhorn
# Home-LAN address of the same machine. Used when the Tailscale name does not accept SSH.
lan_host=192.168.68.66
image=vastai-hosting
platform=linux/amd64
remote=apps/vastai-hosting
stage=$(mktemp -d)
ssh_opts=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 -o HostKeyAlias=darkhorn)
trap 'rm -rf "$stage"' EXIT

for cmd in docker doppler gzip ssh rsync python3; do
  command -v "$cmd" >/dev/null || {
    echo "missing $cmd" >&2
    exit 1
  }
done

doppler secrets download -p vastai-hosting -c prd --no-file --format json >"$stage/secrets.json"
python3 - "$stage/secrets.json" "$stage/.env" <<'PY'
import json
import sys

source, dest = sys.argv[1:]
with open(source, encoding="utf-8") as handle:
    data = json.load(handle)
lines: list[str] = []
for key in ("VAST_API_KEY", "GEMINI_API_KEY"):
    value = str(data.get(key, "")).strip()
    if not value or any(char in value for char in "\n\r"):
        raise SystemExit(f"Doppler vastai-hosting/prd is missing a usable {key}")
    lines.append(f"{key}={value}\n")
with open(dest, "w", encoding="utf-8") as handle:
    handle.writelines(lines)
PY
rm -f "$stage/secrets.json"
chmod 600 "$stage/.env"
cp "$root/compose.yml" "$stage/compose.yml"

target=$host
if ! ssh "${ssh_opts[@]}" -o ConnectTimeout=5 "$target" true; then
  echo "$host did not accept SSH; using $lan_host"
  target=$lan_host
  ssh "${ssh_opts[@]}" -o ConnectTimeout=5 "$target" true
fi

echo "building $image for $platform"
docker build --platform "$platform" -t "$image" "$root"

echo "loading image on $target"
docker save "$image:latest" | gzip -c | ssh "${ssh_opts[@]}" "$target" "gunzip -c | docker load"

ssh "${ssh_opts[@]}" "$target" "mkdir -p ~/$remote"
rsync -az --delete -e "ssh ${ssh_opts[*]}" "$stage/" "$target:$remote/"
ssh "${ssh_opts[@]}" "$target" "cd ~/$remote && docker compose up -d --no-build --pull never --force-recreate --remove-orphans"
ssh "${ssh_opts[@]}" "$target" "cd ~/$remote && docker compose ps"
