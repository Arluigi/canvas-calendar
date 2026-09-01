"""Credential resolution order.

The legacy canvas-mcp dotenv path exists on exactly one machine, so it must
stay last rather than remain the only source. Fixture files are deliberately
not named ``*.env``; ``dotenv_values`` does not care about the extension.
"""

import json

import pytest

from canvas_calendar import config


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch):
    """The real token is in this process's environment during a live run."""
    monkeypatch.delenv("CANVAS_API_TOKEN", raising=False)
    monkeypatch.delenv("CANVAS_API_URL", raising=False)


def _legacy(tmp_path, body: str):
    p = tmp_path / "legacy_dotenv.txt"
    p.write_text(body)
    return p


def test_environment_wins_over_everything(tmp_path, monkeypatch):
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"CANVAS_API_TOKEN": "from-file"}))
    monkeypatch.setattr(config, "CREDENTIALS", creds)
    monkeypatch.setenv("CANVAS_API_TOKEN", "from-env")

    _, token = config.load_canvas_credentials(env_path=tmp_path / "absent.txt")
    assert token == "from-env"


def test_credentials_file_wins_over_legacy(tmp_path, monkeypatch):
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"CANVAS_API_TOKEN": "from-file"}))
    monkeypatch.setattr(config, "CREDENTIALS", creds)

    _, token = config.load_canvas_credentials(
        env_path=_legacy(tmp_path, "CANVAS_API_TOKEN=from-legacy\n")
    )
    assert token == "from-file"


def test_legacy_still_works_when_nothing_else_is_set(tmp_path, monkeypatch):
    """The author's machine relies on this path; it must not regress."""
    monkeypatch.setattr(config, "CREDENTIALS", tmp_path / "absent.json")

    url, token = config.load_canvas_credentials(
        env_path=_legacy(
            tmp_path, "CANVAS_API_TOKEN=from-legacy\nCANVAS_API_URL=https://x.edu\n"
        )
    )
    assert token == "from-legacy"
    assert url == "https://x.edu/api/v1"


def test_api_v1_suffix_is_added_exactly_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CREDENTIALS", tmp_path / "absent.json")
    url, _ = config.load_canvas_credentials(
        env_path=_legacy(
            tmp_path, "CANVAS_API_TOKEN=t\nCANVAS_API_URL=https://x.edu/api/v1\n"
        )
    )
    assert url == "https://x.edu/api/v1"


def test_credentials_file_supplies_the_url_too(tmp_path, monkeypatch):
    creds = tmp_path / "credentials.json"
    creds.write_text(
        json.dumps({"CANVAS_API_TOKEN": "t", "CANVAS_API_URL": "https://y.edu"})
    )
    monkeypatch.setattr(config, "CREDENTIALS", creds)

    url, _ = config.load_canvas_credentials(env_path=tmp_path / "absent.txt")
    assert url == "https://y.edu/api/v1"


def test_missing_token_names_the_setup_command(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CREDENTIALS", tmp_path / "absent.json")
    with pytest.raises(RuntimeError, match="canvas-calendar setup"):
        config.load_canvas_credentials(env_path=tmp_path / "absent.txt")
