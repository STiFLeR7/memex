from memex.watcher.health import record, read_health


def test_health_roundtrip_accumulates(tmp_path):
    repo = str(tmp_path)
    assert read_health(repo) == {}

    record(repo, handler="handle_file_change", errors=1)
    record(repo, handler="handle_file_change", errors=2)
    record(repo, episodes_skipped=5)
    record(repo, indexed_ok=True)

    h = read_health(repo)
    assert h["handler_errors"] == 3
    assert h["handler_errors_by"]["handle_file_change"] == 3
    assert h["episodes_skipped"] == 5
    assert "last_error_at" in h
    assert "last_episode_skip_at" in h
    assert "last_indexed_at" in h


def test_health_record_noop_on_empty_repo():
    # Must not raise, must not write anywhere.
    record(None, errors=1)
    record("", errors=1)
    assert read_health(None) == {}


def test_health_read_missing_returns_empty(tmp_path):
    assert read_health(str(tmp_path / "does_not_exist")) == {}
