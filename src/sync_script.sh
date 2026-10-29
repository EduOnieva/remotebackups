#!/bin/bash
set -euo pipefail

# Enhanced logging functions with consistent quoting and documentation
log_file="/config/logs/sync.log"
# Log an informational message with timestamp
log_info() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] $*" | tee -a "$log_file"
}

# Log an error message with timestamp to stderr
log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $*" | tee -a "$log_file" >&2
}

# Log a warning message with timestamp
log_warn() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] $*" | tee -a "$log_file"
}

# Log a success message with timestamp
log_success() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [SUCCESS] $*" | tee -a "$log_file"
}

# One run per file: the web view and its error count then cover the latest run
# only, and rsync's per-file output cannot grow the log without bound.
LOG_KEEP=5
rotate_log() {
    local i
    for ((i = LOG_KEEP - 1; i >= 1; i--)); do
        if [ -f "$log_file.$i" ]; then
            mv -f "$log_file.$i" "$log_file.$((i + 1))"
        fi
    done
    if [ -s "$log_file" ]; then
        mv -f "$log_file" "$log_file.1"
    fi
    touch "$log_file"
}

# Path validation function to prevent directory traversal
# Arguments:
#   $1 - path to validate
#   $2 - type of path ("source" or "destination")
# Returns: validated path on success, exits with error on failure
validate_path() {
    local path="$1"
    local type="$2"  # "source" or "destination"
    
    # Check for empty path
    if [ -z "$path" ]; then
        log_error "Empty $type path not allowed"
        return 1
    fi
    
    # Check for directory traversal attempts
    if [[ "$path" == *"../"* ]] || [[ "$path" == *"..\\"* ]]; then
        log_error "Directory traversal detected in $type path: $path"
        return 1
    fi
    
    # Ensure absolute path for source (local paths)
    if [ "$type" = "source" ] && [[ "$path" != /* ]]; then
        log_error "Source path must be absolute: $path"
        return 1
    fi
    
    # Check for suspicious characters
    if [[ "$path" =~ [\;\&\|\`\$\(\)] ]]; then
        log_error "Potentially dangerous characters in $type path: $path"
        return 1
    fi
    
    # Normalize path (remove double slashes, trailing slashes except root)
    path="$(echo "$path" | sed 's|//*|/|g' | sed 's|/$||' | sed 's|^$|/|')"
    echo "$path"
    return 0
}

# Cleanup function to handle script interruption
cleanup() {
    log_info "Sync script interrupted, cleaning up..."
    jobs -p | xargs -r kill 2>/dev/null || true
    exit 130
}
trap cleanup SIGINT SIGTERM

# Mark a run that dies before its summary so the web status does not stay on "Running"
run_started=0
run_completed=0
on_exit() {
    local rc=$?
    if [ "$run_started" = 1 ] && [ "$run_completed" = 0 ]; then
        log_error "Sync process aborted (exit code: $rc)"
    fi
}
trap on_exit EXIT

# Source environment variables with error checking
if ! source /etc/environment; then
    log_error "Failed to source environment variables"
    exit 1
fi

# Validate required environment variables with consistent quoting
required_vars=("REMOTE_USER" "REMOTE_HOST" "ROUTES_FILE")
for var in "${required_vars[@]}"; do
    if [ -z "${!var:-}" ]; then
        log_error "Required environment variable '$var' is missing"
        exit 1
    fi
done

# Validate routes file with enhanced checks and consistent quoting
if [ ! -f "$ROUTES_FILE" ]; then
    log_error "Routes file not found: '$ROUTES_FILE'"
    exit 1
fi

if [ ! -r "$ROUTES_FILE" ]; then
    log_error "Routes file not readable: '$ROUTES_FILE'"
    exit 1
fi

# Ensure detail log file exists
mkdir -p /config/logs
touch "$log_file"

# Create and open lock file explicitly
LOCK_FILE="/var/lock/sync_script.lock"
mkdir -p "$(dirname "$LOCK_FILE")"
exec 200>"$LOCK_FILE"

# Take the lock before announcing the run, so a skipped run is never reported as aborted.
# Non-blocking on purpose: a run that overlaps the previous one is skipped, not queued.
if ! flock -n 200; then
    log_warn "Another sync instance is running, skipping this run"
    exit 1
fi

rotate_log
log_info "Starting sync process..."
run_started=1
log_info "Acquired sync lock successfully"

  # Validate JSON with comprehensive error handling
  if ! jq empty "$ROUTES_FILE" 2>/dev/null; then
    log_error "Invalid JSON in routes file: $ROUTES_FILE"
    exit 1
  fi

  # Get route count with validation
  route_count=$(jq -r 'length' "$ROUTES_FILE" 2>/dev/null || echo "0")
  
  if ! [[ "$route_count" =~ ^[0-9]+$ ]] || [ "$route_count" -eq 0 ]; then
    log_warn "No valid routes found in $ROUTES_FILE"
    exit 0
  fi

  log_info "Processing $route_count backup route(s)"

  # Test SSH connection once before processing routes
  log_info "Testing SSH connection to $REMOTE_USER@$REMOTE_HOST"
  
  # Check if SSH key exists
  if [ ! -f "/.ssh/id_rsa" ]; then
    log_error "SSH private key not found at /.ssh/id_rsa"
    exit 1
  fi
  
  # Test SSH connection with verbose output for debugging
  ssh_output=$(timeout 15 ssh -i /.ssh/id_rsa -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -v \
       "$REMOTE_USER@$REMOTE_HOST" "echo 'Connection OK'" 2>&1) || ssh_result=$?
  
  if [ ${ssh_result:-0} -ne 0 ]; then
    log_error "SSH connection failed to $REMOTE_USER@$REMOTE_HOST (exit code: ${ssh_result:-0})"
    log_error "SSH debug output: $ssh_output"
    exit 1
  else
    log_info "SSH connection successful"
  fi

  # Statistics tracking
  success_count=0
  failure_count=0
  total_transferred=0
  total_start_time=$(date +%s)
  # Build optional rsync flags from environment (applies to all routes)
  RSYNC_EXTRA_OPTS=()
  if [ "${RSYNC_DELETE:-}" = "1" ] || [ "${RSYNC_DELETE:-}" = "true" ]; then
    RSYNC_EXTRA_OPTS+=("--delete")
    log_warn "RSYNC_DELETE enabled: rsync will remove extraneous files on destination"
  fi
  # Set by the cron entry only; a stalled route then fails instead of holding the lock forever
  if [ "${RSYNC_TIMEOUT:-0}" -gt 0 ]; then
    RSYNC_EXTRA_OPTS+=("--timeout=$RSYNC_TIMEOUT")
    log_info "Scheduled run: rsync gives up on a route after ${RSYNC_TIMEOUT}s without I/O"
  fi

  # Process each route with enhanced error handling
  while IFS=$'\t' read -r src dst; do
    
    # Validate route entry
    if [ -z "$src" ] || [ -z "$dst" ]; then
      log_error "Invalid route entry: source='$src' destination='$dst'"
      failure_count=$((failure_count + 1))
      continue
    fi

    # Validate and sanitize paths
    if ! validated_src=$(validate_path "$src" "source"); then
      failure_count=$((failure_count + 1))
      continue
    fi
    
    if ! validated_dst=$(validate_path "$dst" "destination"); then
      failure_count=$((failure_count + 1))
      continue
    fi
    
    # Use validated paths
    src="$validated_src"
    dst="$validated_dst"

    # Debug: Show exact paths being used
    log_info "Raw source path: '$src'"
    log_info "Raw destination path: '$dst'"
    log_info "Starting sync: $src -> $dst"
    
    # Pre-sync validation - check local source directory
    if [ ! -d "$src" ]; then
      log_error "Local source directory not found: $src"
      failure_count=$((failure_count + 1))
      continue
    fi

    # Execute rsync with comprehensive options and error handling
    start_time=$(date +%s)
      if rsync -avz "${RSYNC_EXTRA_OPTS[@]}" --partial --stats \
      --exclude='*.Trash*' --exclude='lost+found' --exclude='System Volume Information' \
      --exclude='.DS_Store' --exclude='Thumbs.db' --exclude='desktop.ini' --exclude='sync.log' \
      --exclude='.venv' \
      -e "ssh -i /.ssh/id_rsa -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=60 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null" \
      "$src/" "$REMOTE_USER@$REMOTE_HOST:$dst/" 2>&1 | tee -a "$log_file"; then
      
      end_time=$(date +%s)
      duration=$((end_time - start_time))
      
      log_success "Sync completed: $src -> $dst (${duration}s)"
      success_count=$((success_count + 1))
    else
      rsync_exit_code=$?
      end_time=$(date +%s)
      duration=$((end_time - start_time))
      
      log_error "Sync failed: $src -> $dst (exit code: $rsync_exit_code, duration: ${duration}s)"
      failure_count=$((failure_count + 1))
    fi
  done < <(jq -r 'to_entries[] | "\(.key)\t\(.value)"' "$ROUTES_FILE")

  # Final statistics and summary
  total_end_time=$(date +%s)
  total_duration=$((total_end_time - total_start_time))
  
  # Convert duration to HH:MM:SS format
  hours=$((total_duration / 3600))
  minutes=$(((total_duration % 3600) / 60))
  seconds=$((total_duration % 60))
  formatted_duration=$(printf "%02d:%02d:%02d" $hours $minutes $seconds)
  
  log_info "Sync process completed - Success: $success_count, Failures: $failure_count, Total Duration: $formatted_duration"
  
  if [ "$failure_count" -gt 0 ]; then
    log_warn "Some syncs failed. Check logs for details."
  else
    log_success "All syncs completed successfully"
  fi

run_completed=1

# Close the lock file
exec 200>&-
