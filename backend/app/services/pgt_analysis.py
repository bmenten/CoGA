"""The PGT analysis's embryo results at the region of interest, read from the stored data.

``family_pgt_analysis`` reads what the embryo calls rest on: the family's ROI and inheritance
model, its members' roles, sexes and statuses, the haplotype blocks over the ROI and 1 Mb on
each side as ``GET /families/{family_id}/haplotypes`` serves them (lineage-tagged, each block
marked one-copy in males or not), and the parents' phase corrections. It calls each embryo
with ``pgt_embryo_calls`` and adds each embryo's phasing QC at the ROI (the informative sites
and Mendelian errors the ROI marker review counts, REQ-PGT-004), that QC's verdict against the
thresholds of ``pgt_embryo_qc`` (a warning beside the call, never a gate), and the inputs'
identity: the haplotype files each member's blocks were built from, and a digest of the
blocks read.

The family page shows these calls (``GET /families/{family_id}/pgt-analysis``), and a sign-out
of a PGT family freezes the same object into the signed record (``signed_record_sections``),
so the record holds the calls the analyst saw, made by one implementation from stored data.
Deterministic: every list is sorted, and nothing reads the clock.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import (
    PgtAnalysisOut,
    PgtDiseaseHaplotypeOut,
    PgtEmbryoOut,
    PgtEmbryoQcOut,
    PgtHaplotypeSourceOut,
    PgtInheritanceModelOut,
    PgtInheritedHaplotypeOut,
    PgtInputsOut,
    PgtMemberOut,
    PgtPhaseCorrectionOut,
    PgtRegionOfInterestOut,
    PgtRulesOut,
    PgtSexDependentCallOut,
    PgtWindowOut,
)
from .bed_service import get_family_haplotypes_response, haplotype_source_rows
from .haplotype_block_builder import blocks_outdated
from .family_metadata_context import FamilyMetadataContext
from .pgt_embryo_calls import (
    PHASE_SWITCH_UNCERTAINTY,
    ROI_RECOMBINATION_FLANK,
    ROI_WINDOW_FLANK,
    EmbryoCall,
    Region,
    call_embryos,
    is_embryo,
    normalize_inheritance,
    phase_corrections_near,
    resolve_inheritance_model,
)
from .pgt_embryo_qc import QC_THRESHOLDS, embryo_qc_verdict
from .phased_marker_service import get_family_phased_markers_response

RULES = PgtRulesOut(
    window_flank=ROI_WINDOW_FLANK,
    recombination_flank=ROI_RECOMBINATION_FLANK,
    phase_switch_uncertainty=PHASE_SWITCH_UNCERTAINTY,
    qc_thresholds=QC_THRESHOLDS,
)

# What the QC verdict added to the analysis on 2026-10-20: each embryo's ``qc_verdict`` and the
# thresholds in ``rules``. A record signed before holds neither (``comparable_with_record``).
QC_VERDICT_NOT_COMPARED = "pgt_analysis.qc_verdict"

# What the outdated-blocks check added on 2026-10-27: ``inputs.blocks_outdated``. A record signed
# before holds none (``comparable_with_record``).
BLOCKS_OUTDATED_NOT_COMPARED = "pgt_analysis.inputs.blocks_outdated"

# A block as the calls read it: what the digest of the blocks read covers.
_BLOCK_FIELDS = ("chr", "start", "end", "hap1", "hap2", "ps", "hap1_lineage", "hap2_lineage", "hemizygous_in_males")

_FAMILY_SQL = text(
    """
    SELECT roi_query, roi_label, roi_source, roi_chr, roi_start, roi_end,
           metadata -> 'pgt' AS pgt
    FROM families
    WHERE id = CAST(:family_uuid AS uuid)
    """
)

def is_pgt_family(context: FamilyMetadataContext | Any) -> bool:
    """A family with an embryo among its members: the family page calls its embryos."""
    return any(is_embryo(row) for row in getattr(context, "sample_rows", None) or [])


def _text(value: Any) -> str | None:
    text_value = str(value).strip() if value is not None else ""
    return text_value or None


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _recorded_inheritance_model(pgt: Any) -> str | None:
    if isinstance(pgt, str):
        # jsonb decodes through the asyncpg codec; a driver that hands back text reads the same.
        try:
            pgt = json.loads(pgt)
        except ValueError:
            return None
    if not isinstance(pgt, Mapping):
        return None
    return _text(pgt.get("inheritance_model"))


def _region_of_interest(row: Mapping[str, Any] | None) -> PgtRegionOfInterestOut | None:
    """The ROI as the family page has it: set when it has a query and a locus."""
    if not row or not _text(row.get("roi_query")):
        return None
    if row.get("roi_chr") is None or row.get("roi_start") is None or row.get("roi_end") is None:
        return None
    query = str(row["roi_query"])
    return PgtRegionOfInterestOut(
        query=query,
        label=_text(row.get("roi_label")) or query,
        source=_text(row.get("roi_source")),
        chrom=str(row["roi_chr"]),
        start=int(row["roi_start"]),
        end=int(row["roi_end"]),
    )


def _member(row: Mapping[str, Any]) -> PgtMemberOut:
    return PgtMemberOut(
        sample=str(row.get("sample_id")),
        role=_text(row.get("role")),
        sex=_text(row.get("sex")),
        affected=bool(row.get("affected")),
        clinical_status=_text(row.get("clinical_status")),
        carrier_status=_text(row.get("carrier_status")),
        carrier_type=_text(row.get("carrier_type")),
    )


def _haplotype_sources(context: FamilyMetadataContext, rows: Sequence[Mapping[str, Any]]) -> list[PgtHaplotypeSourceOut]:
    """The files each member's stored haplotype blocks were built from."""
    sources = [
        PgtHaplotypeSourceOut(
            sample=str(row["sample"]),
            source=str(row.get("source") or ""),
            filename=str(row.get("filename") or ""),
            blocks=int(row.get("row_count") or 0),
            loaded_at=(
                row["uploaded_at"].isoformat()
                if isinstance(row.get("uploaded_at"), datetime)
                else _text(row.get("uploaded_at"))
            ),
        )
        for row in rows
    ]
    return sorted(sources, key=lambda source: (source.sample, source.source, source.filename))


def _canonical_samples(samples: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each member's blocks, in a fixed order: the calls and the digest do not depend on the
    order the store returned them in."""
    return [
        {
            "sample": str(sample.get("sample")),
            "segments": sorted(
                ({field: segment.get(field) for field in _BLOCK_FIELDS} for segment in sample.get("segments") or []),
                key=lambda block: (int(block["start"]), int(block["end"]), _canonical(block)),
            ),
        }
        for sample in samples
    ]


def _blocks_digest(samples: Sequence[Mapping[str, Any]]) -> str | None:
    blocks = sorted(
        (
            {"sample": sample["sample"], **segment}
            for sample in samples
            for segment in sample["segments"]
        ),
        key=_canonical,
    )
    if not blocks:
        return None
    return hashlib.sha256(_canonical(blocks).encode("utf-8")).hexdigest()


def _not_assessed(row: Mapping[str, Any]) -> PgtEmbryoOut:
    return PgtEmbryoOut(sample=str(row.get("sample_id")), sex=_text(row.get("sex")))


def _embryo(
    row: Mapping[str, Any], call: EmbryoCall, qc: PgtEmbryoQcOut | None
) -> PgtEmbryoOut:
    assessment = call.assessment
    return PgtEmbryoOut(
        sample=call.sample,
        sex=_text(row.get("sex")),
        call=assessment.state,
        roi_not_covered=assessment.roi_not_covered,
        sex_dependent=(
            PgtSexDependentCallOut(
                if_male=assessment.sex_dependent.if_male,
                if_female=assessment.sex_dependent.if_female,
            )
            if assessment.sex_dependent is not None
            else None
        ),
        inherited=sorted(
            (
                PgtInheritedHaplotypeOut(
                    origin=homolog.signature.origin,
                    homolog=homolog.signature.value,
                    start=homolog.start,
                    end=homolog.end,
                )
                for homolog in call.inherited
            ),
            key=lambda homolog: (homolog.start, homolog.end, homolog.origin, homolog.homolog),
        ),
        recombinations=sorted(call.recombinations),
        qc=qc,
        # Against the thresholds the analysis states in its rules.
        qc_verdict=embryo_qc_verdict(qc, call.sides, RULES.qc_thresholds),
    )


async def _embryo_qc(
    context: FamilyMetadataContext, roi: PgtRegionOfInterestOut
) -> tuple[dict[str, PgtEmbryoQcOut], bool]:
    """Each child's phasing QC over the ROI, as the ROI marker review reads it, and whether
    the ROI held more sites than that read takes (then none is counted)."""
    markers = await get_family_phased_markers_response(
        context, chr=roi.chrom, start=max(0, roi.start), end=roi.end
    )
    by_sample: dict[str, PgtEmbryoQcOut] = {}
    for sample in markers.samples:
        if sample.qc is None:
            continue
        by_sample[sample.sample] = PgtEmbryoQcOut(
            informative_sites=sample.qc.informative_sites,
            mendel_errors=sample.qc.mendel_errors,
            mendel_rate=sample.qc.mendel_rate,
            paternal_markers=sum(1 for marker in sample.markers if marker.hap1 is not None),
            maternal_markers=sum(1 for marker in sample.markers if marker.hap2 is not None),
        )
    return by_sample, bool(markers.truncated)


async def family_pgt_analysis(session: AsyncSession, context: FamilyMetadataContext) -> PgtAnalysisOut:
    """The disease haplotype at the family's ROI and each embryo's call against it."""
    found = (await session.execute(_FAMILY_SQL, {"family_uuid": context.family_uuid})).mappings().first()
    family: dict[str, Any] | None = dict(found) if found is not None else None
    rows = list(context.sample_rows)
    recorded = _recorded_inheritance_model(family.get("pgt") if family else None)
    applied = resolve_inheritance_model(recorded, rows)
    roi = _region_of_interest(family)
    embryo_rows = sorted((row for row in rows if is_embryo(row)), key=lambda row: str(row.get("sample_id")))
    source_rows = await haplotype_source_rows(session, context)
    analysis = PgtAnalysisOut(
        region_of_interest=roi,
        inheritance_model=PgtInheritanceModelOut(
            recorded=recorded, applied=applied, mode=normalize_inheritance(applied)
        ),
        members=sorted((_member(row) for row in rows), key=lambda member: member.sample),
        embryos=[_not_assessed(row) for row in embryo_rows],
        inputs=PgtInputsOut(
            haplotype_sources=_haplotype_sources(context, source_rows),
            blocks_outdated=blocks_outdated(context, source_rows),
        ),
        rules=RULES,
    )
    if roi is None:
        return analysis
    window = PgtWindowOut(chrom=roi.chrom, start=max(0, roi.start - ROI_WINDOW_FLANK), end=roi.end + ROI_WINDOW_FLANK)
    haplotypes = await get_family_haplotypes_response(
        session, context=context, chr=roi.chrom, start=window.start, end=window.end
    )
    samples = _canonical_samples([sample.model_dump() for sample in haplotypes.samples])
    analysis.window = window
    analysis.inputs.blocks_read = sum(len(sample["segments"]) for sample in samples)
    analysis.inputs.blocks_digest = _blocks_digest(samples)
    if not samples:
        # No haplotypes to read (no assembly, or no member): no embryo is called.
        return analysis
    if analysis.inputs.blocks_outdated:
        # Built for another pedigree or other sexes (CLIN-66): every embryo is uninformative
        # until the phased genotypes are imported again, which the page and the record say.
        analysis.embryos = [
            PgtEmbryoOut(sample=str(row.get("sample_id")), sex=_text(row.get("sex")), call="uninformative")
            for row in embryo_rows
        ]
        return analysis
    region = Region(roi.chrom, roi.start, roi.end)
    model, calls = call_embryos(members=rows, samples=samples, inheritance_model=recorded, region=region)
    qc_by_sample, truncated = await _embryo_qc(context, roi)
    call_by_sample = {call.sample: call for call in calls}
    analysis.disease_haplotype_resolved = model.informative
    analysis.disease_haplotypes = [
        PgtDiseaseHaplotypeOut(origin=signature.origin, homolog=signature.value, kind=signature.kind)
        for signature in model.signatures
    ]
    analysis.phase_corrections = sorted(
        (PgtPhaseCorrectionOut(**correction) for correction in phase_corrections_near(context.phase_corrections, region)),
        key=lambda correction: (correction.chr, correction.position, correction.parent),
    )
    analysis.embryos = [
        _embryo(row, call_by_sample[str(row.get("sample_id"))], qc_by_sample.get(str(row.get("sample_id"))))
        for row in embryo_rows
    ]
    analysis.inputs.markers_truncated = truncated
    return analysis


def comparable_with_record(signed: Any, current: Any) -> tuple[Any, list[str]]:
    """The current analysis to compare with a signed record's ``pgt_analysis``, and what of it
    the record predates.

    A record signed before CoGA checked each embryo's phasing QC against thresholds holds no
    ``qc_verdict`` and no ``rules.qc_thresholds``, and one signed before the outdated-blocks
    check no ``inputs.blocks_outdated`` (``BLOCKS_OUTDATED_NOT_COMPARED``). Like a section a record predates, these are
    left out of the comparison and named as not compared (``QC_VERDICT_NOT_COMPARED``), rather
    than every such record reading as changed; the rest of the analysis is compared on what the
    record holds. A record that holds them is compared whole, so a verdict or a threshold that
    changed since it was signed reads as a change.
    """
    if not isinstance(signed, Mapping) or not isinstance(current, Mapping):
        return current, []
    comparable = dict(current)
    not_compared: list[str] = []
    signed_rules = signed.get("rules")
    if isinstance(signed_rules, Mapping) and "qc_thresholds" not in signed_rules:
        predated = False
        rules = comparable.get("rules")
        if isinstance(rules, Mapping) and "qc_thresholds" in rules:
            comparable["rules"] = {key: value for key, value in rules.items() if key != "qc_thresholds"}
            predated = True
        embryos = comparable.get("embryos")
        if isinstance(embryos, list):
            held = [
                {key: value for key, value in embryo.items() if key != "qc_verdict"}
                if isinstance(embryo, Mapping)
                else embryo
                for embryo in embryos
            ]
            predated = predated or held != embryos
            comparable["embryos"] = held
        if predated:
            not_compared.append(QC_VERDICT_NOT_COMPARED)
    # A record signed before the outdated-blocks check (CLIN-66) holds no blocks_outdated: the
    # flag is left out of the comparison and named as not compared. Blocks outdated now leave
    # every embryo uninformative, which the embryos' comparison sees.
    signed_inputs = signed.get("inputs")
    inputs = comparable.get("inputs")
    if (
        isinstance(signed_inputs, Mapping)
        and "blocks_outdated" not in signed_inputs
        and isinstance(inputs, Mapping)
        and "blocks_outdated" in inputs
    ):
        comparable["inputs"] = {key: value for key, value in inputs.items() if key != "blocks_outdated"}
        not_compared.append(BLOCKS_OUTDATED_NOT_COMPARED)
    return comparable, not_compared
