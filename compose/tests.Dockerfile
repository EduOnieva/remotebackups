# The integration test runner: the project's dev dependencies, no project install.
FROM python:3.13.18-alpine3.24
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/uv
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
WORKDIR /work
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --only-group dev --no-install-project
COPY tests ./tests
# --noconftest: the unit-test conftest imports the package, which this image does not have
CMD ["pytest", "--noconftest", "-p", "no:cacheprovider", "-o", "addopts=", "-v", "tests/integration"]
