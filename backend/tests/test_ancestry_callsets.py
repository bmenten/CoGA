"""What each stored callset holds of each individual at the ancestry markers, measured not declared.

Synthetic reference of 100 markers (reference frequency 0.5, so three in four markers carry the
ALT allele in a sequenced genome), the first 80 of them in the exome mask.
"""
import asyncio

import numpy as np
import pytest

from backend.app.ancestry_schemas import AncestryReferenceOut
from backend.app.services import ancestry_callsets as callsets
from backend.app.services.ancestry_callsets import MarkerRow
from backend.app.services.ancestry_projection import AncestryMarker, AncestryReference, ExomeMask

N = 100
EXOME = 80


@pytest.fixture(autouse=True)
def _few_markers(monkeypatch):
    # The synthetic reference has 100 markers, the real one about 9,600.
    monkeypatch.setattr(callsets, "MIN_MARKERS", 40)


def reference(*, mask: bool = True) -> AncestryReference:
    public = AncestryReferenceOut(sha256="0" * 64, name="Synthetic reference", source_url="https://example.org/reference",
        source_revision="synthetic", method="Synthetic orthogonal axes", variant_count=N, sample_count=5,
        explained_variance=[0.4, 0.3, 0.2, 0.1], populations={"SYN": "Synthetic"}, points=[])
    loadings, _ = np.linalg.qr(np.random.default_rng(3).normal(size=(N, 4)))
    covered = np.arange(N) < EXOME
    exome = ExomeMask(sha256="e" * 64, definition="synthetic exome", covered=covered) if mask else None
    return AncestryReference(public, [AncestryMarker("1", i + 1, "A", "G") for i in range(N)],
                             np.ones(N), np.ones(N), loadings, exome)


GT = {0: "0/0", 1: "0/1", 2: "1/1"}
# Three in four markers carry ALT: what a sequenced genome has at this reference.
DOSAGES = np.array([[0, 1, 2, 1][i % 4] for i in range(N)], dtype=float)


def rows(sample: str, markers, *, callset: str = "clair3", dosages=DOSAGES, only_carriers: bool = True,
         site_filter: tuple[str, ...] = ("PASS",)) -> list[MarkerRow]:
    """A one-sample callset's rows at ``markers``: by default only where the sample carries ALT."""
    return [MarkerRow(callset, "1", i + 1, "A", "G", site_filter, (sample,), (GT[int(dosages[i])],), ((),))
            for i in markers if dosages[i] > 0 or not only_carriers]


def member(sample: str, role: str = "proband", metadata=None) -> dict:
    return {"sample_id": sample, "role": role, "sample_metadata": metadata or {}}


def expected_pcs(model: AncestryReference, usable) -> list[float]:
    """An independent least-squares fit of the axes to the usable standardized dosages."""
    fit, *_ = np.linalg.lstsq(model.loadings[usable], (DOSAGES - model.means)[usable] / model.scales[usable], rcond=None)
    return fit.tolist()


def only(results, sample):
    return next(r for r in results if r.sample_id == sample)


def test_a_genome_callset_reads_an_absent_record_as_reference_everywhere():
    model = reference()
    result = only(callsets.project_family(rows("PROBAND", range(N)), model, [member("PROBAND")]), "PROBAND")
    scan = result.candidates[0]
    assert (scan.kind, scan.coverage_ratio, scan.exome_ratio, scan.outside_exome_ratio) == ("genome", 1.0, 1.0, 1.0)
    assert scan.carriers == 75 and scan.expected_carriers == 75.0
    assert scan.qc["called"] == 75 and scan.qc["assumed_ref"] == 25 and scan.covered_markers == N
    assert result.status == "projected" and result.callset == "clair3" and result.kind == "genome"
    assert result.pcs == pytest.approx(expected_pcs(model, np.ones(N, dtype=bool)))


def test_an_exome_callset_reads_absence_as_reference_only_inside_the_mask():
    model = reference()
    result = only(callsets.project_family(rows("PROBAND", range(EXOME)), model, [member("PROBAND")]), "PROBAND")
    scan = result.candidates[0]
    assert (scan.kind, scan.exome_ratio, scan.outside_exome_ratio) == ("exome", 1.0, 0.0)
    assert scan.covered_markers == EXOME and scan.usable_markers == EXOME and scan.qc["outside_covered"] == N - EXOME
    assert scan.qc["assumed_ref"] == EXOME - 60
    assert result.status == "projected" and result.kind == "exome"
    assert result.pcs == pytest.approx(expected_pcs(model, np.arange(N) < EXOME))


def test_without_a_mask_no_callset_is_read_as_an_exome():
    result = only(callsets.project_family(rows("PROBAND", range(EXOME)), reference(mask=False), [member("PROBAND")]),
                  "PROBAND")
    assert result.candidates[0].kind == "genome" and result.candidates[0].exome_ratio is None
    thin = only(callsets.project_family(rows("PROBAND", range(0, N, 3)), reference(mask=False), [member("PROBAND")]),
                "PROBAND")
    assert thin.candidates[0].kind == "partial" and thin.status == "insufficient_data"


def test_a_panel_or_subset_of_sites_is_not_projected():
    # One marker in four held: far fewer carriers than a sequenced genome or exome has.
    model = reference()
    result = only(callsets.project_family(rows("PROBAND", range(1, N, 4)), model, [member("PROBAND")]), "PROBAND")
    scan = result.candidates[0]
    assert scan.kind == "partial" and scan.coverage_ratio < callsets.MIN_COVERAGE_RATIO
    assert scan.qc["assumed_ref"] == 0 and scan.usable_markers == scan.qc["called"]
    assert result.status == "insufficient_data" and result.pcs is None and result.reason == callsets.REASON_PARTIAL


def test_a_callset_with_a_call_at_nearly_every_marker_needs_no_absence_rule():
    model = reference()
    held = [i for i in range(N) if i != 7]
    result = only(callsets.project_family(rows("PROBAND", held, only_carriers=False), model, [member("PROBAND")]),
                  "PROBAND")
    scan = result.candidates[0]
    assert scan.kind == "complete" and scan.explicit_calls == N - 1 and scan.qc["assumed_ref"] == 0
    usable = np.ones(N, dtype=bool)
    usable[7] = False
    assert result.status == "projected" and result.pcs == pytest.approx(expected_pcs(model, usable))


def test_too_few_usable_markers_or_too_many_failed_calls_are_not_projected(monkeypatch):
    model = reference()
    monkeypatch.setattr(callsets, "MIN_MARKERS", 200)
    few = only(callsets.project_family(rows("PROBAND", range(N)), model, [member("PROBAND")]), "PROBAND")
    assert few.status == "insufficient_data" and few.reason.startswith("Too few usable markers: 100 of at least 200")
    monkeypatch.setattr(callsets, "MIN_MARKERS", 40)
    # A tenth of the carrier calls fail their filter: 90 % of the genome's markers usable.
    failing = [r if r.pos % 10 else MarkerRow(r.callset, r.chrom, r.pos, r.ref, r.alt, ("LowQual",), r.samples,
                                               r.gts, r.call_filters) for r in rows("PROBAND", range(N))]
    low = only(callsets.project_family(failing, model, [member("PROBAND")]), "PROBAND")
    assert low.candidates[0].qc["filtered"] > 5 and low.candidates[0].call_rate < 0.95
    assert low.status == "insufficient_data" and "usable genotype" in low.reason


def test_unfiltered_calls_count_for_imputed_genotypes_and_an_unfiltered_callset_only():
    model = reference()
    mixed = rows("PROBAND", range(N))
    mixed[0] = MarkerRow(*[*vars_of(mixed[0])[:5], (), *vars_of(mixed[0])[6:]])
    scan = callsets.read_callsets(mixed, model, ["PROBAND"])["PROBAND"]["clair3"].scan
    assert not scan.accepts_unfiltered and scan.qc["unfiltered"] == 1
    unfiltered = rows("PROBAND", range(N), site_filter=())
    scan = callsets.read_callsets(unfiltered, model, ["PROBAND"])["PROBAND"]["clair3"].scan
    assert scan.accepts_unfiltered and scan.qc["called_unfiltered"] == 75 and scan.projectable
    # Imputed genotypes keep their unfiltered calls, even beside PASS ones.
    imputed = rows("PROBAND", range(N), callset="glimpse2", site_filter=())
    imputed[0] = MarkerRow(*[*vars_of(imputed[0])[:5], ("PASS",), *vars_of(imputed[0])[6:]])
    scan = callsets.read_callsets(imputed, model, ["PROBAND"])["PROBAND"]["glimpse2"].scan
    assert scan.accepts_unfiltered and scan.label == "Imputed genotypes"
    assert scan.qc["called"] == 1 and scan.qc["called_unfiltered"] == 74 and scan.qc["unfiltered"] == 0


def vars_of(row: MarkerRow) -> list:
    return [row.callset, row.chrom, row.pos, row.ref, row.alt, row.filters, row.samples, row.gts, row.call_filters]


def test_a_merged_one_sample_row_reads_each_samples_own_filter():
    model = reference()
    merged = MarkerRow("clair3", "1", 2, "A", "G", ("PASS", "LowQual"), ("PROBAND", "MOTHER"), ("0/1", "0/1"),
                       (("PASS",), ("LowQual",)))
    joint = MarkerRow("clair3", "1", 3, "A", "G", ("LowQual",), ("PROBAND", "MOTHER"), ("1/1", "0/1"), ((), ()))
    read = callsets.read_callsets([merged, joint], model, ["PROBAND", "MOTHER"])
    assert read["PROBAND"]["clair3"].scan.qc["called"] == 1 and read["PROBAND"]["clair3"].scan.qc["filtered"] == 1
    assert read["MOTHER"]["clair3"].scan.qc["called"] == 0 and read["MOTHER"]["clair3"].scan.qc["filtered"] == 2


def test_two_rows_of_one_callset_at_a_marker_leave_it_out():
    model = reference()
    data = rows("PROBAND", range(N)) + [MarkerRow("clair3", "1", 2, "A", "G", ("PASS",), ("PROBAND",), ("1/1",), ((),))]
    reading = callsets.read_callsets(data, model, ["PROBAND"])["PROBAND"]["clair3"]
    assert reading.scan.qc["ambiguous_record"] == 1 and not reading.usable[1]


def test_direct_calls_are_preferred_and_imputed_genotypes_fill_in():
    model = reference()
    both = rows("PROBAND", range(N)) + rows("PROBAND", range(N), callset="glimpse2", site_filter=())
    result = only(callsets.project_family(both, model, [member("PROBAND")]), "PROBAND")
    assert result.callset == "clair3" and [c.callset for c in result.candidates] == ["clair3", "glimpse2"]
    thin_direct = rows("PROBAND", range(1, N, 4)) + rows("PROBAND", range(N), callset="glimpse2", site_filter=())
    result = only(callsets.project_family(thin_direct, model, [member("PROBAND")]), "PROBAND")
    assert result.callset == "glimpse2" and result.status == "projected"
    assert [(c.callset, c.projectable) for c in result.candidates] == [("clair3", False), ("glimpse2", True)]


def test_cfdna_samples_and_embryos_are_left_out_and_an_absent_individual_says_so():
    model = reference()
    family = [member("PROBAND"), member("PLASMA", "mother", {"assay": "nipt_cfdna"}), member("EMB1", "Embryo"),
              member("FATHER1", "father")]
    data = rows("PROBAND", range(N)) + rows("PLASMA", range(N)) + rows("EMB1", range(N))
    results = {r.sample_id: r for r in callsets.project_family(data, model, family)}
    assert results["PROBAND"].status == "projected"
    assert (results["PLASMA"].status, results["PLASMA"].reason) == ("excluded", callsets.EXCLUDED_CFDNA)
    assert (results["EMB1"].status, results["EMB1"].reason) == ("excluded", callsets.EXCLUDED_EMBRYO)
    assert results["PLASMA"].candidates == [] and results["EMB1"].pcs is None
    assert (results["FATHER1"].status, results["FATHER1"].reason) == ("insufficient_data", callsets.NO_CALLSET)


def test_the_rows_digest_changes_with_any_call():
    data = rows("PROBAND", range(N))
    assert callsets.rows_digest(data) == callsets.rows_digest(list(data))
    changed = data[:-1] + [MarkerRow(*[*vars_of(data[-1])[:7], ("1/1",), data[-1].call_filters])]
    assert callsets.rows_digest(changed) != callsets.rows_digest(data)


def test_the_marker_rows_are_read_in_bound_chunks_of_the_familys_own_callsets(monkeypatch):
    model = reference()
    monkeypatch.setattr(callsets, "_QUERY_CHUNK", 40)
    queries = []

    async def fake_execute(query, params):
        queries.append((query, params))
        first = params["xpos"][0] - 1_000_000_000
        return [("clair3", "1", first, "A", "G", ["PASS"], ["PROBAND"], ["0/1"], [[]])]

    monkeypatch.setattr(callsets, "storage_execute", fake_execute)
    read = asyncio.run(callsets.read_marker_rows("family-uuid", "project-uuid", model))
    assert len(queries) == 3 and [len(p["xpos"]) for _, p in queries] == [40, 40, 20]
    assert all(p["family"] == "family-uuid" and p["project"] == "project-uuid"
               and p["callsets"] == ("clair3", "nipt", "glimpse2") for _, p in queries)
    assert all("%(xpos)s" in q and "family-uuid" not in q for q, _ in queries)
    assert [r.pos for r in read] == [1, 41, 81] and read[0].filters == ("PASS",) and read[0].call_filters == ((),)
