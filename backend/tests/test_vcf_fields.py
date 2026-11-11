"""The one parser of a VCF record's INFO column and of a sample's FORMAT values
(``services/vcf_fields.py``), shared by every reader that splits a record itself:
the TRGT repeat calls, the structural-variant and small-variant uploads, the per-sample
callsets and the family-package readers. These tests pin what each reader reads through it."""

from __future__ import annotations

import pytest

from backend.app.services import (
    family_package_tracks,
    family_package_variants,
    per_sample_small_variants,
    repeat_expansion_pg,
    structural_variant_ingest,
    variant_upload_service,
)
from backend.app.services.family_package_variants import _iter_cnv_structural_records
from backend.app.services.vcf_fields import parse_format, parse_info

# --- INFO ------------------------------------------------------------------------------------


def test_info_reads_each_item_as_a_key_and_its_value_split_at_the_first_equals_sign() -> None:
    assert parse_info("SVTYPE=DEL;END=2000;ANN=a=b") == {"SVTYPE": "DEL", "END": "2000", "ANN": "a=b"}


def test_a_flag_is_left_out_unless_the_reader_keeps_flags() -> None:
    # The family-package readers keep a flag as "true"; the other readers leave it out.
    assert parse_info("IMPRECISE;SVTYPE=DEL") == {"SVTYPE": "DEL"}
    assert parse_info("IMPRECISE;SVTYPE=DEL", flags=True) == {"IMPRECISE": "true", "SVTYPE": "DEL"}


@pytest.mark.parametrize("info_field", ["", ".", ";", ";;"])
def test_a_missing_info_and_empty_items_give_nothing(info_field: str) -> None:
    assert parse_info(info_field) == {}
    assert parse_info(info_field, flags=True) == {}


def test_an_empty_value_is_kept_and_a_key_given_twice_keeps_its_last_value() -> None:
    assert parse_info("DB=;AF=0.1;AF=0.2") == {"DB": "", "AF": "0.2"}
    assert parse_info("=1") == {"": "1"}


# --- FORMAT ----------------------------------------------------------------------------------


def test_format_pairs_each_key_with_its_value_in_order() -> None:
    assert parse_format("GT:DP:AD", "0/1:12:3,9") == {"GT": "0/1", "DP": "12", "AD": "3,9"}


def test_a_key_without_a_value_is_left_out_never_paired_with_another_keys_value() -> None:
    # VCF lets a sample drop its trailing fields.
    assert parse_format("GT:DP:AD", "0/1:12") == {"GT": "0/1", "DP": "12"}
    assert parse_format("GT", "0/1:12") == {"GT": "0/1"}


def test_an_empty_sample_field_gives_the_first_key_an_empty_value() -> None:
    assert parse_format("GT:DP", "") == {"GT": ""}
    assert parse_format("", "0/1") == {"": "0/1"}


# --- The readers -----------------------------------------------------------------------------


def test_every_reader_reads_through_the_shared_parsers() -> None:
    # Under the names each reader's code and tests use; a copy of its own would differ again.
    assert repeat_expansion_pg.parse_info is structural_variant_ingest.parse_info is parse_info
    assert repeat_expansion_pg.parse_format is structural_variant_ingest.parse_format is parse_format
    assert variant_upload_service._parse_info is per_sample_small_variants._parse_info is parse_info
    assert variant_upload_service._parse_format is per_sample_small_variants._parse_format is parse_format
    assert family_package_variants.parse_info is family_package_tracks.parse_info is parse_info
    assert family_package_variants.parse_format is family_package_tracks.parse_format is parse_format


def test_a_package_cnv_call_without_sample_values_is_a_no_call_without_a_copy_number() -> None:
    # The shared parser reads an empty sample field as an empty GT, which the reader takes for
    # a no-call.
    vcf = (
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample0\n"
        "chr1\t1000\tHiFiCNV.DUP.1\tN\t<DUP>\t20\tPASS\tSVTYPE=DUP;END=9000;IMPRECISE\tGT:CN\t\n"
    )

    (record,) = _iter_cnv_structural_records(vcf, sample_id="PROBAND")

    (call,) = record.calls
    assert (call.gt, call.copy_number) == ("./.", None)
    assert (record.start, record.end) == (1000, 9000)
