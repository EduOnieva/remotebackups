# Multi-stage build: production and testing
FROM alpine:latest as base

# Install common dependencies
RUN apk add --no-cache openssh-client git rsync util-linux bash tzdata jq python3 py3-pip

# Install requests for web server
RUN pip3 install --no-cache-dir --break-system-packages requests==2.32.5
RUN pip3 install --no-cache-dir --break-system-packages websocket-client==1.9.0

# Production stage
FROM base as production

COPY build/entrypoint.sh /entrypoint.sh
COPY backup_routes.json /backup_routes.json
COPY src /src

RUN chmod +x /entrypoint.sh /src/sync_script.sh /src/web_server.py

VOLUME /data

CMD ["/entrypoint.sh"]

# Testing stage
FROM base as testing

# Install test dependencies
RUN pip3 install --no-cache-dir --break-system-packages \
    pytest>=7.0.0 \
    pytest-cov>=4.0.0 \
    pytest-mock>=3.10.0 \
    pytest-timeout>=2.1.0 \
    psutil>=5.9.0 \
    requests>=2.28.0

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