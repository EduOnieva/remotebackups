# The integration test runner: the project's dev dependencies, no project install.
# uv is a stage so that Dependabot, which reads FROM lines, moves it with the main Dockerfile's
FROM ghcr.io/astral-sh/uv:0.13.8 AS uv
FROM python:3.15.0-alpine3.24
COPY --from=uv /uv /bin/uv
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
WORKDIR /work
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --only-group dev --no-install-project
COPY tests ./tests
# --noconftest: the unit-test conftest imports the package, which this image does not have
CMD ["pytest", "--noconftest", "-p", "no:cacheprovider", "-o", "addopts=", "-v", "tests/integration"]
