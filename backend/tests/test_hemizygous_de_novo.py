"""A son's hemizygous de novo call on chrX or chrY is a de novo candidate (#545).

Outside the pseudo-autosomal regions a male carries one X and one Y, so a de novo variant
there is written as a haploid ``1`` or as ``1/1``, never as ``0/1``. The de novo patterns
required a heterozygous child and never admitted it. A son's X comes from his mother and
his Y from his father, so only that parent has to be confidently reference.
"""

from __future__ import annotations

import types

import pytest

from backend.app.services.clickhouse_variant_queries import (
    _inheritance_result_items,
    _record_matches_de_novo,
    _record_matches_de_novo_dominant,
    _segregation_modes_by_variant,
    _small_native_inheritance_clauses,
)
from backend.app.services.clickhouse_variant_records import SmallVariantCall, SmallVariantRecord
from backend.app.services.family_metadata_context import FamilyMetadataContext
from backend.app.services.family_variant_filters import SmallVariantQueryFilters
from backend.app.services.variant_prioritization import MODE_DE_NOVO, MODE_DOMINANT

X_NON_PAR = 31_500_000
X_PAR1 = 1_000_000
Y_NON_PAR = 2_787_000

TRIO = {"SON": {"MOTHER", "FATHER"}}
ROLES = {"MOTHER": "mother", "FATHER": "father"}
SEXES = {"SON": "male", "DAUGHTER": "female", "MOTHER": "female", "FATHER": "male"}


def _call(sample: str, gt: str, dp: int | None = 30) -> SmallVariantCall:
    return SmallVariantCall(sample=sample, gt=gt, gq=99, dp=dp, af=[], ad=[], ps=None)


def _record(chrom: str, pos: int, *calls: SmallVariantCall) -> SmallVariantRecord:
    return SmallVariantRecord(
        variant_key=1,
        variant_id=f"{chrom}-{pos}",
        chr=chrom,
        start=pos,
        end=pos,
        ref="A",
        alt="G",
        source=None,
        rsid=None,
        filters=[],
        gene_symbols=["GENE1"],
        annotations=[],
        calls=list(calls),
    )


def _is_de_novo(record: SmallVariantRecord, *, child: str = "SON", parents=TRIO, roles=ROLES, assembly="GRCh38") -> bool:
    return _record_matches_de_novo(
        record,
        affected_samples=[child],
        child_parents=parents,
        sample_sex=SEXES,
        parent_roles=roles,
        assembly_name=assembly,
    )


@pytest.mark.parametrize("son_gt", ["1", "1/1", "0/1"])
def test_a_sons_call_on_x_with_a_reference_mother_is_de_novo(son_gt: str) -> None:
    record = _record("chrX", X_NON_PAR, _call("SON", son_gt), _call("MOTHER", "0/0"), _call("FATHER", "0"))
    assert _is_de_novo(record)


def test_the_father_need_not_be_genotyped_for_a_sons_x() -> None:
    # He does not pass an X to a son: a no-call or a low-coverage call is not needed.
    no_call = _record("chrX", X_NON_PAR, _call("SON", "1"), _call("MOTHER", "0/0"), _call("FATHER", "."))
    low_depth = _record("chrX", X_NON_PAR, _call("SON", "1"), _call("MOTHER", "0/0"), _call("FATHER", "0", dp=3))
    absent = _record("chrX", X_NON_PAR, _call("SON", "1"), _call("MOTHER", "0/0"))
    assert _is_de_novo(no_call)
    assert _is_de_novo(low_depth)
    assert _is_de_novo(absent)
    # A mother-son duo is enough for the X.
    assert _is_de_novo(absent, parents={"SON": {"MOTHER"}})


def test_a_father_carrying_the_alt_rules_out_a_sons_x_de_novo() -> None:
    # He cannot have passed it on, but an ALT in both males points to an artifact.
    record = _record("chrX", X_NON_PAR, _call("SON", "1"), _call("MOTHER", "0/0"), _call("FATHER", "1"))
    assert not _is_de_novo(record)


@pytest.mark.parametrize(
    "mother",
    [_call("MOTHER", "0/1"), _call("MOTHER", "0/0", dp=3), _call("MOTHER", "./.")],
    ids=["carrier", "low-depth", "no-call"],
)
def test_the_mother_must_be_confidently_reference_for_a_sons_x(mother: SmallVariantCall) -> None:
    record = _record("chrX", X_NON_PAR, _call("SON", "1"), mother, _call("FATHER", "0"))
    assert not _is_de_novo(record)


def test_a_sons_y_needs_a_reference_father_and_not_the_mother() -> None:
    record = _record("chrY", Y_NON_PAR, _call("SON", "1"), _call("FATHER", "0"), _call("MOTHER", "."))
    assert _is_de_novo(record)
    father_missing = _record("chrY", Y_NON_PAR, _call("SON", "1"), _call("FATHER", "."))
    assert not _is_de_novo(father_missing)
    father_carrier = _record("chrY", Y_NON_PAR, _call("SON", "1"), _call("FATHER", "1"))
    assert not _is_de_novo(father_carrier)


def test_the_diploid_rules_hold_in_a_par_in_a_daughter_and_off_scope() -> None:
    # In PAR1 a son is diploid: a 1/1 call is not a single de novo event.
    par = _record("chrX", X_PAR1, _call("SON", "1/1"), _call("MOTHER", "0/0"), _call("FATHER", "0/0"))
    assert not _is_de_novo(par)
    # A daughter has two X chromosomes: heterozygous, both parents reference.
    daughter_trio = {"DAUGHTER": {"MOTHER", "FATHER"}}
    daughter_hom = _record("chrX", X_NON_PAR, _call("DAUGHTER", "1/1"), _call("MOTHER", "0/0"), _call("FATHER", "0"))
    daughter_het = _record("chrX", X_NON_PAR, _call("DAUGHTER", "0/1"), _call("MOTHER", "0/0"), _call("FATHER", "0"))
    assert not _is_de_novo(daughter_hom, child="DAUGHTER", parents=daughter_trio)
    assert _is_de_novo(daughter_het, child="DAUGHTER", parents=daughter_trio)
    # No listed PARs (T2T): read as diploid, as before.
    off_scope = _record("chrX", X_NON_PAR, _call("SON", "1"), _call("MOTHER", "0/0"), _call("FATHER", "0"))
    assert not _is_de_novo(off_scope, assembly="T2T-CHM13v2.0")


def test_without_parent_roles_both_parents_must_be_reference() -> None:
    both_ref = _record("chrX", X_NON_PAR, _call("SON", "1"), _call("MOTHER", "0/0"), _call("FATHER", "0"))
    father_no_call = _record("chrX", X_NON_PAR, _call("SON", "1"), _call("MOTHER", "0/0"), _call("FATHER", "."))
    assert _is_de_novo(both_ref, roles={})
    assert not _is_de_novo(father_no_call, roles={})


def test_the_de_novo_dominant_pattern_reads_a_male_hemizygous_call_as_one_copy() -> None:
    def matches(record: SmallVariantRecord, assembly: str = "GRCh38") -> bool:
        return _record_matches_de_novo_dominant(
            record,
            affected_samples=["SON"],
            unaffected_samples=["MOTHER", "FATHER"],
            sample_sex=SEXES,
            assembly_name=assembly,
        )

    assert matches(_record("chrX", X_NON_PAR, _call("SON", "1"), _call("MOTHER", "0/0"), _call("FATHER", "0")))
    assert matches(_record("chrY", Y_NON_PAR, _call("SON", "1/1"), _call("FATHER", "0")))
    # An unaffected carrier still rules it out, as for any dominant pattern.
    assert not matches(_record("chrX", X_NON_PAR, _call("SON", "1"), _call("MOTHER", "0/0"), _call("FATHER", "1")))
    # A 1/1 in a PAR, or where the PARs are unknown, is not one copy.
    assert not matches(_record("chrX", X_PAR1, _call("SON", "1/1"), _call("MOTHER", "0/0"), _call("FATHER", "0/0")))
    assert not matches(
        _record("chrX", X_NON_PAR, _call("SON", "1"), _call("MOTHER", "0/0"), _call("FATHER", "0")),
        assembly="T2T-CHM13v2.0",
    )


def test_the_python_filter_path_passes_the_assembly_and_sexes() -> None:
    record = _record("chrX", X_NON_PAR, _call("SON", "1"), _call("MOTHER", "0/0"), _call("FATHER", "0"))
    items = _inheritance_result_items(
        inheritance="de_novo_dominant",
        records=[record],
        affected_samples=["SON"],
        unaffected_samples=["MOTHER", "FATHER"],
        sample_rows=[{"sample_id": name, "sex": sex} for name, sex in SEXES.items()],
        assembly_name="GRCh38",
    )
    assert [(kind, payload.variant_id) for kind, payload in items] == [("variant", record.variant_id)]


def _sql_context(sex: str, assembly: str | None = "GRCh38") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        affected_sample_names=["PROBAND"],
        sample_rows=[
            {"sample_id": "PROBAND", "clinical_status": "affected", "sex": sex},
            {"sample_id": "MOTHER", "clinical_status": "unaffected", "sex": "female"},
        ],
        relationship_rows=[],
        sample_name_to_uuid={"PROBAND": "u-proband", "MOTHER": "u-mother"},
        assembly_name=assembly,
    )


def test_the_sql_admits_a_hemizygous_alt_call_for_an_affected_male_outside_the_pars() -> None:
    filters = SmallVariantQueryFilters(page=1, page_size=50, inheritance="de_novo_dominant")
    clauses, params = _small_native_inheritance_clauses(_sql_context("male"), filters)

    proband_clause = clauses[0]
    assert "inheritance_affected_het_0_gts" in proband_clause
    assert "inheritance_affected_hemizygous_0_gts" in proband_clause
    assert "e.pos BETWEEN %(inheritance_hemizygous_x_par1_start)s" in proband_clause
    # The haploid "1" and the diploid "1/1" are both in the hemizygous vocabulary.
    assert {"1", "1/1"} <= set(params["inheritance_affected_hemizygous_0_gts"])
    assert params["inheritance_hemizygous_x_chromosomes"] == ("X", "23")
    assert params["inheritance_hemizygous_y_chromosomes"] == ("Y", "24")
    assert (params["inheritance_hemizygous_x_par1_start"], params["inheritance_hemizygous_x_par1_end"]) == (10_001, 2_781_479)
    assert (params["inheritance_hemizygous_y_par2_start"], params["inheritance_hemizygous_y_par2_end"]) == (56_887_903, 57_217_415)
    # The unaffected mother must still carry no ALT at all.
    assert clauses[1].startswith("NOT ")


@pytest.mark.parametrize(("sex", "assembly"), [("female", "GRCh38"), ("male", "T2T-CHM13v2.0"), ("", "GRCh38")])
def test_the_sql_keeps_the_heterozygous_rule_otherwise(sex: str, assembly: str) -> None:
    filters = SmallVariantQueryFilters(page=1, page_size=50, inheritance="de_novo_dominant")
    clauses, params = _small_native_inheritance_clauses(_sql_context(sex, assembly), filters)

    assert "hemizygous" not in clauses[0]
    assert not any(key.startswith("inheritance_hemizygous") for key in params)
    assert not any(key.startswith("inheritance_affected_hemizygous") for key in params)


def test_the_segregation_modes_call_a_sons_hemizygous_x_variant_de_novo() -> None:
    context = FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id="FAM",
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_id": "SON", "role": "proband", "clinical_status": "affected", "sex": "male"},
            {"sample_id": "MOTHER", "role": "mother", "clinical_status": "unaffected", "sex": "female"},
            {"sample_id": "FATHER", "role": "father", "clinical_status": "unaffected", "sex": "male"},
        ],
        sample_uuid_to_name={},
        sample_name_to_uuid={"SON": "u-son", "MOTHER": "u-mother", "FATHER": "u-father"},
        affected_sample_names=["SON"],
        relationship_rows=[
            {"relationship_type": "parent_child", "sample_id_a": "MOTHER", "role_a": "mother",
             "sample_id_b": "SON", "role_b": "child"},
            {"relationship_type": "parent_child", "sample_id_a": "FATHER", "role_a": "father",
             "sample_id_b": "SON", "role_b": "child"},
        ],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )
    de_novo = _record("chrX", X_NON_PAR, _call("SON", "1"), _call("MOTHER", "0/0"), _call("FATHER", "."))
    inherited = _record("chrX", X_NON_PAR + 1, _call("SON", "1"), _call("MOTHER", "0/1"), _call("FATHER", "0"))
    modes = _segregation_modes_by_variant([de_novo, inherited], context=context)

    assert MODE_DE_NOVO in modes[de_novo.variant_id]
    assert MODE_DE_NOVO not in modes[inherited.variant_id]
    assert MODE_DOMINANT not in modes[inherited.variant_id]
