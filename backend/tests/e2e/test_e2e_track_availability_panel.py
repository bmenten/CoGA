"""The chromosome view offers a member's small-variant track under a gene panel exactly when the list shows them a variant (E2E, CLIN-62).

The chromosome view asks which tracks each member has (``GET /families/{family_id}/
track-availability``) with the filters it came with, the small-variant page's gene panel among
them, and draws a member's small-variant track only when the answer offers it. The small-variant
presence applied a panel's regions alone, each row tested against every region, while the list
and the member's track keep a variant annotated with one of the panel's genes or in one of its
regions, whatever the panel's size (#897): a member whose only variant under the panel is named
by one of its genes outside its regions (VEP's upstream and downstream genes) was offered no
small-variant track, although the list showed them that variant. The presence now applies the
panel with the list's own SQL.

A trio is created through the PED upload under a new id each run, and a VEP-annotated VCF goes in
through the family small-variant upload. Two genes are seeded in the gene reference for the run,
TAVPNLA with one locus and TAVPNLB with two transcript loci that chain, and two panels are made
through the panel API: one of the two genes, and one of the two genes and 5,298 filler genes (a
locus each on chromosomes 1 to 22, away from the family's variants), 5,300 genes as the
Mendeliome has; a third panel has no gene, so that, like a panel whose regions are stored for
another assembly only, it keeps no variant of this family. The father's only variant lies 3 kb
before TAVPNLA, its annotation naming the gene (reached by the gene alone); the mother's in
TAVPNLB's second locus, its annotation naming the gene otherwise (reached by the region alone);
the proband's elsewhere, naming another gene.

For each panel, in a window around the genes and over the whole chromosome, as the chromosome
view asks for them: the track availability, each member's track (the list in track mode with the
member's own non-reference calls, as ``SmallVariantTrack.tsx`` asks for it) and the plain list.
Each member is offered a small-variant track exactly when their track and the list hold a variant
of theirs: under both gene panels the father and the mother, not the proband; under the panel
without genes, which keeps nothing, nobody (it was read as no panel, and every member was
offered a track). Without a panel every member is. The panels and the seeded genes are removed
afterwards. Everything runs in ONE event loop, with an in-process ``httpx.ASGITransport`` client
(see test_e2e_api_contract.py for why).

Synthetic data only. Skipped unless ``RUN_INTEGRATION=1`` (see conftest.py); the CI ``e2e`` job
sets it.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_SEEDED_GENE_SOURCE = "e2e-track-availability-panel"
_CSQ_FORMAT = "Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|CANONICAL|gnomADe_AF"

# The gene reference's loci of the panel's genes: symbol -> [(chromosome, start, end)].
_LOCI = {
    "TAVPNLA": [("7", 30_000_000, 30_050_000)],
    "TAVPNLB": [("7", 30_200_000, 30_240_000), ("chr7", 30_230_000, 30_260_000)],
}
# The filler genes of the Mendeliome-sized panel: a locus each on chromosomes 1 to 22, from
# 40 Mb on, where the family has no variant.
_FILLERS = {
    f"TAVPNLF{index:04d}": [(str(index % 22 + 1), 40_000_001 + 10_000 * (index // 22), 40_005_001 + 10_000 * (index // 22))]
    for index in range(5_298)
}

_ROLES = ("FATHER", "MOTHER", "PROBAND")
# Each member's one variant: (chromosome, position, REF, ALT, the gene its CSQ names, the
# genotypes of the father, the mother and the proband).
_Variant = tuple[str, int, str, str, str, tuple[str, str, str]]
_VARIANTS: dict[str, _Variant] = {
    # 3 kb before TAVPNLA's locus, its annotation naming TAVPNLA (VEP's upstream gene).
    "FATHER": ("7", 29_997_000, "C", "T", "TAVPNLA", ("0/1", "0/0", "0/0")),
    # In TAVPNLB's second locus only, its annotation naming the gene by an earlier symbol.
    "MOTHER": ("7", 30_250_000, "G", "A", "OLDTAVPNLB", ("0/0", "0/1", "0/0")),
    # Elsewhere, naming another gene.
    "PROBAND": ("7", 31_000_000, "A", "G", "TAVPNLOTHER", ("0/0", "0/0", "0/1")),
}
# Who has a variant the gene panels keep, and so a track the chromosome view should offer.
_OFFERED = {"FATHER": True, "MOTHER": True, "PROBAND": False}

_PANELS = ("two genes", "Mendeliome-sized")
# The panels made for the run: name -> genes.
_PANEL_GENES: dict[str, list[str]] = {
    "two genes": sorted(_LOCI),
    "Mendeliome-sized": [*sorted(_LOCI), *_FILLERS],
    "no gene": [],
}
# Where the chromosome view looks, as it asks for it: chromosome 7 (159,345,973 bases in GRCh38).
_VIEWS: dict[str, tuple[int, int]] = {
    "a window": (29_500_000, 31_500_000),
    "the whole chromosome": (0, 159_345_973),
}

_VCF_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##DeepVariant_version=1.10.0\n"
    "##reference=GRCh38\n"
    f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: {_CSQ_FORMAT}">\n'
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
    '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
)


def _call(genotype: str) -> str:
    depths = {"0/0": "30,0", "0/1": "15,15", "1/1": "0,30"}[genotype]
    return f"{genotype}:40:30:{depths}"


def _vcf(samples: tuple[str, str, str]) -> bytes:
    lines = [_VCF_HEADER + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(samples)]
    for chrom, pos, ref, alt, gene, genotypes in sorted(_VARIANTS.values(), key=lambda variant: variant[1]):
        csq = "|".join([alt, "missense_variant", "MODERATE", gene, f"ENSG_{gene}", "Transcript", f"ENST_{gene}_1", "YES", "0.0001"])
        lines.append(
            f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\tCSQ={csq}\tGT:GQ:DP:AD\t"
            + "\t".join(_call(genotype) for genotype in genotypes)
        )
    return ("\n".join(lines) + "\n").encode()


async def _seed_genes(assembly_id: str | None) -> int:
    """Seed (or, with None, remove) the run's gene-reference loci, and return how many are left."""
    from sqlalchemy import text

    from backend.app.core.postgres import get_postgres_sessionmaker

    async with get_postgres_sessionmaker()() as session:
        await session.execute(text("DELETE FROM genes WHERE source = :source"), {"source": _SEEDED_GENE_SOURCE})
        if assembly_id is not None:
            await session.execute(
                text(
                    """
                    INSERT INTO genes (assembly_id, gene_id, hgnc_symbol, chr, start, "end",
                                       strand, biotype, description, source)
                    VALUES (CAST(:assembly_id AS uuid), :gene_id, :symbol, :chr, :start, :end,
                            1, 'protein_coding', 'E2E track availability panel', :source)
                    """
                ),
                [
                    {
                        "assembly_id": assembly_id,
                        "gene_id": f"E2E_TAVPNL_{symbol}_{number}",
                        "symbol": symbol,
                        "chr": chrom,
                        "start": start,
                        "end": end,
                        "source": _SEEDED_GENE_SOURCE,
                    }
                    for symbol, loci in {**_LOCI, **_FILLERS}.items()
                    for number, (chrom, start, end) in enumerate(loci)
                ],
            )
        left = (
            await session.execute(
                text("SELECT count(*) FROM genes WHERE source = :source"), {"source": _SEEDED_GENE_SOURCE}
            )
        ).scalar_one()
        await session.commit()
    return int(left)


def _status(response: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": response.status_code, "text": response.text[:2000]}
    if response.status_code == 200 and "json" in response.headers.get("content-type", ""):
        out["json"] = response.json()
    return out


async def _exercise(family_id: str) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from backend.app.core.clickhouse import init_clickhouse_schema
    from backend.app.core.postgres import get_postgres_sessionmaker, init_postgres_schema
    from backend.app.main import app
    from backend.app.services.clickhouse_variant_storage import ensure_clickhouse_variant_tables
    from backend.tests.e2e import _harness

    await init_postgres_schema()
    await init_clickhouse_schema()
    await ensure_clickhouse_variant_tables(_harness.ASSEMBLY)
    async with get_postgres_sessionmaker()() as session:
        _admin, project_id, assembly_id = await _harness.ensure_e2e_project(session)

    samples = tuple(f"{role}_{family_id}" for role in _ROLES)
    father, mother, proband = samples
    ped = (
        f"{family_id}\t{father}\t0\t0\t1\t1\n"
        f"{family_id}\t{mother}\t0\t0\t2\t1\n"
        f"{family_id}\t{proband}\t{father}\t{mother}\t1\t2\n"
    )
    run = family_id.lower()
    out: dict[str, Any] = {"panels": {}, "answers": {}, "cleanup": {}}
    panels: dict[str, str] = {}
    small_variants = f"/api/families/{family_id}/small-variants"
    availability = f"/api/families/{family_id}/track-availability"
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://e2e", timeout=300) as ac:
        token = await _harness.login_admin_token(ac)
        ac.headers["Authorization"] = f"Bearer {token}"
        try:
            ped_response = await ac.post(
                "/api/ped/upload",
                params={"project_id": project_id},
                files={"file": (f"{family_id}.ped", ped.encode(), "text/plain")},
            )
            upload = await ac.post(
                f"{small_variants}/upload",
                params={"overwrite": "false"},
                files={"file": (f"{family_id}.vcf", _vcf(samples), "text/plain")},
            )
            out["setup"] = {
                "ped": (ped_response.status_code, ped_response.text[:2000]),
                "upload": (upload.status_code, upload.text[:2000]),
            }
            await _seed_genes(assembly_id)
            for name, genes in _PANEL_GENES.items():
                created = await ac.post("/api/panels/", json={"name": f"e2e-tavpnl-{run}-{len(genes)}", "genes": genes})
                out["panels"][name] = (created.status_code, created.text[:500])
                if created.status_code == 201:
                    panels[name] = created.json()["panel"]["_id"]
                    out["panels"][name] = (201, len(created.json()["panel"]["genes"]), len(created.json()["panel"]["regions"]))

            async def answers(panel_id: str | None, start: int, end: int) -> dict[str, Any]:
                """What the chromosome view asks for over chromosome 7 from ``start`` to ``end``:
                the track availability, each member's track and the plain list."""
                panel = {"panel_id": panel_id} if panel_id else {}
                locus = {"start": start, "end": end}
                answered = {
                    "track availability": _status(
                        await ac.get(availability, params={"chrom": "7", **locus, "include_small_variants": "true", **panel})
                    ),
                    "list": _status(
                        await ac.get(small_variants, params={"chr": "7", **locus, "overlap": "true", "page_size": 100, **panel})
                    ),
                }
                for role, sample in zip(_ROLES, samples):
                    answered[f"track {role}"] = _status(
                        await ac.get(
                            small_variants,
                            params={
                                "chr": "7",
                                **locus,
                                "overlap": "true",
                                "page_size": 9_999,
                                "track_mode": "true",
                                "track_result_limit": 10_000,
                                "sample_filter": f"{sample}:het|hom",
                                **panel,
                            },
                        )
                    )
                return answered

            out["answers"]["no panel"] = await answers(None, *_VIEWS["a window"])
            for name, panel_id in panels.items():
                for view, (start, end) in _VIEWS.items():
                    out["answers"][(name, view)] = await answers(panel_id, start, end)
        finally:
            for name, panel_id in panels.items():
                out["cleanup"][name] = (await ac.delete(f"/api/panels/{panel_id}")).status_code
            out["seeded_genes_left"] = await _seed_genes(None)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict[str, Any]:
    from backend.app.services import raw_import_files_pg
    from backend.tests.e2e import _harness

    mp = pytest.MonkeyPatch()
    # The upload keeps a managed copy of each file; keep it out of the repository's data/.
    mp.setattr(raw_import_files_pg, "DATA_DIR", tmp_path_factory.mktemp("track_availability_panel_upload"))
    try:
        return _harness.run_async(lambda: _exercise(f"TAVPNL_{uuid4().hex[:8].upper()}"))
    finally:
        mp.undo()


def _role(sample: str) -> str:
    return sample.split("_", 1)[0]


def _offered(answer: dict[str, Any]) -> dict[str, bool]:
    """Whom the track availability offers a small-variant track."""
    assert answer["status"] == 200, answer["text"]
    return {_role(sample): entry["small_variants"] for sample, entry in answer["json"]["samples"].items()}


def _carriers(answer: dict[str, Any]) -> set[str]:
    """The members with a non-reference call in a variant the page holds."""
    assert answer["status"] == 200, answer["text"]
    return {
        _role(genotype["sample"])
        for variant in answer["json"]["variants"]
        for genotype in variant.get("genotypes") or []
        if any(allele not in {"0", "."} for allele in genotype["gt"].replace("|", "/").split("/"))
    }


def _shown(answers: dict[str, Any]) -> dict[str, dict[str, bool]]:
    """Per member: offered a track, their track holding a variant of theirs, the list
    holding one."""
    listed = _carriers(answers["list"])
    return {
        role: {
            "offered": _offered(answers["track availability"])[role],
            "track": role in _carriers(answers[f"track {role}"]),
            "list": role in listed,
        }
        for role in _ROLES
    }


def test_the_family_its_variants_and_the_panels_are_stored(run: dict[str, Any]) -> None:
    assert run["setup"]["ped"][0] == 200, run["setup"]["ped"]
    assert run["setup"]["upload"][0] == 200, run["setup"]["upload"]
    # A panel stores one locus per gene; the search adds each gene's loci in the gene
    # reference (TAVPNLB's two).
    assert run["panels"]["two genes"] == (201, 2, 2)
    assert run["panels"]["Mendeliome-sized"] == (201, 5_300, 5_300)
    assert run["panels"]["no gene"] == (201, 0, 0)


def test_without_a_panel_each_member_is_offered_the_track_of_their_variants(run: dict[str, Any]) -> None:
    shown = _shown(run["answers"]["no panel"])
    assert shown == {role: {"offered": True, "track": True, "list": True} for role in _ROLES}


@pytest.mark.parametrize("view", list(_VIEWS))
@pytest.mark.parametrize("panel", _PANELS)
def test_a_member_is_offered_a_small_variant_track_exactly_when_the_list_shows_them_a_variant(
    run: dict[str, Any], panel: str, view: str
) -> None:
    # Before: the availability read the panel's regions alone, so the father, whose only
    # variant the panel keeps by its gene TAVPNLA, was offered no track although his track and
    # the list showed that variant.
    shown = _shown(run["answers"][(panel, view)])
    assert shown == {role: {"offered": kept, "track": kept, "list": kept} for role, kept in _OFFERED.items()}


@pytest.mark.parametrize("view", list(_VIEWS))
def test_a_panel_that_keeps_nothing_offers_no_member_a_small_variant_track(run: dict[str, Any], view: str) -> None:
    # Before: the availability read a panel without genes or regions as no panel, and offered
    # every member a track, which then drew nothing: the list keeps no variant under it.
    shown = _shown(run["answers"][("no gene", view)])
    assert shown == {role: {"offered": False, "track": False, "list": False} for role in _ROLES}


def test_the_panels_and_seeded_genes_are_removed(run: dict[str, Any]) -> None:
    assert set(run["cleanup"].values()) <= {200, 204}, run["cleanup"]
    assert run["seeded_genes_left"] == 0
