FROM ghcr.io/astral-sh/uv:0.12.17 AS uv

FROM python:3.12-alpine3.22 AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
# Vast's PDF, CLI presentation and serverless/async dependencies are not used
# by show_machines, search_offers or list_machine. Keep the lockfile as the
# dependency source, omitting these optional SDK paths from the runtime image.
RUN uv sync --locked --no-dev --no-editable --no-cache \
    --no-install-package aiodns --no-install-package aiohttp \
    --no-install-package aiohappyeyeballs --no-install-package aiosignal \
    --no-install-package argcomplete --no-install-package attrs \
    --no-install-package borb --no-install-package curlify \
    --no-install-package fonttools --no-install-package frozenlist \
    --no-install-package lxml --no-install-package markdown-it-py \
    --no-install-package mdurl --no-install-package multidict \
    --no-install-package pillow --no-install-package propcache \
    --no-install-package psutil --no-install-package pycares \
    --no-install-package pycryptodome --no-install-package pygments \
    --no-install-package python-barcode \
    --no-install-package qrcode --no-install-package rich \
    --no-install-package setuptools --no-install-package xdg \
    --no-install-package yarl \
    && find .venv -type d -name __pycache__ -prune -exec rm -rf {} + \
    && find .venv -type d -name tests -prune -exec rm -rf {} +

FROM python:3.12-alpine3.22
RUN adduser -D -u 10001 app
WORKDIR /app
COPY --from=build /app/.venv/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/home/app
USER app
ENTRYPOINT ["python", "-m", "vastai_hosting.main"]
