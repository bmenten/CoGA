"""The code, its comments and the docs describe CoGA as it is; its history stays on GitHub.

CoGA is a development build with test data only (AGENTS.md): a file states what the code does
now, and why, never which pull request or closed issue made it so. Git, the pull requests and
the issues keep that history. Here every tracked source file, comment and document outside the
technical file (which ``test_regulatory_docs_cite_no_pr_numbers.py`` reads) is checked for:

- an issue ID that is not open: one that is neither a row of ``docs/open-issues.md`` nor an
  item of ``docs/ROADMAP.md``. An open ID may stay where it points at the open work. A
  requirement ID (``REQ-SEC-001``) is not an issue ID;
- a pull-request or issue number: in a document in every form the technical file's check
  refuses (``#123``, ``PR 123``, a numbered GitHub link), and in source code as ``#123`` after a
  space or an opening parenthesis. A colour (``"#333"``, a stylesheet) is no reference.

``docs/open-issues.md`` and ``docs/ROADMAP.md`` define the open IDs and may name the GitHub
issues they track; ``CHANGELOG.md`` is the release history. They are not read here.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from backend.tests.test_regulatory_docs_cite_no_pr_numbers import (
    _open_issue_ids,
    cited_issue_ids,
    numbered_references,
)

_REPO = Path(__file__).resolve().parents[2]

# Documents read whole. The technical file has its own check.
_DOCUMENT_SUFFIXES = (".md",)
_NOT_READ = (
    "docs/regulatory/",
    "docs/open-issues.md",
    "docs/ROADMAP.md",
    "CHANGELOG.md",
)
# Source files: every issue ID they hold, and a ``#123`` after a space or "(".
_SOURCE_SUFFIXES = (".py", ".sql", ".sh", ".tf", ".ts", ".tsx", ".yml", ".yaml", ".in", ".mako")
_SOURCE_NAMES = (".env.example",)
_SOURCE_ROOTS = ("backend/", "frontend/src/", "scripts/", "terraform/", "ci/", "clickhouse/", "demo/")
# Generated from the backend's docstrings, which this check reads at their source; and the two
# checks themselves, whose own tests hold the forms they refuse.
_GENERATED = (
    "frontend/src/lib/apiSchema.generated.ts",
    "backend/tests/test_regulatory_docs_cite_no_pr_numbers.py",
    "backend/tests/test_no_history_labels.py",
)

# ``#123`` in code: after a space or "(", not a colour or an anchor.
_CODE_NUMBER = re.compile(r"(?:(?<=\s)|(?<=\())#\d{2,5}\b(?![\w-])")


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=_REPO, check=True, capture_output=True, text=True
    ).stdout
    return [line for line in out.splitlines() if line]


def _documents(tracked: list[str]) -> list[str]:
    return [
        path
        for path in tracked
        if path.endswith(_DOCUMENT_SUFFIXES) and not path.startswith(_NOT_READ)
    ]


def _sources(tracked: list[str]) -> list[str]:
    return [
        path
        for path in tracked
        if (
            (path.startswith(_SOURCE_ROOTS) and path.endswith(_SOURCE_SUFFIXES))
            or (path.startswith("docker-compose") and path.endswith(".yml"))
            or path in _SOURCE_NAMES
        )
        and path not in _GENERATED
    ]


def code_numbers(text: str) -> list[str]:
    """Each ``#123`` reference in source ``text``, as ``line: reference``."""
    return [
        f"{lineno}: {match.group(0)}"
        for lineno, line in enumerate(text.splitlines(), 1)
        for match in _CODE_NUMBER.finditer(line)
    ]


def _read(path: str) -> str:
    return (_REPO / path).read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def tracked() -> list[str]:
    files = _tracked()
    assert len(files) > 1000, "git ls-files listed too few files: is this a checkout?"
    return files


def test_no_file_cites_an_issue_id_that_is_not_open(tracked: list[str]) -> None:
    open_ids = _open_issue_ids()
    closed = [
        f"{path}: {issue_id}"
        for path in _documents(tracked) + _sources(tracked)
        for issue_id in sorted(cited_issue_ids(_read(path)))
        if issue_id not in open_ids
    ]
    assert not closed, (
        "Files cite issue IDs that are not open (neither a row of docs/open-issues.md nor an item "
        "of docs/ROADMAP.md). State the behaviour, or the reason, as it is now and drop the ID; "
        "the history stays on GitHub:\n" + "\n".join(closed)
    )


def test_no_document_cites_a_pull_request_or_issue_number(tracked: list[str]) -> None:
    problems = [
        f"{path}:{reference}"
        for path in _documents(tracked)
        for reference in numbered_references(_read(path))
    ]
    assert not problems, (
        "Documents cite pull-request or issue numbers. Write what each stood for instead; the "
        "history stays on GitHub:\n" + "\n".join(problems)
    )


def test_no_source_file_cites_a_pull_request_or_issue_number(tracked: list[str]) -> None:
    problems = [
        f"{path}:{reference}" for path in _sources(tracked) for reference in code_numbers(_read(path))
    ]
    assert not problems, (
        "Source files cite pull-request or issue numbers in a comment or a docstring. State the "
        "behaviour or the reason instead; the history stays on GitHub:\n" + "\n".join(problems)
    )


def test_the_checks_read_what_they_should() -> None:
    files = _tracked()
    documents, sources = _documents(files), _sources(files)

    assert "AGENTS.md" in documents and "docs/database.md" in documents
    assert "docs/handleiding/03-databankstructuren.md" in documents
    assert not [path for path in documents if path.startswith("docs/regulatory/")]
    assert "backend/app/core/postgres_migrations.py" in sources
    assert "backend/db/schema/postgres/04_traceability.sql" in sources
    assert ".env.example" in sources and "frontend/src/lib/apiSchema.generated.ts" not in sources
    assert any(path.endswith(".tsx") for path in sources)


def test_a_code_reference_is_told_from_a_colour_and_an_anchor() -> None:
    text = "\n".join(
        [
            "# Fixed in #123 (#45); see  #6789.",
            "color = '#333'; style={{ color: \"#000\" }}; href='#top'; x#12",
            "// a comment naming (#1012) and #99",
        ]
    )

    assert code_numbers(text) == ["1: #123", "1: #45", "1: #6789", "3: #1012", "3: #99"]
