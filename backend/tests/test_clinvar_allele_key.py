"""The allele key that joins a family's rows to a ClinVar release (``minimal_allele_id``,
``small_allele_key``).

CoGA stores a record as its VCF writes it: untrimmed, a multi-allelic record one row. ClinVar
writes one allele per record, anchored. Both sides key each ALT by its minimal form: the
chromosome canonical (``MT`` as ``M``), the shared suffix taken off, then the shared prefix down
to one anchor base. The form is not left-aligned (that needs the reference genome). Synthetic
alleles only.
"""

from __future__ import annotations

import pytest

from backend.app.services.clickhouse_variant_ids import (
    minimal_allele_id,
    small_allele_key,
    small_variant_key,
)


@pytest.mark.parametrize(
    ("record", "minimal"),
    [
        # A SNV is its own minimal form.
        (("chr1", 100, "G", "A"), "1-100-G-A"),
        # The second ALT of 1 100 AT A,ATT is ClinVar's 1 100 A AT.
        (("1", 100, "AT", "ATT"), "1-100-A-AT"),
        (("1", 100, "AT", "A"), "1-100-AT-A"),
        # The shared suffix first, then the prefix down to one anchor base.
        (("1", 100, "CAGT", "CGT"), "1-100-CA-C"),
        (("1", 100, "GACT", "GATT"), "1-102-C-T"),
        # A multi-base substitution keeps the bases that differ.
        (("1", 100, "ACG", "ATG"), "1-101-C-T"),
        # Bases are compared upper case.
        (("1", 100, "at", "att"), "1-100-A-AT"),
        # mtDNA is M however the file names it.
        (("MT", 3243, "A", "G"), "M-3243-A-G"),
        (("chrM", 3243, "A", "G"), "M-3243-A-G"),
        (("chrMT", 3243, "A", "G"), "M-3243-A-G"),
    ],
)
def test_an_allele_is_keyed_in_its_minimal_form(record: tuple[str, int, str, str], minimal: str) -> None:
    assert minimal_allele_id(*record) == minimal


@pytest.mark.parametrize("alt", [".", "*", "<DEL>", "<INS:ME>", "", "A]1:100]"])
def test_an_alt_that_names_no_allele_has_no_key(alt: str) -> None:
    assert minimal_allele_id("1", 100, "A", alt) is None


def test_a_ref_that_is_no_sequence_of_bases_has_no_key() -> None:
    assert minimal_allele_id("1", 100, ".", "A") is None


def test_the_pipelines_and_clinvars_writing_of_one_allele_share_a_key() -> None:
    """A pipeline's multi-allelic, untrimmed record and ClinVar's anchored record of the same
    allele: one key, per assembly."""
    pipeline = minimal_allele_id("chr1", 100, "ATG", "ATTG")
    clinvar = minimal_allele_id("1", 100, "A", "AT")

    assert pipeline == clinvar == "1-100-A-AT"
    assert small_allele_key("GRCh38", pipeline) == small_allele_key("GRCh38", clinvar)
    assert small_allele_key("GRCh38", clinvar) != small_allele_key("GRCh37", clinvar)


def test_the_allele_key_is_not_the_storage_key() -> None:
    """The storage key hashes the record as written; the allele key is a hash of its own."""
    assert small_allele_key("GRCh38", "1-100-G-A") != small_variant_key("GRCh38", "1-100-G-A")
    assert 0 <= small_allele_key("GRCh38", "1-100-G-A") < 2**64
