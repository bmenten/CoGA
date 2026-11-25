"""Paraphase's paralogous-gene calls (``paraphase_pg``).

The SMN metrics (copy numbers, read counts, haplotype groups) and the extra fields a region
names; the medical-region catalogue (aliases, clinical fields, a copy-number norm for every
region, a configured file read first and one that does not read passed over, a malformed norm
left unassessed, field descriptions from the catalogue's defaults, a region's reading guide,
gene symbols the gene page knows); a copy-number change judged against each region's own norm times the copies
the sample's sex gives it (a man's single X is no loss; with no sex on a sex chromosome nothing
is called normal); and the clinical status a region's rule calls (SMN1, STRC and HBA by their
copy number, each band at its boundary), or review for an allele-dependent region with a change,
and for RCCX a label for a chromosome that is not a normal allele, or alleles it cannot read.
All values are synthetic.
"""

import json

import pytest

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
    the opsin array 2-4. Against a flat diploid 2 every man would read as changed and
    land on review."""
    ikbkg = paraphase_pg._paraphase_region_for_gene("ikbkg")
    f8 = paraphase_pg._paraphase_region_for_gene("f8")
    opsin = paraphase_pg._paraphase_region_for_gene("opn1lw")
    assert {ikbkg["chromosome"], f8["chromosome"], opsin["chromosome"]} == {"X"}

    male_ikbkg = _x_linked_metrics(2, 1)
    assert not _signal(ikbkg, male_ikbkg, chromosome="X", sex="male")
    assert paraphase_pg._clinical_status(ikbkg, male_ikbkg, copy_number_signal=False, fusion_count=None, payload={}) == "none"
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
    assert paraphase_pg._clinical_status(gba, normal_gba, copy_number_signal=False, fusion_count=None, payload={}) == "none"
    # An exploratory region is judged on its functional gene CN only.
    assert not _signal(None, _x_linked_metrics(5, None), chromosome="autosome", sex="male")
    assert _signal(None, _x_linked_metrics(5, 1), chromosome="autosome", sex="male")


def test_paraphase_allele_dependent_region_reports_a_no_call() -> None:
    f8 = paraphase_pg._paraphase_region_for_gene("f8")
    metrics = _x_linked_metrics(None, None)
    assert not _signal(f8, metrics, chromosome="X", sex="female")
    assert paraphase_pg._clinical_status(f8, metrics, copy_number_signal=False, fusion_count=None, payload={}) == "no_call"


def test_paraphase_phase_region_gives_the_chromosome() -> None:
    assert paraphase_pg._phase_region_chromosome("38:chrX:155376507-155386059") == "X"
    assert paraphase_pg._phase_region_chromosome("38:chrY:22983765-23005964") == "Y"
    assert paraphase_pg._phase_region_chromosome("38:chr5:70917100-70961220") == "autosome"
    assert paraphase_pg._phase_region_chromosome(None) is None
    assert paraphase_pg._chromosome_copies("Y", "female") == 0
    assert paraphase_pg._chromosome_copies("autosome", None) == 2


def test_a_regions_genes_are_the_genes_its_card_links_to() -> None:
    """The card links each gene to its gene page, which knows HGNC symbols only: a region's
    own key (RCCX) or a gene's former symbol (GBA) would lead nowhere. Paraphase's keys stay
    aliases, so its results still find their region."""
    paraphase_pg.load_paraphase_medical_regions.cache_clear()
    regions = {region["region_id"]: region for region in paraphase_pg.load_paraphase_medical_regions()}

    assert regions["rccx"]["genes"] == ["CYP21A2", "TNXB", "C4A", "C4B"]
    assert regions["gba"]["genes"] == ["GBA1"]
    assert paraphase_pg._paraphase_region_for_gene("RCCX") is regions["rccx"]
    assert paraphase_pg._paraphase_region_for_gene("GBA") is regions["gba"]
    # Paraphase's own TNXB result is its region's by alias, though RCCX lists TNXB as a gene.
    assert paraphase_pg._paraphase_region_for_gene("tnxb") is regions["tnxb"]
    assert "TNXB" in regions["rccx"]["genes"]


def test_the_shipped_catalogue_explains_its_fields_and_the_rccx_alleles() -> None:
    paraphase_pg.load_paraphase_medical_regions.cache_clear()
    regions = {region["region_id"]: region for region in paraphase_pg.load_paraphase_medical_regions()}

    # Every region explains the copy numbers Paraphase gives; RCCX its own total.
    for region in regions.values():
        assert {"total_cn", "gene_cn", "highest_total_cn"} <= set(region["field_descriptions"])
    assert regions["rccx"]["field_descriptions"]["total_cn"].startswith("RCCX modules")
    assert regions["smn1_smn2"]["field_descriptions"]["total_cn"].startswith("Copies of the gene")
    labels = next(section for section in regions["rccx"]["guide"] if section["heading"] == "Allele labels")
    assert [term["term"] for term in labels["terms"]][:2] == ["WT", "pseudogene_duplication"]
    # The guide reaches the API as the region's.
    out = paraphase_pg.ParaphaseGeneResultOut(
        gene_symbol="RCCX", region_info=paraphase_pg._region_out_payload(regions["rccx"])
    )
    assert out.region_info is not None
    assert out.region_info.guide[0].heading == "The module"


def test_paraphase_catalog_gives_every_region_a_copy_number_norm() -> None:
    paraphase_pg.load_paraphase_medical_regions.cache_clear()
    regions = paraphase_pg.load_paraphase_medical_regions()
    assert regions
    for region in regions:
        assert region["expected_copy_number"], region["region_id"]
        assert region["chromosome"] in {"X", "autosome"}
        for low, high in region["expected_copy_number"].values():
            assert 0 <= low <= high


# --- the clinical status a region's rule calls -----------------------------------------------------
# The shipped catalogue calls three regions by a copy-number rule: SMN1/SMN2 (spinal muscular
# atrophy) on the SMN1 copy number, STRC on the gene's, the alpha-globin cluster (HBA) on the
# total. Each band at its boundary, and what no band holds.


def _shipped_region(region_id: str) -> dict:
    paraphase_pg.load_paraphase_medical_regions.cache_clear()
    regions = paraphase_pg.load_paraphase_medical_regions()
    [region] = [region for region in regions if region["region_id"] == region_id]
    return region


@pytest.mark.parametrize(
    ("region_id", "field", "value", "status"),
    [
        ("smn1_smn2", "smn1_cn", 0, "pathogenic"),
        ("smn1_smn2", "smn1_cn", 1, "carrier"),
        ("smn1_smn2", "smn1_cn", 2, "normal"),
        ("smn1_smn2", "smn1_cn", 4, "normal"),
        # A copy number between the bands is called by none of them.
        ("smn1_smn2", "smn1_cn", 1.5, "review"),
        ("smn1_smn2", "smn1_cn", None, "no_call"),
        ("strc", "gene_cn", 0, "pathogenic"),
        ("strc", "gene_cn", 1, "carrier"),
        ("strc", "gene_cn", 2, "normal"),
        ("hba", "total_cn", 0, "pathogenic"),
        ("hba", "total_cn", 1, "pathogenic"),
        ("hba", "total_cn", 2, "carrier"),
        ("hba", "total_cn", 3, "carrier"),
        ("hba", "total_cn", 4, "normal"),
    ],
)
def test_a_regions_status_rule_calls_it_by_its_copy_number(
    region_id: str, field: str, value: float | None, status: str
) -> None:
    region = _shipped_region(region_id)
    assert region["status_rule"]["field"] == field
    metrics = [paraphase_pg._metric(field, value)]

    assert paraphase_pg._clinical_status(region, metrics, copy_number_signal=False, fusion_count=None, payload={}) == status


def test_a_rule_whose_field_paraphase_did_not_report_is_a_no_call() -> None:
    region = _shipped_region("smn1_smn2")
    metrics = [paraphase_pg._metric("smn2_cn", 3)]
    assert paraphase_pg._clinical_status(region, metrics, copy_number_signal=False, fusion_count=None, payload={}) == "no_call"


def test_a_rule_condition_that_names_no_number_never_holds() -> None:
    region = {
        "status_rule": {"field": "gene_cn", "pathogenic": {"eq": "zero"}, "carrier": {"lt": 2}, "normal": {"gt": 1}},
    }

    def status(value: float) -> str | None:
        metrics = [paraphase_pg._metric("gene_cn", value)]
        return paraphase_pg._clinical_status(region, metrics, copy_number_signal=False, fusion_count=None, payload={})

    # Pathogenic is never called on a condition it cannot read.
    assert status(0) == "carrier"
    assert status(1) == "carrier"
    assert status(2) == "normal"
    # No region, or one without a rule or a clinical block, has no status.
    assert paraphase_pg._clinical_status(None, [], copy_number_signal=True, fusion_count=1, payload={}) is None
    assert paraphase_pg._clinical_status({"region_id": "x"}, [], copy_number_signal=True, fusion_count=1, payload={}) is None


def test_an_allele_dependent_region_puts_a_change_or_a_fusion_up_for_review() -> None:
    f8 = paraphase_pg._paraphase_region_for_gene("f8")
    normal = paraphase_pg._extract_copy_number_metrics({"total_cn": 3}, {"total_cn": 3})
    assert paraphase_pg._clinical_status(f8, normal, copy_number_signal=True, fusion_count=None, payload={}) == "review"
    assert paraphase_pg._clinical_status(f8, normal, copy_number_signal=False, fusion_count=1, payload={}) == "review"
    assert paraphase_pg._clinical_status(f8, normal, copy_number_signal=False, fusion_count=0, payload={}) == "none"


# --- the RCCX alleles --------------------------------------------------------------------------------
# RCCX has no copy-number rule: its status reads Paraphase's label for each chromosome. A label
# the catalogue does not name as a normal allele, a count of labels other than two, or failed
# phasing puts the call up for review, whatever its copy number.

_RCCX_CN4 = [paraphase_pg._metric("total_cn", 4)]


@pytest.mark.parametrize(
    ("alleles", "phased", "status"),
    [
        (["WT", "WT"], True, "none"),
        (["WT", "pseudogene_deletion"], True, "none"),
        (["pseudogene_duplication", "gene_duplication"], True, "none"),
        (["WT", "duplication_WT_plus_Q319X"], True, "none"),
        # A damaging allele on one chromosome or on both, at the usual four copies.
        (["WT", "Q319X"], True, "review"),
        (["Q319X", "Q319X"], True, "review"),
        (["WT", "deletion_P31L,G111Vfs"], True, "review"),
        (["WT", "gene_deletion"], True, "review"),
        # A label the catalogue does not know is read, never passed.
        (["WT", "pseudogene_conversion"], True, "review"),
        # Alleles that cannot be read.
        (["WT", "WT"], False, "review"),
        (["WT", "WT"], None, "review"),
        (["WT"], True, "review"),
        (["WT", "WT", "WT"], True, "review"),
        ([], True, "review"),
        (None, True, "review"),
        ([["WT"], ["WT"]], True, "review"),
    ],
)
def test_rccx_reads_its_alleles(alleles, phased, status: str) -> None:
    rccx = _shipped_region("rccx")
    payload = {"total_cn": 4}
    if alleles is not None:
        payload["annotated_alleles"] = alleles
    if phased is not None:
        payload["phasing_success"] = phased

    assert (
        paraphase_pg._clinical_status(rccx, _RCCX_CN4, copy_number_signal=False, fusion_count=None, payload=payload)
        == status
    )


def test_normal_rccx_alleles_keep_a_copy_number_change_up_for_review() -> None:
    rccx = _shipped_region("rccx")
    payload = {"annotated_alleles": ["WT", "pseudogene_duplication"], "phasing_success": True}
    metrics = [paraphase_pg._metric("total_cn", 5)]
    assert (
        paraphase_pg._clinical_status(rccx, metrics, copy_number_signal=True, fusion_count=None, payload=payload)
        == "review"
    )
    # A copy-number no-call with a damaging allele is read too.
    no_call = [paraphase_pg._metric("total_cn", None)]
    damaging = {"annotated_alleles": ["WT", "Q319X"], "phasing_success": True}
    assert (
        paraphase_pg._clinical_status(rccx, no_call, copy_number_signal=False, fusion_count=None, payload=damaging)
        == "review"
    )


def test_an_allele_rule_never_lowers_a_copy_number_call() -> None:
    region = {
        "status_rule": {"field": "gene_cn", "pathogenic": {"eq": 0}, "carrier": {"eq": 1}, "normal": {"gte": 2}},
        "allele_rule": {"field": "labels", "alleles": 2, "phasing_field": None, "normal": ["WT"], "normal_prefixes": []},
    }
    damaging = {"labels": ["WT", "del"]}

    def status(value: float) -> str | None:
        metrics = [paraphase_pg._metric("gene_cn", value)]
        return paraphase_pg._clinical_status(region, metrics, copy_number_signal=False, fusion_count=None, payload=damaging)

    assert status(0) == "pathogenic"
    assert status(1) == "carrier"
    assert status(2) == "review"


def test_a_malformed_allele_rule_is_dropped() -> None:
    assert paraphase_pg._allele_rule(None) is None
    assert paraphase_pg._allele_rule({"normal": ["WT"]}) is None
    assert paraphase_pg._allele_rule({"field": "annotated_alleles", "normal": [], "normal_prefixes": [1]}) is None
    rule = paraphase_pg._allele_rule({"field": "annotated_alleles", "alleles": True, "normal": ["WT", 3]})
    assert rule == {
        "field": "annotated_alleles",
        "alleles": None,
        "phasing_field": None,
        "normal": ["WT"],
        "normal_prefixes": [],
    }
    # Without a count or a phasing field, the labels alone are read.
    assert not paraphase_pg._alleles_need_review(rule, {"annotated_alleles": ["WT", "WT", "WT"]})


# --- the catalogue a start reads ----------------------------------------------------------------------


def test_a_malformed_copy_number_expectation_leaves_that_field_unassessed() -> None:
    block = paraphase_pg._expected_copy_number_block(
        {"total_cn": 2, "gene_cn": [1, 3], "reversed": [3, 1], "short": [1], "text": "two", "none": None}
    )
    assert block == {"total_cn": (2.0, 2.0), "gene_cn": (1.0, 3.0)}
    assert paraphase_pg._expected_copy_number_block(["total_cn", 2]) == {}


def test_an_exploratory_smn_deletion_copy_is_a_change() -> None:
    # Without a catalogue region the SMN exon 7-8 deletion copies keep their norm of none.
    assert not _signal(None, [paraphase_pg._metric("smn_del78_cn", 0)], chromosome="autosome", sex="female")
    assert _signal(None, [paraphase_pg._metric("smn2_del78_cn", 1)], chromosome="autosome", sex="female")


@pytest.fixture
def _no_cached_catalogue():
    paraphase_pg.load_paraphase_medical_regions.cache_clear()
    yield
    paraphase_pg.load_paraphase_medical_regions.cache_clear()


@pytest.mark.usefixtures("_no_cached_catalogue")
def test_a_configured_catalogue_is_read_and_an_unreadable_one_is_passed_over(monkeypatch, tmp_path) -> None:
    configured = tmp_path / "regions.json"
    configured.write_text(
        json.dumps(
            {
                "regions": [
                    "not a region",
                    {"region_id": "", "display_name": ""},
                    {
                        "region_id": "synthetic",
                        "genes": ["syn1"],
                        "expected_copy_number": {"gene_cn": 1},
                        "disorders": [{"name": "Synthetic disorder"}, {"omim_url": "no name"}],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(paraphase_pg.settings, "paraphase_medical_regions_path", str(configured))

    [region] = paraphase_pg.load_paraphase_medical_regions()
    assert (region["region_id"], region["display_name"], region["genes"]) == ("synthetic", "synthetic", ["SYN1"])
    assert (region["chromosome"], region["clinical_priority"], region["clinical"]) == ("autosome", 999, None)
    assert region["disorders"] == [{"name": "Synthetic disorder", "omim_url": None}]

    assert region["guide"] == []

    # Field descriptions start from the catalogue's own, a region's entry winning; a guide
    # section without a heading or without content, and a term without a meaning, is dropped.
    configured.write_text(
        json.dumps(
            {
                "field_descriptions": {"total_cn": "Default total.", "gene_cn": "Default gene.", "empty": ""},
                "regions": [
                    {
                        "region_id": "synthetic",
                        "genes": ["SYN1"],
                        "field_descriptions": {"total_cn": "Region total."},
                        "guide": [
                            "not a section",
                            {"heading": "", "text": "No heading."},
                            {"heading": "Empty"},
                            {"heading": "Labels", "terms": [{"term": "WT", "text": "Normal."}, {"term": "X"}]},
                            {"heading": "Text", "text": " Only text. ", "terms": "not a list"},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    paraphase_pg.load_paraphase_medical_regions.cache_clear()
    [region] = paraphase_pg.load_paraphase_medical_regions()
    assert region["field_descriptions"] == {"total_cn": "Region total.", "gene_cn": "Default gene."}
    assert region["guide"] == [
        {"heading": "Labels", "text": None, "terms": [{"term": "WT", "text": "Normal."}]},
        {"heading": "Text", "text": "Only text.", "terms": []},
    ]

    # A file that does not read as a catalogue is passed over for the shipped one.
    configured.write_text("{not json", encoding="utf-8")
    paraphase_pg.load_paraphase_medical_regions.cache_clear()
    regions = paraphase_pg.load_paraphase_medical_regions()
    assert {"smn1_smn2", "hba", "strc"} <= {region["region_id"] for region in regions}
