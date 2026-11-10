"""A package dataset imported on its own, as the package import runs it, for the tests that
fake the stores at their edge: the family, the files under the package's root, and the
dataset's job, run by the dataset's importer.

The family has two members: S1, the affected proband (female), and S2, the father (male),
stored under the uuids ``uuid-S1`` and ``uuid-S2``. Synthetic throughout.

Import as ``from backend.tests._package_dataset_fakes import ...``; the job is built from the
``backend.app`` module tree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import family_package_datasets
from backend.app.services.family_metadata_context import FamilyMetadataContext, family_sample_contexts
from backend.app.services.family_package_common import (
    FamilyPackageBundle,
    ManifestDataset,
    PackageManifest,
    ParsedPed,
)

SAMPLES = ("S1", "S2")


def sample_uuid(sample_id: str) -> str:
    return f"uuid-{sample_id}"


def family_context(family_id: str) -> FamilyMetadataContext:
    """The family ``family_id``: S1 the affected proband, S2 the father, on GRCh38."""
    return FamilyMetadataContext(
        family_uuid="family-uuid",
        family_id=family_id,
        project_ids=["project-uuid"],
        sample_rows=[
            {"sample_uuid": sample_uuid("S1"), "sample_id": "S1", "sex": "female", "role": "proband", "affected": True},
            {"sample_uuid": sample_uuid("S2"), "sample_id": "S2", "sex": "male", "role": "father", "affected": False},
        ],
        sample_uuid_to_name={sample_uuid(sample): sample for sample in SAMPLES},
        sample_name_to_uuid={sample: sample_uuid(sample) for sample in SAMPLES},
        affected_sample_names=["S1"],
        assembly_id="assembly-uuid",
        assembly_name="GRCh38",
    )


def package_file(root: Path, name: str, content: str | bytes) -> str:
    """Write ``content`` at ``name`` under the package's ``root``; return ``name``, the path
    a manifest names the file by."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return name


def dataset_job(
    session: Any,
    root: Path,
    family_id: str,
    dataset_type: str,
    dataset: ManifestDataset,
    *,
    conflict_mode: str = "overwrite",
) -> family_package_datasets.DatasetImportJob:
    """The job of the package's one dataset ``dataset_type``, its files under ``root``, run on
    ``session``."""
    family = family_context(family_id)
    return family_package_datasets.DatasetImportJob(
        session=session,
        bundle=FamilyPackageBundle(
            root=root,
            manifest=PackageManifest(datasets={dataset_type: dataset}),
            ped=ParsedPed(family_ids=[family_id], members=[], sample_ids=list(SAMPLES), text=""),
            kept_manifest_text="{}",
        ),
        dataset=dataset,
        summary=FamilyImportDatasetSummary(dataset_type=dataset_type, status="valid"),
        family_context=family,
        sample_contexts=family_sample_contexts(family),
        conflict_mode=conflict_mode,
    )


async def run_dataset(job: family_package_datasets.DatasetImportJob) -> FamilyImportDatasetSummary:
    """The job, run by its dataset's importer."""
    return await family_package_datasets.DATASET_IMPORTERS[job.summary.dataset_type](job)
