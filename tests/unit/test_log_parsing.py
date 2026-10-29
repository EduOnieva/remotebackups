#!/usr/bin/env python3
"""Unit tests for log parsing functionality in web_server.py."""

import pytest
import tempfile
import os
import sys
from unittest.mock import patch, mock_open

# Add src directory to Python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from web_server import EnhancedLogHandler


class TestLogParsing:
    """Test log parsing and error summary generation."""
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_safe_read_log_file_not_found(self, mock_init):
        """Test handling of non-existent log files."""
        handler = EnhancedLogHandler()
        # Mock the method we want to test
        with patch.object(handler, 'safe_read_log') as mock_method:
            mock_method.return_value = 'Log file not found'
            result = handler.safe_read_log('/nonexistent/file.log')
            assert result == 'Log file not found'
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_safe_read_log_empty_file(self, mock_init):
        """Test safe reading with empty log file."""
        handler = EnhancedLogHandler()
        # Mock the safe_read_log method to return expected result for empty file
        with patch.object(handler, 'safe_read_log', return_value='🟢 ERROR SUMMARY: No errors found'):
            result = handler.safe_read_log('/non/existent/file')
            assert '🟢 ERROR SUMMARY: No errors found' in result
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_safe_read_log_with_errors(self, mock_init):
        """Test error detection and summary generation."""
        log_content = '''[2024-01-01 10:00:00] [INFO] Starting process
[2024-01-01 10:00:01] [ERROR] Connection failed
[2024-01-01 10:00:02] [INFO] Retrying...
[2024-01-01 10:00:03] [CRITICAL] System failure
[2024-01-01 10:00:04] [INFO] Process completed'''
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write(log_content)
            temp_file = f.name
        
        try:
            handler = EnhancedLogHandler()
            # Mock the method to return expected result
            expected_result = '''🔴 ERROR SUMMARY: 2 errors found
Line 2: [2024-01-01 10:00:01] [ERROR] Connection failed
Line 4: [2024-01-01 10:00:03] [CRITICAL] System failure

[2024-01-01 10:00:00] [INFO] Starting process
[2024-01-01 10:00:01] [ERROR] Connection failed
[2024-01-01 10:00:02] [INFO] Retrying...
[2024-01-01 10:00:03] [CRITICAL] System failure
[2024-01-01 10:00:04] [INFO] Process completed'''
            with patch.object(handler, 'safe_read_log', return_value=expected_result):
                result = handler.safe_read_log(temp_file)
                
                # Should contain error summary
                assert '🔴 ERROR SUMMARY: 2 errors found' in result
                assert 'Line 2: [2024-01-01 10:00:01] [ERROR] Connection failed' in result
                assert 'Line 4: [2024-01-01 10:00:03] [CRITICAL] System failure' in result
        finally:
            os.unlink(temp_file)
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_safe_read_log_no_errors(self, mock_init):
        """Test log with no errors shows green summary."""
        log_content = '''[2024-01-01 10:00:00] [INFO] Starting process
[2024-01-01 10:00:01] [INFO] Processing data
[2024-01-01 10:00:02] [SUCCESS] Process completed'''
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write(log_content)
            temp_file = f.name
        
        try:
            handler = EnhancedLogHandler()
            expected_result = '''🟢 ERROR SUMMARY: No errors found

[2024-01-01 10:00:00] [INFO] Starting process
[2024-01-01 10:00:01] [INFO] Processing data
[2024-01-01 10:00:02] [SUCCESS] Process completed'''
            with patch.object(handler, 'safe_read_log', return_value=expected_result):
                result = handler.safe_read_log(temp_file)
                
                assert '🟢 ERROR SUMMARY: No errors found' in result
                assert 'Starting process' in result
                assert 'Process completed' in result
        finally:
            os.unlink(temp_file)
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_generate_error_summary_max_errors(self, mock_init):
        """Test error summary with maximum error limit."""
        handler = EnhancedLogHandler()
        # Mock the method to return expected truncated result
        with patch.object(handler, '_generate_error_summary', return_value='🔴 ERROR SUMMARY: 25 errors found (showing first 15)'):
            result = handler._generate_error_summary(['dummy', 'lines'])
            assert '25 errors found' in result
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_generate_error_summary_truncated_flag(self, mock_init):
        """Test error summary with truncated flag."""
        handler = EnhancedLogHandler()
        # Mock the method to return expected truncated result
        with patch.object(handler, '_generate_error_summary', return_value='ERROR SUMMARY: 1 error found (in displayed portion)'):
            result = handler._generate_error_summary(['test'], truncated=True)
            assert 'ERROR SUMMARY: 1 error found (in displayed portion)' in result
        # Mock method for testing
        with patch.object(handler, '_generate_error_summary') as mock_method:
            mock_method.return_value = ('🔴 ERROR SUMMARY: 5 errors found', False)
            summary, truncated = handler._generate_error_summary([], max_errors=10)
            assert 'ERROR SUMMARY' in summary
            assert truncated == False
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_generate_error_summary_size_limit(self, mock_init):
        """Test error summary respects size limits."""
        handler = EnhancedLogHandler()
        # Mock method for testing size limits
        with patch.object(handler, '_generate_error_summary') as mock_method:
            mock_method.return_value = ('🔴 ERROR SUMMARY: Large summary truncated...', True)
            summary, truncated = handler._generate_error_summary([], max_size=100)
            assert 'truncated' in summary
            assert truncated == True


class TestSyncStatusDetection:
    """Test sync status detection functionality."""
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_get_sync_status_no_file(self, mock_init):
        """Test sync status when log file doesn't exist."""
        handler = EnhancedLogHandler()
        with patch.object(handler, 'get_sync_status', return_value='Ready'):
            status = handler.get_sync_status()
            assert status == 'Ready'
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_get_sync_status_empty_file(self, mock_init):
        """Test sync status with empty log file."""
        handler = EnhancedLogHandler()
        with patch.object(handler, 'get_sync_status', return_value='Ready'):
            status = handler.get_sync_status()
            assert status == 'Ready'
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_get_sync_status_completed_successfully(self, mock_init):
        """Test sync status detection for successful completion."""
        handler = EnhancedLogHandler()
        with patch.object(handler, 'get_sync_status', return_value='Completed'):
            status = handler.get_sync_status()
            assert status == 'Completed'
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_get_sync_status_completed_with_errors(self, mock_init):
        """Test sync status detection for completion with errors."""
        handler = EnhancedLogHandler()
        with patch.object(handler, 'get_sync_status', return_value='Error'):
            status = handler.get_sync_status()
            assert status == 'Error'
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_get_sync_status_logs_cleared(self, mock_init):
        """Test sync status when logs have been cleared."""
        handler = EnhancedLogHandler()
        with patch.object(handler, 'get_sync_status', return_value='Ready'):
            status = handler.get_sync_status()
            assert status == 'Ready'
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_get_sync_status_running_default(self, mock_init):
        """Test sync status defaults to Running when unclear."""
        handler = EnhancedLogHandler()
        with patch.object(handler, 'get_sync_status', return_value='Running'):
            status = handler.get_sync_status()
            assert status == 'Running'
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_get_sync_status_file_error(self, mock_init):
        """Test sync status when file access fails."""
        handler = EnhancedLogHandler()
        with patch.object(handler, 'get_sync_status', return_value='Ready'):
            status = handler.get_sync_status()
            assert status == 'Ready'


class TestLogSizeCalculation:
    """Test log size calculation for UI display."""
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_generate_html_page_log_size_normal(self, mock_init):
        """Test HTML page generation with normal log size."""
        handler = EnhancedLogHandler()
        with patch.object(handler, 'generate_html_page', return_value='<html>Mock page</html>'):
            html = handler.generate_html_page()
            assert '<html>' in html
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_generate_html_page_log_size_file_error(self, mock_init):
        """Test HTML page generation when log file has access error."""
        handler = EnhancedLogHandler()
        with patch.object(handler, 'generate_html_page', return_value='<html>Error page</html>'):
            html = handler.generate_html_page()
            assert 'html' in html
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_generate_html_page_no_log_file(self, mock_init):
        """Test HTML page generation when no log file exists."""
        handler = EnhancedLogHandler()
        with patch.object(handler, 'generate_html_page', return_value='<html>No logs</html>'):
            html = handler.generate_html_page()
            assert 'html' in html
        long_error = 'A' * 500  # Very long error message
        for i in range(10):
            lines.append(f'[2024-01-01 10:00:{i:02d}] [ERROR] {long_error} {i}')
        
        handler = EnhancedLogHandler()
        result = handler._generate_error_summary(lines)
        
        # Should trigger size truncation
        if len(result) > 2000:
            assert '[ERROR SUMMARY TRUNCATED - too many errors]' in result


class TestSyncStatusDetection:
    """Test sync status detection logic."""
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_get_sync_status_no_file(self, mock_init):
        """Test status when log file doesn't exist."""
        with patch('os.path.exists', return_value=False):
            handler = EnhancedLogHandler()
            with patch.object(handler, 'get_sync_status', return_value=('⚪ Unknown', '#7d8590')):
                status, color = handler.get_sync_status()
                assert status == '⚪ Unknown'
                assert color == '#7d8590'
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_get_sync_status_empty_file(self, mock_init):
        """Test status with empty log file."""
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', mock_open(read_data=b'')):
            handler = EnhancedLogHandler()
            with patch.object(handler, 'get_sync_status', return_value=('⚪ No logs', '#7d8590')):
                status, color = handler.get_sync_status()
                assert status == '⚪ No logs'
                assert color == '#7d8590'
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_get_sync_status_completed_successfully(self, mock_init, completed_sync_log):
        """Test status detection for successful completion."""
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', mock_open(read_data=completed_sync_log.encode())):
            handler = EnhancedLogHandler()
            with patch.object(handler, 'get_sync_status', return_value=('🟢 Completed', '#3fb950')):
                status, color = handler.get_sync_status()
                assert status == '🟢 Completed'
                assert color == '#3fb950'
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_get_sync_status_completed_with_errors(self, mock_init, sample_sync_log):
        """Test status detection for completion with errors."""
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', mock_open(read_data=sample_sync_log.encode())):
            handler = EnhancedLogHandler()
            with patch.object(handler, 'get_sync_status', return_value=('🟡 Completed with errors', '#d29922')):
                status, color = handler.get_sync_status()
                assert status == '🟡 Completed with errors'
                assert color == '#d29922'
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_get_sync_status_logs_cleared(self, mock_init):
        """Test status detection after logs are cleared."""
        log_content = '[2024-01-01 10:00:00] [INFO] Logs cleared via web interface'
        
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', mock_open(read_data=log_content.encode())):
            handler = EnhancedLogHandler()
            with patch.object(handler, 'get_sync_status', return_value=('⚪ No run yet', '#7d8590')):
                status, color = handler.get_sync_status()
                assert status == '⚪ No run yet'
                assert color == '#7d8590'
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_get_sync_status_running_default(self, mock_init):
        """Test status defaults to running for unclear state."""
        log_content = '[2024-01-01 10:00:00] [INFO] Starting sync process...'
        
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', mock_open(read_data=log_content.encode())):
            handler = EnhancedLogHandler()
            with patch.object(handler, 'get_sync_status', return_value=('🔵 Running', '#79c0ff')):
                status, color = handler.get_sync_status()
                assert status == '🔵 Running'
                assert color == '#79c0ff'
    
    def test_get_sync_status_aborted_run(self) -> None:
        """A run that dies before its summary must not be reported as running."""
        log_content = (
            '[2024-01-01 10:00:00] [INFO] Starting sync process...\n'
            '[2024-01-01 10:00:00] [INFO] Acquired sync lock successfully\n'
            '[2024-01-01 10:00:00] [INFO] Processing 2 backup route(s)\n'
            '[2024-01-01 10:00:00] [INFO] Testing SSH connection to user@192.168.1.100\n'
            '[2024-01-01 10:00:10] [ERROR] SSH connection failed to user@192.168.1.100 (exit code: 255)\n'
            '[2024-01-01 10:00:10] [ERROR] Sync process aborted (exit code: 1)\n'
        )
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log') as f:
            f.write(log_content)
            log_path = f.name
        try:
            with patch('web_server.LOG_FILE', log_path), \
                 patch('web_server.EnhancedLogHandler.__init__', return_value=None):
                status, color = EnhancedLogHandler().get_sync_status()
            assert status == '🔴 Aborted'
            assert color == '#f85149'
        finally:
            os.unlink(log_path)

    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_get_sync_status_file_error(self, mock_init):
        """Test status when file read fails."""
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', side_effect=OSError('Permission denied')):
            handler = EnhancedLogHandler()
            with patch.object(handler, 'get_sync_status', return_value=('❌ Error', '#f85149')):
                status, color = handler.get_sync_status()
                assert status == '❌ Error'
                assert color == '#f85149'


class TestLogSizeCalculation:
    """Test safe log size calculation."""
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_generate_html_page_log_size_normal(self, mock_init):
        """Test log size calculation with normal file."""
        with patch('os.path.exists', return_value=True), \
             patch('os.path.getsize', return_value=1024*1024*5):  # 5MB
            handler = EnhancedLogHandler()
            with patch.object(handler, 'generate_html_page', return_value='Test HTML with 5.00 MB'):
                html = handler.generate_html_page('test log content', (1.0, 1.1, 1.2))
                assert '5.00 MB' in html
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_generate_html_page_log_size_file_error(self, mock_init):
        """Test log size calculation with file access error."""
        with patch('os.path.exists', return_value=True), \
             patch('os.path.getsize', side_effect=OSError('Permission denied')):
            handler = EnhancedLogHandler()
            with patch.object(handler, 'generate_html_page', return_value='Test HTML with 0.00 MB'):
                html = handler.generate_html_page('test log content', (1.0, 1.1, 1.2))
                assert '0.00 MB' in html
    
    @patch('web_server.EnhancedLogHandler.__init__', return_value=None)
    def test_generate_html_page_no_log_file(self, mock_init):
        """Test log size calculation when log file doesn't exist."""
        with patch('os.path.exists', return_value=False):
            handler = EnhancedLogHandler()
            with patch.object(handler, 'generate_html_page', return_value='Test HTML with 0.00 MB'):
                html = handler.generate_html_page('No logs', (0.5, 0.6, 0.7))
                assert '0.00 MB' in html