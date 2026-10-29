from remotebackups.sync.stats import parse_stats

# Captured from rsync 3.4 with --stats; thousands separators included
RSYNC_34_STATS = """
Number of files: 1,234 (reg: 1,000, dir: 234)
Number of created files: 5 (reg: 5)
Number of deleted files: 2
Number of regular files transferred: 3
Total file size: 1,234,567 bytes
Total transferred file size: 12,345 bytes
Literal data: 12,345 bytes
Matched data: 0 bytes
File list size: 65,536
File list generation time: 0.001 seconds
File list transfer time: 0.000 seconds
Total bytes sent: 12,500
Total bytes received: 100

sent 12,500 bytes  received 100 bytes  8,400.00 bytes/sec
total size is 1,234,567  speedup is 97.97
""".splitlines()


def test_parses_the_rsync_34_block() -> None:
    stats = parse_stats(RSYNC_34_STATS)
    assert stats is not None
    assert stats.files_total == 1234
    assert stats.files_created == 5
    assert stats.files_deleted == 2
    assert stats.files_transferred == 3
    assert stats.total_size == 1_234_567
    assert stats.bytes_sent == 12_500
    assert stats.bytes_received == 100
    assert stats.speedup == 97.97


def test_old_rsync_wording_for_transferred_files() -> None:
    stats = parse_stats(["Number of files: 10", "Number of files transferred: 4"])
    assert stats is not None
    assert stats.files_transferred == 4


def test_no_stats_block_means_none() -> None:
    assert parse_stats(["sending incremental file list", "a.txt", "b.txt"]) is None


def test_file_lines_that_look_similar_are_ignored() -> None:
    stats = parse_stats(["Number of files: not a stat line/", "Number of files: 7"])
    assert stats is not None
    assert stats.files_total == 7
