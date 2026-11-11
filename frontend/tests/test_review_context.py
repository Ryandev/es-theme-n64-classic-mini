from __future__ import annotations

import json
import os
import subprocess
from typing import TYPE_CHECKING

import pytest
from tools import review_context as rc

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(
    "MUTANT_UNDER_TEST" in os.environ, reason="needs git and the real checkout layout"
)


def _git(cwd: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("hi\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "first")
    return tmp_path


@pytest.mark.parametrize(
    ("files", "blind"),
    [
        (["frontend/src/n64mini/theme_schema/validate.py"], True),
        (["docs/requirements/validator.md"], True),
        (["frontend/vendor/batocera-emulationstation/ThemeData.cpp"], True),
        (["frontend/src/n64mini/harness/render.py", "README.md"], False),
        (["themes/n64-classic-mini/base.xml"], False),
    ],
)
def test_blind_pass_runs_only_for_validator_changes(files: list[str], *, blind: bool) -> None:
    assert rc.needs_blind_pass(files) is blind


def test_rules_follow_the_files_touched() -> None:
    assert rc.rules_for(["README.md"]) == ["AGENTS.md"]
    assert rc.rules_for(["frontend/run"]) == ["AGENTS.md", "frontend/AGENTS.md"]
    assert rc.rules_for(["frontend/src/n64mini/theme_schema/validate.py"]) == [
        "AGENTS.md",
        "frontend/AGENTS.md",
        "docs/requirements/validator.md",
        "docs/MFIC.md",
    ]
    assert ".agents/skills/batocera-theme-harness/SKILL.md" in rc.rules_for(["themes/x.xml"])


def test_build_writes_the_context(repo: Path) -> None:
    (repo / "docs" / "requirements").mkdir(parents=True)
    (repo / "docs" / "requirements" / "validator.md").write_text("spec\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs(spec): add AC")
    meta = rc.build(rc.default_base(repo), cwd=repo)
    assert meta is not None
    assert meta["files"] == ["docs/requirements/validator.md"]
    assert meta["needs_blind_pass"] is True
    ctx = repo / ".review" / meta["head"]
    assert "validator.md" in (ctx / "diff.patch").read_text(encoding="utf-8")
    assert "docs(spec): add AC" in (ctx / "commits.txt").read_text(encoding="utf-8")
    assert json.loads((ctx / "meta.json").read_text(encoding="utf-8")) == meta


def test_empty_diff_is_nothing_to_review(repo: Path) -> None:
    assert rc.build("HEAD", cwd=repo) is None


def test_branch_base_is_the_merge_base(repo: Path) -> None:
    trunk = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "checkout", "-q", "-b", "feat/x")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat(x): a")
    assert rc.default_base(repo) == trunk


def test_git_errors_are_reported_not_swallowed(repo: Path) -> None:
    with pytest.raises(rc.GitError, match="no-such-ref"):
        rc.build("no-such-ref", cwd=repo)


def test_cli_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    assert rc.main(["--base", "HEAD"]) == 2
    assert "nothing to review" in capsys.readouterr().err
    assert rc.main(["--base", "no-such-ref"]) == 1
