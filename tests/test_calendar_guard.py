import pytest

from canvas_calendar.calendars.base import UID_PREFIX, ForeignEventError, assert_ours


def test_accepts_our_uids():
    assert_ours("cc-1682585")
    assert_ours("cc-mi-5440597")


@pytest.mark.parametrize(
    "uid",
    [
        "",
        "AAMkAGI2-outlook-native-id",
        "cc",
        "cc-",
        "ccx-1",
        "1682585",
        "google-event-abc",
        "CC-1",  # case must match exactly
        " cc-1",  # leading whitespace is not ours
    ],
)
def test_rejects_foreign_uids(uid):
    """Anything we did not create must be untouchable. This guard is what
    stands between a bug in this tool and someone's real calendar."""
    with pytest.raises(ForeignEventError):
        assert_ours(uid)


def test_rejects_none():
    with pytest.raises(ForeignEventError):
        assert_ours(None)


def test_prefix_is_the_documented_one():
    """Changing this orphans every event already on the calendar."""
    assert UID_PREFIX == "cc-"
