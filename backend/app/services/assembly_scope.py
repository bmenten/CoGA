"""Which reference assemblies CoGA is validated on (TF-01 §4 item 6, TF-06 H12).

T2T-CHM13 can be imported as a second assembly, but the validated scope is GRCh38 only
until another assembly is separately validated. Everything that decides whether a family
is inside that scope asks here, so the sign-out gate and the "not validated for clinical
use" labels cannot disagree.
"""

from __future__ import annotations

from ..core.config import settings


def validated_assemblies() -> list[str]:
    """The configured validated assemblies, as written in ``VALIDATED_ASSEMBLIES``."""
    return [name.strip() for name in settings.validated_assemblies if name and name.strip()]


def is_validated_assembly(assembly_name: str | None) -> bool:
    """Is this assembly inside the validated scope? An unknown assembly is not."""
    if not assembly_name or not assembly_name.strip():
        return False
    wanted = assembly_name.strip().casefold()
    return any(wanted == name.casefold() for name in validated_assemblies())


def off_scope_message(assembly_name: str | None) -> str:
    """Why a family on this assembly cannot be signed out, in words for the reviewer."""
    scope = ", ".join(validated_assemblies()) or "none configured"
    if not assembly_name:
        return (
            "This family has no reference assembly linked, so it cannot be confirmed to be "
            f"inside the validated scope ({scope}). Its report cannot be signed out."
        )
    return (
        f"This family is on {assembly_name}, which is not validated for clinical use "
        f"(validated: {scope}). Its report is for research use only and cannot be signed out."
    )
