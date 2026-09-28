"""The validated-assembly scope (TF-01 §4 item 6, TF-06 H12, #515)."""

import pytest

from backend.app.core.config import settings
from backend.app.services import assembly_scope


@pytest.mark.parametrize("name", ["GRCh38", "grch38", " GRCh38 "])
def test_grch38_is_inside_the_default_scope(name: str) -> None:
    assert assembly_scope.is_validated_assembly(name)


@pytest.mark.parametrize("name", ["T2T-CHM13v2.0", "T2T-CHM13", "GRCh37", "hg38", "", None])
def test_everything_else_is_outside_it(name) -> None:
    # hg38 is the same sequence under another name, but the scope names what was
    # validated; an alias is not silently treated as validated.
    assert not assembly_scope.is_validated_assembly(name)


def test_the_scope_follows_the_configuration(monkeypatch) -> None:
    monkeypatch.setattr(settings, "validated_assemblies", ["GRCh38", " T2T-CHM13v2.0 ", ""])
    assert assembly_scope.validated_assemblies() == ["GRCh38", "T2T-CHM13v2.0"]
    assert assembly_scope.is_validated_assembly("T2T-CHM13v2.0")


def test_an_empty_scope_validates_nothing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "validated_assemblies", [])
    assert not assembly_scope.is_validated_assembly("GRCh38")
    assert "none configured" in assembly_scope.off_scope_message("GRCh38")


def test_the_refusal_names_the_assembly_and_the_scope() -> None:
    message = assembly_scope.off_scope_message("T2T-CHM13v2.0")
    assert "T2T-CHM13v2.0" in message
    assert "validated: GRCh38" in message
    assert "cannot be signed out" in message
