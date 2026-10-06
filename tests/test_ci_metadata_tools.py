"""Pull-request metadata tooling must classify titles and diffs deterministically."""

from __future__ import annotations

import importlib
import importlib.util

import pytest


def tool(name):
    assert importlib.util.find_spec(name) is not None, f"Missing CI tool: {name}"
    return importlib.import_module(name)


def test_title_check_accepts_repository_convention():
    check = tool("tools.check_pr_title")
    for title in (
        "fix: retire legacy tracker prompt and cap request input",
        "feat(miniapp): add usage panel",
        "security: confine runtime loader modules to base directory",
        "chore(deps): bump coverage from 7.6.1 to 7.6.2",
        "refactor!: drop the legacy rag facade",
    ):
        assert check.title_errors(title) == [], title


@pytest.mark.parametrize(
    "title",
    [
        "",
        "   ",
        "Maintainability audit: dedupe test helpers",
        "fixed the thing",
        "fix:",
        "fix(Scope): uppercase scope",
        "fix: trailing whitespace ",
        "fix: " + "x" * 120,
    ],
)
def test_title_check_rejects_non_conforming_titles(title):
    assert tool("tools.check_pr_title").title_errors(title)


def test_title_check_reports_every_violation_of_one_title():
    errors = tool("tools.check_pr_title").title_errors(" Updated the docs " + "x" * 120 + " ")
    assert len(errors) == 3


def test_title_check_cli_reads_the_environment_and_exits_nonzero(monkeypatch, capsys):
    check = tool("tools.check_pr_title")
    monkeypatch.setenv("PR_TITLE", "fix: keep polling errors bounded")
    assert check.main([]) == 0
    monkeypatch.setenv("PR_TITLE", "loose title")
    assert check.main([]) == 1
    assert "Accepted types" in capsys.readouterr().err


def test_size_label_boundaries_are_inclusive_and_ordered():
    label = tool("tools.pr_size_label").size_label
    assert [label(lines) for lines in (0, 10, 11, 50, 51, 250, 251, 1000, 1001)] == [
        "size/xs",
        "size/xs",
        "size/s",
        "size/s",
        "size/m",
        "size/m",
        "size/l",
        "size/l",
        "size/xl",
    ]


def test_size_label_rejects_negative_counts():
    with pytest.raises(ValueError):
        tool("tools.pr_size_label").size_label(-1)


@pytest.mark.parametrize(
    "existing,stale,needs_target",
    [
        ((), [], True),
        (("size/l",), [], False),
        (("size/m",), ["size/m"], True),
        (("size/xs", "enhancement"), ["size/xs"], True),
        (("size/xl", "size/s", "size/m"), ["size/m", "size/s", "size/xl"], True),
    ],
)
def test_size_label_plan_replaces_only_other_size_labels(existing, stale, needs_target):
    assert tool("tools.pr_size_label").label_plan(300, existing) == ("size/l", stale, needs_target)


def test_size_labeler_dry_run_needs_no_credentials_or_network(capsys, monkeypatch):
    monkeypatch.delenv("PR_NUMBER", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert tool("tools.pr_size_label").main(["--dry-run", "--changed-lines", "51", "--labels", "size/xs,size/s"]) == 0
    assert "size/m" in capsys.readouterr().out
