import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
NAMES = ["backup.sh", "restore.sh"]
SH = shutil.which("sh")
needs_sh = pytest.mark.skipif(SH is None, reason="no POSIX shell available")


def text(name):
    return (SCRIPTS / name).read_bytes().decode("utf-8")


def run(name, *args, **env):
    environment = {
        k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "CONFIRM_RESTORE"}
    }
    environment.update(env)
    return subprocess.run(
        [SH, (SCRIPTS / name).as_posix(), *args],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("name", NAMES)
def test_scripts_are_portable_and_strict(name):
    content = text(name)
    assert content.startswith("#!/bin/sh\n")
    assert "set -eu" in content
    assert "\r" not in content
    assert "[[" not in content
    assert "bash" not in content


@pytest.mark.parametrize("name", NAMES)
def test_scripts_never_print_connection_strings(name):
    assert not re.search(r"(echo|printf)[^\n]*\$\{?(DATABASE_URL|target)\b", text(name))


def test_backup_script_creates_verified_private_archives():
    content = text("backup.sh")
    assert "--format=custom" in content
    assert "pg_restore --list" in content
    assert "umask 077" in content
    assert "trap" in content
    assert "BACKUP_KEEP" in content


def test_restore_script_is_guarded_and_transactional():
    content = text("restore.sh")
    assert "CONFIRM_RESTORE" in content
    assert "--single-transaction" in content
    assert "--clean --if-exists" in content


def test_gitattributes_keeps_shell_scripts_on_unix_line_endings():
    assert "*.sh text eol=lf" in (ROOT / ".gitattributes").read_text()


def test_scripts_are_not_shipped_in_the_image():
    assert "scripts/" in (ROOT / ".dockerignore").read_text().splitlines()


@needs_sh
def test_backup_requires_database_url():
    result = run("backup.sh")
    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr


@needs_sh
@pytest.mark.parametrize("keep", ["abc", "-1", "0", "1.5"])
def test_backup_rejects_invalid_retention(keep):
    result = run("backup.sh", DATABASE_URL="postgresql://u:p@localhost/x", BACKUP_KEEP=keep)
    assert result.returncode == 2
    assert "BACKUP_KEEP" in result.stderr


@needs_sh
def test_restore_requires_a_backup_file():
    assert run("restore.sh").returncode == 2
    missing = run("restore.sh", "does-not-exist.dump", DATABASE_URL="postgresql://u:p@localhost/x")
    assert missing.returncode == 2
    assert "not found" in missing.stderr


@needs_sh
def test_restore_refuses_without_explicit_confirmation(tmp_path):
    dump = tmp_path / "linkforge.dump"
    dump.write_bytes(b"not a real dump")

    result = run("restore.sh", dump.as_posix(), DATABASE_URL="postgresql://u:p@localhost/x")

    assert result.returncode == 3
    assert "CONFIRM_RESTORE=yes" in result.stderr


@needs_sh
def test_restore_requires_a_target(tmp_path):
    dump = tmp_path / "linkforge.dump"
    dump.write_bytes(b"x")
    result = run("restore.sh", dump.as_posix(), CONFIRM_RESTORE="yes")
    assert result.returncode == 2
    assert "target" in result.stderr.lower()
