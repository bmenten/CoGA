from backend.app.services import paraphase_pg


def test_paraphase_extracts_smn_metrics_and_copy_number_signal() -> None:
    payload = {
        "smn1_cn": None,
        "smn2_cn": 3,
        "smn_del78_cn": 0,
        "smn1_read_number": 14,
        "smn2_read_number": 24,
        "smn1_haplotypes": {"h1": "smn1_smn1hap1"},
        "smn2_haplotypes": {"h2": "smn1_smn2hap1", "h3": "smn1_smn2hap2"},
    }

    copy_number_metrics = paraphase_pg._extract_copy_number_metrics(payload, {})
    read_metrics = paraphase_pg._extract_read_metrics(payload)
    haplotype_groups = paraphase_pg._extract_haplotype_groups(payload)

    assert [(metric.key, metric.label, metric.value) for metric in copy_number_metrics] == [
        ("smn1_cn", "SMN1 CN", None),
        ("smn2_cn", "SMN2 CN", 3.0),
        ("smn_del78_cn", "SMNΔ7-8 CN", 0.0),
    ]
    smn_region = paraphase_pg._paraphase_region_for_gene("smn1")
    # SMN2 CN 3 is within the normal 0-4 range and an SMN1 no-call is not a change.
    assert not any(
        paraphase_pg._copy_number_is_signal(metric, region=smn_region, copies=2)
        for metric in copy_number_metrics
    )
    deleted = paraphase_pg._metric("smn_del78_cn", 1)
    assert paraphase_pg._copy_number_is_signal(deleted, region=smn_region, copies=2)
    assert [(metric.key, metric.value) for metric in read_metrics] == [
        ("smn1_read_number", 14.0),
        ("smn2_read_number", 24.0),
    ]
    assert [(group.key, group.count, group.haplotypes) for group in haplotype_groups] == [
        ("smn1_haplotypes", 1, ["smn1_smn1hap1"]),
        ("smn2_haplotypes", 2, ["smn1_smn2hap1", "smn1_smn2hap2"]),
    ]


def test_paraphase_medical_region_catalog_matches_aliases() -> None:
    paraphase_pg.load_paraphase_medical_regions.cache_clear()

    smn_region = paraphase_pg._paraphase_region_for_gene("smn1")
    exploratory_region = paraphase_pg._paraphase_region_for_gene("EXPLORATORY")

    assert smn_region is not None
    assert smn_region["display_name"] == "SMN1/SMN2"
    assert "smn1_cn" in smn_region["key_copy_number_fields"]
    assert smn_region["disorders"][0]["omim_url"] == "https://www.omim.org/entry/253300"
    assert exploratory_region is None


def test_paraphase_extracts_clinical_extra_fields_for_region() -> None:
    region = {
        "key_extra_fields": ["annotated_alleles", "hap_variants"],
        "field_descriptions": {
            "annotated_alleles": "Per-allele CYP21A2 annotations.",
        },
    }
    payload = {
        "total_cn": 4,
        "gene_cn": 2,
        "annotated_alleles": ["WT", "deletion_P31L,G111Vfs"],
        "hap_variants": {"hap1": ["P31L"], "hap2": ["Q319X"]},
        "phasing_success": True,
        "assembled_haplotypes": {"h1": "not shown here"},
        "smn1_cn": 2,
    }

    fields = paraphase_pg._extract_extra_fields(payload, region)

    assert [(field.key, field.label) for field in fields] == [
        ("annotated_alleles", "Annotated Alleles"),
        ("hap_variants", "Hap Variants"),
        ("phasing_success", "Phasing Success"),
    ]
    assert fields[0].description == "Per-allele CYP21A2 annotations."
    assert fields[1].value == {"hap1": ["P31L"], "hap2": ["Q319X"]}


def _x_linked_metrics(total_cn: int | None, gene_cn: int | None) -> list:
    return paraphase_pg._extract_copy_number_metrics(
        {"total_cn": total_cn, "gene_cn": gene_cn, "highest_total_cn": total_cn},
        {"total_cn": total_cn, "gene_cn": gene_cn, "highest_total_cn": total_cn},
    )


def _signal(region, metrics, *, chromosome: str, sex: str | None) -> bool:
    copies = paraphase_pg._chromosome_copies(chromosome, paraphase_pg._normalized_sex(sex))
    return any(
        paraphase_pg._copy_number_is_signal(metric, region=region, copies=copies)
        for metric in metrics
    )


def test_paraphase_male_x_linked_single_copy_is_normal() -> None:
    """A man has one X: IKBKG + IKBKGP1 is total CN 2 / gene CN 1, F8 int22h is 3 and
    the opsin array 2-4. Against a flat diploid 2 every man used to read as changed and
    land on review."""
    ikbkg = paraphase_pg._paraphase_region_for_gene("ikbkg")
    f8 = paraphase_pg._paraphase_region_for_gene("f8")
    opsin = paraphase_pg._paraphase_region_for_gene("opn1lw")
    assert {ikbkg["chromosome"], f8["chromosome"], opsin["chromosome"]} == {"X"}

    male_ikbkg = _x_linked_metrics(2, 1)
    assert not _signal(ikbkg, male_ikbkg, chromosome="X", sex="male")
    assert paraphase_pg._clinical_status(ikbkg, male_ikbkg, copy_number_signal=False, fusion_count=None) == "none"
    assert not _signal(f8, _x_linked_metrics(3, None)[:1], chromosome="X", sex="male")
    male_opsin = paraphase_pg._extract_copy_number_metrics(
        {"total_cn": 3, "opn1lw_cn": 1, "opn1mw_cn": 2, "highest_total_cn": 3}, {"total_cn": 3}
    )
    assert not _signal(opsin, male_opsin, chromosome="X", sex="male")

    # The same values in a woman are a one-copy loss, and a male gene CN 0 is a loss.
    assert _signal(ikbkg, male_ikbkg, chromosome="X", sex="female")
    assert not _signal(ikbkg, _x_linked_metrics(4, 2), chromosome="X", sex="female")
    assert _signal(ikbkg, _x_linked_metrics(2, 0), chromosome="X", sex="male")
    # With no sex to choose an expectation by, the value is not called normal.
    assert _signal(ikbkg, _x_linked_metrics(4, 2), chromosome="X", sex=None)


def test_paraphase_copy_number_change_uses_the_region_norm() -> None:
    """NEB is normally 6 and a gene + pseudogene pair 4; neither is a change, and a
    field with no fixed norm (highest_total_cn) never is one."""
    neb = paraphase_pg._paraphase_region_for_gene("neb")
    gba = paraphase_pg._paraphase_region_for_gene("gba")
    assert not _signal(neb, _x_linked_metrics(6, None), chromosome="autosome", sex="female")
    assert _signal(neb, _x_linked_metrics(8, None), chromosome="autosome", sex="female")
    normal_gba = _x_linked_metrics(4, None)
    assert not _signal(gba, normal_gba, chromosome="autosome", sex="male")
    assert paraphase_pg._clinical_status(gba, normal_gba, copy_number_signal=False, fusion_count=None) == "none"
    # An exploratory region is judged on its functional gene CN only.
    assert not _signal(None, _x_linked_metrics(5, None), chromosome="autosome", sex="male")
    assert _signal(None, _x_linked_metrics(5, 1), chromosome="autosome", sex="male")


def test_paraphase_allele_dependent_region_reports_a_no_call() -> None:
    f8 = paraphase_pg._paraphase_region_for_gene("f8")
    metrics = _x_linked_metrics(None, None)
    assert not _signal(f8, metrics, chromosome="X", sex="female")
    assert paraphase_pg._clinical_status(f8, metrics, copy_number_signal=False, fusion_count=None) == "no_call"


def test_paraphase_phase_region_gives_the_chromosome() -> None:
    assert paraphase_pg._phase_region_chromosome("38:chrX:155376507-155386059") == "X"
    assert paraphase_pg._phase_region_chromosome("38:chrY:22983765-23005964") == "Y"
    assert paraphase_pg._phase_region_chromosome("38:chr5:70917100-70961220") == "autosome"
    assert paraphase_pg._phase_region_chromosome(None) is None
    assert paraphase_pg._chromosome_copies("Y", "female") == 0
    assert paraphase_pg._chromosome_copies("autosome", None) == 2


def test_paraphase_catalog_gives_every_region_a_copy_number_norm() -> None:
    paraphase_pg.load_paraphase_medical_regions.cache_clear()
    regions = paraphase_pg.load_paraphase_medical_regions()
    assert regions
    for region in regions:
        assert region["expected_copy_number"], region["region_id"]
        assert region["chromosome"] in {"X", "autosome"}
        for low, high in region["expected_copy_number"].values():
            assert 0 <= low <= high
