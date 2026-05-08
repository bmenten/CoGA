from pathlib import Path
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "scripts"))
import load_demo_quartet as script  # noqa: E402
from backend.app.schemas import ManualPedMemberCreate  # noqa: E402


def test_load_demo_bundle_reads_manifest_and_family_definition() -> None:
    bundle = script.load_demo_bundle(script.DEFAULT_BUNDLE_ROOT)

    assert bundle.family_id == "demo_family"
    assert bundle.project_name == "CoGA demo family"
    assert bundle.species_name == "Homo sapiens"
    assert bundle.assembly_name == "GRCh38"
    assert bundle.sample_ids == ["father", "mother", "son", "daughter"]
    assert bundle.family.family_id == "demo_family"


def test_demo_bundle_path_helpers_resolve_current_upload_files() -> None:
    bundle = script.load_demo_bundle(script.DEFAULT_BUNDLE_ROOT)

    assert script.small_variant_upload_path(bundle, "glimpse2").name == "demo_family.glimpse2.vcf"
    assert script.structural_variant_upload_path(bundle, "father", "manual").name == "father.structural.tsv"
    assert script.structural_variant_upload_path(bundle, "mother", "sniffles").name == "mother.sniffles.vcf"
    assert script.bed_upload_path(bundle, "son", "coverage").name == "son.coverage.bed"
    assert script.repeat_expansion_upload_path(bundle, "daughter").name == "daughter.trgt.vcf"


def test_missing_small_variant_file_raises() -> None:
    bundle = script.DemoBundle(
        root=Path("/tmp/does-not-exist"),
        manifest={},
        family=script.ManualPedFamilyCreate(
            family_id="demo_family",
            members=[ManualPedMemberCreate(sample_id="demo_sample")],
        ),
        family_id="demo_family",
        project_name="Demo",
        species_name="Homo sapiens",
        assembly_name="GRCh38",
        sample_ids=[],
    )

    with pytest.raises(FileNotFoundError):
        script.small_variant_upload_path(bundle, "clair3")


def test_ensure_backend_runtime_reexecs_into_backend_venv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    backend_python = tmp_path / "backend" / ".venv" / "bin" / "python"
    backend_python.parent.mkdir(parents=True)
    backend_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(script, "_runtime_missing_modules", lambda: ["sqlalchemy"])
    monkeypatch.setattr(script, "_find_backend_venv_python", lambda: backend_python)
    monkeypatch.setattr(script.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(script.sys, "argv", ["scripts/load_demo_quartet.py"])

    captured: dict[str, object] = {}

    def fake_execv(path: str, argv: list[str]) -> None:
        captured["path"] = path
        captured["argv"] = argv
        raise SystemExit(0)

    monkeypatch.setattr(script.os, "execv", fake_execv)

    with pytest.raises(SystemExit):
        script.ensure_backend_runtime(is_main_module=True)

    assert captured["path"] == str(backend_python)
    assert captured["argv"] == [
        str(backend_python),
        str(Path(script.__file__).resolve()),
    ]


def test_ensure_backend_runtime_raises_helpful_error_without_backend_venv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(script, "_runtime_missing_modules", lambda: ["sqlalchemy"])
    monkeypatch.setattr(script, "_find_backend_venv_python", lambda: None)

    with pytest.raises(ModuleNotFoundError, match=r"backend/\.venv/bin/python"):
        script.ensure_backend_runtime(is_main_module=False)
