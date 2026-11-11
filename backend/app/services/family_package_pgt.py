"""The PGT pipeline's (nf-cmgg/copgtm) description of a family, read when a manifest is
discovered.

The family's pedigree is the PED at the top of the package folder (``<family>.ped``): the
couple, their embryos and the index, the relative whose haplotypes tell the affected parent's
two haplotypes apart. The pipeline's own PED (``ped/combined.ped``) is not read: it lacks the
index and gives the embryos the sex ngs-bits read, which a PED cannot tell from a born child's.
A PGT family's PED marks its embryos with a ``role=embryo`` column after the sixth (or gives
them sex 0), and links an index the couple did not have to the parent it is related through
with a ``related_to=`` column (:func:`pgt_ped_indexes`).

The run's own parameters give the region it analysed and the parent whose haplotype it traced
(:func:`pgt_run_context`). Nothing here is read at import: the import reads only the manifest,
which the user can correct first.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any

from fastapi import HTTPException
import pysam

from ..schemas import FamilyImportValidationIssue
from .data_scope import normalize_chromosome
from .family_package_common import ParsedPed, _issue, _resolve_package_path, package_path_within
from .family_package_manifest import _ped_embryo_sample_ids, ped_related_to
from .upload_safety import read_path_text_bounded


logger = logging.getLogger(__name__)


_INDEX_STATUS_NOTE = (
    "It is recorded under metadata.pgt.indexes: with metadata.pgt.inheritance_model set, the "
    "import records it as affected (under XLR, a female index as a proven carrier), as the "
    "risk haplotype is found from it; a clinical status in the PED's sixth column wins."
)


def _read_package_file(root: Path, relative_path: str, *, kind: str) -> str | None:
    """A small package file's text, or None when it is absent or unreadable. A path that leads
    out of the package is never looked up (``package_path_within``)."""
    path = package_path_within(root, relative_path)
    if path is None or not path.is_file():
        return None
    try:
        return read_path_text_bounded(path, kind=kind)
    except (HTTPException, OSError, UnicodeDecodeError):
        # Named by its kind, not its path: a log line never carries a package's paths.
        logger.warning("Could not read the package's %s file", kind)
        return None


def _known(parent_id: str) -> bool:
    return parent_id not in {"", "0"}


def pgt_ped_indexes(ped: ParsedPed) -> tuple[dict[str, str], list[str], list[FamilyImportValidationIssue]]:
    """A PGT family's indexes as its PED holds them: the role each index the PED gives none
    gets, the indexes (for ``metadata.pgt.indexes``), and a warning per index.

    The embryos are the members the PED marks as embryos (``role=embryo``, or a child of a
    couple of unknown sex); the couple are their parents. Every other member who is no one's
    parent is an index: a member who is someone's parent is in the PED to link a relative
    through (the affected mother's parents, above her sister). The couple's child is the
    proband; any other index is a relative, linked through its parents in the PED or through
    the members its ``related_to=`` column names. An index linked neither way is warned about:
    its haplotype stays grey and is not used to find the risk haplotype until it is linked.

    Nothing when the PED holds no embryo: then it is no PGT family's.
    """
    embryos = _ped_embryo_sample_ids(ped)
    if not embryos:
        return {}, [], []
    parents = {parent for member in ped.members for parent in (member.pid, member.mid) if _known(parent)}
    couples = {
        (member.pid, member.mid)
        for member in ped.members
        if member.iid in embryos and _known(member.pid) and _known(member.mid)
    }
    couple = next(iter(couples)) if len(couples) == 1 else None
    roles: dict[str, str] = {}
    indexes: list[str] = []
    warnings: list[FamilyImportValidationIssue] = []
    for member in ped.members:
        if member.iid in embryos or member.iid in parents:
            continue
        indexes.append(member.iid)
        child_of_couple = couple is not None and (member.pid, member.mid) == couple
        related_to = ped_related_to(member)
        if member.role_hint is None:
            roles[member.iid] = "proband" if child_of_couple else "relative"
        role = member.role_hint or roles[member.iid]
        if child_of_couple:
            how = f"the couple's child, as {role}"
        elif related_to:
            how = f"a {role} linked through {' and '.join(related_to)} by an unknown degree (related_to)"
        elif _known(member.pid) or _known(member.mid):
            how = f"a {role} linked through its parents in the PED"
        else:
            warnings.append(
                _issue(
                    "pgt_index_unlinked",
                    f"Index {member.iid} is in the PED without parents and without a related_to column, "
                    "so nothing says how it is related: its haplotype stays grey and is not used to find "
                    "the risk haplotype. Give it its parents in the PED, or name the member it is related "
                    "through in a column after the sixth (related_to=<the parent's ID>); or link it with "
                    "Correct... in Deliveries, or on the family page after the import.",
                    sample_id=member.iid,
                )
            )
            continue
        warnings.append(
            _issue("pgt_index", f"Index {member.iid} is {how}. {_INDEX_STATUS_NOTE}", sample_id=member.iid)
        )
    return roles, indexes, warnings


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


# A region as the run's ``roi`` parameter writes it (``chr7:1000-2000``). A gene name is
# resolved when the family is registered, and is not read here.
_ROI_REGION = re.compile(r"^\s*([^\s:]+)\s*:\s*[\d,]+\s*-\s*[\d,]+\s*$")


def _chromosome_order(contig: str) -> tuple[int, int, str]:
    # An index lists its sequences as text sorts (chr1, chr10, ...): numbered ones go in number order.
    name = normalize_chromosome(contig)
    return (0, int(name), "") if name.isdigit() else (1, 0, name)


def pgt_roi_phasing_issue(
    root: Path, *, roi: str | None, family_vcf: str | None, index: str | None
) -> FamilyImportValidationIssue | None:
    """A warning when the phased family VCF holds no record on the ROI's chromosome, as its
    index lists them: the embryos cannot then be called at the ROI. The PGT pipeline goes on
    when it fails to phase a chromosome (SHAPEIT5 on chrX, say), and that chromosome is
    left out of the VCF. None when the ROI is not a region, or the index cannot be read."""
    match = _ROI_REGION.match(roi or "")
    if match is None or not family_vcf:
        return None
    try:
        vcf_path = _resolve_package_path(root, family_vcf)
        index_path = _resolve_package_path(root, index)
    except HTTPException:
        return None
    if vcf_path is None or not vcf_path.is_file():
        return None
    try:
        with pysam.TabixFile(str(vcf_path), index=str(index_path) if index_path is not None else None) as tabix:
            contigs = list(tabix.contigs)
    except (OSError, ValueError):
        return None
    roi_chromosome = normalize_chromosome(match.group(1)).upper()
    if not contigs or roi_chromosome in {normalize_chromosome(contig).upper() for contig in contigs}:
        return None
    contigs.sort(key=_chromosome_order)
    return _issue(
        "haplotypes_roi_chromosome_missing",
        f"The phased family VCF ({family_vcf}) holds no record on {match.group(1)}, where the ROI "
        f"({roi.strip() if roi else ''}) lies, so no embryo can be called at the ROI: it holds "
        f"{', '.join(contigs)}. A failed phasing step leaves its chromosome out; the run's "
        "pipeline_info/execution_trace_*.txt lists the failed tasks.",
        dataset="haplotypes",
    )
