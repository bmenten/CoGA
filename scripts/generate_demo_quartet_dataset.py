#!/usr/bin/env python3
"""Generate a dense deterministic demo family dataset for CoGA."""

from __future__ import annotations

import json
import math
import random
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


SEED = 20260328
FAMILY_ID = "demo_family"
PROJECT_NAME = "CoGA demo family"
ASSEMBLY = "GRCh38"
SPECIES = "Homo sapiens"
OUTPUT_ROOT = Path(__file__).resolve().parents[1] / "demo" / "quartet_family"

COVERAGE_BIN_SIZE = 15_000
APCAD_TARGET_POINTS = 50_000
SMALL_VARIANT_TARGET = 5_184

CHROM_LENGTHS: Dict[str, int] = {
    "1": 248_956_422,
    "2": 242_193_529,
    "3": 198_295_559,
    "4": 190_214_555,
    "5": 181_538_259,
    "6": 170_805_979,
    "7": 159_345_973,
    "8": 145_138_636,
    "9": 138_394_717,
    "10": 133_797_422,
    "11": 135_086_622,
    "12": 133_275_309,
    "13": 114_364_328,
    "14": 107_043_718,
    "15": 101_991_189,
    "16": 90_338_345,
    "17": 83_257_441,
    "18": 80_373_285,
    "19": 58_617_616,
    "20": 64_444_167,
    "21": 46_709_983,
    "22": 50_818_468,
    "X": 156_040_895,
}

ALL_CHROMS = list(CHROM_LENGTHS.keys())

SAMPLES = [
    {"sample_id": "father", "role": "father", "sex": "male", "affected": False, "is_proband": False},
    {"sample_id": "mother", "role": "mother", "sex": "female", "affected": False, "is_proband": False},
    {"sample_id": "son", "role": "proband", "sex": "male", "affected": True, "is_proband": True},
    {"sample_id": "daughter", "role": "sibling", "sex": "female", "affected": False, "is_proband": False},
]
SAMPLE_INDEX = {sample["sample_id"]: index for index, sample in enumerate(SAMPLES)}


NO_DATA_INTERVALS: Dict[str, List[Tuple[int, int]]] = {
    "1": [(121_700_000, 125_100_000)],
    "2": [(91_800_000, 96_000_000)],
    "3": [(87_800_000, 94_000_000)],
    "4": [(48_200_000, 52_700_000)],
    "5": [(46_100_000, 51_400_000)],
    "6": [(58_500_000, 62_600_000)],
    "7": [(58_100_000, 62_000_000)],
    "8": [(43_200_000, 47_200_000)],
    "9": [(39_000_000, 45_500_000), (68_500_000, 71_500_000)],
    "10": [(38_000_000, 41_600_000)],
    "11": [(51_000_000, 55_800_000)],
    "12": [(33_200_000, 37_800_000)],
    "13": [(0, 17_200_000), (17_200_000, 19_000_000)],
    "14": [(0, 16_100_000), (16_100_000, 18_200_000)],
    "15": [(0, 17_500_000), (17_500_000, 20_500_000)],
    "16": [(35_300_000, 38_400_000), (44_800_000, 46_700_000)],
    "17": [(22_200_000, 25_800_000)],
    "18": [(15_400_000, 21_500_000)],
    "19": [(24_200_000, 28_100_000)],
    "20": [(25_700_000, 30_400_000)],
    "21": [(0, 11_200_000), (11_200_000, 13_100_000)],
    "22": [(0, 13_700_000), (13_700_000, 17_400_000)],
    "X": [(58_100_000, 63_800_000)],
}


STRUCTURAL_CATEGORY_COUNTS: Tuple[Tuple[str, Tuple[str, ...], int], ...] = (
    ("all_shared", ("father", "mother", "son", "daughter"), 2_820),
    ("father_son", ("father", "son"), 430),
    ("mother_son", ("mother", "son"), 445),
    ("father_daughter", ("father", "daughter"), 410),
    ("mother_daughter", ("mother", "daughter"), 435),
    ("father_son_daughter", ("father", "son", "daughter"), 125),
    ("mother_son_daughter", ("mother", "son", "daughter"), 118),
    ("parents_only", ("father", "mother"), 320),
    ("children_only", ("son", "daughter"), 325),
    ("father_private", ("father",), 905),
    ("mother_private", ("mother",), 930),
    ("son_private", ("son",), 780),
    ("daughter_private", ("daughter",), 795),
)


AMINO_ACIDS = [
    ("Ala", "A"),
    ("Arg", "R"),
    ("Asn", "N"),
    ("Asp", "D"),
    ("Cys", "C"),
    ("Gln", "Q"),
    ("Glu", "E"),
    ("Gly", "G"),
    ("His", "H"),
    ("Ile", "I"),
    ("Leu", "L"),
    ("Lys", "K"),
    ("Met", "M"),
    ("Phe", "F"),
    ("Pro", "P"),
    ("Ser", "S"),
    ("Thr", "T"),
    ("Trp", "W"),
    ("Tyr", "Y"),
    ("Val", "V"),
]


GENES_BY_CHROM: Dict[str, List[Dict[str, str]]] = {
    "1": [
        {"symbol": "MTHFR", "gene_id": "ENSG00000177000", "transcript": "ENST00000376592", "omim": "607093"},
        {"symbol": "LMNA", "gene_id": "ENSG00000160789", "transcript": "ENST00000368300", "omim": "150330"},
    ],
    "2": [{"symbol": "APOB", "gene_id": "ENSG00000084674", "transcript": "ENST00000399256", "omim": "107730"}],
    "3": [{"symbol": "MLH1", "gene_id": "ENSG00000076242", "transcript": "ENST00000231790", "omim": "120436"}],
    "4": [{"symbol": "PDGFRA", "gene_id": "ENSG00000134853", "transcript": "ENST00000257290", "omim": "173490"}],
    "5": [{"symbol": "APC", "gene_id": "ENSG00000134982", "transcript": "ENST00000257430", "omim": "611731"}],
    "6": [{"symbol": "HFE", "gene_id": "ENSG00000010704", "transcript": "ENST00000357618", "omim": "613609"}],
    "7": [{"symbol": "CFTR", "gene_id": "ENSG00000001626", "transcript": "ENST00000003084", "omim": "602421"}],
    "8": [{"symbol": "MYC", "gene_id": "ENSG00000136997", "transcript": "ENST00000621592", "omim": "190080"}],
    "9": [{"symbol": "TSC1", "gene_id": "ENSG00000165699", "transcript": "ENST00000298552", "omim": "605284"}],
    "10": [{"symbol": "RET", "gene_id": "ENSG00000165731", "transcript": "ENST00000355710", "omim": "164761"}],
    "11": [{"symbol": "HBB", "gene_id": "ENSG00000244734", "transcript": "ENST00000335295", "omim": "141900"}],
    "12": [{"symbol": "PAH", "gene_id": "ENSG00000171759", "transcript": "ENST00000553106", "omim": "612349"}],
    "13": [{"symbol": "BRCA2", "gene_id": "ENSG00000139618", "transcript": "ENST00000380152", "omim": "600185"}],
    "14": [{"symbol": "FOXG1", "gene_id": "ENSG00000176165", "transcript": "ENST00000355276", "omim": "164874"}],
    "15": [{"symbol": "FBN1", "gene_id": "ENSG00000166147", "transcript": "ENST00000316623", "omim": "134797"}],
    "16": [{"symbol": "PKD1", "gene_id": "ENSG00000160113", "transcript": "ENST00000423118", "omim": "601313"}],
    "17": [
        {"symbol": "BRCA1", "gene_id": "ENSG00000012048", "transcript": "ENST00000357654", "omim": "113705"},
        {"symbol": "TP53", "gene_id": "ENSG00000141510", "transcript": "ENST00000269305", "omim": "191170"},
    ],
    "18": [{"symbol": "SMAD4", "gene_id": "ENSG00000141646", "transcript": "ENST00000342988", "omim": "600993"}],
    "19": [{"symbol": "LDLR", "gene_id": "ENSG00000130164", "transcript": "ENST00000558518", "omim": "606945"}],
    "20": [{"symbol": "JAG1", "gene_id": "ENSG00000101384", "transcript": "ENST00000254958", "omim": "601920"}],
    "21": [{"symbol": "RUNX1", "gene_id": "ENSG00000159216", "transcript": "ENST00000300305", "omim": "151385"}],
    "22": [{"symbol": "NF2", "gene_id": "ENSG00000186575", "transcript": "ENST00000338641", "omim": "607379"}],
    "X": [{"symbol": "DMD", "gene_id": "ENSG00000198947", "transcript": "ENST00000357033", "omim": "300377"}],
}


@dataclass(frozen=True)
class CopyNumberEvent:
    chrom: str
    start: int
    end: int
    svtype: str
    label: str
    origin: str
    members: Tuple[str, ...]


@dataclass(frozen=True)
class StructuralEvent:
    variant_id: str
    chrom: str
    start: int
    end: int
    svtype: str
    ref: str
    alt: str
    members: Tuple[str, ...]
    qual: float
    source_label: str
    remote_chr: str | None = None
    remote_start: int | None = None
    remote_end: int | None = None


@dataclass(frozen=True)
class TransmissionBlock:
    chrom: str
    start: int
    end: int
    paternal_haplotype: int
    maternal_haplotype: int
    block_id: int
    phase_set: int


@dataclass(frozen=True)
class SmallVariantSpec:
    index: int
    chrom: str
    pos: int
    ref: str
    alt: str
    effect: str
    impact: str
    gene: Dict[str, str]
    exon_number: int
    hgvsc: str
    hgvsp: str
    rsid: str
    clinvar: str
    gnomad_af: float
    gnomad_hom_count: int
    gene_pli: float
    gene_missense_z: float
    cadd_phred: float
    revel: float
    spliceai: tuple[float, float, float, float]
    omim: str
    pubmed: str


CNV_GROUPS: Tuple[CopyNumberEvent, ...] = (
    CopyNumberEvent("1", 88_500_000, 92_100_000, "DUP", "inherited paternal chr1 duplication", "paternal", ("father", "son")),
    CopyNumberEvent("4", 51_900_000, 56_400_000, "DEL", "inherited maternal chr4 deletion", "maternal", ("mother", "daughter")),
    CopyNumberEvent("7", 44_100_000, 49_300_000, "DUP", "shared paternal chr7 duplication", "paternal", ("father", "son", "daughter")),
    CopyNumberEvent("12", 18_100_000, 23_900_000, "DUP", "shared maternal chr12 duplication", "maternal", ("mother", "son", "daughter")),
    CopyNumberEvent("16", 29_600_000, 33_400_000, "DEL", "inherited paternal chr16 deletion", "paternal", ("father", "daughter")),
    CopyNumberEvent("17", 42_300_000, 45_100_000, "DUP", "inherited maternal chr17 duplication", "maternal", ("mother", "son")),
    CopyNumberEvent("3", 111_200_000, 116_700_000, "DUP", "de novo chr3 duplication", "paternal", ("son",)),
    CopyNumberEvent("10", 77_600_000, 81_900_000, "DUP", "de novo chr10 duplication", "maternal", ("daughter",)),
    CopyNumberEvent("18", 21_300_000, 24_700_000, "DEL", "de novo chr18 deletion", "maternal", ("son",)),
    CopyNumberEvent("22", 35_900_000, 39_000_000, "DEL", "de novo chr22 deletion", "paternal", ("daughter",)),
)


TRGT_REPEAT_SPECS: Tuple[Dict[str, object], ...] = (
    {"locus_id": "HTT", "chrom": "4", "start": 3_075_000, "motif": "CAG", "counts": {"father": [17, 20], "mother": [18, 18], "son": [18, 44], "daughter": [17, 19]}},
    {"locus_id": "ATXN1", "chrom": "6", "start": 16_327_800, "motif": "CAG", "counts": {"father": [30, 31], "mother": [31, 33], "son": [30, 32], "daughter": [30, 31]}},
    {"locus_id": "ATXN2", "chrom": "12", "start": 111_803_000, "motif": "CAG", "counts": {"father": [21, 22], "mother": [22, 23], "son": [22, 34], "daughter": [22, 33]}},
    {"locus_id": "ATXN3", "chrom": "14", "start": 92_537_300, "motif": "CAG", "counts": {"father": [20, 23], "mother": [21, 24], "son": [22, 25], "daughter": [20, 24]}},
    {"locus_id": "CACNA1A", "chrom": "19", "start": 13_207_800, "motif": "CAG", "counts": {"father": [10, 11], "mother": [11, 19], "son": [10, 11], "daughter": [11, 12]}},
    {"locus_id": "ATXN7", "chrom": "3", "start": 63_898_300, "motif": "CAG", "counts": {"father": [10, 12], "mother": [11, 12], "son": [11, 13], "daughter": [10, 12]}},
    {"locus_id": "TBP", "chrom": "6", "start": 170_554_000, "motif": "CAG", "counts": {"father": [34, 36], "mother": [35, 37], "son": [34, 35], "daughter": [34, 36]}},
    {"locus_id": "ATN1", "chrom": "12", "start": 6_944_000, "motif": "CAG", "counts": {"father": [15, 16], "mother": [15, 17], "son": [16, 17], "daughter": [15, 16]}},
    {"locus_id": "AR", "chrom": "X", "start": 67_545_000, "motif": "CAG", "counts": {"father": [21], "mother": [21, 22], "son": [52], "daughter": [21, 22]}},
    {"locus_id": "FMR1", "chrom": "X", "start": 147_911_000, "motif": "CGG", "counts": {"father": [29], "mother": [31, 72], "son": [92], "daughter": [30, 31]}},
    {"locus_id": "DMPK", "chrom": "19", "start": 46_273_400, "motif": "CTG", "counts": {"father": [11, 14], "mother": [12, 13], "son": [12, 128], "daughter": [11, 13]}},
    {"locus_id": "ATXN8OS", "chrom": "13", "start": 70_139_300, "motif": "CTG", "counts": {"father": [24, 28], "mother": [25, 30], "son": [26, 32], "daughter": [32, 74]}},
    {"locus_id": "CNBP", "chrom": "3", "start": 128_744_000, "motif": "CCTG", "counts": {"father": [20, 24], "mother": [22, 88], "son": [21, 26], "daughter": [20, 22]}},
    {"locus_id": "FXN", "chrom": "9", "start": 71_650_800, "motif": "GAA", "counts": {"father": [9, 12], "mother": [10, 13], "son": [14, 72], "daughter": [10, 14]}},
    {"locus_id": "C9orf72", "chrom": "9", "start": 27_573_500, "motif": "GGGGCC", "counts": {"father": [2, 8], "mother": [2, 7], "son": [6, 82], "daughter": [2, 9]}},
    {"locus_id": "ATXN10", "chrom": "22", "start": 46_192_000, "motif": "ATTCT", "counts": {"father": [14, 18], "mother": [15, 20], "son": [16, 19], "daughter": [14, 18]}},
    {"locus_id": "BEAN1", "chrom": "16", "start": 88_752_000, "motif": "TGGAA", "counts": {"father": [18, 21], "mother": [17, 20], "son": [18, 22], "daughter": [17, 19]}},
    {"locus_id": "PPP2R2B", "chrom": "5", "start": 146_700_000, "motif": "CAG", "counts": {"father": [18, 20], "mother": [18, 21], "son": [19, 22], "daughter": [19, 53]}},
    {"locus_id": "NOP56", "chrom": "20", "start": 26_527_300, "motif": "GGCCTG", "counts": {"father": [4, 6], "mother": [4, 5], "son": [4, 6], "daughter": [4, 5]}},
    {"locus_id": "JPH3", "chrom": "16", "start": 87_538_000, "motif": "CTG", "counts": {"father": [14, 16], "mother": [15, 16], "son": [14, 17], "daughter": [15, 16]}},
    {"locus_id": "PABPN1", "chrom": "14", "start": 23_321_400, "motif": "GCN", "counts": {"father": [10, 10], "mother": [10, 11], "son": [10, 11], "daughter": [10, 10]}},
)


def ensure_clean_dir(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def chrom_sort_key(chrom: str) -> Tuple[int, str]:
    if chrom.isdigit():
        return (int(chrom), chrom)
    if chrom == "X":
        return (23, chrom)
    return (99, chrom)


def chrom_numeric(chrom: str) -> int:
    return chrom_sort_key(chrom)[0]


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def merge_intervals(intervals: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    merged: List[Tuple[int, int]] = []
    for start, end in sorted(intervals):
        if start >= end:
            continue
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def accessible_segments(chrom: str) -> List[Tuple[int, int]]:
    segments: List[Tuple[int, int]] = []
    cursor = 0
    for start, end in merge_intervals(NO_DATA_INTERVALS.get(chrom, [])):
        start = max(0, start)
        end = min(CHROM_LENGTHS[chrom], end)
        if cursor < start:
            segments.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < CHROM_LENGTHS[chrom]:
        segments.append((cursor, CHROM_LENGTHS[chrom]))
    return [(start, end) for start, end in segments if end - start >= 1]


ACCESSIBLE_SEGMENTS = {chrom: accessible_segments(chrom) for chrom in ALL_CHROMS}


def event_ratio(event: CopyNumberEvent) -> float:
    return 0.58 if event.svtype == "DUP" else -1.0


def sample_cnv_events() -> Dict[str, List[CopyNumberEvent]]:
    events: Dict[str, List[CopyNumberEvent]] = {sample["sample_id"]: [] for sample in SAMPLES}
    for event in CNV_GROUPS:
        for member in event.members:
            events[member].append(event)
    return events


CNV_EVENTS_BY_SAMPLE = sample_cnv_events()


def ped_lines() -> List[str]:
    return [
        f"{FAMILY_ID}\tfather\t0\t0\t1\t1",
        f"{FAMILY_ID}\tmother\t0\t0\t2\t1",
        f"{FAMILY_ID}\tson\tfather\tmother\t1\t2",
        f"{FAMILY_ID}\tdaughter\tfather\tmother\t2\t1",
    ]


def family_manual_payload() -> Dict[str, object]:
    return {
        "family_id": FAMILY_ID,
        "members": [
            {"sample_id": "father", "father_id": None, "mother_id": None, "sex": "male", "affected": False, "is_proband": False},
            {"sample_id": "mother", "father_id": None, "mother_id": None, "sex": "female", "affected": False, "is_proband": False},
            {"sample_id": "son", "father_id": "father", "mother_id": "mother", "sex": "male", "affected": True, "is_proband": True},
            {"sample_id": "daughter", "father_id": "father", "mother_id": "mother", "sex": "female", "affected": False, "is_proband": False},
        ],
    }


def sample_manifest_rows() -> List[str]:
    rows = ["sample_id,role,sex,affected,is_proband"]
    for sample in SAMPLES:
        rows.append(
            ",".join(
                [
                    sample["sample_id"],
                    sample["role"],
                    sample["sex"],
                    str(sample["affected"]).lower(),
                    str(sample["is_proband"]).lower(),
                ]
            )
        )
    return rows


def event_at(sample_id: str, chrom: str, pos: int) -> CopyNumberEvent | None:
    for event in CNV_EVENTS_BY_SAMPLE[sample_id]:
        if event.chrom == chrom and event.start <= pos < event.end:
            return event
    return None


def total_accessible_length(chrom: str) -> int:
    return sum(end - start for start, end in ACCESSIBLE_SEGMENTS[chrom])


def allocate_counts(total: int, chroms: Sequence[str], *, use_accessible: bool = False) -> Dict[str, int]:
    lengths = {
        chrom: total_accessible_length(chrom) if use_accessible else CHROM_LENGTHS[chrom]
        for chrom in chroms
    }
    total_length = sum(lengths.values())
    raw = {chrom: (total * lengths[chrom]) / total_length for chrom in chroms}
    counts = {chrom: int(math.floor(value)) for chrom, value in raw.items()}
    remainder = total - sum(counts.values())
    order = sorted(
        chroms,
        key=lambda chrom: (raw[chrom] - counts[chrom], lengths[chrom]),
        reverse=True,
    )
    for chrom in order[:remainder]:
        counts[chrom] += 1
    return counts


def allocate_counts_with_floor(
    total: int,
    chroms: Sequence[str],
    *,
    floor: int,
    use_accessible: bool = False,
) -> Dict[str, int]:
    reserved = floor * len(chroms)
    if reserved >= total:
        return allocate_counts(total, chroms, use_accessible=use_accessible)
    extra = allocate_counts(total - reserved, chroms, use_accessible=use_accessible)
    return {chrom: floor + extra.get(chrom, 0) for chrom in chroms}


def positions_in_segments(
    chrom: str,
    count: int,
    rng: random.Random,
    *,
    margin: int,
    anchor_edges: bool = False,
) -> List[int]:
    segments = [
        (max(start + margin, start), min(end - margin, end))
        for start, end in ACCESSIBLE_SEGMENTS[chrom]
    ]
    segments = [(start, end) for start, end in segments if end - start > 10]
    if count <= 0 or not segments:
        return []

    lengths = [end - start for start, end in segments]
    total_length = sum(lengths)
    raw = [(count * length) / total_length for length in lengths]
    per_segment = [int(math.floor(value)) for value in raw]
    remainder = count - sum(per_segment)
    order = sorted(range(len(segments)), key=lambda idx: (raw[idx] - per_segment[idx], lengths[idx]), reverse=True)
    for idx in order[:remainder]:
        per_segment[idx] += 1

    if anchor_edges and count >= 2 * len(segments):
        minimum = 2
        for idx in range(len(per_segment)):
            if per_segment[idx] >= minimum:
                continue
            deficit = minimum - per_segment[idx]
            donors = sorted(
                [donor for donor in range(len(per_segment)) if donor != idx],
                key=lambda donor: per_segment[donor],
                reverse=True,
            )
            for donor in donors:
                if deficit <= 0:
                    break
                spare = max(0, per_segment[donor] - minimum)
                if spare <= 0:
                    continue
                take = min(spare, deficit)
                per_segment[donor] -= take
                per_segment[idx] += take
                deficit -= take

    positions: List[int] = []
    for (seg_start, seg_end), seg_count in zip(segments, per_segment):
        if seg_count <= 0:
            continue
        if anchor_edges and seg_count >= 2:
            positions.append(seg_start)
            positions.append(seg_end - 1)
            remaining = seg_count - 2
            if remaining <= 0:
                continue
            inner_start = seg_start + 1
            inner_end = seg_end - 1
            step = (inner_end - inner_start) / remaining
            for index in range(remaining):
                center = inner_start + (index + 0.5) * step
                jitter = rng.uniform(-0.28, 0.28) * step if remaining > 1 else 0.0
                pos = int(clamp(center + jitter, inner_start, inner_end - 1))
                positions.append(pos)
            continue
        step = (seg_end - seg_start) / seg_count
        for index in range(seg_count):
            center = seg_start + (index + 0.5) * step
            jitter = rng.uniform(-0.35, 0.35) * step if seg_count > 1 else 0.0
            pos = int(clamp(center + jitter, seg_start + 1, seg_end - 1))
            if positions and pos <= positions[-1]:
                pos = min(seg_end - 1, positions[-1] + 1)
            positions.append(pos)
    return sorted(set(positions))


def slice_accessible_block_groups(
    chrom: str,
    block_count: int,
) -> List[List[Tuple[int, int]]]:
    segments = ACCESSIBLE_SEGMENTS[chrom]
    total_length = total_accessible_length(chrom)
    boundaries = [0]
    for index in range(1, block_count):
        boundaries.append(int((total_length * index) / block_count))
    boundaries.append(total_length)

    grouped_intervals: List[List[Tuple[int, int]]] = []
    seg_index = 0
    seg_start, seg_end = segments[seg_index]
    current = seg_start

    for block_start_offset, block_end_offset in zip(boundaries[:-1], boundaries[1:]):
        block_intervals: List[Tuple[int, int]] = []
        block_cursor = block_start_offset
        while block_cursor < block_end_offset and seg_index < len(segments):
            seg_available = seg_end - current
            if seg_available <= 0:
                seg_index += 1
                if seg_index >= len(segments):
                    break
                seg_start, seg_end = segments[seg_index]
                current = seg_start
                continue
            remaining = block_end_offset - block_cursor
            chunk = min(seg_available, remaining)
            chunk_end = current + chunk
            block_intervals.append((current, chunk_end))
            current = chunk_end
            block_cursor += chunk
            if current >= seg_end and seg_index + 1 < len(segments):
                seg_index += 1
                seg_start, seg_end = segments[seg_index]
                current = seg_start
        grouped_intervals.append(block_intervals)
    return grouped_intervals


def build_transmissions() -> Dict[str, List[TransmissionBlock]]:
    transmissions: Dict[str, List[TransmissionBlock]] = {"son": [], "daughter": []}
    for sample_id in transmissions:
        sample_offset = SAMPLE_INDEX[sample_id]
        for chrom in ALL_CHROMS:
            block_count = 3 + ((chrom_numeric(chrom) + sample_offset) % 2)
            paternal = (chrom_numeric(chrom) + sample_offset) % 2
            maternal = (chrom_numeric(chrom) + sample_offset + 1) % 2
            for block_index, block_intervals in enumerate(slice_accessible_block_groups(chrom, block_count), start=1):
                if block_index > 1:
                    if (chrom_numeric(chrom) + block_index + sample_offset) % 2 == 0:
                        paternal = 1 - paternal
                    else:
                        maternal = 1 - maternal
                block_id = int(f"{chrom_numeric(chrom):02d}{sample_offset + 1}{block_index:02d}")
                for piece_index, (start, end) in enumerate(block_intervals, start=1):
                    phase_set = int(f"{chrom_numeric(chrom):02d}{sample_offset + 1}{block_index:02d}{piece_index:02d}")
                    transmissions[sample_id].append(
                        TransmissionBlock(
                            chrom=chrom,
                            start=start,
                            end=end,
                            paternal_haplotype=paternal,
                            maternal_haplotype=maternal,
                            block_id=block_id,
                            phase_set=phase_set,
                        )
                    )
    return transmissions


def transmission_for(
    transmissions: Mapping[str, Sequence[TransmissionBlock]],
    sample_id: str,
    chrom: str,
    pos: int,
) -> Tuple[int, int, int]:
    for block in transmissions[sample_id]:
        if block.chrom == chrom and block.start <= pos < block.end:
            return block.paternal_haplotype, block.maternal_haplotype, block.phase_set
    fallback = int(f"{chrom_numeric(chrom):02d}{SAMPLE_INDEX[sample_id] + 1}999")
    return 0, 1, fallback


def parent_phase_set(chrom: str, pos: int, sample_id: str) -> int:
    segment_index = 1
    for start, end in ACCESSIBLE_SEGMENTS[chrom]:
        if start <= pos < end:
            break
        segment_index += 1
    return int(f"{chrom_numeric(chrom):02d}{SAMPLE_INDEX[sample_id] + 1}{segment_index:03d}")


def generate_coverage(sample_id: str, rng: random.Random) -> List[str]:
    lines: List[str] = []
    sample_offset = (SAMPLE_INDEX[sample_id] - 1.5) * 0.006
    for chrom in ALL_CHROMS:
        chrom_index = chrom_numeric(chrom)
        for seg_start, seg_end in ACCESSIBLE_SEGMENTS[chrom]:
            start = seg_start
            while start < seg_end:
                end = min(start + COVERAGE_BIN_SIZE, seg_end)
                center = (start + end) // 2
                event = event_at(sample_id, chrom, center)
                if event is None:
                    value = sample_offset
                    value += 0.032 * math.sin(center / 2_800_000 + chrom_index * 0.37)
                    value += 0.019 * math.cos(center / 900_000 + chrom_index * 0.21)
                    value += rng.uniform(-0.085, 0.085)
                else:
                    value = event_ratio(event)
                    value += 0.016 * math.sin(center / 220_000 + chrom_index * 0.09)
                    value += rng.uniform(-0.060, 0.060)
                lines.append(f"{chrom}\t{start}\t{end}\t{clamp(value, -1.18, 0.82):.4f}")
                start = end
    return lines


def generate_segments(sample_id: str) -> List[str]:
    lines: List[str] = []
    events_by_chrom: Dict[str, List[CopyNumberEvent]] = {chrom: [] for chrom in ALL_CHROMS}
    for event in CNV_EVENTS_BY_SAMPLE[sample_id]:
        events_by_chrom[event.chrom].append(event)

    for chrom in ALL_CHROMS:
        events = sorted(events_by_chrom[chrom], key=lambda item: item.start)
        for seg_start, seg_end in ACCESSIBLE_SEGMENTS[chrom]:
            cursor = seg_start
            overlapping = [event for event in events if event.end > seg_start and event.start < seg_end]
            if not overlapping:
                lines.append(f"{chrom}\t{seg_start}\t{seg_end}\t0.0000")
                continue
            for event in overlapping:
                event_start = max(seg_start, event.start)
                event_end = min(seg_end, event.end)
                if cursor < event_start:
                    lines.append(f"{chrom}\t{cursor}\t{event_start}\t0.0000")
                lines.append(f"{chrom}\t{event_start}\t{event_end}\t{event_ratio(event):.4f}")
                cursor = event_end
            if cursor < seg_end:
                lines.append(f"{chrom}\t{cursor}\t{seg_end}\t0.0000")
    return lines


def apcad_origin(sample_id: str, chrom: str, pos: int, transmissions: Mapping[str, Sequence[TransmissionBlock]]) -> str:
    event = event_at(sample_id, chrom, pos)
    if event is not None:
        return event.origin
    if sample_id in {"son", "daughter"}:
        paternal, maternal, _ = transmission_for(transmissions, sample_id, chrom, pos)
        return "paternal" if (paternal + maternal + pos // 250_000) % 2 == 0 else "maternal"
    if (chrom_numeric(chrom) + pos // 400_000 + SAMPLE_INDEX[sample_id]) % 3 == 0:
        return "und"
    return "paternal" if (chrom_numeric(chrom) + pos // 175_000) % 2 == 0 else "maternal"


def noisy_cluster(center: float, rng: random.Random, width: float = 0.035) -> float:
    return clamp(rng.uniform(center - width, center + width), 0.0, 1.0)


def generate_apcad(sample_id: str, rng: random.Random, transmissions: Mapping[str, Sequence[TransmissionBlock]]) -> Tuple[List[str], List[str]]:
    upload_lines: List[str] = []
    import_lines: List[str] = []
    quotas = allocate_counts(APCAD_TARGET_POINTS, ALL_CHROMS, use_accessible=True)

    record_index = 1
    for chrom in ALL_CHROMS:
        positions = positions_in_segments(chrom, quotas[chrom], rng, margin=2_000)
        for pos in positions:
            event = event_at(sample_id, chrom, pos)
            origin = apcad_origin(sample_id, chrom, pos, transmissions)
            if event is None:
                if origin == "und":
                    value = noisy_cluster(0.50, rng, 0.045)
                else:
                    value = rng.choices(
                        [noisy_cluster(0.05, rng), noisy_cluster(0.50, rng), noisy_cluster(0.95, rng)],
                        weights=[0.28, 0.44, 0.28],
                        k=1,
                    )[0]
            elif event.svtype == "DEL":
                value = rng.choice([noisy_cluster(0.04, rng), noisy_cluster(0.96, rng)])
            else:
                if event.origin in {"paternal", "maternal"}:
                    value = rng.choices(
                        [
                            noisy_cluster(0.04, rng),
                            noisy_cluster(0.3333, rng, 0.028),
                            noisy_cluster(0.6667, rng, 0.028),
                            noisy_cluster(0.96, rng),
                        ],
                        weights=[0.12, 0.38, 0.38, 0.12],
                        k=1,
                    )[0]
                else:
                    value = rng.choices(
                        [
                            noisy_cluster(0.04, rng),
                            noisy_cluster(0.3333, rng, 0.03),
                            noisy_cluster(0.6667, rng, 0.03),
                            noisy_cluster(0.96, rng),
                        ],
                        weights=[0.18, 0.32, 0.32, 0.18],
                        k=1,
                    )[0]

            record_id = f"apcad_{sample_id}_{record_index:05d}"
            ref, alt = rng.choice([("A", "G"), ("C", "T"), ("G", "A"), ("T", "C")])
            upload_lines.append(f"{chrom}\t{pos - 1}\t{pos}\t{record_id}\t{value:.4f}\t{origin}")
            import_lines.append(f"{chrom}\t{pos}\t{ref}\t{alt}\t{record_id}\t{origin}\t{value:.4f}")
            record_index += 1

    return upload_lines, import_lines


def membership_counts_per_sample() -> Dict[str, int]:
    counts = {sample["sample_id"]: 0 for sample in SAMPLES}
    for _, members, count in STRUCTURAL_CATEGORY_COUNTS:
        for member in members:
            counts[member] += count
    return counts


def expand_structural_memberships() -> List[Tuple[str, Tuple[str, ...]]]:
    memberships: List[Tuple[str, Tuple[str, ...]]] = []
    for label, members, count in STRUCTURAL_CATEGORY_COUNTS:
        memberships.extend([(label, members)] * count)
    return memberships


def pop_membership(memberships: List[Tuple[str, Tuple[str, ...]]], members: Tuple[str, ...]) -> Tuple[str, Tuple[str, ...]]:
    members = tuple(sorted(members))
    for index, item in enumerate(memberships):
        if tuple(sorted(item[1])) == members:
            return memberships.pop(index)
    raise ValueError(f"No remaining structural membership slot for {members}")


def choose_small_sv_type(rng: random.Random) -> str:
    return rng.choices(["DEL", "INS", "INV", "DUP"], weights=[0.34, 0.26, 0.26, 0.14], k=1)[0]


def sv_length(svtype: str, rng: random.Random, *, large: bool = False) -> int:
    if svtype == "INS":
        return rng.randint(50, 3_000)
    if svtype == "INV":
        return rng.randint(500_000, 2_400_000) if large else rng.randint(5_000, 50_000)
    if svtype == "DUP":
        return rng.randint(8_000, 140_000)
    return rng.randint(6_000, 120_000)


def bnd_alt(chrom: str, pos: int, remote_chrom: str, remote_pos: int) -> str:
    if chrom_sort_key(chrom) <= chrom_sort_key(remote_chrom):
        return f"N[{remote_chrom}:{remote_pos}["
    return f"]{remote_chrom}:{remote_pos}]N"


def build_structural_events(rng: random.Random) -> List[StructuralEvent]:
    memberships = expand_structural_memberships()
    events: List[StructuralEvent] = []
    used_positions: set[Tuple[str, int, int, str]] = set()

    def add_event(event: StructuralEvent) -> None:
        events.append(event)
        used_positions.add((event.chrom, event.start, event.end, event.svtype))

    reserved_index = 1
    for group in CNV_GROUPS:
        pop_membership(memberships, group.members)
        start = group.start
        end = group.end
        add_event(
            StructuralEvent(
                variant_id=f"DEMO_SV_{reserved_index:05d}",
                chrom=group.chrom,
                start=start,
                end=end,
                svtype=group.svtype,
                ref="N",
                alt=f"<{group.svtype}>",
                members=group.members,
                qual=88.0,
                source_label=group.label,
            )
        )
        reserved_index += 1

    large_inversions = [
        ("3", 61_200_000, 63_100_000, ("father", "mother", "son", "daughter")),
        ("8", 18_300_000, 19_700_000, ("father", "daughter")),
        ("14", 72_500_000, 74_200_000, ("mother", "son")),
    ]
    for chrom, start, end, members in large_inversions:
        pop_membership(memberships, members)
        add_event(
            StructuralEvent(
                variant_id=f"DEMO_SV_{reserved_index:05d}",
                chrom=chrom,
                start=start,
                end=end,
                svtype="INV",
                ref="N",
                alt="<INV>",
                members=members,
                qual=79.0,
                source_label="large inversion",
            )
        )
        reserved_index += 1

    translocations = [
        ("5", 33_400_000, "12", 44_200_000, ("father", "son")),
        ("7", 72_100_000, "18", 14_300_000, ("mother", "daughter")),
        ("9", 58_300_000, "22", 23_400_000, ("father", "mother", "son", "daughter")),
    ]
    for chrom, start, remote_chrom, remote_start, members in translocations:
        pop_membership(memberships, members)
        add_event(
            StructuralEvent(
                variant_id=f"DEMO_SV_{reserved_index:05d}",
                chrom=chrom,
                start=start,
                end=start + 1,
                svtype="BND",
                ref="N",
                alt=bnd_alt(chrom, start, remote_chrom, remote_start),
                members=members,
                qual=71.0,
                source_label="interchromosomal translocation",
                remote_chr=remote_chrom,
                remote_start=remote_start,
                remote_end=remote_start + 1,
            )
        )
        reserved_index += 1

    rng.shuffle(memberships)

    quotas = allocate_counts(len(memberships), ALL_CHROMS, use_accessible=True)
    slots: List[Tuple[str, int]] = []
    for chrom in ALL_CHROMS:
        slots.extend((chrom, pos) for pos in positions_in_segments(chrom, quotas[chrom], rng, margin=8_000))
    slots.sort(key=lambda item: (chrom_sort_key(item[0]), item[1]))

    for index, ((label, members), (chrom, start)) in enumerate(zip(memberships, slots), start=reserved_index):
        svtype = choose_small_sv_type(rng)
        if (chrom, start, start + 1, svtype) in used_positions:
            start += 137
        length = sv_length(svtype, rng)
        end = min(CHROM_LENGTHS[chrom] - 1, start + length)
        if end <= start:
            end = start + 1
        add_event(
            StructuralEvent(
                variant_id=f"DEMO_SV_{index:05d}",
                chrom=chrom,
                start=start,
                end=end,
                svtype=svtype,
                ref="N",
                alt=f"<{svtype}>",
                members=members,
                qual=round(rng.uniform(32.0, 96.0), 2),
                source_label=label,
            )
        )
    return events


def sniffles_vcf_for(sample_id: str, events: Sequence[StructuralEvent]) -> str:
    sample_events = [event for event in events if sample_id in event.members]
    lines = [
        "##fileformat=VCFv4.2",
        "##source=CoGADemoFamilySniffles",
        "##INFO=<ID=SVTYPE,Number=1,Type=String,Description=\"Structural variant type\">",
        "##INFO=<ID=END,Number=1,Type=Integer,Description=\"End position\">",
        "##INFO=<ID=SVLEN,Number=1,Type=Integer,Description=\"SV length\">",
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">",
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample_id}",
    ]
    for event in sample_events:
        info = [f"SVTYPE={event.svtype}", f"END={event.end}", f"SVLEN={event.end - event.start}"]
        lines.append(
            "\t".join(
                [
                    event.chrom,
                    str(event.start),
                    event.variant_id,
                    event.ref,
                    event.alt,
                    f"{event.qual:.2f}",
                    "PASS",
                    ";".join(info),
                    "GT",
                    "0/1",
                ]
            )
        )
    return "\n".join(lines) + "\n"


def spectre_vcf_for(sample_id: str, events: Sequence[StructuralEvent]) -> str:
    sample_events = [event for event in events if sample_id in event.members]
    lines = [
        "##fileformat=VCFv4.2",
        "##source=CoGADemoFamilySpectre",
        "##INFO=<ID=SVTYPE,Number=1,Type=String,Description=\"Structural variant type\">",
        "##INFO=<ID=END,Number=1,Type=Integer,Description=\"End position\">",
        "##INFO=<ID=SVLEN,Number=1,Type=Integer,Description=\"SV length\">",
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">",
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample_id}",
    ]
    for index, event in enumerate(sample_events, start=1):
        info = [f"SVTYPE={event.svtype}", f"END={event.end}", f"SVLEN={event.end - event.start}"]
        lines.append(
            "\t".join(
                [
                    event.chrom,
                    str(event.start),
                    f"{sample_id}_SPEC_{index:05d}",
                    event.ref,
                    event.alt,
                    f"{event.qual + 1.2:.2f}",
                    "PASS",
                    ";".join(info),
                    "GT",
                    "0/1",
                ]
            )
        )
    return "\n".join(lines) + "\n"


def manual_structural_tsv_for(sample_id: str, events: Sequence[StructuralEvent]) -> str:
    sample_events = [event for event in events if sample_id in event.members]
    lines = []
    for event in sample_events:
        alt = event.alt
        if event.svtype == "BND" and event.remote_chr and event.remote_start:
            alt = bnd_alt(event.chrom, event.start, event.remote_chr, event.remote_start)
        lines.append(
            "\t".join(
                [
                    event.variant_id,
                    event.chrom,
                    str(event.start),
                    str(event.end),
                    event.ref,
                    alt,
                    event.svtype,
                    "0/1",
                ]
            )
        )
    return "\n".join(lines) + "\n"


def choose_snv_bases(rng: random.Random) -> Tuple[str, str]:
    ref = rng.choice(["A", "C", "G", "T"])
    alt = rng.choice([base for base in ["A", "C", "G", "T"] if base != ref])
    return ref, alt


def choose_gene(chrom: str, index: int) -> Dict[str, str]:
    genes = GENES_BY_CHROM.get(chrom, GENES_BY_CHROM["17"])
    return genes[index % len(genes)]


def choose_effect(index: int) -> Tuple[str, str]:
    bucket = index % 20
    if bucket < 12:
        return "synonymous_variant", "LOW"
    if bucket < 17:
        return "missense_variant", "MODERATE"
    if bucket < 19:
        return "splice_donor_variant", "HIGH"
    return "stop_gained", "HIGH"


def protein_change(index: int, effect: str) -> Tuple[str, str]:
    aa_left = AMINO_ACIDS[index % len(AMINO_ACIDS)][0]
    aa_right = AMINO_ACIDS[(index + 7) % len(AMINO_ACIDS)][0]
    codon = 45 + (index % 320)
    if effect == "synonymous_variant":
        return f"c.{codon * 3}A>G", f"p.{aa_left}{codon}="
    if effect == "missense_variant":
        return f"c.{codon * 3 + 1}A>G", f"p.{aa_left}{codon}{aa_right}"
    if effect == "splice_donor_variant":
        exon = (index % 18) + 1
        return f"c.{exon * 120 + 1}+1G>A", ""
    return f"c.{codon * 3 + 2}C>T", f"p.{aa_left}{codon}Ter"


def choose_clinvar(effect: str, rng: random.Random) -> str:
    if effect == "synonymous_variant":
        return rng.choices(["Benign", "Likely_benign", "Uncertain_significance"], weights=[0.45, 0.40, 0.15], k=1)[0]
    if effect == "missense_variant":
        return rng.choices(["Likely_benign", "Uncertain_significance", "Likely_pathogenic"], weights=[0.25, 0.55, 0.20], k=1)[0]
    return rng.choices(["Uncertain_significance", "Likely_pathogenic", "Pathogenic"], weights=[0.20, 0.35, 0.45], k=1)[0]


def choose_frequency(effect: str, rng: random.Random) -> float:
    if effect == "synonymous_variant":
        return round(rng.uniform(0.0005, 0.08), 6)
    if effect == "missense_variant":
        return round(rng.uniform(0.0, 0.008), 6)
    return round(rng.uniform(0.0, 0.0004), 6)


def choose_gnomad_hom_count(effect: str, gnomad_af: float, rng: random.Random) -> int:
    if effect == "synonymous_variant":
        return rng.randint(2, 38) if gnomad_af > 0.001 else rng.randint(0, 4)
    if effect == "missense_variant":
        return rng.randint(0, 3) if gnomad_af > 0.0005 else 0
    return 0


def choose_gene_constraint(effect: str, rng: random.Random) -> tuple[float, float]:
    if effect == "synonymous_variant":
        return round(rng.uniform(0.02, 0.35), 3), round(rng.uniform(-1.4, 1.2), 2)
    if effect == "missense_variant":
        return round(rng.uniform(0.45, 0.96), 3), round(rng.uniform(2.2, 6.8), 2)
    return round(rng.uniform(0.82, 0.999), 3), round(rng.uniform(3.5, 7.8), 2)


def choose_scores(effect: str, rng: random.Random) -> Tuple[float, float, tuple[float, float, float, float]]:
    if effect == "synonymous_variant":
        cadd = round(rng.uniform(3.0, 14.0), 2)
        revel = round(rng.uniform(0.01, 0.18), 3)
        splice = (0.0, 0.0, 0.0, 0.0)
    elif effect == "missense_variant":
        cadd = round(rng.uniform(18.0, 31.0), 2)
        revel = round(rng.uniform(0.32, 0.87), 3)
        splice = (0.0, 0.0, 0.0, 0.0)
    else:
        cadd = round(rng.uniform(24.0, 38.0), 2)
        revel = round(rng.uniform(0.68, 0.99), 3)
        splice = (
            round(rng.uniform(0.55, 0.98), 3),
            round(rng.uniform(0.05, 0.25), 3),
            round(rng.uniform(0.05, 0.25), 3),
            round(rng.uniform(0.05, 0.25), 3),
        )
    return cadd, revel, splice


def small_variant_positions(rng: random.Random) -> List[Tuple[str, int]]:
    quotas = allocate_counts_with_floor(
        SMALL_VARIANT_TARGET,
        ALL_CHROMS,
        floor=120,
        use_accessible=True,
    )
    positions: List[Tuple[str, int]] = []
    for chrom in ALL_CHROMS:
        positions.extend(
            (chrom, pos)
            for pos in positions_in_segments(
                chrom,
                quotas[chrom],
                rng,
                margin=1,
                anchor_edges=True,
            )
        )
    return sorted(positions, key=lambda item: (chrom_sort_key(item[0]), item[1]))[:SMALL_VARIANT_TARGET]


def build_small_variant_specs(rng: random.Random) -> List[SmallVariantSpec]:
    specs: List[SmallVariantSpec] = []
    for index, (chrom, pos) in enumerate(small_variant_positions(random.Random(SEED + 91)), start=1):
        gene = choose_gene(chrom, index)
        effect, impact = choose_effect(index)
        hgvsc, hgvsp = protein_change(index, effect)
        cadd, revel, splice = choose_scores(effect, rng)
        gnomad_af = choose_frequency(effect, rng)
        gene_pli, gene_missense_z = choose_gene_constraint(effect, rng)
        ref, alt = choose_snv_bases(rng)
        specs.append(
            SmallVariantSpec(
                index=index,
                chrom=chrom,
                pos=pos,
                ref=ref,
                alt=alt,
                effect=effect,
                impact=impact,
                gene=gene,
                exon_number=(index % 18) + 1,
                hgvsc=hgvsc,
                hgvsp=hgvsp,
                rsid=f"rs{index + 1_540_000}",
                clinvar=choose_clinvar(effect, rng),
                gnomad_af=gnomad_af,
                gnomad_hom_count=choose_gnomad_hom_count(effect, gnomad_af, rng),
                gene_pli=gene_pli,
                gene_missense_z=gene_missense_z,
                cadd_phred=cadd,
                revel=revel,
                spliceai=splice,
                omim=gene["omim"],
                pubmed=str(32000000 + index),
            )
        )
    return specs


def genotype_string(a: int, b: int) -> str:
    return f"{a}|{b}"


def gt_tuple(gt: str) -> Tuple[int, int]:
    left, right = gt.split("|", 1)
    return int(left), int(right)


def build_clair3_sample_field(gt: str, ps: int | None, rng: random.Random) -> str:
    alt_count = gt.count("1")
    dp = rng.randint(32, 118)
    if alt_count == 0:
        alt_reads = max(1, int(dp * rng.uniform(0.01, 0.05)))
    elif alt_count == 1:
        alt_reads = int(dp * rng.uniform(0.42, 0.58))
    else:
        alt_reads = int(dp * rng.uniform(0.92, 0.99))
    ref_reads = max(dp - alt_reads, 1)
    gq = rng.randint(55, 99)
    af = alt_reads / max(dp, 1)
    ps_field = "." if ps is None else str(ps)
    return f"{gt}:{gq}:{dp}:{ref_reads},{alt_reads}:{af:.3f}:{ps_field}"


def build_glimpse_sample_field(gt: str, ps: int, rng: random.Random) -> str:
    called_alleles = [allele for allele in gt.replace("/", "|").split("|") if allele in {"0", "1"}]
    alt_count = called_alleles.count("1")
    dp = rng.randint(22, 96)
    if len(called_alleles) == 1:
        if alt_count == 0:
            alt_reads = max(0, int(dp * rng.uniform(0.00, 0.04)))
        else:
            alt_reads = dp - max(0, int(dp * rng.uniform(0.00, 0.05)))
    elif alt_count == 0:
        alt_reads = max(1, int(dp * rng.uniform(0.01, 0.05)))
    elif alt_count == 1:
        alt_reads = int(dp * rng.uniform(0.42, 0.58))
    else:
        alt_reads = int(dp * rng.uniform(0.92, 0.99))
    ref_reads = max(dp - alt_reads, 0)
    af = alt_reads / max(dp, 1)
    if alt_count == 0:
        gp = (0.988, 0.010, 0.002)
    elif alt_count == 1:
        gp = (0.009, 0.980, 0.011)
    else:
        gp = (0.002, 0.012, 0.986)
    jitter = rng.uniform(-0.003, 0.003)
    probs = [
        max(0.001, gp[0] + jitter),
        max(0.001, gp[1] - jitter / 2),
        max(0.001, gp[2] - jitter / 2),
    ]
    total = sum(probs)
    norm = [value / total for value in probs]
    return (
        f"{gt}:{dp}:{ref_reads},{alt_reads}:{af:.3f}:"
        f"{','.join(f'{value:.3f}' for value in norm)}:{ps}"
    )


def sample_genotypes(spec: SmallVariantSpec, transmissions: Mapping[str, Sequence[TransmissionBlock]]) -> Dict[str, Tuple[str, int]]:
    father_gt = "0|1"
    mother_gt = "0|1"
    son_pat, son_mat, son_ps = transmission_for(transmissions, "son", spec.chrom, spec.pos)
    daughter_pat, daughter_mat, daughter_ps = transmission_for(transmissions, "daughter", spec.chrom, spec.pos)
    son_event = event_at("son", spec.chrom, spec.pos)
    daughter_event = event_at("daughter", spec.chrom, spec.pos)

    son_gt = genotype_string(son_pat, son_mat)
    if son_event is not None and son_event.svtype == "DEL":
        if son_event.origin == "paternal":
            son_gt = f".|{son_mat}"
        elif son_event.origin == "maternal":
            son_gt = f"{son_pat}|."

    daughter_gt = genotype_string(daughter_pat, daughter_mat)
    if daughter_event is not None and daughter_event.svtype == "DEL":
        if daughter_event.origin == "paternal":
            daughter_gt = f".|{daughter_mat}"
        elif daughter_event.origin == "maternal":
            daughter_gt = f"{daughter_pat}|."

    return {
        "father": (father_gt, parent_phase_set(spec.chrom, spec.pos, "father")),
        "mother": (mother_gt, parent_phase_set(spec.chrom, spec.pos, "mother")),
        "son": (son_gt, son_ps),
        "daughter": (daughter_gt, daughter_ps),
    }


def csq_fields() -> List[str]:
    return [
        "Allele",
        "Consequence",
        "IMPACT",
        "SYMBOL",
        "Gene",
        "Feature_type",
        "Feature",
        "BIOTYPE",
        "EXON",
        "INTRON",
        "HGVSc",
        "HGVSp",
        "Existing_variation",
        "CLIN_SIG",
        "CANONICAL",
        "MANE_SELECT",
        "gnomAD_AF",
        "gnomAD_Hom",
        "PLI",
        "MISSENSE_Z",
        "CADD_PHRED",
        "REVEL",
        "SpliceAI_pred_DS_AG",
        "SpliceAI_pred_DS_AL",
        "SpliceAI_pred_DS_DG",
        "SpliceAI_pred_DS_DL",
    ]


def csq_value(spec: SmallVariantSpec) -> str:
    ag, al, dg, dl = spec.spliceai
    return "|".join(
        [
            spec.alt,
            spec.effect,
            spec.impact,
            spec.gene["symbol"],
            spec.gene["gene_id"],
            "Transcript",
            spec.gene["transcript"],
            "protein_coding",
            f"{spec.exon_number}/20",
            "",
            spec.hgvsc,
            spec.hgvsp,
            spec.rsid,
            spec.clinvar,
            "YES",
            "YES",
            f"{spec.gnomad_af:.6f}",
            str(spec.gnomad_hom_count),
            f"{spec.gene_pli:.3f}",
            f"{spec.gene_missense_z:.2f}",
            f"{spec.cadd_phred:.2f}",
            f"{spec.revel:.3f}",
            f"{ag:.3f}",
            f"{al:.3f}",
            f"{dg:.3f}",
            f"{dl:.3f}",
        ]
    )


def vcf_info(spec: SmallVariantSpec, genotypes: Mapping[str, Tuple[str, int]]) -> str:
    ac = sum(gt.count("1") for gt, _ in genotypes.values())
    return ";".join(
        [
            f"AC={ac}",
            "AN=8",
            "VT=SNV",
            f"CSQ={csq_value(spec)}",
            f"OMIM={spec.omim}",
            f"PUBMED={spec.pubmed}",
        ]
    )


def generate_clair3_vcf(rng: random.Random, transmissions: Mapping[str, Sequence[TransmissionBlock]]) -> str:
    lines = [
        "##fileformat=VCFv4.2",
        "##source=CoGADemoFamilyClair3VEP",
        f"##INFO=<ID=CSQ,Number=.,Type=String,Description=\"Consequence annotations from Ensembl VEP. Format: {'|'.join(csq_fields())}\">",
        "##INFO=<ID=AC,Number=A,Type=Integer,Description=\"Alternate allele count\">",
        "##INFO=<ID=AN,Number=1,Type=Integer,Description=\"Total number of alleles\">",
        "##INFO=<ID=OMIM,Number=1,Type=String,Description=\"OMIM gene identifier\">",
        "##INFO=<ID=PUBMED,Number=1,Type=String,Description=\"Supporting PubMed identifier\">",
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">",
        "##FORMAT=<ID=GQ,Number=1,Type=Integer,Description=\"Genotype quality\">",
        "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Read depth\">",
        "##FORMAT=<ID=AD,Number=R,Type=Integer,Description=\"Allelic depths\">",
        "##FORMAT=<ID=AF,Number=A,Type=Float,Description=\"Observed alt fraction\">",
        "##FORMAT=<ID=PS,Number=1,Type=Integer,Description=\"Phase set\">",
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tfather\tmother\tson\tdaughter",
    ]

    for spec in build_small_variant_specs(random.Random(SEED + 111)):
        genotypes = sample_genotypes(spec, transmissions)
        sample_fields = [
            build_clair3_sample_field(genotypes["father"][0], genotypes["father"][1], rng),
            build_clair3_sample_field(genotypes["mother"][0], genotypes["mother"][1], rng),
            build_clair3_sample_field(genotypes["son"][0], genotypes["son"][1], rng),
            build_clair3_sample_field(genotypes["daughter"][0], genotypes["daughter"][1], rng),
        ]
        lines.append(
            "\t".join(
                [
                    spec.chrom,
                    str(spec.pos),
                    f"DEMO_SNV_{spec.index:05d}",
                    spec.ref,
                    spec.alt,
                    f"{rng.uniform(96, 260):.2f}",
                    "PASS",
                    vcf_info(spec, genotypes),
                    "GT:GQ:DP:AD:AF:PS",
                    *sample_fields,
                ]
            )
        )
    return "\n".join(lines) + "\n"


def generate_glimpse_vcf(rng: random.Random, transmissions: Mapping[str, Sequence[TransmissionBlock]]) -> str:
    lines = [
        "##fileformat=VCFv4.2",
        "##source=CoGADemoFamilyGLIMPSE2VEP",
        f"##INFO=<ID=CSQ,Number=.,Type=String,Description=\"Consequence annotations from Ensembl VEP. Format: {'|'.join(csq_fields())}\">",
        "##INFO=<ID=OMIM,Number=1,Type=String,Description=\"OMIM gene identifier\">",
        "##INFO=<ID=PUBMED,Number=1,Type=String,Description=\"Supporting PubMed identifier\">",
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Phased genotype\">",
        "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Read depth\">",
        "##FORMAT=<ID=AD,Number=R,Type=Integer,Description=\"Allelic depths\">",
        "##FORMAT=<ID=AF,Number=A,Type=Float,Description=\"Observed alt fraction\">",
        "##FORMAT=<ID=GP,Number=G,Type=Float,Description=\"Genotype probabilities\">",
        "##FORMAT=<ID=PS,Number=1,Type=Integer,Description=\"Phase set\">",
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tfather\tmother\tson\tdaughter",
    ]

    for spec in build_small_variant_specs(random.Random(SEED + 111)):
        genotypes = sample_genotypes(spec, transmissions)
        sample_fields = [
            build_glimpse_sample_field(genotypes["father"][0], genotypes["father"][1], rng),
            build_glimpse_sample_field(genotypes["mother"][0], genotypes["mother"][1], rng),
            build_glimpse_sample_field(genotypes["son"][0], genotypes["son"][1], rng),
            build_glimpse_sample_field(genotypes["daughter"][0], genotypes["daughter"][1], rng),
        ]
        lines.append(
            "\t".join(
                [
                    spec.chrom,
                    str(spec.pos),
                    f"DEMO_HAP_{spec.index:05d}",
                    spec.ref,
                    spec.alt,
                    f"{rng.uniform(52, 90):.2f}",
                    "PASS",
                    vcf_info(spec, genotypes),
                    "GT:DP:AD:AF:GP:PS",
                    *sample_fields,
                ]
            )
        )
    return "\n".join(lines) + "\n"


def trgt_sample_field(sample_id: str, chrom: str, motif: str, allele_counts: Sequence[int]) -> str:
    gt = "1" if chrom == "X" and sample_id in {"father", "son"} else "1/2"
    bp_lengths = [count * max(len(motif), 1) for count in allele_counts]
    support_reads = [max(12, min(48, 10 + count // 3)) for count in allele_counts]
    purity = [f"{min(0.995, 0.94 + (count % 5) * 0.01):.2f}" for count in allele_counts]
    methylation = ["." for _ in allele_counts]
    return ":".join(
        [
            gt,
            ",".join(str(value) for value in bp_lengths),
            ",".join(str(value) for value in allele_counts),
            ",".join(str(value) for value in support_reads),
            ",".join(purity),
            ",".join(methylation),
        ]
    )


def generate_trgt_vcf(sample_id: str) -> str:
    lines = [
        "##fileformat=VCFv4.2",
        "##source=CoGADemoTRGT",
        '##INFO=<ID=TRID,Number=1,Type=String,Description="Repeat locus identifier">',
        '##INFO=<ID=END,Number=1,Type=Integer,Description="End position of the repeat tract">',
        '##INFO=<ID=MOTIFS,Number=.,Type=String,Description="Repeat motifs in locus order">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        '##FORMAT=<ID=AL,Number=.,Type=Integer,Description="Allele lengths in base pairs">',
        '##FORMAT=<ID=MC,Number=.,Type=String,Description="Motif copy counts per allele">',
        '##FORMAT=<ID=SD,Number=.,Type=Integer,Description="Supporting reads per allele">',
        '##FORMAT=<ID=AP,Number=.,Type=Float,Description="Allele purity per allele">',
        '##FORMAT=<ID=AM,Number=.,Type=String,Description="Allele methylation values">',
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample_id}",
    ]
    for spec in TRGT_REPEAT_SPECS:
        motif = str(spec["motif"])
        allele_counts = list(spec["counts"][sample_id])
        start = int(spec["start"])
        end = start + max(allele_counts) * max(len(motif), 1) - 1
        info = f'TRID={spec["locus_id"]};END={end};MOTIFS={motif}'
        lines.append(
            "\t".join(
                [
                    str(spec["chrom"]),
                    str(start),
                    str(spec["locus_id"]),
                    "A",
                    "<STR>",
                    ".",
                    "PASS",
                    info,
                    "GT:AL:MC:SD:AP:AM",
                    trgt_sample_field(sample_id, str(spec["chrom"]), motif, allele_counts),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def recombination_summary(sample_id: str, transmissions: Mapping[str, Sequence[TransmissionBlock]]) -> str:
    lines = ["chrom\tstart\tend\tpaternal_haplotype\tmaternal_haplotype\tphase_set"]
    merged_blocks: List[TransmissionBlock] = []
    for block in transmissions[sample_id]:
        if (
            merged_blocks
            and merged_blocks[-1].chrom == block.chrom
            and merged_blocks[-1].block_id == block.block_id
        ):
            previous = merged_blocks[-1]
            merged_blocks[-1] = TransmissionBlock(
                chrom=previous.chrom,
                start=previous.start,
                end=block.end,
                paternal_haplotype=previous.paternal_haplotype,
                maternal_haplotype=previous.maternal_haplotype,
                block_id=previous.block_id,
                phase_set=previous.phase_set,
            )
            continue
        merged_blocks.append(block)

    for block in merged_blocks:
        lines.append(
            "\t".join(
                [
                    block.chrom,
                    str(block.start),
                    str(block.end),
                    str(block.paternal_haplotype),
                    str(block.maternal_haplotype),
                    str(block.phase_set),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def build_readme(manifest: Dict[str, object]) -> str:
    return f"""# Demo family dataset

This folder contains a deterministic synthetic family dataset for exercising CoGA end-to-end.

## Family

- family id: `{FAMILY_ID}`
- project label suggestion: `{PROJECT_NAME}`
- species: `{SPECIES}`
- assembly: `{ASSEMBLY}`
- father: `father`
- mother: `mother`
- affected child / proband: `son`
- unaffected sibling: `daughter`

This dataset is tuned to behave more like a family-based review case:

- 15 kb coverage bins with noisier log2 ratios centred around `0`
- no coverage, APCAD, or haplotype data in centromeric or heterochromatic gaps
- deletions near `-1.0` and duplications near `+0.58`, mirrored in the SV callsets
- APCAD clusters around `0`, `0.5`, and `1.0` in diploid regions, with deletion and duplication-specific cluster behaviour
- parent haplotypes act as the reference, while both children show about 2 to 3 recombinations per chromosome
- VEP-style annotated Clair3 and GLIMPSE2 VCFs with mostly exonic consequences and rich transcript / ClinVar / gnomAD metadata
- TRGT repeat-expansion VCFs for all four samples, spanning normal, grey-zone, and pathogenic loci

## Folder map

- `pedigree/demo_family.ped`
- `metadata/family_manual.json`
- `metadata/samples.csv`
- `metadata/manifest.json`
- `uploads/bed/coverage/*.coverage.bed`
- `uploads/bed/segments/*.segments.bed`
- `uploads/bed/apcad/*.apcad.bed`
- `uploads/structural_variants/*.structural.tsv`
- `imports/apcad/*.apcad.tsv`
- `imports/structural_variants/sniffles/*.sniffles.vcf`
- `imports/structural_variants/spectre/*.spectre.vcf`
- `imports/small_variants/demo_family.clair3.vcf`
- `imports/small_variants/demo_family.glimpse2.vcf`
- `imports/haplotypes/*.recombination.tsv`
- `imports/repeat_expansions/trgt/*.trgt.vcf`
- `uploads/repeat_expansions/*.trgt.vcf`

## Density summary

{json.dumps(manifest["counts"], indent=2)}

## Regeneration

```bash
python scripts/generate_demo_quartet_dataset.py
```
"""


def build_manifest(counts: Dict[str, object]) -> Dict[str, object]:
    return {
        "family_id": FAMILY_ID,
        "project_name": PROJECT_NAME,
        "assembly": ASSEMBLY,
        "species": SPECIES,
        "samples": SAMPLES,
        "counts": counts,
        "no_data_intervals": {chrom: [[start, end] for start, end in intervals] for chrom, intervals in NO_DATA_INTERVALS.items()},
    }


def main() -> None:
    rng = random.Random(SEED)
    transmissions = build_transmissions()
    structural_events = build_structural_events(random.Random(SEED + 41))

    ensure_clean_dir(OUTPUT_ROOT)

    counts: Dict[str, object] = {
        "coverage_bin_size": COVERAGE_BIN_SIZE,
        "apcad_target_points_per_sample": APCAD_TARGET_POINTS,
        "coverage_bins_per_sample": {},
        "segments_per_sample": {},
        "apcad_upload_rows_per_sample": {},
        "apcad_import_rows_per_sample": {},
        "manual_structural_rows_per_sample": {},
        "sniffles_records_per_sample": {},
        "spectre_records_per_sample": {},
        "trgt_records_per_sample": {},
    }

    write_text(OUTPUT_ROOT / "pedigree" / "demo_family.ped", "\n".join(ped_lines()) + "\n")
    write_json(OUTPUT_ROOT / "metadata" / "family_manual.json", family_manual_payload())
    write_text(OUTPUT_ROOT / "metadata" / "samples.csv", "\n".join(sample_manifest_rows()) + "\n")

    for sample in SAMPLES:
        sample_id = sample["sample_id"]
        coverage = generate_coverage(sample_id, rng)
        segments = generate_segments(sample_id)
        apcad_upload, apcad_import = generate_apcad(sample_id, rng, transmissions)
        manual_structural = manual_structural_tsv_for(sample_id, structural_events)
        sniffles = sniffles_vcf_for(sample_id, structural_events)
        spectre = spectre_vcf_for(sample_id, structural_events)
        trgt = generate_trgt_vcf(sample_id)

        write_text(OUTPUT_ROOT / "uploads" / "bed" / "coverage" / f"{sample_id}.coverage.bed", "\n".join(coverage) + "\n")
        write_text(OUTPUT_ROOT / "uploads" / "bed" / "segments" / f"{sample_id}.segments.bed", "\n".join(segments) + "\n")
        write_text(OUTPUT_ROOT / "uploads" / "bed" / "apcad" / f"{sample_id}.apcad.bed", "\n".join(apcad_upload) + "\n")
        write_text(OUTPUT_ROOT / "imports" / "apcad" / f"{sample_id}.apcad.tsv", "\n".join(apcad_import) + "\n")
        write_text(OUTPUT_ROOT / "uploads" / "structural_variants" / f"{sample_id}.structural.tsv", manual_structural)
        write_text(OUTPUT_ROOT / "imports" / "structural_variants" / "sniffles" / f"{sample_id}.sniffles.vcf", sniffles)
        write_text(OUTPUT_ROOT / "imports" / "structural_variants" / "spectre" / f"{sample_id}.spectre.vcf", spectre)
        write_text(OUTPUT_ROOT / "uploads" / "repeat_expansions" / f"{sample_id}.trgt.vcf", trgt)
        write_text(OUTPUT_ROOT / "imports" / "repeat_expansions" / "trgt" / f"{sample_id}.trgt.vcf", trgt)

        counts["coverage_bins_per_sample"][sample_id] = len(coverage)
        counts["segments_per_sample"][sample_id] = len(segments)
        counts["apcad_upload_rows_per_sample"][sample_id] = len(apcad_upload)
        counts["apcad_import_rows_per_sample"][sample_id] = len(apcad_import)
        counts["manual_structural_rows_per_sample"][sample_id] = len([line for line in manual_structural.splitlines() if line.strip()])
        counts["sniffles_records_per_sample"][sample_id] = len([line for line in sniffles.splitlines() if line and not line.startswith("#")])
        counts["spectre_records_per_sample"][sample_id] = len([line for line in spectre.splitlines() if line and not line.startswith("#")])
        counts["trgt_records_per_sample"][sample_id] = len([line for line in trgt.splitlines() if line and not line.startswith("#")])

    clair3_vcf = generate_clair3_vcf(random.Random(SEED + 61), transmissions)
    glimpse_vcf = generate_glimpse_vcf(random.Random(SEED + 73), transmissions)
    write_text(OUTPUT_ROOT / "imports" / "small_variants" / "demo_family.clair3.vcf", clair3_vcf)
    write_text(OUTPUT_ROOT / "imports" / "small_variants" / "demo_family.glimpse2.vcf", glimpse_vcf)
    counts["clair3_family_variants"] = len([line for line in clair3_vcf.splitlines() if line and not line.startswith("#")])
    counts["glimpse_family_variants"] = len([line for line in glimpse_vcf.splitlines() if line and not line.startswith("#")])

    for sample_id in ("son", "daughter"):
        summary = recombination_summary(sample_id, transmissions)
        write_text(OUTPUT_ROOT / "imports" / "haplotypes" / f"{sample_id}.recombination.tsv", summary)
        counts.setdefault("recombination_blocks_per_child", {})[sample_id] = len([line for line in summary.splitlines()[1:] if line.strip()])

    manifest = build_manifest(counts)
    write_json(OUTPUT_ROOT / "metadata" / "manifest.json", manifest)
    write_text(OUTPUT_ROOT / "README.md", build_readme(manifest))

    print(f"Demo dataset written to {OUTPUT_ROOT}")


if __name__ == "__main__":
    main()
