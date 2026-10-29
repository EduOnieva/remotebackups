#!/bin/bash
set -euo pipefail  # Exit on error, undefined vars, pipe failures

# Logging functions
log_info() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] $*" | tee -a /config/logs/entrypoint.log
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $*" | tee -a /config/logs/entrypoint.log >&2
}

log_warn() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] $*" | tee -a /config/logs/entrypoint.log
}

# Cleanup function
cleanup() {
    log_info "Container shutting down, performing cleanup..."
    jobs -p | xargs -r kill
    exit 0
}
trap cleanup SIGTERM SIGINT

# Create log directory early
mkdir -p /config/logs
chmod 755 /config/logs

log_info "Starting rsync-backup container..."

# Validate required environment variables
required_vars=("CRON_SCHEDULE" "REMOTE_USER" "REMOTE_HOST" "ROUTES_FILE" "SSH_KEY_FILE")
for var in "${required_vars[@]}"; do
    if [ -z "${!var:-}" ]; then
        log_error "Required environment variable $var is not set"
        exit 1
    fi
done

log_info "Environment validation passed"

# Validate SSH key file with proper permissions
SSH_KEY_PATH="/mnt/ssh_keys/$SSH_KEY_FILE"
if [ ! -f "$SSH_KEY_PATH" ]; then
    log_error "SSH private key not found at $SSH_KEY_PATH"
    exit 1
fi

# Check SSH key permissions
SSH_KEY_PERMS=$(stat -c "%a" "$SSH_KEY_PATH")
if [ "$SSH_KEY_PERMS" != "600" ] && [ "$SSH_KEY_PERMS" != "400" ]; then
    log_warn "SSH key has permissions $SSH_KEY_PERMS, should be 600 or 400"
fi

# Setup SSH configuration securely
mkdir -p /.ssh
chmod 700 /.ssh
cp "$SSH_KEY_PATH" "/.ssh/id_rsa"
chmod 600 /.ssh/id_rsa

# Create SSH config for better security and suppress warnings
cat > /.ssh/config << EOF
Host *
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel QUIET
    ServerAliveInterval 60
    ServerAliveCountMax 3
    ConnectTimeout 10
EOF
chmod 600 /.ssh/config

log_info "SSH configuration completed"

# Export environment variables securely
echo "export REMOTE_USER='$REMOTE_USER'" > /etc/environment
echo "export REMOTE_HOST='$REMOTE_HOST'" >> /etc/environment
# Optional: only the TrueNAS status endpoint needs it, so an unset token must not abort startup under set -u
echo "export REMOTE_API_TOKEN='${REMOTE_API_TOKEN:-}'" >> /etc/environment
echo "export ROUTES_FILE='$ROUTES_FILE'" >> /etc/environment
echo "export SSH_KEY_FILE='$SSH_KEY_FILE'" >> /etc/environment
chmod 644 /etc/environment

# Enhanced connectivity check with timeout and retries
log_info "Testing connectivity to remote host $REMOTE_HOST..."
MAX_RETRIES=10
RETRY_COUNT=0
CONNECTIVITY_OK=false

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if timeout 10 ping -c 1 -W 5 "$REMOTE_HOST" >/dev/null 2>&1; then
        CONNECTIVITY_OK=true
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    log_warn "Cannot reach $REMOTE_HOST (attempt $RETRY_COUNT/$MAX_RETRIES), waiting 10 seconds..."
    sleep 10
done

if [ "$CONNECTIVITY_OK" = "false" ]; then
    log_error "Failed to connect to $REMOTE_HOST after $MAX_RETRIES attempts"
    exit 1
fi

log_info "Remote host $REMOTE_HOST is reachable"

# Setup cron job with enhanced logging
echo "$CRON_SCHEDULE . /etc/environment; /bin/bash /src/sync_script.sh >> /config/logs/sync.log 2>&1" > /etc/crontabs/root
chmod 0644 /etc/crontabs/root
log_info "Cron job scheduled: $CRON_SCHEDULE"

# Start web server in background (non-blocking if it fails)
log_info "Starting enhanced web server on port 80..."
python3 /src/web_server.py &
WEB_PID=$!
if kill -0 $WEB_PID 2>/dev/null; then
    log_info "Web server started with PID $WEB_PID"
else
    log_warn "Web server failed to start, continuing without web interface"
fi

# Start cron daemon
log_info "Starting cron daemon..."
exec crond -f -l 2
