from __future__ import annotations

from pathlib import Path
from typing import Sequence

from fastapi import HTTPException
import pysam
from pyfaidx import Fasta

from ..core.config import settings
from ..schemas import ReferenceReadOut, ReferenceReadsOut, ReferenceSequenceOut
from .data_scope import chromosome_aliases


def _chromosome_options(chrom: str) -> list[str]:
    return chromosome_aliases(chrom)


def _resolve_matching_name(available_names: Sequence[str], chrom: str) -> str:
    for candidate in _chromosome_options(chrom):
        if candidate in available_names:
            return candidate
    raise HTTPException(status_code=404, detail=f"Chromosome '{chrom}' not found")


def _validate_interval(start: int, end: int) -> None:
    if end <= start:
        raise HTTPException(status_code=400, detail="The end coordinate must be greater than start")


def get_reference_sequence_data(chrom: str, start: int, end: int) -> ReferenceSequenceOut:
    _validate_interval(start, end)

    fasta_path = settings.reference_fasta_path
    if not fasta_path:
        raise HTTPException(status_code=503, detail="Reference FASTA path is not configured")

    fasta = Fasta(fasta_path)
    try:
        reference_name = _resolve_matching_name(list(fasta.keys()), chrom)
        sequence = fasta[reference_name][start:end].seq
    finally:
        fasta.close()

    return ReferenceSequenceOut(sequence=sequence)


def _resolve_alignment_path(sample_id: str) -> tuple[Path, str]:
    reads_path = settings.reads_path
    if not reads_path:
        raise HTTPException(status_code=503, detail="Reads path is not configured")

    root = Path(reads_path)
    candidates = (
        (root / f"{sample_id}.bam", "rb"),
        (root / f"{sample_id}.cram", "rc"),
    )
    for path, mode in candidates:
        if path.exists():
            return path, mode

    raise HTTPException(status_code=404, detail=f"No alignment file found for sample '{sample_id}'")


def get_reference_reads_data(
    sample_id: str,
    chrom: str,
    start: int,
    end: int,
) -> ReferenceReadsOut:
    _validate_interval(start, end)

    alignment_path, mode = _resolve_alignment_path(sample_id)
    open_kwargs = {}
    if mode == "rc" and settings.reference_fasta_path:
        open_kwargs["reference_filename"] = settings.reference_fasta_path

    with pysam.AlignmentFile(str(alignment_path), mode, **open_kwargs) as alignment_file:
        reference_name = _resolve_matching_name(alignment_file.references, chrom)
        reads = [
            ReferenceReadOut(
                pos=read.reference_start,
                seq=read.query_sequence or "",
            )
            for read in alignment_file.fetch(reference_name, start, end)
        ]

    return ReferenceReadsOut(reads=reads)
