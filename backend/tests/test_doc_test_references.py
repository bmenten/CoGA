"""Every test the documents name exists: the RTM's verifying tests cannot go stale.

The Requirements Traceability Matrix (TF-09b) names each requirement's verifying tests as
``test_file.py::test_name``, followed by ``::other_test`` for more tests of the same file, and
frontend tests as ``File.test.tsx`` or ``File.test.tsx::it name``; TF-09c, docs/testing.md,
docs/security-posture.md and other documents do the same. A test renamed, merged into a
parametrised one or deleted would leave its reference pointing at nothing, and the catalogue
check (``scripts/check-test-catalogue.sh``) reads only docs/testing.md's file links, not test
names, and only that one document.

Here every tracked Markdown document under ``docs/`` and at the repository root is read, and
each test it names in an inline code span must exist: a backend test file by its name (or its
path, when the reference gives one) with the function, class or method named after ``::``; a
frontend test file with the ``it``/``test``/``describe`` title named after ``::``; a Playwright
spec by its name. A name with ``*`` matches by pattern (``test_category_*``) and a parameter id
(``[...]``) is ignored. A relative Markdown link to a test file must lead to one. The checker
itself is tested on a document that names tests which do not exist, and on the RTM, so that it
cannot pass by reading nothing.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from fnmatch import fnmatch
from pathlib import Path
import re

import pytest

_REPO = Path(__file__).resolve().parents[2]
_RTM = _REPO / "docs" / "regulatory" / "TF-09b-requirements-traceability-matrix.md"

_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")
_TEST_FILE = re.compile(
    r"^(?P<path>(?:[\w.-]+/)*)(?P<file>test_\w+\.py|[\w.-]+\.test\.tsx?|[\w.-]+\.spec\.ts)(?:::(?P<name>.+))?$"
)
_TEST_FILE_PATH = re.compile(r"(?:^|/)(?:test_\w+\.py|[\w.-]+\.test\.tsx?|[\w.-]+\.spec\.ts)$")
# A vitest or Playwright title: it('…'), test("…"), describe(`…`), it.each(…)('…').
_FRONTEND_TITLE = re.compile(
    r"""\b(?:it|test|describe)(?:\.(?:each|skip|only|todo)(?:\([^)]*\))?)?\(\s*(['"`])((?:\\.|(?!\1).)*)\1""",
    re.S,
)


def _plain(text: str) -> str:
    """A title as a reader writes it: typographic apostrophes and escapes made plain."""
    return text.replace("’", "'").replace("\\'", "'").replace('\\"', '"').strip()


def _backend_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.update({member.name, f"{node.name}::{member.name}"})
    return names


def _frontend_names(path: Path) -> set[str]:
    return {_plain(match.group(2)) for match in _FRONTEND_TITLE.finditer(path.read_text(encoding="utf-8"))}


def indexed_tests(repo: Path) -> dict[str, list[tuple[Path, set[str]]]]:
    """Each test file of the tree by its file name, with the test names it defines."""
    index: dict[str, list[tuple[Path, set[str]]]] = defaultdict(list)
    for path in sorted((repo / "backend" / "tests").rglob("test_*.py")):
        index[path.name].append((path, _backend_names(path)))
    frontend = repo / "frontend"
    for pattern in ("src/**/*.test.ts", "src/**/*.test.tsx", "e2e/*.spec.ts"):
        for path in sorted(frontend.glob(pattern)):
            if "node_modules" not in path.parts:
                index[path.name].append((path, _frontend_names(path)))
    return index



def _defines(name: str, defined: set[str], *, frontend: bool) -> bool:
    name = _plain(name) if frontend else name.split("[", 1)[0].strip()
    if "*" in name:
        return any(fnmatch(candidate, name) for candidate in defined)
    if name in defined:
        return True
    # An it.each title with a placeholder (%s, $name) names each of its cases.
    return frontend and any(
        re.fullmatch(re.sub(r"%[sdifjo#]|\\\$\\\{.*?\\\}|\\\$\w+", ".+", re.escape(candidate)), name)
        for candidate in defined
        if "%" in candidate or "$" in candidate
    )


def unresolved_references(
    doc: Path, text: str, index: dict[str, list[tuple[Path, set[str]]]], repo: Path
) -> tuple[int, list[str]]:
    """How many test references ``text`` makes, and those that name nothing in the tree."""
    checked = 0
    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        where = f"{doc.relative_to(repo) if doc.is_relative_to(repo) else doc.name}:{lineno}"
        current: tuple[str, list[tuple[Path, set[str]]]] | None = None
        for span in _CODE_SPAN.finditer(line):
            token = span.group(1).strip()
            match = _TEST_FILE.match(token)
            if match:
                name = match.group("name")
                files = index.get(match.group("file"), [])
                if match.group("path"):
                    files = [entry for entry in files if entry[0].as_posix().endswith(match.group("path") + match.group("file"))]
                checked += 1
                if not files:
                    problems.append(f"{where}: `{token}` names no test file in the tree")
                    current = None
                    continue
                current = (match.group("file"), files)
            elif token.startswith("::") and current is not None:
                name = token[2:]
            else:
                continue
            if not name:
                continue
            checked += 1
            frontend = not current[0].endswith(".py")
            if not any(_defines(name, defined, frontend=frontend) for _, defined in current[1]):
                problems.append(f"{where}: `{current[0]}::{name}` names no test that exists")
        for link in _LINK.finditer(line):
            target = link.group(1)
            if "://" in target or not _TEST_FILE_PATH.search(target):
                continue
            checked += 1
            if not (doc.parent / target).resolve().is_file():
                problems.append(f"{where}: the link to {target} leads to no file")
    return checked, problems


def _documents(repo: Path) -> list[Path]:
    return sorted(set(repo.glob("*.md")) | set((repo / "docs").rglob("*.md")))


def test_every_test_a_document_names_exists() -> None:
    index = indexed_tests(_REPO)
    checked = 0
    problems: list[str] = []
    for doc in _documents(_REPO):
        count, found = unresolved_references(doc, doc.read_text(encoding="utf-8"), index, _REPO)
        checked += count
        problems.extend(found)
    assert not problems, "Documents name tests that do not exist:\n" + "\n".join(problems)
    assert checked > 1000  # the RTM alone names several hundred


def test_the_rtm_names_its_verifying_tests_in_the_form_this_check_reads() -> None:
    checked, problems = unresolved_references(_RTM, _RTM.read_text(encoding="utf-8"), indexed_tests(_REPO), _REPO)
    assert not problems
    assert checked > 500


def test_a_reference_to_a_test_that_does_not_exist_is_reported(tmp_path: Path) -> None:
    (tmp_path / "backend" / "tests").mkdir(parents=True)
    (tmp_path / "backend" / "tests" / "test_thing.py").write_text(
        "def test_kept():\n    pass\n\nclass TestGroup:\n    def test_member(self):\n        pass\n"
    )
    (tmp_path / "frontend" / "src" / "__tests__").mkdir(parents=True)
    (tmp_path / "frontend" / "src" / "__tests__" / "Thing.test.tsx").write_text(
        "describe('Thing', () => {\n  it('shows the PROBAND’s call', () => {})\n"
        "  it.each(['a'])('reads %s as a', () => {})\n})\n"
    )
    doc = tmp_path / "docs" / "TF.md"
    doc.parent.mkdir()
    text = "\n".join(
        [
            "| REQ-1 | `test_thing.py::test_kept`, `::TestGroup::test_member`, `::test_k*`, `::test_kept[case]` |",
            "| REQ-2 | `test_thing.py::test_renamed`, `::test_gone*` |",
            "| REQ-3 | `Thing.test.tsx::shows the PROBAND's call`, `::reads b as a`, `::draws nothing` |",
            "| REQ-4 | `test_missing.py`, `backend/tests/e2e/test_thing.py` |",
            "| REQ-5 | [kept](../backend/tests/test_thing.py), [gone](../backend/tests/test_gone.py) |",
            "| REQ-6 | `::test_kept` without a file before it, `pytest -k test_kept` |",
        ]
    )
    checked, problems = unresolved_references(doc, text, indexed_tests(tmp_path), tmp_path)

    assert problems == [
        "docs/TF.md:2: `test_thing.py::test_renamed` names no test that exists",
        "docs/TF.md:2: `test_thing.py::test_gone*` names no test that exists",
        "docs/TF.md:3: `Thing.test.tsx::draws nothing` names no test that exists",
        "docs/TF.md:4: `test_missing.py` names no test file in the tree",
        "docs/TF.md:4: `backend/tests/e2e/test_thing.py` names no test file in the tree",
        "docs/TF.md:5: the link to ../backend/tests/test_gone.py leads to no file",
    ]
    assert checked == 16


@pytest.mark.parametrize(
    ("name", "defined", "frontend", "expected"),
    [
        ("test_a", {"test_a"}, False, True),
        ("test_a[x-y]", {"test_a"}, False, True),
        ("test_category_*", {"test_category_8_false_negative"}, False, True),
        ("test_category_*", {"test_other"}, False, False),
        ("draws a man’s one X", {"draws a man's one X"}, True, True),
        ("reads chr1 as autosomal", {"reads %s as autosomal"}, True, True),
        ("reads chr1 as autosomal", {"reads chr1 as sex-linked"}, True, False),
    ],
)
def test_a_name_matches_as_the_documents_write_it(
    name: str, defined: set[str], frontend: bool, expected: bool
) -> None:
    assert _defines(name, defined, frontend=frontend) is expected
