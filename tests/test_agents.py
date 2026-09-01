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


def test_plist_templates_ship_inside_the_package():
    """They lived in deploy/, which the wheel does not include -- so a
    `uv tool install` produced a tool whose install-agents crashed."""
    from canvas_calendar.agents import _templates_dir

    names = {p.name for p in _templates_dir().glob("*.plist.template")}
    assert names == {"canvas-calendar.plist.template", "canvas-debrief.plist.template"}


def test_rendered_plists_have_no_placeholders_left():
    from canvas_calendar.agents import _templates_dir

    for tmpl in _templates_dir().glob("*.plist.template"):
        out = render_plist(tmpl.read_text(), binary="/x/bin/cc",
                           label=LABEL_SYNC, home="/Users/x")
        assert "{{" not in out, f"{tmpl.name} kept a placeholder"
