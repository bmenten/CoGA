"""An import of an analysis takes away what an earlier import of it recorded, in real Postgres.

Before its first dataset, an import of a family's analysis clears the entries kept under that
analysis's key (``family_package_qc.clear_analysis_import_records``): the run parameters
(``families.metadata.pipeline[<analysis>]``) and each of the family's samples' sequencing QC and
alignment (``samples.metadata.sequencing_qc[<analysis>]``, ``samples.metadata.alignments[<analysis>]``).
Its datasets then write the package's own again, so one the new package does not carry is not
shown as current, nor read in the folder the new package record names. This drives the real
statements against the real schema:

- the analysis's entries go, the other analyses' entries and every other key stay;
- a sample or a family without those entries is left as it is, and no key is added;
- another family's samples are not touched.

The import's placement of the clear (after the snapshot, before the first dataset, only for a
family that existed) is in test_family_package_import.py. Skipped unless ``RUN_INTEGRATION=1``
(see conftest.py); the CI ``smoke`` job sets it. All IDs and values are synthetic.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.tests.integration._datastores import run_closing_stores

pytestmark = pytest.mark.integration


def test_an_import_clears_its_own_analysis_entries_and_keeps_the_others() -> None:
    from backend.app.core.postgres import get_postgres_sessionmaker
    from backend.app.services.family_package_qc import clear_analysis_import_records

    run = uuid4().hex[:12]
    short_read = {"depth": {"mean_depth": 35.0}, "report": "qc/S1.html"}
    long_read = {"depth": {"mean_depth": 18.0}, "report": "qc/S1.long.html"}
    sample_metadata = {
        "sequencing_qc": {"short_read_wgs": short_read, "long_read_wgs": long_read},
        "alignments": {
            "short_read_wgs": {"format": "cram", "path": "S1/S1.cram"},
            "long_read_wgs": {"format": "bam", "path": "bams/S1.bam"},
        },
        "mtdna": {"haplogroup": "H1"},
    }

    async def scenario() -> None:
        sm = get_postgres_sessionmaker()
        families: dict[str, str] = {}
        async with sm() as s:
            for name, metadata in (
                ("F1", {"pipeline": {"short_read_wgs": {"genome": "GRCh38"}, "long_read_wgs": {"genome": "hg38"}}, "qc_profile": "default"}),
                ("F2", {"pipeline": {"long_read_wgs": {"genome": "hg38"}}}),
                ("F3", {"qc_profile": "default"}),
            ):
                families[name] = (
                    await s.execute(
                        text("INSERT INTO families (family_id, metadata) VALUES (:f, CAST(:m AS jsonb)) RETURNING id::text"),
                        {"f": f"CLEAR_{run}_{name}", "m": json.dumps(metadata)},
                    )
                ).scalar_one()
            for family, sample, metadata in (
                ("F1", "S1", sample_metadata),
                ("F1", "S2", {"mtdna": {"haplogroup": "U5"}}),
                ("F2", "S3", sample_metadata),
            ):
                await s.execute(
                    text(
                        "INSERT INTO samples (sample_id, family_id, sex, metadata) "
                        "VALUES (:s, CAST(:f AS uuid), 'female', CAST(:m AS jsonb))"
                    ),
                    {"s": f"CLEAR_{run}_{sample}", "f": families[family], "m": json.dumps(metadata)},
                )
            await s.commit()

        try:
            async with sm() as s:
                await clear_analysis_import_records(s, family_uuid=families["F1"], analysis="long_read_wgs")
                await clear_analysis_import_records(s, family_uuid=families["F3"], analysis="long_read_wgs")
            async with sm() as s:
                family_metadata = {
                    name: (
                        await s.execute(
                            text("SELECT metadata FROM families WHERE id = CAST(:f AS uuid)"), {"f": family_uuid}
                        )
                    ).scalar_one()
                    for name, family_uuid in families.items()
                }
                samples = {
                    row[0].rsplit("_", 1)[1]: row[1]
                    for row in (
                        await s.execute(
                            text("SELECT sample_id, metadata FROM samples WHERE sample_id LIKE :p"),
                            {"p": f"CLEAR_{run}_%"},
                        )
                    ).all()
                }
        finally:
            async with sm() as s:
                await s.execute(
                    text("DELETE FROM families WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": list(families.values())}
                )
                await s.commit()

        # The long-read run parameters go; the short-read ones and every other key stay.
        assert family_metadata["F1"] == {"pipeline": {"short_read_wgs": {"genome": "GRCh38"}}, "qc_profile": "default"}
        # A family without run parameters gets no key; another family is not touched.
        assert family_metadata["F3"] == {"qc_profile": "default"}
        assert family_metadata["F2"] == {"pipeline": {"long_read_wgs": {"genome": "hg38"}}}
        # The long-read QC and alignment of the sample go; the short-read ones and the rest stay.
        assert samples["S1"] == {
            "sequencing_qc": {"short_read_wgs": short_read},
            "alignments": {"short_read_wgs": {"format": "cram", "path": "S1/S1.cram"}},
            "mtdna": {"haplogroup": "H1"},
        }
        # A sample without those entries is left as it was.
        assert samples["S2"] == {"mtdna": {"haplogroup": "U5"}}
        # Another family's sample keeps every entry.
        assert samples["S3"] == sample_metadata

    run_closing_stores(scenario, init_schema=True)
