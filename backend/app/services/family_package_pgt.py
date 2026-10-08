"""The PGT pipeline's (nf-cmgg/copgtm) description of a family, read when a manifest is
discovered.

The pipeline's PED (``ped/combined.ped``) holds the couple and the embryos, the embryos
with the sex ngs-bits read off their reads. It does not say which members are embryos,
and it lacks the index: the relative whose haplotypes tell the affected parent's two
haplotypes apart. Both are in the samplesheet the pipeline ran from, which it copies to
``dashboard/samplesheet.csv`` (``id,fam,role,...``, role ``embryo``, ``father``,
``mother`` or ``index``) beside ``dashboard/pedigree.csv``
(``fam,father_id,mother_id,index_id,embryo_id``).

Discovery turns them into the manifest's ``family`` block: the embryos get the embryo
role, and an index the PED lacks is added under ``family.add_members``. How the index is
related is not in any of the pipeline's files, so it is added without parents; the
warning says what KING measured, for whoever completes the pedigree. Nothing here is
read at import: the import reads only the manifest, which the user can edit first.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ..core.coga_logging import scrub_log
from ..schemas import FamilyImportValidationIssue
from .family_package_common import ParsedPed, _issue, _resolve_package_path
from .family_package_qc import _csv_rows, parse_king_kin0_text, parse_ngsbits_sample_gender_text
from .sample_integrity_qc import KINSHIP_FIRST_DEGREE, KINSHIP_SECOND_DEGREE, KINSHIP_THIRD_DEGREE
from .upload_safety import read_path_text_bounded


logger = logging.getLogger(__name__)


PGT_SAMPLESHEET = "dashboard/samplesheet.csv"
PGT_PEDIGREE = "dashboard/pedigree.csv"

_PIPELINE_ROLES = frozenset({"embryo", "father", "mother", "index"})

_INDEX_STATUS_NOTE = (
    "It is recorded under metadata.pgt.indexes: with metadata.pgt.inheritance_model set, the "
    "import records it as affected (under XLR, a female index as a proven carrier), as the "
    "risk haplotype is found from it; a status under family.add_members or family.members wins."
)


def _read_package_file(root: Path, relative_path: str, *, kind: str) -> str | None:
    """A small package file's text, or None when it is absent or unreadable."""
    try:
        path = _resolve_package_path(root, relative_path)
    except HTTPException:
        return None
    if path is None or not path.is_file():
        return None
    try:
        return read_path_text_bounded(path, kind=kind)
    except (HTTPException, OSError, UnicodeDecodeError):
        # The path can hold the family ID the Discover request names (the KING table's).
        logger.warning("Could not read %s from the package", scrub_log(relative_path))
        return None


def _rows_by_header(text_value: str) -> list[dict[str, str]]:
    rows = _csv_rows(text_value)
    if not rows:
        return []
    header = [cell.lower() for cell in rows[0]]
    return [dict(zip(header, row)) for row in rows[1:]]


def read_pgt_pipeline_roles(root: Path) -> dict[str, str]:
    """``{sample_id: pipeline role}`` from the pipeline's samplesheet and pedigree.

    The samplesheet names every sample's role; the pedigree adds its embryo and index
    columns, so a package carrying only one of the two files is read as well. Empty when
    the package has neither, which is how a package from another pipeline is recognised.
    """
    roles: dict[str, str] = {}
    samplesheet = _read_package_file(root, PGT_SAMPLESHEET, kind="PGT samplesheet")
    if samplesheet:
        for row in _rows_by_header(samplesheet):
            sample_id = (row.get("id") or row.get("sample") or "").strip()
            role = (row.get("role") or "").strip().lower()
            if sample_id and role in _PIPELINE_ROLES:
                roles[sample_id] = role
    pedigree = _read_package_file(root, PGT_PEDIGREE, kind="PGT pedigree")
    if pedigree:
        for row in _rows_by_header(pedigree):
            for column, role in (
                ("father_id", "father"),
                ("mother_id", "mother"),
                ("index_id", "index"),
                ("embryo_id", "embryo"),
            ):
                sample_id = (row.get(column) or "").strip()
                if sample_id and sample_id not in {"0", "NA", "na"}:
                    roles.setdefault(sample_id, role)
    return roles


def _kinship_degree(kinship: float) -> str:
    # The KING cut-points sample-integrity QC uses. Only the degree is named: within the
    # first degree the IBS0 rate separates parent-offspring from siblings, and on imputed
    # genotypes the siblings' rate falls below the usual cut-off, so it is quoted, not read.
    if kinship > KINSHIP_FIRST_DEGREE:
        return "first-degree"
    if kinship > KINSHIP_SECOND_DEGREE:
        return "second-degree"
    if kinship > KINSHIP_THIRD_DEGREE:
        return "third-degree"
    return "unrelated"


def _kinship_pairs(
    root: Path, family_id: str, sample_id: str, members: set[str]
) -> tuple[str, dict[str, dict[str, Any]]]:
    """The pipeline's KING table (its path) and its pairs of ``sample_id`` with each of
    ``members``, by the other member."""
    relative_path = f"king/{family_id}.kin0"
    text_value = _read_package_file(root, relative_path, kind="KING kinship")
    pairs: dict[str, dict[str, Any]] = {}
    for pair in parse_king_kin0_text(text_value) if text_value else []:
        if sample_id not in (pair["sample_a"], pair["sample_b"]):
            continue
        other = pair["sample_b"] if pair["sample_a"] == sample_id else pair["sample_a"]
        if other in members:
            pairs[other] = pair
    return relative_path, pairs


def _kinship_evidence(sample_id: str, relative_path: str, pairs: dict[str, dict[str, Any]]) -> str | None:
    """What the pipeline's KING table measured between ``sample_id`` and the members in
    ``pairs``, closest first."""
    described: list[tuple[float, str]] = []
    for other, pair in pairs.items():
        ibs0 = pair.get("ibs0")
        figures = f"kinship {pair['kinship']:.3f}" + (f", IBS0 {ibs0:.4f}" if ibs0 is not None else "")
        described.append((pair["kinship"], f"{_kinship_degree(pair['kinship'])} to {other} ({figures})"))
    if not described:
        return None
    texts = [text for _kinship, text in sorted(described, reverse=True)]
    return f"KING ({relative_path}) measured {sample_id} as " + " and ".join(texts) + "."


def _index_link(
    *,
    father: str | None,
    mother: str | None,
    pairs: dict[str, dict[str, Any]],
    affected_parent: str | None,
) -> tuple[str, list[str]] | None:
    """How an index the PED lacks hangs on the couple, as far as the package says: the
    couple's child (``("child", [father, mother])``) when KING measures it first-degree to
    both, a relative of unknown degree of the parents KING measures it related to, or else
    of the affected parent the run traced (``("relative", [parent, ...])``); ``None`` when
    nothing says."""
    def kinship(parent: str | None) -> float | None:
        pair = pairs.get(parent) if parent else None
        return pair["kinship"] if pair else None

    father_kinship, mother_kinship = kinship(father), kinship(mother)
    if (
        father
        and mother
        and father_kinship is not None
        and mother_kinship is not None
        and father_kinship > KINSHIP_FIRST_DEGREE
        and mother_kinship > KINSHIP_FIRST_DEGREE
    ):
        return "child", [father, mother]
    related = [
        parent
        for parent, value in ((father, father_kinship), (mother, mother_kinship))
        if parent and value is not None and value > KINSHIP_THIRD_DEGREE
    ]
    if related:
        return "relative", related
    if affected_parent and affected_parent in (father, mother):
        return "relative", [affected_parent]
    return None


def _ngsbits_sex(root: Path, sample_id: str) -> str | None:
    text_value = _read_package_file(root, f"ngsbits/{sample_id}.tsv", kind="ngs-bits sex check")
    if not text_value:
        return None
    inferred = parse_ngsbits_sample_gender_text(text_value).get("inferred_sex")
    return inferred if inferred in {"male", "female"} else None


def pgt_family_block(
    *,
    root: Path,
    family_id: str,
    ped: ParsedPed,
    roles: dict[str, str],
    affected_parent: str | None = None,
) -> tuple[dict[str, Any], list[FamilyImportValidationIssue]]:
    """The manifest ``family`` block the pipeline's roles call for, and the warnings for
    what the user still has to supply.

    - An embryo gets the embryo role. The PED alone cannot say so: the pipeline writes the
      embryos' sex into it, and CoGA reads a child of the couple as an embryo only when
      its sex is unknown.
    - An index that is in the PED as a child of the couple is its proband; any other
      index in the PED is a relative.
    - An index the PED lacks is added, with the sex ngs-bits read (unknown without it) and
      no clinical status: discovery records the run's indexes under
      ``metadata.pgt.indexes``, and the import derives their status from the inheritance
      model. None of the pipeline's files says how it is related, so the link is proposed
      from KING (see :func:`_index_link`): as the couple's child (the proband) when KING
      measures it first-degree to both, otherwise as a relative of unknown degree
      (``family.relationships.relatives``) of the parent it is related to, or of the
      affected parent. The warning says which, and on what.
    """
    members_by_id = {member.iid: member for member in ped.members}
    fathers = {member.pid for member in ped.members if member.pid not in {"", "0"}}
    mothers = {member.mid for member in ped.members if member.mid not in {"", "0"}}
    # The couple: the embryos' parents (a PED may hold grandparents too), else the PED's
    # only father and mother.
    embryo_parents = {
        (member.pid, member.mid)
        for member in ped.members
        if roles.get(member.iid) == "embryo" and member.pid not in {"", "0"} and member.mid not in {"", "0"}
    }
    if len(embryo_parents) == 1:
        father, mother = next(iter(embryo_parents))
    else:
        father = next(iter(fathers)) if len(fathers) == 1 else None
        mother = next(iter(mothers)) if len(mothers) == 1 else None
    overrides: dict[str, dict[str, Any]] = {}
    added: list[dict[str, Any]] = []
    relatives: list[dict[str, Any]] = []
    warnings: list[FamilyImportValidationIssue] = []
    for sample_id, role in sorted(roles.items()):
        member = members_by_id.get(sample_id)
        if member is None:
            if role != "index":
                warnings.append(
                    _issue(
                        "pgt_sample_not_in_ped",
                        f"{PGT_SAMPLESHEET} lists {role} {sample_id}, which is not in the PED; "
                        "it is not imported",
                        sample_id=sample_id,
                    )
                )
                continue
            sex = _ngsbits_sex(root, sample_id)
            # Against the couple: that is where the index has to be linked.
            king_path, pairs = _kinship_pairs(root, family_id, sample_id, (fathers | mothers) or set(members_by_id))
            evidence = _kinship_evidence(sample_id, king_path, pairs)
            link = _index_link(father=father, mother=mother, pairs=pairs, affected_parent=affected_parent)
            entry: dict[str, Any] = {"sample_id": sample_id, "sex": sex or "unknown"}
            sex_note = f", with the sex ngs-bits read ({sex})" if sex else ""
            if link is not None and link[0] == "child":
                entry.update({"father": father, "mother": mother, "role": "proband"})
                message = (
                    f"Index {sample_id} ({PGT_SAMPLESHEET}) is not in the PED; it is added under "
                    f"family.add_members as the couple's child, the proband{sex_note}, because KING "
                    "measured it first-degree to both parents."
                )
            elif link is not None:
                entry["role"] = "relative"
                relatives.append({"member": sample_id, "related_to": link[1]})
                basis = (
                    "the parent KING measured it related to"
                    if any(parent in pairs and pairs[parent]["kinship"] > KINSHIP_THIRD_DEGREE for parent in link[1])
                    else "the affected parent the run traced, as KING did not measure it related to either parent"
                )
                message = (
                    f"Index {sample_id} ({PGT_SAMPLESHEET}) is not in the PED; it is added under "
                    f"family.add_members as a relative{sex_note}, linked under "
                    f"family.relationships.relatives to {' and '.join(link[1])} by an unknown "
                    f"degree: {basis}. Its haplotype is coloured along the genome if it turns "
                    f"out to be {' and '.join(link[1])}'s parent or child (sharing one of the "
                    "haplotypes along nearly every chromosome), and a more distant relative "
                    "around the ROI, where it shares one of them on both sides of it."
                )
            else:
                entry["role"] = "relative"
                message = (
                    f"Index {sample_id} ({PGT_SAMPLESHEET}) is not in the PED; it is added under "
                    f"family.add_members as a relative without parents{sex_note}. Declare how it "
                    "is related under family.relationships before writing the manifest, or on the "
                    "family page after the import: until then its haplotype stays grey and is not "
                    "used to find the risk haplotype."
                )
            added.append(entry)
            message = f"{message} {_INDEX_STATUS_NOTE}"
            if link is not None:
                message = f"{message} Change the link before writing the manifest, or on the family page after the import."
            if evidence:
                message = f"{message} {evidence}"
            warnings.append(_issue("pgt_index_added", message, sample_id=sample_id))
            continue
        if role == "embryo":
            overrides[sample_id] = {"role": "embryo"}
        elif role == "index":
            child_of_couple = father is not None and mother is not None and (member.pid, member.mid) == (father, mother)
            overrides[sample_id] = {"role": "proband" if child_of_couple else "relative"}
            linked_in_ped = member.pid not in {"", "0"} or member.mid not in {"", "0"} or sample_id in fathers | mothers
            if not linked_in_ped:
                # In the PED, but linked to no one there: propose a link, as for an index
                # the PED lacks (the couple's child would need its parents in the PED).
                king_path, pairs = _kinship_pairs(root, family_id, sample_id, (fathers | mothers) or set(members_by_id))
                evidence = _kinship_evidence(sample_id, king_path, pairs)
                link = _index_link(father=father, mother=mother, pairs=pairs, affected_parent=affected_parent)
                if link is not None and link[0] == "child":
                    message = (
                        f"Index {sample_id} is in the PED without parents, though KING measured it "
                        "first-degree to both parents: give it the couple as its parents in the PED, "
                        "or link it under family.relationships."
                    )
                elif link is not None:
                    relatives.append({"member": sample_id, "related_to": link[1]})
                    message = (
                        f"Index {sample_id} is in the PED without parents; it is linked under "
                        f"family.relationships.relatives to {' and '.join(link[1])} by an unknown "
                        "degree. Change the link before writing the manifest, or on the family page "
                        "after the import."
                    )
                else:
                    message = (
                        f"Index {sample_id} is in the PED without parents and nothing says how it is "
                        "related: declare it under family.relationships, or its haplotype stays grey "
                        "and is not used to find the risk haplotype."
                    )
                if evidence:
                    message = f"{message} {evidence}"
                warnings.append(_issue("pgt_index_unlinked", message, sample_id=sample_id))
        elif (role == "father" and sample_id not in fathers) or (
            role == "mother" and sample_id not in mothers
        ):
            warnings.append(
                _issue(
                    "pgt_role_mismatch",
                    f"{PGT_SAMPLESHEET} lists {sample_id} as the {role}, but the PED gives it "
                    "no children; the PED is used",
                    sample_id=sample_id,
                )
            )
    block: dict[str, Any] = {}
    if overrides:
        block["members"] = overrides
    if added:
        block["add_members"] = added
    if relatives:
        block["relationships"] = {"relatives": relatives}
    return block, warnings


def _latest_params(root: Path) -> tuple[str, dict[str, Any]] | None:
    """The newest ``pipeline_info/params_*.json`` (the run that wrote the outputs) and its
    contents. A resumed run writes one per launch; the timestamped names sort by time."""
    folder = root / "pipeline_info"
    if not folder.is_dir():
        return None
    candidates = sorted(path.name for path in folder.glob("params_*.json") if path.is_file())
    if not candidates:
        return None
    relative_path = f"pipeline_info/{candidates[-1]}"
    text_value = _read_package_file(root, relative_path, kind="Pipeline params")
    if not text_value:
        return None
    try:
        payload = json.loads(text_value)
    except json.JSONDecodeError:
        return None
    return (relative_path, payload) if isinstance(payload, dict) else None


def pgt_run_context(
    root: Path, *, sample_ids: list[str], parent_ids: set[str]
) -> tuple[str | None, str | None, list[FamilyImportValidationIssue]]:
    """The region the run analysed (its ``roi`` parameter, for the manifest's ``roi``),
    the parent whose haplotype it traced (``affected_parent``, for the manifest's
    ``metadata.pgt.affected_parents``) when that is a parent in the PED, and a warning
    about that parent.

    The run does not record the inheritance model, which decides whether the affected
    parent is recorded as affected or as a carrier; the warning asks for it.
    """
    latest = _latest_params(root)
    if latest is None:
        return None, None, []
    relative_path, params = latest
    roi = params.get("roi")
    roi_value = roi.strip() if isinstance(roi, str) and roi.strip() else None
    warnings: list[FamilyImportValidationIssue] = []
    affected_parent = params.get("affected_parent")
    traced_parent: str | None = None
    if isinstance(affected_parent, str) and affected_parent.strip():
        parent = affected_parent.strip()
        if parent in parent_ids:
            traced_parent = parent
            message = (
                f"The pipeline traced the disease haplotype of {parent} (affected_parent in "
                f"{relative_path}), recorded under metadata.pgt.affected_parents. With "
                "metadata.pgt.inheritance_model set (AD, AR, XLD or XLR), the import records "
                f"{parent} as affected or as a proven carrier, as the model asks; a status "
                "under family.members wins."
            )
        elif parent in sample_ids:
            message = (
                f"The pipeline's affected_parent ({parent}, {relative_path}) is no one's "
                "parent in the PED, so it is not recorded as the affected parent."
            )
        else:
            message = (
                f"The pipeline's affected_parent ({parent}, {relative_path}) is not a member "
                "of this family."
            )
        warnings.append(_issue("pgt_affected_parent", message, sample_id=parent))
    return roi_value, traced_parent, warnings
