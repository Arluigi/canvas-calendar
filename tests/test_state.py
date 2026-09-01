from canvas_calendar.state import StateStore


def test_roundtrips_a_record(tmp_path):
    s = StateStore(tmp_path / "s.db")
    s.upsert("cc-1", due_at="2026-08-25T19:00:00Z", title_hash="abc", source="canvas")
    rec = s.get("cc-1")
    assert rec.title_hash == "abc"
    assert rec.source == "canvas"


def test_missing_uid_returns_none(tmp_path):
    assert StateStore(tmp_path / "s.db").get("cc-nope") is None


def test_upsert_overwrites(tmp_path):
    s = StateStore(tmp_path / "s.db")
    s.upsert("cc-1", due_at="a", title_hash="h1", source="canvas")
    s.upsert("cc-1", due_at="b", title_hash="h2", source="extracted")
    assert s.get("cc-1").title_hash == "h2"
    assert s.all_uids() == {"cc-1"}


def test_delete_removes(tmp_path):
    s = StateStore(tmp_path / "s.db")
    s.upsert("cc-1", due_at="a", title_hash="h", source="canvas")
    s.delete("cc-1")
    assert s.get("cc-1") is None


def test_survives_reopen(tmp_path):
    p = tmp_path / "s.db"
    StateStore(p).upsert("cc-1", due_at="a", title_hash="h", source="canvas")
    assert StateStore(p).get("cc-1") is not None


def test_null_due_at_is_allowed(tmp_path):
    s = StateStore(tmp_path / "s.db")
    s.upsert("cc-1", due_at=None, title_hash="h", source="unresolved")
    assert s.get("cc-1").due_at is None


def test_creates_parent_directory(tmp_path):
    s = StateStore(tmp_path / "nested" / "deep" / "s.db")
    s.upsert("cc-1", due_at="a", title_hash="h", source="canvas")
    assert s.get("cc-1") is not None
