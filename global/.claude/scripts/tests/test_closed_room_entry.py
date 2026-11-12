"""Tests for closed_room_entry.py (ticket 81): which folders under the home the restarted pytest run keeps visible.

Invented folders under pytest's tmp_path only. The account home and the checkout are swapped for those folders, so nothing here looks at
the real ones, and nothing is started: the tests read the command line _plan() would hand to closed_room.sh.
"""
import os
import sys

import pytest

import closed_room_entry as entry

PRIVATE = (".claude", ".config", ".ssh", ".gnupg")


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    top = tmp_path / "checkout"
    home.mkdir()
    top.mkdir()
    monkeypatch.setattr(entry, "_account_home", lambda: os.path.realpath(home))
    monkeypatch.setattr(entry, "_checkout_top", lambda: os.path.realpath(top))
    monkeypatch.setattr(sys, "path", [])
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("HOME", os.path.realpath(home))
    return home


def _kept(cmd):
    """The folders a plan keeps visible: every word that follows --ro, up to the -- that starts the command."""
    words = cmd[1:cmd.index("--")]
    assert words[0::2] == ["--ro"] * (len(words) // 2), words
    return words[1::2]


def _folder(home, *parts):
    d = home.joinpath(*parts)
    d.mkdir(parents=True)
    return os.path.realpath(d)


def test_the_private_list_is_the_four_the_shell_guard_refuses():
    assert entry.PRIVATE == PRIVATE
    guard = os.path.join(os.path.dirname(os.path.abspath(entry.__file__)), "closed_room_guard.sh")
    with open(guard, encoding="utf-8") as fh:
        text = fh.read()
    for name in PRIVATE:
        assert '"$__cr_home/%s/"*' % name in text, name


def test_a_package_folder_under_the_home_stays_visible(home, monkeypatch):
    site = _folder(home, ".local", "lib", "site-packages")
    monkeypatch.setattr(sys, "path", [site])
    assert _kept(entry._plan(["python3", "-m", "pytest", "-q"])) == [site]


@pytest.mark.parametrize("name", PRIVATE)
def test_a_python_path_entry_in_a_private_folder_is_not_kept(home, monkeypatch, name):
    site = _folder(home, ".local", "lib", "site-packages")
    inside = _folder(home, name, "lib")
    monkeypatch.setattr(sys, "path", [inside, os.path.join(os.path.realpath(home), name), site])
    assert _kept(entry._plan(["python3", "-m", "pytest"])) == [site]


@pytest.mark.parametrize("name", PRIVATE)
def test_a_link_into_a_private_folder_on_the_python_path_is_not_kept(home, monkeypatch, name):
    inside = _folder(home, name, "lib")
    link = home / "looks-harmless"
    link.symlink_to(inside)
    monkeypatch.setattr(sys, "path", [str(link)])
    assert _kept(entry._plan(["python3", "-m", "pytest"])) == []


@pytest.mark.parametrize("name", PRIVATE)
def test_a_search_path_entry_in_a_private_folder_is_not_kept(home, monkeypatch, name):
    tools = _folder(home, ".local", "bin")
    inside = _folder(home, name, "bin")
    monkeypatch.setenv("PATH", os.pathsep.join([inside, tools]))
    assert _kept(entry._plan(["python3", "-m", "pytest"])) == [tools]


def test_the_home_itself_and_the_checkout_are_never_listed(home, monkeypatch):
    top = entry._checkout_top()
    monkeypatch.setattr(sys, "path", [os.path.realpath(home), top])
    monkeypatch.setenv("PATH", os.pathsep.join([os.path.realpath(home), top]))
    assert _kept(entry._plan(["python3", "-m", "pytest"])) == []


def test_a_home_the_caller_set_elsewhere_is_kept_and_seeded(home, tmp_path, monkeypatch):
    other = tmp_path / "ci-home"
    other.mkdir()
    monkeypatch.setenv("HOME", str(other))
    assert entry._given_home(os.path.realpath(home)) == os.path.realpath(other)
    assert _kept(entry._plan(["python3", "-m", "pytest"])) == [os.path.realpath(other)]


@pytest.mark.parametrize("name", PRIVATE)
def test_a_home_the_caller_set_inside_a_private_folder_is_not_kept_or_seeded(home, monkeypatch, name):
    inside = _folder(home, name, "pretend-home")
    monkeypatch.setenv("HOME", inside)
    assert entry._given_home(os.path.realpath(home)) == ""
    assert _kept(entry._plan(["python3", "-m", "pytest"])) == []
    monkeypatch.setenv("HOME", os.path.join(os.path.realpath(home), name))
    assert entry._given_home(os.path.realpath(home)) == ""
    assert _kept(entry._plan(["python3", "-m", "pytest"])) == []


def test_the_account_home_or_a_missing_folder_as_home_is_not_a_given_home(home, tmp_path, monkeypatch):
    assert entry._given_home(os.path.realpath(home)) == ""
    monkeypatch.setenv("HOME", str(tmp_path / "not-there"))
    assert entry._given_home(os.path.realpath(home)) == ""
    monkeypatch.delenv("HOME")
    assert entry._given_home(os.path.realpath(home)) == ""


def test_the_pytest_words_are_carried_over(home):
    cmd = entry._plan(["python3", "-X", "dev", "-m", "pytest", "-q", "tests/x.py"])
    assert cmd[0] == entry.ROOM
    assert cmd[cmd.index("--"):] == ["--", sys.executable, "-m", "pytest", "-q", "tests/x.py"]
