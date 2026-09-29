"""The knowledgebase's ClinVar loss/gain support reaches clinical_cnvs and the API (#624).

The build writes, per region, the pathogenic ClinVar CNVs that overlap it (loss and gain
counts and their VariationIDs). The loader dropped those columns, so curators never saw
them. It now keeps them, and a knowledgebase without them (built without ClinVar, or a
BED-style file) stores NULL: not recorded, never 0.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.app.services import reference_metadata_service as rms

_HEADER = (
    "cnv_id\tsyndrome_name\tchromosome\tstart\tend\tcytoband\tsource\tsource_id\t"
    "clinvar_pathogenic_loss_count\tclinvar_pathogenic_gain_count\tclinvar_pathogenic_accessions"
)


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))

    async def commit(self) -> None:
        return None


def _load(text_value: str, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    async def fake_assembly(_session, assembly_id):
        return {"id": assembly_id, "assembly_name": "GRCh38"}

    async def no_existing(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(rms, "_get_assembly_by_id", fake_assembly)
    monkeypatch.setattr(rms, "_assembly_dataset_count", no_existing)
    session = _RecordingSession()
    asyncio.run(
        rms.apply_reference_dataset_text(
            session,  # type: ignore[arg-type]
            assembly_id="assembly-1",
            dataset_type="clinical_cnvs",
            text_value=text_value,
            overwrite=True,
        )
    )
    inserts = [params for sql, params in session.calls if "INSERT INTO clinical_cnvs" in sql]
    assert len(inserts) == 1
    return list(inserts[0])  # type: ignore[call-overload]


def _support(row: dict) -> tuple:
    return (
        row["clinvar_pathogenic_loss_count"],
        row["clinvar_pathogenic_gain_count"],
        row["clinvar_pathogenic_accessions"],
    )


def test_the_knowledgebase_clinvar_support_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _load(
        "\n".join([
            _HEADER,
            "kb1\tWilliams-Beuren\tchr7\t73330452\t74799773\t7q11.23\tClinGen\tISCA-37446\t12\t3\t146513;155700",
            "kb2\t1q21.1 region\tchr1\t146500000\t147400000\t1q21.1\tClinGen\tISCA-37397\t0\t0\t",
        ]),
        monkeypatch,
    )

    assert _support(rows[0]) == (12, 3, ["146513", "155700"])
    # Consulted and none found is recorded as such: 0, with no accessions.
    assert _support(rows[1]) == (0, 0, [])


def test_a_knowledgebase_built_without_clinvar_stores_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _load(
        "\n".join([
            _HEADER,
            "kb1\tWilliams-Beuren\tchr7\t73330452\t74799773\t7q11.23\tClinGen\tISCA-37446\t\t\t",
        ]),
        monkeypatch,
    )

    assert _support(rows[0]) == (None, None, None)


def test_a_file_without_the_columns_stores_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    named = _load(
        "chromosome\tstart\tend\tsyndrome_name\nchr7\t73330452\t74799773\tWilliams-Beuren",
        monkeypatch,
    )
    bed = _load("chr7\t73330452\t74799773\tWilliams-Beuren\tClinGen\t", monkeypatch)

    assert _support(named[0]) == (None, None, None)
    assert _support(bed[0]) == (None, None, None)


def test_the_api_serves_the_clinvar_support() -> None:
    base = {
        "id": "cnv-1", "chr": "7", "start": 73330452, "end": 74799773, "label": "Williams-Beuren",
    }
    recorded = rms._clinical_cnv_out(
        {
            **base,
            "clinvar_pathogenic_loss_count": 12,
            "clinvar_pathogenic_gain_count": 3,
            "clinvar_pathogenic_accessions": ["146513", "155700"],
        },
        assembly="GRCh38",
    )
    unrecorded = rms._clinical_cnv_out(base, assembly="GRCh38")

    assert (recorded.clinvar_pathogenic_loss_count, recorded.clinvar_pathogenic_gain_count) == (12, 3)
    assert recorded.clinvar_pathogenic_accessions == ["146513", "155700"]
    assert unrecorded.clinvar_pathogenic_loss_count is None
    assert unrecorded.clinvar_pathogenic_accessions is None
