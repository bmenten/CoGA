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
from typing import Any, Collection, Iterable, Mapping, Sequence

from ..schemas import FamilyOut
from .analysis_scope import AnalysisNotNamed
from .family_analysis_types import (
    FETUS_ROLE,
    analysis_type_of,
    family_analyses_of_type,
    family_analysis_types,
    sample_assay_panel,
    sample_assays,
)
from .family_package_records import analysis_label, analysis_members

# A family whose analysis types (``family_analysis_types``) hold this is a monogenic NIPT family:
# the UI shows its NIPT pages. It holds one monogenic NIPT per pregnancy (its key names the
# pregnancy: ``monogenic_nipt:ZW1``).
MONOGENIC_NIPT_ANALYSIS_TYPE = "monogenic_nipt"
# A family whose analysis types hold this holds a conventional NIPT: the genome-wide copy number of
# a maternal plasma, from the laboratory's ccnv tool (ccnv_output.py), one per pregnancy. One family
# may hold both, of one pregnancy or of several.
CONVENTIONAL_NIPT_ANALYSIS_TYPE = "conventional_nipt"

# The ``assay`` a package names for a sample (coga_package), which the import adds to the
# sample's assays (``family_analysis_types.sample_assays``). A sample whose assays hold this is
# the maternal-plasma cfDNA of a monogenic NIPT (the maternal + fetal mixture), the mother
# member's sample.
SAMPLE_ASSAY_KEY = "assay"
NIPT_CFDNA_ASSAY = "nipt_cfdna"
# A sample whose assays hold this is the maternal plasma of a conventional NIPT (shallow
# whole-genome sequencing). The monogenic NIPT's artefact list and recurrence counts read the
# samples whose assays hold ``nipt_cfdna``; one plasma may hold both.
CNIPT_CFDNA_ASSAY = "cnipt_cfdna"

# The capture panel/chemistry the monogenic NIPT's package names for its cfDNA sample (its
# ``assay_panel``, kept as that analysis's in Sample.metadata["assay_panels"],
# family_analysis_types.SAMPLE_ASSAY_PANELS_KEY) scopes the recurrent-artifact list. None named ->
# the default scope. A conventional NIPT of the same plasma names its own, which the list never reads.
DEFAULT_NIPT_ASSAY_KEY = NIPT_CFDNA_ASSAY


# Why a monogenic NIPT family has no trio (``resolve_nipt_trio`` returned None), as the NIPT page
# answers it and the signed record freezes it.
NIPT_TRIO_UNRESOLVED = (
    "The NIPT trio could not be resolved: the family has no active father or no maternal plasma "
    "sample, or has two samples that could be one of them (a parent sampled twice, a plasma of "
    "each pregnancy) and the monogenic NIPT's package lists neither."
)


def multiple_pregnancy_note(fetuses: int) -> str | None:
    """What a NIPT result of a pregnancy whose package names ``fetuses`` fetuses says of them:
    nothing for one (or none), and for a twin or a higher multiple pregnancy that a fetal result
    may be present in one fetus or in more (the owner's decision of 2026-11-21: shown whatever the
    zygosity, which CoGA is not told)."""
    if fetuses < 2:
        return None
    return (
        f"Multiple pregnancy ({fetuses} fetuses): a fetal result may be present in one fetus or in "
        "more than one, and the analysis cannot tell which. This does not apply to a monozygotic "
        "twin, whose fetuses share one genotype. The parents' carrier status is read as in a "
        "singleton pregnancy."
    )


@dataclass(slots=True)
class NiptTrio:
    """The samples a monogenic NIPT analysis of one pregnancy runs on.

    ``cfdna_sample_id`` is the maternal-plasma mixture (the mother member's sample).
    ``fetus_sample_ids`` are the pregnancy's fetuses, placeholder members that carry no
    variant data: none when the fetus is purely inferred, two or more in a multiple pregnancy
    (``multiple_pregnancy_note``). ``analysis`` is the key of the monogenic NIPT the trio is of
    (``monogenic_nipt:ZW1``), None for a family that holds none (made in the Family Builder).
    """

    father_sample_id: str
    cfdna_sample_id: str
    fetus_sample_ids: tuple[str, ...] = ()
    analysis: str | None = None


def is_monogenic_nipt_family(family: FamilyOut) -> bool:
    """True when the family holds a monogenic NIPT analysis."""
    return MONOGENIC_NIPT_ANALYSIS_TYPE in family_analysis_types(family.metadata)


def nipt_analysis_in_scope(
    metadata: Mapping[str, Any] | None, analysis_type: str, scope_analysis: str | None
) -> str | None:
    """The NIPT of ``analysis_type`` a reading of one of the family's analyses
    (``scope_analysis``, its key; None: the whole family) reads: that analysis when it is of
    ``analysis_type``, else the family's only one of that type (``nipt_analysis_of``)."""
    if scope_analysis is not None and analysis_type_of(scope_analysis) == analysis_type:
        return scope_analysis
    return nipt_analysis_of(metadata, analysis_type, None)


def nipt_analysis_of(metadata: Mapping[str, Any] | None, analysis_type: str, asked: str | None) -> str | None:
    """The NIPT of ``analysis_type`` (monogenic or conventional) a reading of the family whose
    ``families.metadata`` is ``metadata`` is of, by its key: ``asked``, which must be one of the
    family's analyses of that type; without one, the family's only one; None when it holds none.
    A family that holds one per pregnancy of several is read one pregnancy at a time: a reading
    that names none of them is refused (``AnalysisNotNamed``), never read across them."""
    held = family_analyses_of_type(metadata, analysis_type)
    if asked is not None:
        asked = asked.strip()
        if asked not in held:
            raise AnalysisNotNamed(f"This family holds no such {analysis_label(analysis_type) or analysis_type}.")
        return asked
    if len(held) > 1:
        names = ", ".join(analysis_label(key) or key for key in held)
        raise AnalysisNotNamed(
            f"This family holds several pregnancies' analyses ({names}); name the one to read (analysis)."
        )
    return held[0] if held else None


def is_maternal_plasma(metadata: Mapping[str, Any] | None) -> bool:
    """True when a sample's assays make it a maternal plasma: a monogenic or a conventional
    NIPT's, a mixture of the mother's and a fetus's DNA."""
    return bool({NIPT_CFDNA_ASSAY, CNIPT_CFDNA_ASSAY} & sample_assays(metadata))


def is_cfdna_sample_metadata(metadata: Mapping[str, Any] | None) -> bool:
    """True when a sample's assays make it the maternal-plasma cfDNA of a monogenic NIPT."""
    return NIPT_CFDNA_ASSAY in sample_assays(metadata)


def resolve_nipt_trio(family: FamilyOut, analysis: str | None) -> NiptTrio | None:
    """Resolve the father / cfDNA / fetus samples of the monogenic NIPT ``analysis`` (its key,
    ``nipt_analysis_of``; None for a family that holds no package of one) of a family.

    Returns ``None`` when the family is not tagged for monogenic NIPT or the
    required father and cfDNA samples cannot both be identified.

    The cfDNA sample is identified by its assays (``nipt_cfdna``), falling back to the
    active ``mother`` member when no sample carries the tag. The father is the active ``father``
    member; the fetuses are the active ``fetus`` members (``pregnancy_fetuses``). Of several
    members that could be the plasma or the father (a mother or a father sampled twice, linked as
    the same individual, or the plasma and the father of each pregnancy), the one the analysis's
    package lists is; the fetuses are those it lists (``resolve_nipt_trio_from_members``).
    """
    if not is_monogenic_nipt_family(family):
        return None
    return resolve_nipt_trio_from_members(
        (
            (member.sample_id, member.role, member.sample_metadata)
            for member in family.members
            if member.active
        ),
        listed=analysis_members(family.metadata, analysis) if analysis is not None else frozenset(),
        analysis=analysis,
    )


_Member = tuple[str, str | None, Mapping[str, Any] | None]


def _holder(candidates: Sequence[_Member], listed: Collection[str], *, plasma: bool) -> str | None:
    """Of the members that could hold the monogenic NIPT's sample of one role, the one that holds
    its data: the only one; else the only one its package lists (``listed``); else, for the
    plasma, the only one of those that is no conventional NIPT's plasma alone (``cnipt_cfdna``).
    None when none or several are left: the trio is not guessed."""
    if len(candidates) == 1:
        return candidates[0][0]
    pool = [member for member in candidates if member[0] in listed] or list(candidates)
    if len(pool) == 1:
        return pool[0][0]
    if plasma:
        pool = [member for member in pool if CNIPT_CFDNA_ASSAY not in sample_assays(member[2])]
        if len(pool) == 1:
            return pool[0][0]
    return None


def pregnancy_fetuses(members: Iterable[_Member], listed: Collection[str]) -> tuple[str, ...]:
    """A pregnancy's fetuses among a family's ``members`` (``(sample_id, role, sample_metadata)``):
    its ``fetus`` members that the pregnancy's package lists (``listed``), or every one for a
    family whose analysis no package listed (one the Family Builder made), in sample order. Two
    or more: a multiple pregnancy (``multiple_pregnancy_note``)."""
    fetuses = [member[0] for member in members if member[1] == FETUS_ROLE]
    return tuple(sorted(sample_id for sample_id in fetuses if not listed or sample_id in listed))


def resolve_nipt_trio_from_members(
    members: Iterable[_Member],
    *,
    listed: Collection[str],
    analysis: str | None = None,
) -> NiptTrio | None:
    """The trio of the monogenic NIPT ``analysis`` (its key) of a family from its active
    members, each as ``(sample_id, role, sample_metadata)``, by the rules of
    ``resolve_nipt_trio``; None when the father and the cfDNA sample cannot both be identified.
    ``listed`` holds the members the analysis's package lists (``analysis_members``): of a
    mother or a father sampled twice, or of the samples of each pregnancy, the sample its package
    read holds its data, and of several samples none of them singles out the trio is not
    resolved. The signed record resolves the trio from the family's metadata context with it
    (``signed_record_nipt``).
    """
    tagged: list[_Member] = []
    fathers: list[_Member] = []
    mothers: list[_Member] = []
    fetal: list[_Member] = []

    for member in members:
        sample_id, role, sample_metadata = member
        if is_cfdna_sample_metadata(sample_metadata):
            tagged.append(member)
            continue
        if role == "father":
            fathers.append(member)
        elif role == "mother":
            mothers.append(member)
        elif role == FETUS_ROLE:
            fetal.append(member)

    father_sample_id = _holder(fathers, listed, plasma=False)
    cfdna_sample_id = _holder(tagged or mothers, listed, plasma=True)
    if father_sample_id is None or cfdna_sample_id is None:
        return None

    return NiptTrio(
        father_sample_id=father_sample_id,
        cfdna_sample_id=cfdna_sample_id,
        fetus_sample_ids=pregnancy_fetuses(fetal, listed),
        analysis=analysis,
    )


def assay_key_for_panel(panel: Any) -> str:
    """The artifact-list scope of a capture panel: the panel, else (none) the default."""
    if isinstance(panel, str) and panel.strip():
        return panel.strip()
    return DEFAULT_NIPT_ASSAY_KEY


def assay_key_for_sample_metadata(metadata: Mapping[str, Any] | None, analysis: str | None) -> str:
    """The artifact-list scope of a cfDNA sample: the capture panel the package of the monogenic
    NIPT ``analysis`` (its key) named for it, else the default."""
    panel = sample_assay_panel(metadata, analysis) if analysis is not None else None
    return assay_key_for_panel(panel)


def nipt_assay_key(family: FamilyOut, trio: NiptTrio) -> str:
    """The artifact-list scope key for the cfDNA assay/panel of the trio's pregnancy.

    Read from the capture panel the trio's monogenic NIPT's package named for the cfDNA sample
    (``assay_key_for_sample_metadata``) so labs can keep a panel-specific artifact list; without
    one, all NIPT families on the assembly share the default scope.
    """
    for member in family.members:
        if member.sample_id == trio.cfdna_sample_id:
            return assay_key_for_sample_metadata(member.sample_metadata, trio.analysis)
    return DEFAULT_NIPT_ASSAY_KEY
