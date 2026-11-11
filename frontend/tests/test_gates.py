"""Red-confirmation for the gates themselves.

A gate that checked nothing reports exactly what a gate that found nothing
reports. So for each file-set gate in ``./run`` this pins (a) that its file set
contains the files it must cover, and (b) that the tool goes red on a planted
defect and stays green on a clean file. Both directions matter: a tool that
always fails is as useless as one that never does.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from n64mini.paths import FRONTEND_ROOT, REPO_ROOT

BIN = Path(sys.executable).parent
RUN = FRONTEND_ROOT / "run"

# These read the real repo layout through git; mutmut's sandbox copy has neither,
# and they exercise ./run rather than src/, so they cannot kill a src mutant anyway.
pytestmark = pytest.mark.skipif(
    "MUTANT_UNDER_TEST" in os.environ, reason="gate self-tests need the real checkout"
)


def _run(*args: str | Path, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(a) for a in args], cwd=cwd, capture_output=True, text=True, check=False, timeout=60
    )


def _file_set(kind: str) -> set[str]:
    result = _run("bash", RUN, "files", kind)
    assert result.returncode == 0, result.stderr
    return set(result.stdout.split())


# ---------------------------------------------------------------- (a) coverage of each set
@pytest.mark.parametrize(
    ("kind", "must_include"),
    [
        (
            "text",
            {
                "README.md",
                "docs/MFIC.md",
                "themes/n64-classic-mini/base.xml",
                ".agents/skills/batocera-theme-harness/SKILL.md",
                ".github/workflows/ci.yml",
                "frontend/AGENTS.md",
                "frontend/src/n64mini/theme_schema/validate.py",
            },
        ),
        (
            "shell",
            {
                "frontend/run",
                "bundle/update.sh",
                ".githooks/pre-commit",
                ".agents/skills/batocera-theme-harness/scripts/setup_live_harness.sh",
            },
        ),
        (
            "all",
            {"README.md", "frontend/uv.lock", "themes/n64-classic-mini/art/n64_classic_bg.png"},
        ),
    ],
)
def test_file_set_covers_the_repo(kind: str, must_include: set[str]) -> None:
    assert must_include <= _file_set(kind)


def test_text_set_excludes_vendored_and_binary_files() -> None:
    text = _file_set("text")
    assert not any(f.startswith("frontend/vendor/") for f in text)
    assert not any(f.endswith((".png", ".ttf", ".wav")) for f in text)


def test_shell_set_excludes_vendored_upstream_scripts() -> None:
    shell = _file_set("shell")
    assert "frontend/vendor/batocera-43.1/S31emulationstation" in _file_set("all")
    assert not any(f.startswith("frontend/vendor/") for f in shell)
    assert "bundle/install.sh" in shell  # our own scripts outside frontend/ still are


def test_ignored_research_material_is_never_scanned() -> None:
    assert not any(f.startswith("docs/references/") for f in _file_set("all"))


def test_unknown_file_set_is_an_error() -> None:
    assert _run("bash", RUN, "files", "nope").returncode != 0


def test_empty_file_set_aborts_instead_of_passing(tmp_path: Path) -> None:
    """In a repo with no files, a file-set gate must fail, not report a clean pass."""
    (tmp_path / "frontend").mkdir()
    shutil.copy(RUN, tmp_path / "frontend" / "run")
    (tmp_path / ".gitignore").write_text("*\n", encoding="utf-8")  # nothing is a candidate
    assert _run("git", "init", "-q", cwd=tmp_path).returncode == 0
    probe = "source ./run >/dev/null; on_files text echo SHOULD-NOT-RUN"
    result = _run("bash", "-c", probe, cwd=tmp_path / "frontend")
    assert result.returncode == 1
    assert "file set 'text' matched nothing" in result.stderr
    assert "SHOULD-NOT-RUN" not in result.stdout


def test_a_deleted_tracked_file_is_not_in_the_file_sets(tmp_path: Path) -> None:
    """Deleting a tracked file before staging it must not break the gates that open the set."""
    (tmp_path / "frontend").mkdir()
    shutil.copy(RUN, tmp_path / "frontend" / "run")
    for name in ("kept.md", "gone.md"):
        (tmp_path / name).write_text("text\n", encoding="utf-8")
    assert _run("git", "init", "-q", cwd=tmp_path).returncode == 0
    assert _run("git", "add", "kept.md", "gone.md", cwd=tmp_path).returncode == 0
    (tmp_path / "gone.md").unlink()
    result = _run("bash", "-c", "source ./run >/dev/null; candidates", cwd=tmp_path / "frontend")
    assert result.returncode == 0, result.stderr
    listed = set(result.stdout.split())
    assert "kept.md" in listed
    assert "gone.md" not in listed


# ---------------------------------------------------------------- (b) each tool can fail
@pytest.mark.parametrize(
    ("tool", "args", "bad", "good"),
    [
        (
            "typos",
            ["--config", str(FRONTEND_ROOT / "pyproject.toml")],
            "We " + "rec" + "ieve data.\n",  # built at runtime so this file passes the spell gate
            "We receive the data. Colour and licence are allowed.\n",
        ),
        (
            "shellcheck",
            [],
            "#!/usr/bin/env bash\nrm -rf $DIR/*\n",
            '#!/usr/bin/env bash\necho "ok"\n',
        ),
        (
            "vulture",
            ["--min-confidence", "60"],
            "def never_called() -> None:\n    pass\n",
            "def used() -> None:\n    pass\n\n\nused()\n",
        ),
        (
            "detect-secrets-hook",
            [],
            'db_password = "Xk9mQ2vL7pR4wZ"\n',  # pragma: allowlist secret - fake, must be caught
            'greeting = "hello"\n',
        ),
    ],
)
def test_tool_fails_on_planted_defect_and_passes_clean(
    tmp_path: Path, tool: str, args: list[str], bad: str, good: str
) -> None:
    suffix = ".py" if tool == "vulture" else ".txt"  # vulture only reads Python files
    bad_file, good_file = tmp_path / f"bad{suffix}", tmp_path / f"good{suffix}"
    bad_file.write_text(bad, encoding="utf-8")
    good_file.write_text(good, encoding="utf-8")
    exe = BIN / tool
    assert _run(exe, *args, bad_file, cwd=tmp_path).returncode != 0, f"{tool} missed the defect"
    clean = _run(exe, *args, good_file, cwd=tmp_path)
    assert clean.returncode == 0, f"{tool} flagged a clean file: {clean.stdout}{clean.stderr}"


def test_validator_cli_fails_on_an_invalid_theme(tmp_path: Path) -> None:
    (tmp_path / "theme.xml").write_text(
        "<theme><formatVersion>7</formatVersion><view name='grid'>"
        "<image name='x'><colour>FF0000</colour></image></view></theme>",
        encoding="utf-8",
    )
    result = _run(BIN / "n64mini-validate-theme", tmp_path)
    assert result.returncode == 1
    assert "E031" in result.stdout


_ROM_SUFFIXES = (".z64", ".n64", ".v64")


def test_no_game_rom_can_be_committed() -> None:
    """The owner's ROMs are copyrighted and live in the working tree (N64/), never in git.

    "files all" is everything a commit could include: tracked plus untracked-but-not-ignored.
    """
    result = _run("bash", RUN, "files", "all")
    assert result.returncode == 0, result.stderr
    candidates = result.stdout.splitlines()
    assert candidates, "the file set is empty, so this check would see nothing"
    roms = [f for f in candidates if f.lower().endswith(_ROM_SUFFIXES)]
    assert roms == [], f"ROM files a commit could include: {roms[:5]}"
