"""Review fixes R3a (Lamptown 0.2.1, round 3): text masking.

1. More secret shapes in ``mask_secrets``: Google, Hugging Face and npm tokens, a PEM private
   key block, a JWT, a glued ``-pSECRET`` password flag, and ``PASS=`` names. Masked even with
   ``long_tokens=False`` (titles and room text).
2. Room text keeps URLs, but not their secrets: a userinfo password, a webhook path and a
   signature (``sig=``, ``X-Amz-Signature=``, ``token=``, ``key=``) query value.
3. Home folders in more forms (``/var/home/<u>``, ``\\\\PC\\Users\\<u>``, ``Documents and
   Settings\\<u>``, ``/export/home/<u>``) name no project.

Every canary here is invented and put together at run time, so no scanner sees a token.
"""

from __future__ import annotations

import datetime as dt

import pytest

from lamptown.build import build
from lamptown.collectors import shown_text
from lamptown.config import parse_config
from lamptown.status.adapters import excerpt, mask_secrets, project_folder
from lamptown.status.events import home_or_root, name_only, project_of
from lamptown.status.store import update_status

BS, SL, AT = "\\", "/", "@"
M = "QZCANARYQZ"
NOW = dt.datetime(2026, 11, 5, 12, tzinfo=dt.UTC)
PAD = "0123456789abcdefghijKLMNOP"
PEM_HEAD = "-----BEGIN " + "OPENSSH PRIV" + "ATE KEY-----"
PEM_TAIL = "-----END " + "OPENSSH PRIV" + "ATE KEY-----"


# ----------------------------------------------------------------------------- 1. shapes
SHAPES = {
    "google": lambda m: "deploy with AI" + "za" + "Sy" + m + PAD + " today",
    "huggingface": lambda m: "pull with h" + "f_" + m + PAD + " today",
    "npm": lambda m: "publish with np" + "m_" + m + PAD + " today",
    "pem-one-line": lambda m: "key " + PEM_HEAD + " b3BlbnNzaC1rZXk" + m + "AAAA",
    "pem-block": lambda m: PEM_HEAD + "\nb3BlbnNzaC1rZXk" + m + "\nAAAA+/==\n" + PEM_TAIL
    + "\nthen deploy",
    "pem-rsa": lambda m: "-----BEGIN " + "RSA PRIV" + "ATE KEY----- MIIE" + m,
    "jwt": lambda m: "auth e" + "yJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ" + m + "In0.c2lnbmF0dXJl",
    "jwt-unsigned": lambda m: "auth e" + "yJhbGciOiJub25lIn0.eyJzdWIiOiJ" + m + "In0.",
    "mysql-p": lambda m: "mysql -u root -p" + m + " kites",
    "mysqldump-p": lambda m: "mysqldump --user=root -p'" + m + "' kites",
    "mariadb-p": lambda m: "mariadb -h db -p" + m,
    "pass": lambda m: "PASS=" + m + " make deploy",
    "db-pass": lambda m: "export DB_PASS=" + m,
    "json-pass": lambda m: '{"pass": "' + m + '"}',
    "passwd": lambda m: "PASSWD=" + m + " make deploy",
    "pwd": lambda m: "PWD=" + m + " make deploy",
}


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("long_tokens", [True, False])
def test_mask_secrets_masks_the_new_shapes(shape, long_tokens):
    masked = mask_secrets(SHAPES[shape](M), long_tokens=long_tokens)
    assert M not in masked and M.lower() not in masked.lower(), (shape, masked)
    assert "…" in masked


@pytest.mark.parametrize("shape", SHAPES)
def test_titles_room_text_and_excerpts_mask_the_new_shapes(shape):
    text = SHAPES[shape](M)
    for where, out in (("title", name_only("Kite: " + text, 200)),
                       ("room", shown_text("Kite: " + text)),
                       ("excerpt", excerpt(text, 200))):
        assert M.lower() not in out.lower(), (shape, where, out)


def test_a_pem_block_masks_its_body_up_to_the_end_line():
    out = mask_secrets(SHAPES["pem-block"](M), long_tokens=False)
    assert "AAAA" not in out and "then deploy" in out, out


@pytest.mark.parametrize("title", [
    "Fix mysql -p prompt on login",  # "-p" with no glued value prompts: no secret
    "mkdir -p build/out",            # "-p" glued to nothing, and no password program
    "docker run -p8080:80 kite",     # a port, not after a password program
    "Make the pass rate visible",
    "compass=north in the map",
    "bypass=1 for the cache",
    "Tests pass: 12 of 14",
    "PASSES=3 for the render",
    "hf_transformers notes",
    "npm_config notes",
    "The AIza prefix in Google keys",
    "eyJ is how a JWT starts",
    "Read the BEGIN PRIVATE KEY docs",
    "mysql -P3306 kites",
    "sig=1 in the test",
])
def test_ordinary_titles_stay_whole(title):
    assert mask_secrets(title, long_tokens=False) == title
    assert name_only(title, 200) == title
    assert shown_text(title) == title


def test_mask_secrets_stays_fast_on_hostile_text():
    import time

    start = time.perf_counter()
    for text in ((PEM_HEAD + " ") * 5000, "mysql " + "-p " * 20000, "mysql -px" * 5000,
                 "e" + "yJa." * 20000, "e" + "yJ-" * 40000, "https://" + "a:" * 20000 + "b",
                 "https://h/?" + "&sig" * 20000, "pass" * 10000 + "=x"):
        mask_secrets(text, long_tokens=False)
        shown_text(text)
    assert time.perf_counter() - start < 2.0


# ----------------------------------------------------------------------------- 2. URLs in rooms
URLS = {
    "userinfo": lambda m: "pushed https://bob:" + m + AT + "example.com/kite.git",
    "userinfo-no-user": lambda m: "pushed https://:" + m + AT + "example.com/kite.git",
    "userinfo-ssh": lambda m: "cloned ssh://git:" + m + AT + "example.com:22/kite",
    "slack": lambda m: "posted to https://hooks." + "slack.com/services/T0000/B0000/" + m,
    "slack-workflow": lambda m: "posted to https://hooks." + "slack.com/workflows/T0/A0/1/" + m,
    "discord": lambda m: "posted to https://discord.com/api/web" + "hooks/123456/" + m,
    "azure-sig": lambda m: "got https://x.blob.core.windows.net/c?sv=2020&sig=" + m + "&se=1",
    "aws-sig": lambda m: "got https://b.s3.amazonaws.com/k?X-Amz-Expires=60&X-Amz-Signature="
    + m,
    "goog-sig": lambda m: "got https://storage.googleapis.com/b/k?X-Goog-Signature=" + m,
    "token": lambda m: "got https://example.com/feed?token=" + m + "&page=2",
    "key": lambda m: "got https://example.com/maps?key=" + m,
}


@pytest.mark.parametrize("form", URLS)
def test_room_text_masks_url_secrets(form):
    out = shown_text(URLS[form](M))
    assert M.lower() not in out.lower(), (form, out)
    assert "…" in out


def test_room_text_keeps_the_rest_of_the_url():
    assert shown_text(URLS["userinfo"](M)) == "pushed https://bob:…" + AT + "example.com/kite.git"
    out = shown_text(URLS["azure-sig"](M))
    assert out.startswith("got https://x.blob.core.windows.net/c?sv=2020&sig=…"), out
    assert shown_text(URLS["slack"](M)).startswith("posted to https://hooks.slack.com/services/")


@pytest.mark.parametrize("text", [
    "see https://example.com/kite?page=2&sort=new",
    "open http://localhost:3000/kite",
    "mail bob" + AT + "example.com about https://example.com/x",
    "https://hooks.example.com/services/kite",
])
def test_room_text_keeps_ordinary_urls(text):
    assert shown_text(text) == text


def test_a_job_result_with_a_url_password_reaches_no_page(tmp_path):
    folder = tmp_path / "jobs" / "kite"
    folder.mkdir(parents=True)
    (folder / "run-1.log").write_text("Done: " + URLS["userinfo"](M) + "\n", encoding="utf-8")
    text = f"""version = 1
[town]
title = "Kite field"

[[building]]
id = "jobs"
type = "jobs"
label = "Night jobs"
  [[building.source]]
  kind = "log_glob"
  name = "Kite"
  glob = '{folder.as_posix()}/*.log'
  done = 'Done:\\s*(.+)$'
"""
    cfg = parse_config(text, tmp_path / "lamptown.toml")
    out = tmp_path / "out"
    build(cfg, out_dir=out, now=NOW, tz=dt.UTC)
    pages = {p.name: p.read_text(encoding="utf-8") for p in out.iterdir() if p.is_file()}
    assert "example.com/kite.git" in pages["town-data.js"]  # the result shows
    assert not [n for n, body in pages.items() if M in body]


# ----------------------------------------------------------------------------- 3. home forms
def _homes(user: str) -> dict[str, str]:
    return {
        "var-home": SL + "var" + SL + "home" + SL + user,
        "export-home": SL + "export" + SL + "home" + SL + user,
        "unc-users": BS * 2 + "PC" + BS + "Users" + BS + user,
        "unc-drive": BS * 2 + "PC" + BS + "c$" + BS + "Users" + BS + user,
        "unc-slash": SL * 2 + "PC" + SL + "Users" + SL + user,
        "unc-wsl": BS * 2 + "wsl.localhost" + BS + "Ubuntu" + BS + "home" + BS + user,
        "docs-settings": "C:" + BS + "Documents and Settings" + BS + user,
        "docs-settings-d": "d:" + SL + "Documents and Settings" + SL + user,
    }


@pytest.mark.parametrize("form", _homes("x"))
def test_more_home_forms_are_home_folders(form):
    path = _homes(M)[form]
    assert home_or_root(path), path
    assert home_or_root(path + BS if BS in path else path + SL)
    assert project_folder(path) == ""


@pytest.mark.parametrize("form", _homes("x"))
def test_a_home_in_a_new_form_names_no_project(form):
    path = _homes(M)[form]
    assert project_of(None, path) == ""            # as x-folder
    assert project_of(path, None) == ""            # as an x-project path
    assert project_of(path, path) == ""


@pytest.mark.parametrize("form", _homes("x"))
def test_a_project_under_a_new_home_form_keeps_its_name(form):
    sep = BS if BS in _homes("x")[form] else SL
    path = _homes(M)[form] + sep + "paper-kite"
    assert not home_or_root(path)
    assert project_of(None, path) == "paper-kite"


@pytest.mark.parametrize("path", [
    SL + "var" + SL + "lib" + SL + "kite",
    SL + "export" + SL + "kite",
    BS * 2 + "fileserver" + BS + "share" + BS + "kite",
    "C:" + BS + "Documents" + BS + "kite",
])
def test_ordinary_folders_are_no_home(path):
    assert not home_or_root(path)
    assert project_of(None, path) == "kite"


def test_a_public_record_in_a_new_home_form_shows_no_login(tmp_path):
    status = tmp_path / "status"
    for n, path in enumerate(_homes(M).values()):
        update_status(f"kite-{n}", status, now=NOW, title=f"Kite {n}", state="working",
                      agent="kitebot", **{"x-folder": path})
    cfg = parse_config(f"[live]\nstatus_dir = '{status.as_posix()}'\n", tmp_path / "x.toml")
    out = tmp_path / "out"
    build(cfg, out_dir=out, now=NOW + dt.timedelta(seconds=5), tz=dt.UTC)
    live = (out / "town-live.js").read_text(encoding="utf-8")
    assert "Kite 0" in live
    assert M not in live and M.lower() not in live.lower()
