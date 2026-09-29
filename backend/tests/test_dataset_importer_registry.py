"""The package-import dataset importers (#528): one per manifest dataset type, registered
where it is defined, and reached through the registry instead of one branch per type."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.schemas import FamilyImportDatasetSummary
from backend.app.services import family_package_datasets as datasets
from backend.app.services.family_package_common import SUPPORTED_DATASETS


def test_every_supported_dataset_type_has_exactly_one_importer() -> None:
    # A type without an importer would validate and then import nothing; the dispatcher
    # raises for it, and this keeps it from reaching a user.
    assert sorted(datasets.DATASET_IMPORTERS) == sorted(SUPPORTED_DATASETS)


def test_registering_a_type_twice_is_refused() -> None:
    register = datasets._dataset_importer("snv")
    with pytest.raises(RuntimeError, match="Two importers"):
        register(datasets.DATASET_IMPORTERS["snv"])


def _bundle(**enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        manifest=SimpleNamespace(
            datasets={name: SimpleNamespace(enabled=on) for name, on in enabled.items()}
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("dataset_type", SUPPORTED_DATASETS)
async def test_each_type_reaches_its_importer_with_the_whole_job(
    monkeypatch: pytest.MonkeyPatch, dataset_type: str
) -> None:
    seen: list[datasets.DatasetImportJob] = []

    async def importer(job: datasets.DatasetImportJob) -> FamilyImportDatasetSummary:
        seen.append(job)
        return job.summary.model_copy(update={"status": "imported"})

    monkeypatch.setitem(datasets.DATASET_IMPORTERS, dataset_type, importer)
    bundle = _bundle(**{dataset_type: True})
    summary = FamilyImportDatasetSummary(dataset_type=dataset_type)
    session, family_context, sample_contexts = object(), object(), {"S1": object()}

    async def progress(_summary: FamilyImportDatasetSummary) -> None:
        return None

    result = await datasets._import_dataset(
        session,  # type: ignore[arg-type]
        bundle=bundle,  # type: ignore[arg-type]
        summary=summary,
        family_context=family_context,  # type: ignore[arg-type]
        sample_contexts=sample_contexts,  # type: ignore[arg-type]
        conflict_mode="skip",
        progress=progress,
    )

    assert result.status == "imported"
    (job,) = seen
    assert job.session is session
    assert job.bundle is bundle
    assert job.dataset is bundle.manifest.datasets[dataset_type]
    assert job.summary is summary
    assert job.family_context is family_context
    assert job.sample_contexts is sample_contexts
    assert job.conflict_mode == "skip"
    assert job.progress is progress


@pytest.mark.asyncio
@pytest.mark.parametrize("bundle", [_bundle(snv=False), _bundle()], ids=["disabled", "absent"])
async def test_a_disabled_or_absent_dataset_is_not_imported(
    monkeypatch: pytest.MonkeyPatch, bundle: SimpleNamespace
) -> None:
    async def importer(_job: datasets.DatasetImportJob) -> FamilyImportDatasetSummary:
        raise AssertionError("must not run")

    monkeypatch.setitem(datasets.DATASET_IMPORTERS, "snv", importer)
    summary = FamilyImportDatasetSummary(dataset_type="snv")

    result = await datasets._import_dataset(
        object(),  # type: ignore[arg-type]
        bundle=bundle,  # type: ignore[arg-type]
        summary=summary,
        family_context=object(),  # type: ignore[arg-type]
        sample_contexts={},
    )

    assert result is summary


@pytest.mark.asyncio
async def test_an_enabled_type_without_an_importer_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(datasets.DATASET_IMPORTERS, "qc")

    with pytest.raises(RuntimeError, match="No importer is registered for dataset type 'qc'"):
        await datasets._import_dataset(
            object(),  # type: ignore[arg-type]
            bundle=_bundle(qc=True),  # type: ignore[arg-type]
            summary=FamilyImportDatasetSummary(dataset_type="qc"),
            family_context=object(),  # type: ignore[arg-type]
            sample_contexts={},
        )


@pytest.mark.asyncio
async def test_phenotypes_are_imported_without_a_manifest_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    async def import_phenotypes(session, **kwargs):
        calls.append(kwargs)
        return kwargs["summary"]

    monkeypatch.setattr(datasets, "_import_phenotypes_dataset", import_phenotypes)
    summary = FamilyImportDatasetSummary(dataset_type="phenotypes")

    result = await datasets._import_dataset(
        object(),  # type: ignore[arg-type]
        bundle=_bundle(),  # type: ignore[arg-type]
        summary=summary,
        family_context=object(),  # type: ignore[arg-type]
        sample_contexts={},
    )

    assert result is summary
    assert len(calls) == 1
