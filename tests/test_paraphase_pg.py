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
    assert any(paraphase_pg._copy_number_is_signal(metric) for metric in copy_number_metrics)
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
