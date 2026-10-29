# Multi-stage build: production and testing
FROM alpine:3.24.3 AS base

# Install common dependencies
RUN apk add --no-cache openssh-client rsync util-linux bash tzdata jq python3 py3-pip

# DDP WebSocket client for the TrueNAS status endpoint
RUN pip3 install --no-cache-dir --break-system-packages websocket-client==1.9.0

# Production stage
FROM base AS production

COPY build/entrypoint.sh /entrypoint.sh
COPY backup_routes.json /backup_routes.json
COPY src /src

RUN chmod +x /entrypoint.sh /src/sync_script.sh /src/web_server.py

VOLUME /data

CMD ["/entrypoint.sh"]

# Testing stage
FROM base AS testing

# Install test dependencies
RUN pip3 install --no-cache-dir --break-system-packages \
    pytest==9.2.0 \
    pytest-cov==7.1.0 \
    pytest-mock==3.17.0 \
    pytest-timeout==2.4.0 \
    psutil==7.2.2 \
    requests==2.32.5

# Copy application source
COPY src /src
COPY backup_routes.json /backup_routes.json
COPY build/entrypoint.sh /entrypoint.sh

# Copy test files
COPY tests /tests
COPY pytest.ini /pytest.ini
COPY requirements-test.txt /requirements-test.txt
COPY run_tests.sh /run_tests.sh

# Make scripts executable
RUN chmod +x /entrypoint.sh /src/sync_script.sh /src/web_server.py /run_tests.sh

# Set working directory for tests
WORKDIR /

# Set Python path for module imports
ENV PYTHONPATH=/src

# Default command for test container
CMD ["/run_tests.sh", "help"]