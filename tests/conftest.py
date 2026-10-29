#!/usr/bin/env python3
"""Pytest configuration and shared fixtures for rsync-tailscale-docker tests."""

import json
import os
import tempfile
from typing import Any, Dict, Generator
from unittest.mock import Mock, patch

import pytest

# Resolved through pythonpath=src (pytest.ini) or PYTHONPATH=/src (test image), never as
# the `src` package: importing both ways loads the module twice and patches miss one copy.
from web_server import EnhancedLogHandler


@pytest.fixture
def mock_sync_environment():
    """Setup mock environment for sync testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create source directories
        source_dirs = ['data1', 'data2', 'docs']
        for dir_name in source_dirs:
            os.makedirs(os.path.join(temp_dir, dir_name))
            # Add some test files
            test_file = os.path.join(temp_dir, dir_name, 'test.txt')
            with open(test_file, 'w') as f:
                f.write(f'Test content for {dir_name}')
        
        # Create routes file
        routes = {
            f'{temp_dir}/data1': '/remote/backup/data1',
            f'{temp_dir}/data2': '/remote/backup/data2',
            f'{temp_dir}/docs': '/remote/backup/docs'
        }
        routes_file = os.path.join(temp_dir, 'routes.json')
        with open(routes_file, 'w') as f:
            json.dump(routes, f)
        
        # Create logs directory
        logs_dir = os.path.join(temp_dir, 'logs')
        os.makedirs(logs_dir)
        
        yield {
            'temp_dir': temp_dir,
            'source_dirs': source_dirs,
            'routes_file': routes_file,
            'routes': routes,
            'logs_dir': logs_dir,
            'ssh_key': os.path.join(temp_dir, 'test_key'),
            'remote_host': '192.168.1.100',
            'remote_user': 'testuser'
        }


@pytest.fixture
def temp_log_file() -> Generator[str, None, None]:
    """Create a temporary log file for testing."""
    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as f:
        temp_path = f.name
        f.write('[2024-01-01 12:00:00] [INFO] Test log entry\n')
        f.write('[2024-01-01 12:01:00] [ERROR] Test error entry\n')
        f.write('[2024-01-01 12:02:00] [INFO] Starting sync process...\n')
    
    yield temp_path
    
    # Cleanup
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.fixture
def sample_routes_json() -> Dict[str, str]:
    """Sample routes configuration for testing."""
    return {
        '/data/test1': '/remote/backup1',
        '/data/test2': '/remote/backup2',
        '/data/docs': '/remote/documents'
    }


@pytest.fixture
def mock_log_handler():
    """Mock log handler for testing web interface."""
    with patch('web_server.LOG_FILE', '/tmp/test_sync.log'), \
         patch('web_server.SERVER_LOG_FILE', '/tmp/test_server.log'):
        handler = EnhancedLogHandler()
        handler.setup()
        yield handler


@pytest.fixture
def mock_subprocess():
    """Mock subprocess for testing process execution."""
    with patch('subprocess.Popen') as mock_popen:
        mock_process = Mock()
        mock_process.pid = 12345
        mock_process.returncode = 0
        mock_popen.return_value = mock_process
        yield mock_popen


@pytest.fixture
def sample_sync_log() -> str:
    """Sample sync log content for testing."""
    return '''[2024-01-01 10:00:00] [INFO] Starting sync process...
[2024-01-01 10:00:01] [INFO] Processing 3 backup route(s)
[2024-01-01 10:00:02] [INFO] Testing SSH connection to user@192.168.1.100
[2024-01-01 10:00:03] [INFO] SSH connection successful
[2024-01-01 10:00:04] [INFO] Starting sync: /data/test1 -> /remote/backup1
[2024-01-01 10:00:10] [SUCCESS] Sync completed: /data/test1 -> /remote/backup1 (6s)
[2024-01-01 10:00:11] [INFO] Starting sync: /data/test2 -> /remote/backup2
[2024-01-01 10:00:15] [ERROR] Sync failed: /data/test2 -> /remote/backup2 (exit code: 1, duration: 4s)
[2024-01-01 10:00:16] [INFO] Sync process completed - Success: 1, Failures: 1, Total Duration: 16s
[2024-01-01 10:00:17] [WARN] Some syncs failed. Check logs for details.'''


@pytest.fixture
def completed_sync_log() -> str:
    """Sample completed sync log for status testing."""
    return '''[2024-01-01 10:00:00] [INFO] Starting sync process...
[2024-01-01 10:00:01] [INFO] Processing 2 backup route(s)
[2024-01-01 10:00:02] [INFO] Testing SSH connection to user@192.168.1.100
[2024-01-01 10:00:03] [INFO] SSH connection successful
[2024-01-01 10:00:04] [INFO] Starting sync: /data/test1 -> /remote/backup1
[2024-01-01 10:00:10] [SUCCESS] Sync completed: /data/test1 -> /remote/backup1 (6s)
[2024-01-01 10:00:11] [INFO] Starting sync: /data/test2 -> /remote/backup2
[2024-01-01 10:00:15] [SUCCESS] Sync completed: /data/test2 -> /remote/backup2 (4s)
[2024-01-01 10:00:16] [INFO] Sync process completed - Success: 2, Failures: 0, Total Duration: 16s
[2024-01-01 10:00:17] [SUCCESS] All syncs completed successfully'''


@pytest.fixture(autouse=True)
def reset_environment():
    """Reset environment variables before each test."""
    original_env = os.environ.copy()
    yield
    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def mock_ssh_environment() -> Dict[str, str]:
    """Mock SSH environment variables for testing."""
    return {
        'REMOTE_USER': 'testuser',
        'REMOTE_HOST': '192.168.1.100',
        'ROUTES_FILE': '/test/routes.json',
        'SSH_KEY_FILE': 'test_key'
    }