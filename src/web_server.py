#!/usr/bin/env python3
import http.server
import json
import logging
import logging.handlers
import os
import signal
import socketserver
import ssl
import subprocess
import sys
import threading
import uuid


from websocket import create_connection
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Configuration
LOG_FILE: str = '/config/logs/sync.log'
SERVER_LOG_FILE: str = '/config/logs/web_server.log'
PORT: int = 80
MAX_LOG_SIZE: int = 1024 * 1024 * 500  # 500 MB
MAX_LOG_BACKUPS: int = 10

# Ensure log directory exists before setting up logging
os.makedirs(os.path.dirname(SERVER_LOG_FILE), exist_ok=True)

# Setup logging with rotation
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create formatter
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

# File handler with rotation
file_handler = logging.handlers.RotatingFileHandler(
    SERVER_LOG_FILE, maxBytes=MAX_LOG_SIZE, backupCount=MAX_LOG_BACKUPS
)
file_handler.setFormatter(formatter)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

# Add handlers
logger.addHandler(file_handler)
logger.addHandler(console_handler)

def truenas_enabled() -> bool:
    """TrueNAS status is on unless TRUENAS_DISABLED opts out; then the server is rsync-only."""
    return os.environ.get('TRUENAS_DISABLED', 'false').lower() not in ('1', 'true', 'yes')


class TrueNASHandler:
    """Read alerts and free space per pool from a TrueNAS box over its DDP WebSocket API."""

    # Events and pings can arrive before our result; stop waiting after this many
    MAX_SKIPPED_MESSAGES = 50

    def __init__(self) -> None:
        self.remote_host = os.environ.get('REMOTE_HOST')
        self.remote_api_token = os.environ.get('REMOTE_API_TOKEN')
        # Off by default: a home NAS reached over Tailscale usually has a self-signed certificate
        self.verify_tls = os.environ.get('TRUENAS_VERIFY_TLS', 'false').lower() in ('1', 'true', 'yes')

    def _call_ddp_method(self, ws: Any, method: str, params: Optional[List[Any]] = None) -> Any:
        """Send one DDP method call and return its result, skipping unrelated traffic."""
        request_id = str(uuid.uuid4())
        ws.send(json.dumps({'id': request_id, 'msg': 'method', 'method': method, 'params': params or []}))
        for _ in range(self.MAX_SKIPPED_MESSAGES):
            raw = ws.recv()
            if not raw or not raw.strip():
                continue
            response = json.loads(raw)
            if response.get('msg') != 'result' or response.get('id') != request_id:
                continue
            if 'error' in response:
                raise RuntimeError(f"DDP method {method} failed: {response['error']}")
            return response.get('result')
        raise RuntimeError(f'No result for DDP method {method} after {self.MAX_SKIPPED_MESSAGES} messages')

    def get_pool_status(self) -> Dict[str, Any]:
        """Return the alert count and free space per pool keyed by pool name, or an error entry."""
        if not self.remote_host or not self.remote_api_token:
            return {'error': 'REMOTE_HOST or REMOTE_API_TOKEN not configured'}

        ws = None
        try:
            ws = create_connection(
                f'wss://{self.remote_host}/websocket',
                sslopt={'cert_reqs': ssl.CERT_REQUIRED if self.verify_tls else ssl.CERT_NONE},
                timeout=30,
            )
            ws.send(json.dumps({'msg': 'connect', 'version': '1', 'support': ['1']}))
            handshake = json.loads(ws.recv())
            if handshake.get('msg') != 'connected':
                raise RuntimeError(f"DDP handshake rejected: {handshake.get('msg')}")
            if not self._call_ddp_method(ws, 'auth.login_with_api_key', [self.remote_api_token]):
                raise RuntimeError('API key rejected')
            alerts = self._call_ddp_method(ws, 'alert.list') or []
            pools = self._call_ddp_method(ws, 'pool.query') or []
        except Exception as e:
            # A NAS that is off or asleep is expected; the next request simply retries
            logger.warning('TrueNAS status unavailable: host=%s error=%s', self.remote_host, e)
            return {'error': f'Failed to connect to TrueNAS: {e}'}
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception as e:
                    logger.warning('Error closing TrueNAS WebSocket: %s', e)

        result: Dict[str, Any] = {'alerts': len(alerts)}
        if alerts:
            logger.warning('TrueNAS alerts (%d): %s', len(alerts),
                           '; '.join(a.get('formatted', a.get('text', 'unknown')) for a in alerts))
        for pool in pools:
            # Keyed by pool name: TrueNAS ids are database rows that change when a pool is recreated
            free_tb = round(pool.get('free', 0) / (1024 ** 4), 2)
            result[pool.get('name', 'unknown')] = f'{free_tb} TB'
        logger.info('TrueNAS status: alerts=%d pools=%d', len(alerts), len(pools))
        return result


class EnhancedLogHandler(http.server.SimpleHTTPRequestHandler):
    
    def log_message(self, format: str, *args) -> None:
        '''Override to use our logger'''
        logger.info(f'{self.address_string()} - {format % args}')

    def safe_read_log(self, log_path: str, max_lines: int = 10000) -> str:
        '''Safely read log file with size limits and error summary'''
        try:
            if not os.path.exists(log_path):
                return 'Log file not found'
            
            file_size = os.path.getsize(log_path)
            if file_size > MAX_LOG_SIZE:
                # Read only the last part of large files
                with open(log_path, 'rb') as f:
                    f.seek(-MAX_LOG_SIZE, 2)
                    content = f.read().decode('utf-8', errors='ignore')
                    lines = content.splitlines()
                    _, error_summary = self._generate_error_summary(
                        lines, truncated=True)
                    lines.reverse()
                    full_content = '\n'.join(lines)
                    return (
                        f'{error_summary}[LOG TRUNCATED - showing last '
                        f'{MAX_LOG_SIZE} bytes]\n{full_content}')
            else:
                with open(log_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    lines = [line.rstrip('\n') for line in lines]
                    
                    if len(lines) > max_lines:
                        # Generate error summary for all lines, then truncate display
                        _, error_summary = self._generate_error_summary(lines)
                        lines = lines[-max_lines:]
                        lines.reverse()
                        truncated_lines = '\n'.join(lines[-max_lines:])
                        return (
                            f'{error_summary}[LOG TRUNCATED - showing last '
                            f'{max_lines} lines]\n{truncated_lines}')
                    else:
                        _, error_summary = self._generate_error_summary(lines)
                        lines.reverse()
                        return f'{error_summary}{chr(10).join(lines)}'
        except Exception as e:
            logger.error(f'Error reading log file {log_path}: {e}')
            return f'Error reading log file: {str(e)}'

    def _generate_error_summary(self, lines: List[str], truncated: bool = False) -> Tuple[int, str]:
        '''Summarize the tagged error lines of the log (size-limited)

        Only [ERROR]/[CRITICAL] lines count: rsync lists every transferred file,
        so matching free-text words would flag a media file named "Failed".
        Lines are listed without numbers because the page shows the log reversed.

        Returns:
            Tuple of (error_count, summary_string)
        '''
        error_tags = ('[ERROR]', '[CRITICAL]')
        max_errors_to_show = 15  # Limit errors shown to prevent summary from getting too large
        max_summary_chars = 2000  # Overall character limit for the entire summary

        errors = [line for line in lines if any(tag in line for tag in error_tags)]
        error_count = len(errors)
        
        if not errors:
            return (0, '🟢 ERROR SUMMARY: No errors found\n' + '='*50 + '\n\n')
        
        summary_header = f'🔴 ERROR SUMMARY: {error_count} error{"s" if error_count != 1 else ""} found'
        if truncated:
            summary_header += ' (in displayed portion)'
        summary_header += '\n' + '='*50 + '\n'
        
        # Limit to first max_errors_to_show errors in summary
        displayed_errors = errors[:max_errors_to_show]
        error_lines = '\n'.join(displayed_errors)
        
        if len(errors) > max_errors_to_show:
            error_lines += f'\n... and {len(errors) - max_errors_to_show} more errors (see full log below)'
        
        summary_content = summary_header + error_lines + '\n' + '='*50 + '\n\n'
        
        # Final safety check - truncate entire summary if it's too long
        if len(summary_content) > max_summary_chars:
            truncation_point = max_summary_chars - 100  # Leave room for truncation message
            summary_message = '\n[ERROR SUMMARY TRUNCATED - too many errors]\n' + '='*50 + '\n\n'
            summary_content = summary_content[:truncation_point] + summary_message
        
        return (error_count, summary_content)

    def get_sync_status(self) -> Tuple[str, str]:
        '''Check if sync process is running based on last few log lines with improved robustness'''
        try:
            if not os.path.exists(LOG_FILE):
                return '⚪ Unknown', '#7d8590'
            
            # Read last 5 lines to check status
            with open(LOG_FILE, 'rb') as f:
                # Go to end and read backwards to get last lines efficiently
                f.seek(0, 2)  # Go to end
                file_size = f.tell()
                if file_size == 0:
                    return '⚪ No logs', '#7d8590'
                
                # Read last 1KB to capture last few lines
                read_size = min(1024, file_size)
                f.seek(-read_size, 2)
                content = f.read().decode('utf-8', errors='ignore')
                lines = content.strip().split('\n')[-5:]  # Get last 5 lines
            
            # Check for completion indicators in last lines
            for line in reversed(lines):
                line = line.strip()
                if 'All syncs completed successfully' in line:
                    return '🟢 Completed', '#3fb950'
                elif 'Some syncs failed. Check logs for details.' in line:
                    return '🟡 Completed with errors', '#d29922'
                elif 'Sync process aborted' in line:
                    return '🔴 Aborted', '#f85149'
                elif 'Starting sync process.' in line:
                    return '🔵 Running', '#79c0ff'
                elif 'Logs cleared via web interface' in line:
                    return '⚪ No run yet', '#7d8590'
            
            # Default to running if no clear completion status found
            return '🔵 Running', '#79c0ff'
            
        except Exception as e:
            logger.error(f'Error checking sync status: {e}')
            return '❌ Error', '#f85149'

    def generate_html_page(self, sync_log: str, load_avg: Tuple[float, float, float]) -> str:
        '''Generate the HTML page with enhanced features'''
        # Safe log size calculation with proper error handling
        try:
            log_size = (os.path.getsize(LOG_FILE) / (1024 * 1024)) if os.path.exists(LOG_FILE) else 0
        except (OSError, IOError):
            log_size = 0
            
        last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sync_status, status_color = self.get_sync_status()
        
        css_styles = '''
        body { 
            font-family: 'Monaco', 'Menlo', monospace; margin: 0; 
            background: #0d1117; color: #c9d1d9; 
        }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        .header { 
            background: #161b22; padding: 20px; border-radius: 8px; 
            margin-bottom: 20px; border: 1px solid #30363d; 
        }
        h1 { color: #58a6ff; margin: 0; font-size: 2.2em; }
        .controls-status { 
            display: flex; justify-content: space-between; align-items: center; 
            padding: 15px; background: #161b22; border: 1px solid #30363d; 
            border-radius: 8px; margin-bottom: 20px; 
        }
        .system-status { color: #7d8590; font-size: 14px; }
        .controls { display: flex; gap: 10px; flex-wrap: wrap; }
        .btn { 
            background: #21262d; color: #f0f6fc; padding: 12px 24px; 
            border: 1px solid #30363d; border-radius: 6px; cursor: pointer; 
            font-family: inherit; font-size: 14px; transition: all 0.2s ease;
        }
        .btn:hover { background: #30363d; }
        .btn.primary { background: #238636; border-color: #2ea043; }
        .btn.primary:hover { background: #2ea043; }
        .btn.warning { background: #da3633; border-color: #f85149; }
        .btn.warning:hover { background: #f85149; }
        .btn.danger { background: #da3633; border-color: #f85149; }
        .btn.danger:hover { background: #f85149; }
        .log-container { display: grid; grid-template-columns: 1fr; gap: 20px; }
        .log-section { background: #0d1117; border: 1px solid #30363d; border-radius: 8px; }
        .log-section.full-width { grid-column: 1; }
        .log-header { 
            background: #161b22; padding: 15px; border-bottom: 1px solid #30363d; 
            font-weight: bold; 
        }
        .log-content { 
            background: #010409; padding: 20px; max-height: 73vh; overflow-y: auto; 
            white-space: pre-wrap; font-size: 13px; line-height: 1.4;
            scrollbar-width: thin; scrollbar-color: #30363d #0d1117;
        }
        .log-content::-webkit-scrollbar { width: 8px; }
        .log-content::-webkit-scrollbar-track { background: #0d1117; }
        .log-content::-webkit-scrollbar-thumb { background: #30363d; border-radius: 4px; }
        .timestamp { color: #7d8590; }
        .info { color: #79c0ff; }
        .success { color: #3fb950; }
        .error { color: #f85149; }
        .warning { color: #d29922; }
        @media (max-width: 768px) {
            .log-container { grid-template-columns: 1fr; }
            .controls { flex-direction: column; }
            .btn { width: 100%; }
        }
        '''
        
        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <title>Rsync Backup Management</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>{css_styles}</style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📄 Rsync Backup Management</h1>
            <div class="timestamp">Last updated: {last_updated}</div>
        </div>
        
        <div class="controls-status">
            <div class="system-status">
                📄 Log Size: {log_size:.2f} MB | 
                <span style="color: {status_color}; font-weight: bold;">{sync_status}</span>
            </div>
            <div class="controls">
                <button class="btn primary" onclick="runSync(false,false)">▶️ Run Sync Now</button>
                <button class="btn warning" onclick="runSync(true,true)">⚠️ Run With Delete</button>
                <button class="btn" onclick="location.reload()">🔄 Refresh</button>
                <button class="btn" onclick="clearLogs()">🗑️ Clear Logs</button>
            </div>
        </div>

        <div class="log-container">
            <div class="log-section full-width">
                <div class="log-header">📋 Sync Logs</div>
                <div class="log-content" id="syncLogs">
                    {sync_log if sync_log.strip() else 'No sync logs yet...'}
                </div>
            </div>
        </div>
    </div>

    <script>
        function apiRequest(endpoint, method = 'GET', data = null) {{
            const options = {{
                method: method,
                headers: {{'Content-Type': 'application/json'}}
            }};
            if (data) options.body = JSON.stringify(data);
            
            return fetch(endpoint, options)
                .then(response => {{
                    if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
                    return response;
                }});
        }}

        function showNotification(message, type = 'info') {{
            const colors = {{
                'success': '#3fb950',
                'error': '#f85149', 
                'warning': '#d29922',
                'info': '#79c0ff'
            }};
            
            const notification = document.createElement('div');
            notification.style.cssText = `
                position: fixed; top: 20px; right: 20px; z-index: 1000;
                background: ${{colors[type] || colors.info}}; color: white; padding: 15px 20px;
                border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,0.3);
                max-width: 300px; word-wrap: break-word;
            `;
            notification.textContent = message;
            document.body.appendChild(notification);
            
            setTimeout(() => notification.remove(), 4000);
        }}

        function clearLogs() {{
            if (confirm('Are you sure you want to clear all logs?')) {{
                apiRequest('/clear', 'POST')
                .then(() => {{
                    showNotification('Logs cleared successfully!', 'success');
                    setTimeout(() => location.reload(), 1000);
                }})
                .catch(err => showNotification(`Error clearing logs: ${{err.message}}`, 'error'));
            }}
        }}

        function runSync(withDelete = false, confirmBefore = false) {{
            const confirmMsg = 'Run sync script now with --delete? This will remove extraneous files on the destination and may take several minutes.';

            const doRun = () => {{
                showNotification(withDelete ? 'Sync started with delete enabled! Check logs for progress...' : 'Sync started! Check logs for progress...', withDelete ? 'warning' : 'info');
                const data = withDelete ? {{ delete: true }} : null;
                apiRequest('/run', 'POST', data)
                .then(() => {{
                    setTimeout(() => location.reload(), 2000);
                }})
                .catch(err => showNotification(`Error starting sync: ${{err.message}}`, 'error'));
            }};

            if (withDelete && confirmBefore) {{
                if (confirm(confirmMsg)) {{
                    doRun();
                }}
            }} else {{
                doRun();
            }}
        }}
        
        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {{
            if (e.ctrlKey || e.metaKey) {{
                switch(e.key) {{
                    case 'r': e.preventDefault(); location.reload(); break;
                    case 'Enter': e.preventDefault(); runSync(false,false); break;
                }}
            }}
        }});
    </script>
</body>
</html>'''

    def do_GET(self) -> None:
        try:
            if self.path == '/' or self.path == '/logs':
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                
                # Read log files
                sync_log = self.safe_read_log(LOG_FILE)
                
                # Get system info
                try:
                    load_avg = os.getloadavg()
                except:
                    load_avg = (0, 0, 0)
                
                html = self.generate_html_page(sync_log, load_avg)
                self.wfile.write(html.encode('utf-8'))
                
            elif self.path == '/api/status':
                # JSON API endpoint for status
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                
                # Safe log size calculation with proper error handling
                try:
                    log_size = (
                        round((os.path.getsize(LOG_FILE) / (1024 * 1024)), 2) 
                        if os.path.exists(LOG_FILE) else 0
                    )
                except (OSError, IOError):
                    log_size = 0
                
                status = {
                    'timestamp': datetime.now().isoformat(),
                    'log_exists': os.path.exists(LOG_FILE),
                    'log_size': log_size,
                    'server_uptime': 'online'
                }
                self.wfile.write(json.dumps(status).encode())
                
            elif self.path == '/favicon.ico':
                # Simple SVG favicon with document icon
                self.send_response(200)
                self.send_header('Content-type', 'image/svg+xml')
                self.send_header('Cache-Control', 'max-age=86400')  # Cache for 24 hours
                self.end_headers()
                
                svg_favicon = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
                    <rect width="32" height="32" fill="#161b22"/>
                    <rect x="8" y="6" width="16" height="20" rx="1" fill="#58a6ff" stroke="#30363d"/>
                    <line x1="10" y1="10" x2="22" y2="10" stroke="#161b22" stroke-width="1"/>
                    <line x1="10" y1="13" x2="22" y2="13" stroke="#161b22" stroke-width="1"/>
                    <line x1="10" y1="16" x2="18" y2="16" stroke="#161b22" stroke-width="1"/>
                </svg>'''
                self.wfile.write(svg_favicon.encode())
            
            elif self.path == '/api/pool_status':
                # JSON API endpoint for pool status from remote TrueNAS
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()

                results: Dict[str, Any] = {}

                # Add the sync errors count using existing error summary function
                if os.path.exists(LOG_FILE):
                    try:
                        with open(LOG_FILE, 'r', encoding='utf-8') as f:
                            lines = [line.rstrip('\n') for line in f.readlines()]
                        error_count, _ = self._generate_error_summary(lines)
                        results['sync_errors'] = error_count
                    except Exception as e:
                        logger.warning('Could not count sync errors: %s', e)
                        results['sync_errors'] = 0
                else:
                    results['sync_errors'] = 0

                if truenas_enabled():
                    results.update(TrueNASHandler().get_pool_status())
                else:
                    results['truenas'] = 'disabled'
                self.wfile.write(json.dumps(results).encode())
             
            else:
                self.send_error(404, 'Not found')
        except Exception as e:
            logger.error(f'Error in GET request: {e}')
            self.send_error(500, f'Internal server error: {str(e)}')

    def do_POST(self) -> None:
        try:
            # Security: Validate content type and content length
            content_type = self.headers.get('Content-Type', '')
            content_length = self.headers.get('Content-Length')
            
            # Limit request size to prevent DoS
            if content_length and int(content_length) > 1024:  # 1KB limit
                self.send_error(413, 'Request entity too large')
                return
                
            # Read and validate request body if present
            request_data = None
            if content_length:
                try:
                    body = self.rfile.read(int(content_length))
                    if content_type.startswith('application/json') and body:
                        request_data = json.loads(body.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
                    logger.warning(f'Invalid request data: {e}')
                    self.send_error(400, 'Invalid request data')
                    return
            
            # Add security headers to all responses
            def send_secure_response(status_code: int, 
                                   content_type: str = 'application/json') -> None:
                self.send_response(status_code)
                self.send_header('Content-type', content_type)
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header(
                    'Cache-Control', 'no-cache, no-store, must-revalidate'
                )
                self.end_headers()
            
            if self.path == '/clear':
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                if os.path.exists(LOG_FILE):
                    with open(LOG_FILE, 'w') as f:
                        f.write(f'[{timestamp}] [INFO] Logs cleared via web interface\n')
                
                if os.path.exists(SERVER_LOG_FILE):
                    with open(SERVER_LOG_FILE, 'w') as f:
                        f.write(f'[{timestamp}] [INFO] Server logs cleared via web interface\n')
                
                send_secure_response(200)
                self.wfile.write(json.dumps({'status': 'success'}).encode())
                logger.info('Logs cleared via web interface')
                
            elif self.path == '/run':
                try:
                    # Prepare environment for subprocess; allow delete flag from request JSON
                    proc_env = os.environ.copy()
                    if request_data and isinstance(request_data, dict) and request_data.get('delete'):
                        proc_env['RSYNC_DELETE'] = '1'
                        logger.info('Starting sync with RSYNC_DELETE enabled via web interface')

                    process = subprocess.Popen(
                        ['/bin/bash', '/src/sync_script.sh'],
                        stderr=subprocess.STDOUT,
                        cwd='/',
                        env=proc_env,
                        start_new_session=True
                    )
                    send_secure_response(200)
                    self.wfile.write(
                        json.dumps(
                            {'status': 'started', 'pid': process.pid}
                        ).encode()
                    )
                    logger.info(
                        f'Sync script started via web interface with PID {process.pid}'
                    )
                    
                except (OSError, subprocess.SubprocessError) as e:
                    logger.error(f'Failed to start sync script: {e}')
                    send_secure_response(500)
                    self.wfile.write(
                        json.dumps(
                            {'status': 'error', 'message': 'Failed to start sync process'}
                        ).encode()
                    )
                except Exception as e:
                    logger.error(f'Unexpected error starting sync: {e}')
                    send_secure_response(500)
                    self.wfile.write(
                        json.dumps(
                            {'status': 'error', 'message': 'Internal server error'}
                        ).encode()
                    )
                    
            else:
                self.send_error(404, 'Endpoint not found')
                
        except Exception as e:
            logger.error(f'Error in POST request: {e}')
            try:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {'status': 'error', 'message': 'Internal server error'}
                    ).encode()
                )
            except Exception:
                # If we can't send a proper error response, just close the connection
                pass

def signal_handler(sig: int, frame: Any) -> None:
    logger.info('Received shutdown signal')
    sys.exit(0)

if __name__ == '__main__':
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        # Ensure log directory exists
        os.makedirs(os.path.dirname(SERVER_LOG_FILE), exist_ok=True)

        # Try to bind to port 80, fall back to 8080 if permission denied
        try:
            httpd = socketserver.TCPServer(('', PORT), EnhancedLogHandler)
        except PermissionError:
            logger.warning(
                f'Permission denied for port {PORT}, trying port 8080'
            )
            PORT = 8080
            httpd = socketserver.TCPServer(('', PORT), EnhancedLogHandler)
        
        # Probe TrueNAS off the main thread so an unreachable NAS does not delay serving
        if truenas_enabled():
            threading.Thread(target=TrueNASHandler().get_pool_status, daemon=True).start()

        with httpd:
            logger.info(f'Enhanced web server running on port {PORT}')
            httpd.serve_forever()
    
    except Exception as e:
        logger.error(f'Failed to start web server: {e}')
        sys.exit(1)