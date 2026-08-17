"""
Tests for path resolution across the two ways this ships.

paths.py is the reason the app can find its config and its HTML, and getting
it wrong fails in a way that is invisible off a Mac: the frozen branch is
never exercised by running from source. So the frozen branch is faked here
rather than left to be discovered inside a bundle.

Stdlib-only, like server.py — this runs anywhere.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import paths  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """A real MACROPAD_CONFIG in the shell must not leak into these."""
    monkeypatch.delenv("MACROPAD_CONFIG", raising=False)
    monkeypatch.delenv("RESOURCEPATH", raising=False)


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Pose as a launched py2app bundle."""
    resources = tmp_path / "Contents" / "Resources"
    (resources / "ui").mkdir(parents=True)
    (resources / "config").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(sys, "frozen", "macosx_app", raising=False)
    monkeypatch.setenv("RESOURCEPATH", str(resources))
    monkeypatch.setenv("HOME", str(home))
    return resources


# ------------------------------------------------------------- from source --

def test_not_frozen_when_running_from_source():
    assert paths.is_frozen() is False


def test_source_config_is_the_repo_file():
    assert paths.config_path() == ROOT / "config" / "profiles.json"
    assert paths.config_path().exists(), "the repo config should be findable"


def test_source_ui_dir_holds_the_editor():
    assert paths.ui_dir() == ROOT / "agent" / "ui"
    assert (paths.ui_dir() / "index.html").exists()


def test_source_bundled_config_is_the_live_config():
    """From source there is only one file, so editing in place still works."""
    assert paths.bundled_config() == paths.config_path()


# ------------------------------------------------------------------ frozen --

def test_frozen_reads_resources_from_resourcepath(frozen):
    assert paths.is_frozen() is True
    assert paths.ui_dir() == frozen / "ui"
    assert paths.bundled_config() == frozen / "config" / "profiles.json"


def test_frozen_config_moves_out_of_the_read_only_bundle(frozen):
    """Contents/Resources is read-only, and the editor PUTs to this file."""
    resolved = paths.config_path()
    assert resolved == paths.support_dir() / "profiles.json"
    assert frozen not in resolved.parents


def test_frozen_falls_back_to_the_executable_when_resourcepath_is_unset(
        monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", "macosx_app", raising=False)
    monkeypatch.delenv("RESOURCEPATH", raising=False)
    exe = tmp_path / "MacroPad.app" / "Contents" / "MacOS" / "MacroPad"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(sys, "executable", str(exe))
    assert paths.resource_dir() == exe.parent.parent / "Resources"


# --------------------------------------------------------------- overrides --

def test_env_override_wins_from_source(monkeypatch, tmp_path):
    monkeypatch.setenv("MACROPAD_CONFIG", str(tmp_path / "mine.json"))
    assert paths.config_path() == tmp_path / "mine.json"


def test_env_override_wins_when_frozen(monkeypatch, tmp_path, frozen):
    monkeypatch.setenv("MACROPAD_CONFIG", str(tmp_path / "mine.json"))
    assert paths.config_path() == tmp_path / "mine.json"


def test_env_override_expands_a_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MACROPAD_CONFIG", "~/elsewhere.json")
    assert paths.config_path() == tmp_path / "elsewhere.json"


# -------------------------------------------------------------------- seed --

def test_seed_creates_the_config_and_its_directory(tmp_path):
    src = tmp_path / "bundled.json"
    src.write_text('{"defaultProfile": "global"}')
    dst = tmp_path / "support" / "profiles.json"

    assert paths.seed_config(dst, src) == dst
    assert dst.read_text() == '{"defaultProfile": "global"}'


def test_seed_never_clobbers_an_existing_config(tmp_path):
    """Reinstalling the app is not consent to lose your layout."""
    src = tmp_path / "bundled.json"
    src.write_text('{"shipped": true}')
    dst = tmp_path / "profiles.json"
    dst.write_text('{"mine": true}')

    paths.seed_config(dst, src)
    assert dst.read_text() == '{"mine": true}'


def test_seed_tolerates_a_missing_source(tmp_path):
    """A build with no bundled config should not crash on first launch."""
    dst = tmp_path / "support" / "profiles.json"
    paths.seed_config(dst, tmp_path / "absent.json")
    assert not dst.exists()
