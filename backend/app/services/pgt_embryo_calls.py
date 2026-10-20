"""The PGT embryo call at the region of interest (REQ-PGT-005; REQ-PGT-011's ROI warning).

Pure rules, no I/O: the disease haplotype the family's affected and carrier members share at
the ROI, each member's call against it, and, for an embryo, what the call rests on. The
family page shows the embryo calls this module makes (``pgt_analysis``, served at
``GET /families/{family_id}/pgt-analysis``), and a sign-out freezes the same into the signed
record (``signed_record_sections``): one implementation, from the stored haplotype blocks.

The haplotype tracks still draw the risk line and a member's risk state in the browser
(``frontend/src/lib/haplotypeRisk.ts``). Both implementations answer the cases of
``frontend/src/lib/__tests__/fixtures/pgtEmbryoCallCases.json`` alike: the parity tests of
each side (``test_pgt_embryo_calls.py``, ``haplotypeRisk.test.ts``) read that file, so a
change to one side that the other does not follow fails.

The rules (the in-app reference ``/docs/reference/haplotype-segregation``):

* the disease haplotype: under a dominant model the one homolog every affected member and
  obligate carrier carries at the ROI (at least two of them), less any a member recorded as
  not a carrier carries; under a recessive model, on each parent's side the one homolog the
  affected members share; under X-linked recessive the homolog the affected males share, or
  without one the affected females' on each side. Anything other than exactly one candidate
  (one per side for recessive) resolves none, and every call is then uninformative;
* a call that a member carries a disease haplotype needs it seen anywhere at the ROI; a call
  that it does not (*Unaffected*, the clear side of *Carrier*) needs the member's own homolog
  of that parental side seen at every position of the ROI, else the call is uninformative
  (``roi_not_covered``). A grey lane, a lane without a homolog label ('?', '.', '') and a
  block over only part of the ROI are missing data;
* a male is read on one X only on blocks the backend marks ``hemizygous_in_males`` (chrX
  outside the pseudo-autosomal regions of the family's assembly);
* X-linked recessive, an unaffected father's X is not at risk (the owner's confirmed rule of
  2026-10-14, CLIN-6); a member whose sex is not recorded is called as a son and as a
  daughter, and where the two calls differ, as neither: at risk when either is, else
  uninformative, with both calls (``sex_dependent``);
* an embryo's block boundary within ``ROI_RECOMBINATION_FLANK`` of the ROI is a
  recombination near it, and a parent's phase correction whose switch may lie that close
  (``PHASE_SWITCH_UNCERTAINTY`` before it to its children's last switch) is one too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping, Sequence

Lane = Literal["hap1", "hap2"]
Origin = Literal["paternal", "maternal"]
Mode = Literal["dominant", "recessive", "x_linked_dominant", "x_linked_recessive", "unknown"]
Kind = Literal["dominant", "recessive-maternal", "recessive-paternal", "x-linked"]
RiskState = Literal["affected_or_at_risk", "carrier", "unaffected_non_carrier", "uninformative"]

# A block boundary within the ROI, or within this flank of either edge, is close enough to
# the ROI to undermine the call at the locus.
ROI_RECOMBINATION_FLANK = 250_000
# How far before a phase correction the parent's phase may already have switched: each
# child's switch is read where its own run of agreeing sites begins, and the swap starts at
# the earliest of them (``haplotype_phase_correction.PHASE_SWITCH_CLUSTER_SPAN``).
PHASE_SWITCH_UNCERTAINTY = 2_000_000
# The haplotypes are read over the ROI and this much on each side, so that the relatives'
# lineage resolves; the calls are made at the ROI itself.
ROI_WINDOW_FLANK = 1_000_000

Member = Mapping[str, Any]
Segment = Mapping[str, Any]

_SIDES: tuple[Origin, Origin] = ("paternal", "maternal")
_RECESSIVE_SIDES: tuple[tuple[Origin, Kind], ...] = (
    ("paternal", "recessive-paternal"),
    ("maternal", "recessive-maternal"),
)


@dataclass(frozen=True, slots=True)
class Region:
    chr: str | None
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Signature:
    """One parental homolog: the side it comes from and its label ('0', '1', ...)."""

    origin: Origin
    value: str


@dataclass(frozen=True, slots=True)
class DiseaseSignature:
    origin: Origin
    value: str
    kind: Kind

    @property
    def signature(self) -> Signature:
        return Signature(self.origin, self.value)


@dataclass(frozen=True, slots=True)
class DiseaseModel:
    mode: Mode
    signatures: tuple[DiseaseSignature, ...]
    informative: bool


@dataclass(frozen=True, slots=True)
class Homolog:
    """A homolog a member carries in one of its blocks over the region, with the block's extent."""

    signature: Signature
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class SexDependentCall:
    if_male: RiskState
    if_female: RiskState


@dataclass(frozen=True, slots=True)
class Assessment:
    state: RiskState
    # Uninformative because the member's own haplotype does not cover the ROI on a side the
    # call needs (missing data), not because no disease haplotype was resolved.
    roi_not_covered: bool = False
    # X-linked recessive, the sex not recorded, and a son's call differs from a daughter's.
    sex_dependent: SexDependentCall | None = None


@dataclass(frozen=True, slots=True)
class EmbryoCall:
    sample: str
    assessment: Assessment
    # The homologs the embryo carries at the ROI, as the call reads them.
    inherited: tuple[Homolog, ...]
    # Where the embryo's blocks change within ROI_RECOMBINATION_FLANK of the ROI.
    recombinations: tuple[int, ...]


# --- Reading members and blocks ---------------------------------------------------


def normalize_chrom(chrom: Any) -> str:
    return re.sub(r"^chr", "", str(chrom or "").strip(), flags=re.IGNORECASE).upper()


def normalize_inheritance(inheritance_model: Any) -> Mode:
    normalized = re.sub(r"[\s-]+", "_", str(inheritance_model or "").strip().upper())
    if not normalized:
        return "unknown"
    if normalized in ("AD", "AUTOSOMAL_DOMINANT", "DOMINANT"):
        return "dominant"
    if normalized in ("AR", "AUTOSOMAL_RECESSIVE", "RECESSIVE"):
        return "recessive"
    if normalized in ("XLD", "X_LINKED_DOMINANT", "XL_DOMINANT"):
        return "x_linked_dominant"
    if normalized in ("XLR", "X_LINKED_RECESSIVE", "XL_RECESSIVE", "X_LINKED"):
        return "x_linked_recessive"
    return "unknown"


def is_carrier_status(status: Any) -> bool:
    return status is True or status == "carrier"


def is_affected_member(member: Member) -> bool:
    return bool(member.get("affected")) or member.get("clinical_status") == "affected"


def resolve_inheritance_model(inheritance_model: Any, members: Sequence[Member]) -> str:
    """The model the calls use: the recorded one, or without one recessive when a member is
    marked a carrier and dominant otherwise."""
    if normalize_inheritance(inheritance_model) != "unknown":
        return str(inheritance_model)
    return "AR" if any(is_carrier_status(member.get("carrier_status")) for member in members) else "AD"


def _is_known_non_carrier(member: Member) -> bool:
    status = member.get("carrier_status")
    return status == "not_carrier" or status is False


def _role(member: Member) -> str:
    return str(member.get("role") or "").strip().lower()


def normalize_sex(sex: Any) -> Literal["male", "female", "unknown"]:
    normalized = str(sex or "").strip().lower()
    if normalized in ("male", "m", "1"):
        return "male"
    if normalized in ("female", "f", "2"):
        return "female"
    return "unknown"


def _is_x(chrom: Any) -> bool:
    return normalize_chrom(chrom) == "X"


def _male_with_one_x(member: Member, segment: Segment, chrom: Any) -> bool:
    """A male has one X in this block: chrX outside the PARs, as the backend marks it."""
    return (
        normalize_sex(member.get("sex")) == "male"
        and _is_x(chrom or segment.get("chr"))
        and segment.get("hemizygous_in_males") is True
    )


def _lane_value(segment: Segment, lane: Lane) -> str:
    value = segment.get(lane)
    return "" if value is None else str(value)


def _informative_value(value: str) -> bool:
    return value.strip() != "" and value != "."


def _male_x_visible_lane(member: Member, segment: Segment) -> Lane:
    preferred: Lane = "hap1" if _role(member) == "father" else "hap2"
    fallback: Lane = "hap2" if preferred == "hap1" else "hap1"
    return preferred if _informative_value(_lane_value(segment, preferred)) else fallback


def renderable_lanes(member: Member, segment: Segment, chrom: Any) -> list[Lane]:
    if _male_with_one_x(member, segment, chrom):
        return [_male_x_visible_lane(member, segment)]
    return ["hap1", "hap2"]


def _lane_lineage(segment: Segment, lane: Lane) -> str:
    value = segment.get("hap1_lineage" if lane == "hap1" else "hap2_lineage")
    return ("" if value is None else str(value)).strip().lower()


def lane_signature(member: Member, segment: Segment, lane: Lane, chrom: Any) -> Signature | None:
    """The homolog a lane names. A pedigree-aware lineage tag is authoritative; without one
    the member's role says the side."""
    value = _lane_value(segment, lane)
    if not _informative_value(value):
        return None
    lineage = _lane_lineage(segment, lane)
    if lineage == "paternal":
        return Signature("paternal", value)
    if lineage == "maternal":
        return Signature("maternal", value)
    if lineage in ("untransmitted", "unknown"):
        return None
    role = _role(member)
    origin: Origin
    if _male_with_one_x(member, segment, chrom):
        origin = "paternal" if role == "father" else "maternal"
    elif role == "father":
        origin = "paternal"
    elif role == "mother":
        origin = "maternal"
    else:
        origin = "paternal" if lane == "hap1" else "maternal"
    return Signature(origin, value)


def _overlaps(segment: Segment, region: Region) -> bool:
    segment_chrom = normalize_chrom(segment.get("chr"))
    region_chrom = normalize_chrom(region.chr)
    if segment_chrom and region_chrom and segment_chrom != region_chrom:
        return False
    return int(segment["end"]) >= region.start and int(segment["start"]) <= region.end


SegmentMap = dict[str, list[Segment]]


def segment_map(samples: Iterable[Mapping[str, Any]]) -> SegmentMap:
    """Each sample's blocks; a sample listed twice keeps its last entry."""
    return {str(sample.get("sample")): list(sample.get("segments") or []) for sample in samples}


_HOMOLOG_LABEL = re.compile(r"[0-9]+")


def _homolog_label(value: str) -> bool:
    """A lane names one of a parent's homologs only by a homolog label (0, 1, ...): '?' (not
    confirmed yet), '.' (a deleted homolog) and '' name none."""
    return _HOMOLOG_LABEL.fullmatch(value) is not None


def homologs_in_region(member: Member, segments: SegmentMap, region: Region) -> list[Homolog]:
    """The homologs a member carries in its blocks over the region. A grey lane and a lane
    without a homolog label carry none: there the haplotype is missing data."""
    homologs: list[Homolog] = []
    for segment in segments.get(str(member.get("sample_id")), []):
        if not _overlaps(segment, region):
            continue
        chrom = region.chr or segment.get("chr")
        for lane in renderable_lanes(member, segment, chrom):
            signature = lane_signature(member, segment, lane, chrom)
            if signature is None or not _homolog_label(signature.value):
                continue
            homologs.append(Homolog(signature, int(segment["start"]), int(segment["end"])))
    return homologs


def _carried(
    member: Member, segments: SegmentMap, region: Region, origin: Origin | None = None
) -> set[Signature]:
    return {
        homolog.signature
        for homolog in homologs_in_region(member, segments, region)
        if origin is None or homolog.signature.origin == origin
    }


def _covers_region_on_side(homologs: Sequence[Homolog], region: Region, origin: Origin) -> bool:
    """Whether a homolog of this side is seen at every position of the region. Extents are
    inclusive; blocks that abut (end + 1 = next start) are contiguous."""
    if not region.end >= region.start:
        return False
    spans = sorted(
        (homolog for homolog in homologs if homolog.signature.origin == origin),
        key=lambda homolog: homolog.start,
    )
    covered_to = region.start - 1
    for span in spans:
        if span.start > covered_to + 1:
            return False
        covered_to = max(covered_to, span.end)
        if covered_to >= region.end:
            return True
    return False


def _male_x_origin(member: Member) -> Origin:
    """A male's one X: his mother's, or for the father himself the family's paternal side."""
    return "paternal" if _role(member) == "father" else "maternal"


# --- The disease haplotype --------------------------------------------------------


def _intersect_candidates(
    members: Sequence[Member], segments: SegmentMap, region: Region, origin: Origin | None = None
) -> set[Signature]:
    """The homologs every member that carries any at the region shares. A member with none
    there (greyed by the lineage, or off the region) is not informative, not a zero."""
    intersection: set[Signature] | None = None
    for member in members:
        carried = _carried(member, segments, region, origin)
        if not carried:
            continue
        intersection = set(carried) if intersection is None else intersection & carried
    return intersection or set()


def _remove_known_non_carriers(
    candidates: set[Signature], members: Sequence[Member], segments: SegmentMap, region: Region
) -> set[Signature]:
    non_carriers = [
        member for member in members if not is_affected_member(member) and _is_known_non_carrier(member)
    ]
    if not non_carriers:
        return candidates
    return {
        candidate
        for candidate in candidates
        if not any(candidate in _carried(member, segments, region) for member in non_carriers)
    }


def _unique(candidates: set[Signature]) -> Signature | None:
    return next(iter(candidates)) if len(candidates) == 1 else None


def _dominant(members: Sequence[Member], segments: SegmentMap, region: Region) -> list[DiseaseSignature]:
    informative = [
        member
        for member in members
        if is_affected_member(member)
        or (is_carrier_status(member.get("carrier_status")) and member.get("carrier_type") == "obligate")
    ]
    if len(informative) < 2:
        return []
    signature = _unique(
        _remove_known_non_carriers(
            _intersect_candidates(informative, segments, region), members, segments, region
        )
    )
    return [DiseaseSignature(signature.origin, signature.value, "dominant")] if signature else []


def _recessive(members: Sequence[Member], segments: SegmentMap, region: Region) -> list[DiseaseSignature]:
    affected = [member for member in members if is_affected_member(member)]
    found: list[DiseaseSignature] = []
    if not affected:
        return found
    for origin, kind in _RECESSIVE_SIDES:
        signature = _unique(_intersect_candidates(affected, segments, region, origin))
        if signature:
            found.append(DiseaseSignature(signature.origin, signature.value, kind))
    return found


def _x_linked_recessive(
    members: Sequence[Member], segments: SegmentMap, region: Region
) -> list[DiseaseSignature]:
    affected_males = [
        member for member in members if is_affected_member(member) and normalize_sex(member.get("sex")) == "male"
    ]
    if affected_males:
        signature = _unique(_intersect_candidates(affected_males, segments, region))
        return [DiseaseSignature(signature.origin, signature.value, "x-linked")] if signature else []
    affected_females = [
        member
        for member in members
        if is_affected_member(member) and normalize_sex(member.get("sex")) == "female"
    ]
    found: list[DiseaseSignature] = []
    for origin in _SIDES:
        signature = _unique(_intersect_candidates(affected_females, segments, region, origin))
        if signature:
            found.append(DiseaseSignature(signature.origin, signature.value, "x-linked"))
    return found


def infer_disease_haplotypes(
    *,
    samples: Iterable[Mapping[str, Any]],
    members: Sequence[Member],
    inheritance_model: Any,
    region: Region,
) -> DiseaseModel:
    """The disease haplotype(s) at the region under the model (as resolved by the caller)."""
    mode = normalize_inheritance(inheritance_model)
    segments = segment_map(samples)
    signatures: list[DiseaseSignature] = []
    if mode == "dominant":
        signatures = _dominant(members, segments, region)
    elif mode == "recessive":
        signatures = _recessive(members, segments, region)
    elif mode == "x_linked_dominant":
        signatures = [
            DiseaseSignature(signature.origin, signature.value, "x-linked")
            for signature in _dominant(members, segments, region)
        ]
    elif mode == "x_linked_recessive":
        signatures = _x_linked_recessive(members, segments, region)
    # One per homolog: a later duplicate keeps the first one's place.
    unique: dict[Signature, DiseaseSignature] = {}
    for signature in signatures:
        unique[signature.signature] = signature
    return DiseaseModel(mode, tuple(unique.values()), bool(unique))


# --- A member's call --------------------------------------------------------------


def assess_member(
    *,
    model: DiseaseModel,
    samples: Iterable[Mapping[str, Any]],
    member: Member,
    region: Region,
) -> Assessment:
    """The member's call at the region, and why an uninformative call is so.

    A positive call needs the risk haplotype seen anywhere in the region. A negative call
    (Unaffected, the clear side of Carrier) needs the member's own homolog of that side seen
    at every position of it; the sides are those a disease haplotype was resolved on, and a
    male whose blocks over the region are all one-copy is read on his one X.
    """
    sample_list = list(samples)
    if not model.informative:
        return Assessment("uninformative")
    if model.mode == "x_linked_recessive" and normalize_sex(member.get("sex")) == "unknown":
        if_male = assess_member(model=model, samples=sample_list, region=region, member={**member, "sex": "male"})
        if_female = assess_member(
            model=model, samples=sample_list, region=region, member={**member, "sex": "female"}
        )
        if if_male.state == if_female.state:
            return Assessment(
                if_female.state,
                roi_not_covered=if_male.roi_not_covered and if_female.roi_not_covered,
                sex_dependent=if_female.sex_dependent,
            )
        at_risk = "affected_or_at_risk" in (if_male.state, if_female.state)
        return Assessment(
            "affected_or_at_risk" if at_risk else "uninformative",
            sex_dependent=SexDependentCall(if_male.state, if_female.state),
        )
    segments = segment_map(sample_list)
    homologs = homologs_in_region(member, segments, region)
    carried = {homolog.signature for homolog in homologs}

    def has_kind(kind: Kind) -> bool:
        return any(
            signature.kind == kind and signature.signature in carried for signature in model.signatures
        )

    def clear_on(sides: Sequence[Origin], state: RiskState) -> Assessment:
        if all(_covers_region_on_side(homologs, region, side) for side in sides):
            return Assessment(state)
        return Assessment("uninformative", roi_not_covered=True)

    blocks = [
        segment for segment in segments.get(str(member.get("sample_id")), []) if _overlaps(segment, region)
    ]
    one_x = bool(blocks) and all(_male_with_one_x(member, segment, region.chr) for segment in blocks)
    risk_sides: list[Origin] = (
        [_male_x_origin(member)]
        if one_x
        else list(dict.fromkeys(signature.origin for signature in model.signatures))
    )

    if model.mode in ("dominant", "x_linked_dominant"):
        kind: Kind = "dominant" if model.mode == "dominant" else "x-linked"
        return (
            Assessment("affected_or_at_risk")
            if has_kind(kind)
            else clear_on(risk_sides, "unaffected_non_carrier")
        )

    if model.mode == "recessive":
        has_paternal = has_kind("recessive-paternal")
        has_maternal = has_kind("recessive-maternal")
        kinds = {signature.kind for signature in model.signatures}
        if not {"recessive-paternal", "recessive-maternal"} <= kinds:
            return Assessment("uninformative")
        if has_paternal and has_maternal:
            return Assessment("affected_or_at_risk")
        # A carrier's other homolog must be seen clear; an unaffected member's both.
        if has_paternal:
            return clear_on(["maternal"], "carrier")
        if has_maternal:
            return clear_on(["paternal"], "carrier")
        return clear_on(["paternal", "maternal"], "unaffected_non_carrier")

    if model.mode == "x_linked_recessive":
        matching = [signature for signature in model.signatures if signature.signature in carried]
        if normalize_sex(member.get("sex")) == "male":
            return (
                Assessment("affected_or_at_risk")
                if matching
                else clear_on(risk_sides, "unaffected_non_carrier")
            )
        matching_origins = {signature.origin for signature in matching}
        if len(matching_origins) >= 2:
            return Assessment("affected_or_at_risk")
        # A side without a resolved risk haplotype (an unaffected father's X) is not at risk:
        # the owner's confirmed rule for a fully penetrant X-linked recessive disorder
        # (2026-10-14; TF-09a REQ-PGT-005).
        clear_sides = [side for side in risk_sides if side not in matching_origins]
        return clear_on(clear_sides, "carrier" if len(matching_origins) == 1 else "unaffected_non_carrier")

    return Assessment("uninformative")


# --- The embryos ------------------------------------------------------------------


def is_embryo(member: Member) -> bool:
    return str(member.get("role") or "").lower() == "embryo"


def recombinations_near(
    segments: Sequence[Segment], region: Region, flank: int = ROI_RECOMBINATION_FLANK
) -> list[int]:
    """Where a member's blocks change (a lane's homolog or lineage) inside the region or
    within ``flank`` of it: a recombination across or near the locus."""
    chrom = normalize_chrom(region.chr)
    on_chrom = sorted(
        (
            segment
            for segment in segments
            if not segment.get("chr") or normalize_chrom(segment.get("chr")) == chrom
        ),
        key=lambda segment: int(segment["start"]),
    )
    low, high = region.start - flank, region.end + flank
    positions: list[int] = []
    for previous, current in zip(on_chrom, on_chrom[1:]):
        changed = any(
            previous.get(key) != current.get(key) for key in ("hap1", "hap2", "hap1_lineage", "hap2_lineage")
        )
        start = int(current["start"])
        if changed and low <= start <= high:
            positions.append(start)
    return positions


def phase_correction(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """A parent's phase correction as the views read it from the family's metadata, or None
    when it is not well formed."""
    parent, chrom, position = item.get("parent"), item.get("chr"), item.get("position")
    if (
        not isinstance(parent, str)
        or not isinstance(chrom, str)
        or isinstance(position, bool)
        or not isinstance(position, int)
    ):
        return None
    end = item.get("end")
    children_switching = item.get("children_switching")
    children = item.get("children")

    def count(value: Any) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    return {
        "parent": parent,
        "side": "mother" if item.get("side") == "mother" else "father",
        "chr": chrom,
        "position": position,
        "end": end if isinstance(end, int) and not isinstance(end, bool) else position,
        "children_switching": count(children_switching),
        "children": count(children),
    }


def phase_corrections_near(
    corrections: Iterable[Mapping[str, Any]], region: Region, flank: int = ROI_RECOMBINATION_FLANK
) -> list[dict[str, Any]]:
    """The corrections on the region's chromosome whose switch may lie within ``flank`` of
    it: anywhere from ``PHASE_SWITCH_UNCERTAINTY`` before the correction to its children's
    last switch."""
    if not region.chr:
        return []
    chrom = normalize_chrom(region.chr)
    near: list[dict[str, Any]] = []
    for item in corrections:
        correction = phase_correction(item)
        if (
            correction is not None
            and normalize_chrom(correction["chr"]) == chrom
            and correction["end"] >= region.start - flank
            and correction["position"] - PHASE_SWITCH_UNCERTAINTY <= region.end + flank
        ):
            near.append(correction)
    return near


def call_embryos(
    *,
    members: Sequence[Member],
    samples: Sequence[Mapping[str, Any]],
    inheritance_model: Any,
    region: Region,
) -> tuple[DiseaseModel, list[EmbryoCall]]:
    """The disease haplotype at the ROI and each embryo's call against it, in member order."""
    sample_list = list(samples)
    model = infer_disease_haplotypes(
        samples=sample_list,
        members=members,
        inheritance_model=resolve_inheritance_model(inheritance_model, members),
        region=region,
    )
    segments = segment_map(sample_list)
    calls: list[EmbryoCall] = []
    for member in members:
        if not is_embryo(member):
            continue
        sample = str(member.get("sample_id"))
        # The embryo's own blocks, as the first entry for it lists them.
        own = next((entry.get("segments") or [] for entry in sample_list if entry.get("sample") == sample), [])
        calls.append(
            EmbryoCall(
                sample=sample,
                assessment=assess_member(model=model, samples=sample_list, member=member, region=region),
                inherited=tuple(homologs_in_region(member, segments, region)),
                recombinations=tuple(recombinations_near(own, region)),
            )
        )
    return model, calls
