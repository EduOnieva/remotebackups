# syntax=docker/dockerfile:1.7
# remotebackups: scheduled rsync backups of home-server folders to another host.
# Three stages: resolve the locked Python environment with uv, assemble the runtime filesystem,
# then ship that filesystem as the only layer of an otherwise empty image.

# Tag and digest: a tag can be pushed again with other contents, a digest cannot. Both images
# are stages so that Dependabot, which reads FROM lines, keeps tag and digest current
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv
FROM python:3.13.18-alpine3.24@sha256:9ef1a599f863231c3d91e941389bfdc4a2d6070557243d54df1a4e57015b960e AS base

FROM base AS builder
COPY --from=uv /uv /bin/uv
# UV_NO_BUILD: a dependency without a musl wheel fails the build here instead of compiling quietly
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_BUILD=1 UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM base AS rootfs
# apk upgrade: the base is pinned, so Alpine's fixes for its packages arrive here, at each build.
# Nothing imports sqlite: .python-rundeps is redeclared without it so apk lets it go, and an
# empty list stops the build. apk info without --no-cache leaves the package index in the image.
# The rest never runs here: pip, whose vendored urllib3, msgpack and setuptools are what image
# scanners flag, ensurepip, idle, pydoc, the C headers and CPython's own test modules
RUN apk upgrade --no-cache \
    && apk add --no-cache rsync openssh-client sshpass tini tzdata ca-certificates \
    && rundeps="$(apk info --no-cache -R .python-rundeps | sed 1d | grep -v -e '^$' -e '^so:libsqlite3\.')" \
    && apk add --no-cache --virtual .python-rundeps $rundeps \
    && apk del --no-cache sqlite-libs \
    && rm -rf /usr/local/include /usr/local/bin/pip* /usr/local/bin/idle* /usr/local/bin/pydoc* \
              /usr/local/bin/*-config \
    && cd /usr/local/lib/python3.* \
    && rm -rf site-packages/pip* ensurepip idlelib sqlite3 lib-dynload/_sqlite3.* \
              lib-dynload/_test* lib-dynload/_ctypes_test.* lib-dynload/_xxtestfuzz.* lib-dynload/xx*
COPY --from=builder /opt/venv /opt/venv

# An empty image with that filesystem as its one layer: on top of the base image's layers, what
# the stage above deleted or upgraded would still be shipped underneath, hidden but downloaded
FROM scratch
COPY --from=rootfs / /
# scratch inherits nothing: PATH and LANG are the base image's, restated
ENV PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG_DIR=/config \
    TZ=Etc/UTC
VOLUME ["/config"]
EXPOSE 8080
# busybox wget, not `remotebackups healthcheck`: starting Python and importing the application
# for every check costs far more than the check. -Y off: a proxy variable must not capture it
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD wget -q -Y off -T 4 -O /dev/null "http://127.0.0.1:${WEB_PORT:-8080}/healthz"
ENTRYPOINT ["/sbin/tini", "--", "remotebackups"]
CMD ["run"]
