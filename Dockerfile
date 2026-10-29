# syntax=docker/dockerfile:1.7
# remotebackups: scheduled rsync backups of home-server folders to another host.
# Two stages: resolve the locked Python environment with uv, then copy it onto a slim runtime.

FROM python:3.13.18-alpine3.24 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/uv
# UV_NO_BUILD: a dependency without a musl wheel fails the build here instead of compiling quietly
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_BUILD=1 UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.13.18-alpine3.24
RUN apk add --no-cache rsync openssh-client sshpass tini tzdata ca-certificates
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    CONFIG_DIR=/config \
    TZ=Etc/UTC
VOLUME ["/config"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["remotebackups", "healthcheck"]
ENTRYPOINT ["/sbin/tini", "--", "remotebackups"]
CMD ["run"]
