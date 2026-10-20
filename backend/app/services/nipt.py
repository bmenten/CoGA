"""Monogenic NIPT domain model and family resolution.

Monogenic NIPT analyses cell-free DNA from maternal plasma against a paternal
sample, modelled as a trio (father, mother, fetus) backed by only two physical
samples: the father's germline VCF and the maternal-plasma cfDNA (a mixture of
maternal and fetal signal). The fetus is never sequenced directly; its genotype
is inferred downstream from the cfDNA allele fraction relative to the fetal
fraction.

This module holds the tagging conventions and the resolver that turns a family
record into the two samples the analysis needs. See docs/monogenic-nipt.md for
the full design.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..schemas import FamilyMemberOut, FamilyOut

# Family.metadata["analysis_type"] == this marks a monogenic NIPT family and is
# what surfaces the NIPT analysis section in the UI.
FAMILY_ANALYSIS_TYPE_KEY = "analysis_type"
MONOGENIC_NIPT_ANALYSIS_TYPE = "monogenic_nipt"

# Sample.metadata["assay"] == this marks the maternal-plasma cfDNA sample (the
# maternal + fetal mixture), uploaded as the mother member's sample.
SAMPLE_ASSAY_KEY = "assay"
NIPT_CFDNA_ASSAY = "nipt_cfdna"

# Sample.metadata["assay_panel"] (optional) names the capture panel/chemistry,
# which scopes the recurrent-artifact list. Absent -> the default scope.
SAMPLE_ASSAY_PANEL_KEY = "assay_panel"
DEFAULT_NIPT_ASSAY_KEY = NIPT_CFDNA_ASSAY


@dataclass(slots=True)
class NiptTrio:
    """The samples a monogenic NIPT analysis runs on.

    ``cfdna_sample_id`` is the maternal-plasma mixture (the mother member's
    sample). ``fetus_sample_id`` is the placeholder embryo node, which carries no
    uploaded variant data and may be absent when the fetus is purely inferred.
    """

    father_sample_id: str
    cfdna_sample_id: str
    fetus_sample_id: str | None = None


def is_monogenic_nipt_family(family: FamilyOut) -> bool:
    """True when the family is tagged for monogenic NIPT analysis."""
    metadata = family.metadata or {}
    return metadata.get(FAMILY_ANALYSIS_TYPE_KEY) == MONOGENIC_NIPT_ANALYSIS_TYPE


def is_cfdna_sample_metadata(metadata: Mapping[str, Any] | None) -> bool:
    """True when a sample's metadata tags it as the maternal-plasma cfDNA."""
    return (metadata or {}).get(SAMPLE_ASSAY_KEY) == NIPT_CFDNA_ASSAY


def is_cfdna_member(member: FamilyMemberOut) -> bool:
    """True when the member's sample is tagged as the maternal-plasma cfDNA."""
    return is_cfdna_sample_metadata(member.sample_metadata)


def resolve_nipt_trio(family: FamilyOut) -> NiptTrio | None:
    """Resolve the father / cfDNA / fetus samples for a NIPT family.

    Returns ``None`` when the family is not tagged for monogenic NIPT or the
    required father and cfDNA samples cannot both be identified.

    The cfDNA sample is identified by its ``assay`` tag, falling back to the
    active ``mother`` member when no sample carries the tag (e.g. a family
    created before the assay was recorded). The father is the active ``father``
    member; the fetus is the active ``embryo`` member, or the ``proband`` child
    when no ``embryo`` is present (manual family creation labels the child
    ``proband``), if present at all.
    """
    if not is_monogenic_nipt_family(family):
        return None
    return resolve_nipt_trio_from_members(
        (member.sample_id, member.role, member.sample_metadata)
        for member in family.members
        if member.active
    )


def resolve_nipt_trio_from_members(
    members: Iterable[tuple[str, str | None, Mapping[str, Any] | None]],
) -> NiptTrio | None:
    """The trio of a monogenic NIPT family from its active members, each as
    ``(sample_id, role, sample_metadata)``, by the rules of ``resolve_nipt_trio``; None
    when the father and the cfDNA sample cannot both be identified. The signed record
    resolves the trio from the family's metadata context with it (``signed_record_nipt``).
    """
    father_sample_id: str | None = None
    cfdna_sample_id: str | None = None
    mother_sample_id: str | None = None
    fetus_sample_id: str | None = None

    for sample_id, role, sample_metadata in members:
        if is_cfdna_sample_metadata(sample_metadata):
            cfdna_sample_id = sample_id
            continue
        if role == "father":
            father_sample_id = sample_id
        elif role == "mother":
            mother_sample_id = sample_id
        elif role == "embryo":
            fetus_sample_id = sample_id
        elif role == "proband" and fetus_sample_id is None:
            fetus_sample_id = sample_id

    if cfdna_sample_id is None:
        cfdna_sample_id = mother_sample_id

    if father_sample_id is None or cfdna_sample_id is None:
        return None

    return NiptTrio(
        father_sample_id=father_sample_id,
        cfdna_sample_id=cfdna_sample_id,
        fetus_sample_id=fetus_sample_id,
    )


def assay_key_for_sample_metadata(metadata: dict | None) -> str:
    """The artifact-list scope of a cfDNA sample: its ``assay_panel``, else the default."""
    panel = (metadata or {}).get(SAMPLE_ASSAY_PANEL_KEY)
    if isinstance(panel, str) and panel.strip():
        return panel.strip()
    return DEFAULT_NIPT_ASSAY_KEY


def nipt_assay_key(family: FamilyOut, trio: NiptTrio) -> str:
    """The artifact-list scope key for the family's cfDNA assay/panel.

    Read from the cfDNA sample's ``assay_panel`` metadata so labs can keep a
    panel-specific artifact list; absent, all NIPT families on the assembly
    share the default scope.
    """
    for member in family.members:
        if member.sample_id == trio.cfdna_sample_id:
            return assay_key_for_sample_metadata(member.sample_metadata)
    return DEFAULT_NIPT_ASSAY_KEY
