"""The technical file says what a change did, never only its pull-request or issue number.

A reference such as ``#123`` tells an auditor nothing. The technical file (``docs/regulatory/``)
states the behaviour, the decision with its date and the role that took it, or the test that
verifies it (DOC-7). Here every Markdown file under ``docs/regulatory/`` is read, and a
pull-request or issue number is refused in each form it could take: ``#123``, ``PR 123``,
``pull request 123``, ``issue 123``, and a GitHub link to an issue, pull request, discussion or
security alert by its number.

Not references: a heading anchor in a link or a path (``deployment-gcp.md#10-cicd-the-normal-path``)
and an HTML character reference (``&#8212;``). A reference that must stay is written as a full
link and listed in ``ALLOWED_LINKS``, with the reason it stays; a listed link that no document
cites any more fails, so the list cannot grow stale. The checker itself is tested on a document
that holds every form, so that it cannot pass by reading nothing.

An issue ID the technical file cites (``CLIN-57``, ``SEC-23``) tracks open work: it is a row of
``docs/open-issues.md`` or an item of ``docs/ROADMAP.md``. An ID that only labelled the change
that built a behaviour has no place in the technical file, which states the behaviour instead;
so a closed ID is refused, and an ID that is closed while the file still cites it fails here
until the file says what it stood for.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
import re

_REPO = Path(__file__).resolve().parents[2]
_REGULATORY = _REPO / "docs" / "regulatory"

# Each reference the technical file keeps, written in full, with the reason it stays.
ALLOWED_LINKS: dict[str, str] = {
    "https://github.com/bmenten/CoGA/issues/518": (
        "TF-14 Status: the technical-file sign-off issue, where the open owner and DPO action "
        "to reconcile and file the signed DPIA is tracked"
    ),
}

# A link target, ``](…)``, and a bare URL: their ``#`` starts an anchor, not a number.
_LINK_TARGET = re.compile(r"\]\([^)\s]*\)")
_URL = re.compile(r"https?://[^\s)>\]]+")
# ``#123`` but not ``&#123;`` (a character reference) nor ``#10-cicd`` (a heading anchor).
_HASH_NUMBER = re.compile(r"(?<!&)#\d+(?![\w-])")
_WORD_NUMBER = re.compile(r"\b(?:PRs?|pull[ -]requests?|issues?)[ \t]*(?:no\.?[ \t]*)?\d+\b", re.IGNORECASE)
_GITHUB_NUMBERED = re.compile(
    r"https?://github\.com/[\w.-]+/[\w.-]+/"
    r"(?:issues|pull|discussions|security/code-scanning|security/dependabot)/\d+",
    re.IGNORECASE,
)


def numbered_references(text: str, allowed: Collection[str] = tuple(ALLOWED_LINKS)) -> list[str]:
    """Each pull-request or issue number ``text`` cites, as ``line: reference``."""
    found: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        refs = [link.group(0) for link in _GITHUB_NUMBERED.finditer(line) if link.group(0) not in allowed]
        prose = _URL.sub(" ", _LINK_TARGET.sub("]", line))
        refs.extend(match.group(0) for match in _HASH_NUMBER.finditer(prose))
        refs.extend(match.group(0) for match in _WORD_NUMBER.finditer(prose))
        found.extend(f"{lineno}: {ref}" for ref in refs)
    return found


def _documents() -> list[Path]:
    return sorted(_REGULATORY.rglob("*.md"))


def test_the_technical_file_cites_no_pull_request_or_issue_number() -> None:
    documents = _documents()
    problems = [
        f"{doc.relative_to(_REPO)}:{reference}"
        for doc in documents
        for reference in numbered_references(doc.read_text(encoding="utf-8"))
    ]
    assert not problems, (
        "The technical file cites pull-request or issue numbers. Write what each stood for (the "
        "behaviour, the decision with its date and role, or the verifying test) instead:\n"
        + "\n".join(problems)
    )
    assert len(documents) >= 20  # TF-01 to TF-18, their parts, the README and the questionnaire


def test_each_allowed_link_is_still_cited() -> None:
    cited = "\n".join(doc.read_text(encoding="utf-8") for doc in _documents())
    stale = [link for link in ALLOWED_LINKS if link not in cited]
    assert not stale, f"Allowed links no document cites any more; remove them from ALLOWED_LINKS: {stale}"


def test_a_number_in_any_form_is_reported_and_an_anchor_is_not() -> None:
    text = "\n".join(
        [
            "A fix (#123) and #45, #6–#7; PR#8 too.",
            "Merged as PR 9, pull request 10, pull-request no. 11; see issue 12 and issues 13.",
            "A link [the fix](https://github.com/bmenten/CoGA/pull/14), https://github.com/bmenten/CoGA/issues/15.",
            "An alert https://github.com/bmenten/CoGA/security/code-scanning/16.",
            "Kept: [sign-off](https://github.com/bmenten/CoGA/issues/518) and https://github.com/bmenten/CoGA/issues/518.",
            "Anchors: [§10](../deployment-gcp.md#10-cicd-the-normal-path), `deployment-gcp.md#10-cicd`, [top](#1-scope).",
            "Not numbers: &#8212; CR-118, REQ-NIPT-005, H11.1-OP5 §4.2, S-7, TF-09b §3, ##, # 1.",
            "A blob link https://github.com/bmenten/CoGA/blob/da713be/docs/x.md#L35 is no reference.",
        ]
    )

    assert numbered_references(text, {"https://github.com/bmenten/CoGA/issues/518"}) == [
        "1: #123",
        "1: #45",
        "1: #6",
        "1: #7",
        "1: #8",
        "2: PR 9",
        "2: pull request 10",
        "2: pull-request no. 11",
        "2: issue 12",
        "2: issues 13",
        "3: https://github.com/bmenten/CoGA/pull/14",
        "3: https://github.com/bmenten/CoGA/issues/15",
        "4: https://github.com/bmenten/CoGA/security/code-scanning/16",
    ]


def test_a_link_left_off_the_allowed_list_is_reported() -> None:
    text = "Tracked in [the sign-off issue](https://github.com/bmenten/CoGA/issues/518)."

    assert numbered_references(text, set()) == ["1: https://github.com/bmenten/CoGA/issues/518"]
    assert numbered_references(text) == []


# --- An issue ID the technical file cites tracks open work ---------------------------------------

_DOCS = _REPO / "docs"
_ID = r"(?:CLIN|DATA|DOC|ENG|OPS|PROD|REG|SAFE|SEC|TRACE|UI)-\d+"
# An open-issue ID on its own: not the tail of a requirement ID such as REQ-SEC-001.
_ISSUE_ID = re.compile(rf"(?<![\w-]){_ID}\b")
# The first cell of a row of open-issues.md and a roadmap item's ID
# ("- **Request tracing** (SEC-16): …").
_ROW_ID = re.compile(rf"^\|\s*({_ID})\s*\|", re.MULTILINE)
_ROADMAP_ITEM_ID = re.compile(rf"^\s*-\s+\*\*[^*]+\*\*\s+\(({_ID})\)", re.MULTILINE)


def cited_issue_ids(text: str) -> set[str]:
    """The issue IDs ``text`` cites, whether open or closed."""
    return set(_ISSUE_ID.findall(text))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _open_issue_ids() -> set[str]:
    listed = set(_ROW_ID.findall(_read(_DOCS / "open-issues.md")))
    roadmap = set(_ROADMAP_ITEM_ID.findall(_read(_DOCS / "ROADMAP.md")))
    assert listed and roadmap, "read no open issue or no roadmap item: has a format changed?"
    return listed | roadmap


def test_each_issue_id_the_technical_file_cites_is_open() -> None:
    open_ids = _open_issue_ids()
    closed = [
        f"{doc.relative_to(_REPO)}: {issue_id}"
        for doc in _documents()
        for issue_id in sorted(cited_issue_ids(_read(doc)))
        if issue_id not in open_ids
    ]
    assert not closed, (
        "The technical file cites issue IDs that are not open (neither a row of "
        "docs/open-issues.md nor an item of docs/ROADMAP.md). An ID only labels the change that "
        "built a behaviour: state the behaviour, the decision with its date and role, or the "
        "verifying test instead, and drop the ID:\n" + "\n".join(closed)
    )


def test_an_issue_id_is_told_from_the_ids_around_it() -> None:
    text = (
        "Fixed by CLIN-6 and SAFE-15 (REQ-SEC-001, REQ-TRACE-007); see TF-06 H15, CR-118, "
        "H11.1-OP5, S-7, SHA-256; ENG-19, frontend; (CLIN-66, part 1); PROD-1: done."
    )

    assert cited_issue_ids(text) == {"CLIN-6", "SAFE-15", "ENG-19", "CLIN-66", "PROD-1"}
