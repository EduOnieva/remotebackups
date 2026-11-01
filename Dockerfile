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
# pip, ensurepip, idle and pydoc never run here; pip's vendored urllib3, msgpack and setuptools
# are what image scanners flag, so they leave with it
RUN apk add --no-cache rsync openssh-client sshpass tini tzdata ca-certificates \
    && rm -rf /usr/local/lib/python3.13/site-packages/pip* \
              /usr/local/lib/python3.13/ensurepip \
              /usr/local/lib/python3.13/idlelib \
              /usr/local/bin/pip* /usr/local/bin/idle* /usr/local/bin/pydoc* /usr/local/bin/*-config
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG_DIR=/config \
    TZ=Etc/UTC
VOLUME ["/config"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["remotebackups", "healthcheck"]
ENTRYPOINT ["/sbin/tini", "--", "remotebackups"]
CMD ["run"]
