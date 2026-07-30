"""Guard against SQL that asyncpg cannot assign a parameter type to.

asyncpg prepares every statement, so a bind parameter whose *only* typed use is behind a
bare ``IS NULL`` test has no inferable type and the driver raises
``AmbiguousParameterError`` at execution time:

    CASE WHEN :assembly_id IS NULL THEN NULL ELSE CAST(:assembly_id AS uuid) END

This is not caught by the unit suite, because those tests mock the session and never
reach a real prepare. It bit twice: the family annotation manifest was silently never
written from the day provenance capture was added (the merge is best-effort and logs
instead of raising, so nothing surfaced), and the QC-threshold insert failed the same way
before it shipped.

The working form casts a sentinel instead, giving the parameter a concrete type:

    CAST(NULLIF(:assembly_id, '') AS uuid)

A grep-style test is the cheap guard that would have caught both, and it costs no
containers.
"""

from __future__ import annotations

from pathlib import Path
import re


BACKEND_APP = Path(__file__).resolve().parents[1] / "app"

# `CASE WHEN :param IS NULL THEN NULL ELSE CAST(:param AS <type>) END` on one line, and
# the same split across lines, which is how it is normally formatted in a SQL block.
_AMBIGUOUS_CASE = re.compile(
    r"CASE\s+WHEN\s+:(\w+)\s+IS\s+NULL\s+THEN\s+NULL\s+ELSE\s+CAST\(\s*:\1\s",
    re.IGNORECASE | re.DOTALL,
)


def _python_sources() -> list[Path]:
    return sorted(BACKEND_APP.rglob("*.py"))


def test_no_case_when_null_parameter_casts() -> None:
    offenders: list[str] = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for match in _AMBIGUOUS_CASE.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(BACKEND_APP.parent)}:{line_no} (:{match.group(1)})")
    assert not offenders, (
        "asyncpg cannot infer a parameter's type when its only other use is a bare "
        "`IS NULL`, so these statements raise AmbiguousParameterError at runtime. Use "
        "`CAST(NULLIF(:param, '') AS <type>)` and bind `value or ''` instead:\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_actually_matches_the_pattern_it_describes() -> None:
    # A test that can only ever pass is worthless; prove the regex catches the real form
    # (both single-line and the multi-line layout used in SQL blocks).
    single_line = "CASE WHEN :assembly_id IS NULL THEN NULL ELSE CAST(:assembly_id AS uuid) END"
    multi_line = (
        "CASE WHEN :assembly_id IS NULL THEN NULL\n"
        "     ELSE CAST(:assembly_id AS uuid) END"
    )
    assert _AMBIGUOUS_CASE.search(single_line)
    assert _AMBIGUOUS_CASE.search(multi_line)
    # The safe form must not be flagged.
    assert not _AMBIGUOUS_CASE.search("CAST(NULLIF(:assembly_id, '') AS uuid)")
    # A CASE over two *different* parameters is not this bug: the cast parameter has a
    # type of its own.
    assert not _AMBIGUOUS_CASE.search(
        "CASE WHEN :flag IS NULL THEN NULL ELSE CAST(:other AS uuid) END"
    )
