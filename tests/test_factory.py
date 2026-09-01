import pytest

from canvas_calendar.calendars.factory import UnknownBackend, make_adapter


class FakeAdapter:
    def __init__(self):
        self.asked_for = None

    def ensure_calendar(self, name):
        self.asked_for = name
        return "cal-id-123"


def test_unknown_backend_names_the_setup_command():
    with pytest.raises(UnknownBackend, match="canvas-calendar setup"):
        make_adapter({"calendar_backend": "carrier-pigeon", "assignments_calendar": "X"})


def test_missing_backend_is_an_error_not_a_default():
    """A default of outlook is wrong for new users; a default of eventkit
    would silently change an existing install. Neither is acceptable."""
    with pytest.raises(UnknownBackend, match="canvas-calendar setup"):
        make_adapter({"assignments_calendar": "X"})


def test_none_backend_is_also_rejected():
    with pytest.raises(UnknownBackend):
        make_adapter({"calendar_backend": None, "assignments_calendar": "X"})


def test_factory_resolves_the_calendar_by_configured_name(monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr("canvas_calendar.calendars.factory._build_outlook", lambda opts: fake)
    adapter, cal_id = make_adapter(
        {"calendar_backend": "outlook", "assignments_calendar": "My Calendar"}
    )
    assert adapter is fake
    assert cal_id == "cal-id-123"
    assert fake.asked_for == "My Calendar"
