"""The genome SV track (track_mode=True) must serve a slim payload: no annotation_extra
or population_frequencies (the track only draws a rectangle from chr/start/end/type and
the per-sample genotype). The SV table (track_mode=False) must keep the full annotation
enrichment. These guard the slimming that took the f_18.16172 track from 182 MB -> ~8 MB
per member."""

from app.services.clickhouse_variant_queries import (
    _structural_annotation_extra,
    _structural_variant_out,
)
from app.services.clickhouse_variant_records import StructuralVariantCall, StructuralVariantRecord


def _record() -> StructuralVariantRecord:
    return StructuralVariantRecord(
        variant_key=1,
        variant_id="sv1",
        chr="1",
        start=1000,
        end=2000,
        sv_type="DEL",
        source="needlr",
        remote_chr=None,
        remote_start=None,
        remote_end=None,
        sv_len=1000,
        filters=[],
        gene_symbols=["BRCA1"],
        annotations=[{"Allele_Freq_ALL": "0.05"}],
        calls=[StructuralVariantCall(sample="DAD", gt="0/1", qual=30.0, read_support=12, filter="PASS")],
    )


def test_structural_annotation_extra_track_mode_returns_empty():
    # The chokepoint: track mode skips all annotation extraction regardless of payload.
    assert _structural_annotation_extra(_record(), track_mode=True) == {}


def test_structural_variant_out_track_mode_zeroes_annotations_keeps_track_fields():
    slim = _structural_variant_out(_record(), ["DAD"], cytoband="1p36.33", track_mode=True)
    # Heavy fields dropped...
    assert slim.annotation_extra == {}
    assert slim.population_frequencies == {}
    # ...but everything the track renders is preserved.
    assert slim.chr == "1"
    assert slim.start == 1000
    assert slim.end == 2000
    assert slim.type == "DEL"
    assert slim.source == "needlr"
    assert [g.gt for g in slim.genotypes] == ["0/1"]


def test_structural_variant_out_table_mode_keeps_annotation_enrichment():
    # track_mode=False (the SV table / detail view) is unchanged: cytoband enrichment
    # still lands in annotation_extra.
    full = _structural_variant_out(_record(), ["DAD"], cytoband="1p36.33", track_mode=False)
    assert full.annotation_extra.get("cytoband") == "1p36.33"
    assert full.chr == "1" and full.type == "DEL"
