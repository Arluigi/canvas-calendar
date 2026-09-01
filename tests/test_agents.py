import pytest

from canvas_calendar.agents import (
    LABEL_DEBRIEF,
    LABEL_SYNC,
    DevelopmentCheckout,
    render_plist,
    resolve_binary,
)

TEMPLATE = """<plist><dict>
<key>Label</key><string>{{LABEL}}</string>
<key>ProgramArguments</key><array><string>{{BINARY}}</string><string>daily</string></array>
<key>StandardOutPath</key><string>{{HOME}}/.config/canvas-calendar/out.log</string>
</dict></plist>"""


def test_render_substitutes_every_placeholder():
    out = render_plist(TEMPLATE, binary="/opt/bin/cc", label=LABEL_SYNC, home="/Users/x")
    assert "/opt/bin/cc" in out
    assert LABEL_SYNC in out
    assert "/Users/x/.config" in out
    assert "{{" not in out, "an unsubstituted placeholder survived"


def test_labels_are_not_user_specific():
    """com.aryan.* was baked into the old templates."""
    for label in (LABEL_SYNC, LABEL_DEBRIEF):
        assert "aryan" not in label.lower()
        assert label.startswith("io.github.canvas-calendar")


def test_labels_are_distinct():
    assert LABEL_SYNC != LABEL_DEBRIEF


@pytest.mark.parametrize(
    "path",
    [
        "/Users/x/code/canvas-calendar/.venv/bin/canvas-calendar",
        "/Users/x/proj/src/canvas_calendar/cli.py",
    ],
)
def test_resolve_binary_refuses_a_development_checkout(path, monkeypatch):
    """The branch checked out would otherwise decide what runs at 07:15."""
    monkeypatch.setattr("shutil.which", lambda _: path)
    with pytest.raises(DevelopmentCheckout, match="uv tool install"):
        resolve_binary()


def test_resolve_binary_accepts_an_installed_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/Users/x/.local/bin/canvas-calendar")
    assert resolve_binary().endswith("canvas-calendar")
