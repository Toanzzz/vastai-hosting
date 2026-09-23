# Build natively for the target platform (amd64 for the Vast host).
FROM rust:1.98-alpine3.22 AS build
RUN apk add --no-cache musl-dev
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked

# The official Vast CLI is Python. Install only its CLI runtime dependencies,
# omitting the optional SDK/serverless/PDF/image stack from the final image.
FROM python:3.12-alpine3.22 AS cli
RUN pip install --no-cache-dir --no-deps --target /opt/vendor \
    vastai==1.8.0 requests==2.34.2 rich==15.0.0 argcomplete==3.7.2 \
    curlify==3.0.0 python-dateutil==2.9.0.post0 xdg==6.0.0 \
    urllib3==2.8.0 typing-extensions==4.16.0 certifi==2026.7.22 \
    charset-normalizer==3.5.1 idna==3.20 markdown-it-py==4.2.0 \
    pygments==2.21.0 mdurl==0.1.2 six==1.17.0

FROM alpine:3.22
RUN apk add --no-cache python3 ca-certificates && adduser -D -u 10001 app
COPY --from=cli /opt/vendor /opt/vendor
ENV PYTHONPATH=/opt/vendor HOME=/home/app PYTHONUNBUFFERED=1
# Exec-form wrapper so the CLI uses the system Python without a shell.
COPY --chmod=755 docker/vastai /usr/local/bin/vastai
COPY --from=build /app/target/release/vastai-hosting /usr/local/bin/vastai-hosting
USER app
ENTRYPOINT ["/usr/local/bin/vastai-hosting"]
