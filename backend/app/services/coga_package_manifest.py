"""The manifest the import reads for a package file ``coga-package.json``.

The import reads a package through its manifest (``family_package_source``, the manifest CoGA
keeps for the import's job). A package file says what a manifest says, and the pedigree a PED
says, in one place: ``package_manifest`` writes that manifest, which the delivery keeps and the
approved import reads.

- The file's members are the pedigree: each becomes a row of ``family.add_members``, and the
  manifest names no PED, so a PED in the folder is not read. A member's carrier fields go under
  ``family.members``, its HPO terms under ``individuals``, its assay under ``samples``.
- A file that leaves out its members, or its datasets, leaves them to the draft Discover makes
  of the folder (``draft``): the members from the folder's PED (or its per-sample folders), the
  datasets from its layout. ``proposed_sections`` names what was taken from the draft, so that a
  person checks it before approving.
- What the file gives always wins over the draft: the family ID, the analysis, the region of
  interest and the PGT details.
"""

from __future__ import annotations

from typing import Any

from .coga_package import CogaPackage, PackageMember

# The manifest's parts that describe the members, taken from the draft when the file names none.
_MEMBER_SECTIONS = ("ped", "family", "samples", "individuals", "phenotypes")


def proposed_sections(package: CogaPackage) -> list[str]:
    """What the import takes from Discover's draft for the package: ``members``, ``datasets``,
    both or none."""
    return [name for name in ("members", "datasets") if getattr(package, name) is None]


def _added_member(member: PackageMember) -> dict[str, Any]:
    entry: dict[str, Any] = {"sample_id": member.sample_id, "sex": member.sex}
    for name in ("role", "clinical_status", "father", "mother"):
        value = getattr(member, name)
        if value is not None:
            entry[name] = value
    return entry


def _member_sections(package: CogaPackage, members: list[PackageMember]) -> dict[str, Any]:
    family: dict[str, Any] = {"add_members": [_added_member(member) for member in members]}
    carriers = {
        member.sample_id: {
            name: value
            for name in ("carrier_status", "carrier_type")
            if (value := getattr(member, name)) is not None
        }
        for member in members
        if member.carrier_status is not None or member.carrier_type is not None
    }
    if carriers:
        family["members"] = carriers
    relationships: dict[str, Any] = {}
    if package.couples:
        relationships["couples"] = [
            {"partners": list(couple.partners), **({"context": couple.context} if couple.context else {})}
            for couple in package.couples
        ]
    relatives = [
        {"member": member.sample_id, "related_to": list(member.related_to)} for member in members if member.related_to
    ]
    if relatives:
        relationships["relatives"] = relatives
    if relationships:
        family["relationships"] = relationships
    sections: dict[str, Any] = {"family": family}
    samples = {
        member.sample_id: {
            name: value for name in ("assay", "assay_panel") if (value := getattr(member, name)) is not None
        }
        for member in members
        if member.assay is not None or member.assay_panel is not None
    }
    if samples:
        sections["samples"] = samples
    individuals = {
        member.sample_id: {"hpo": {"present": list(member.hpo.present), "absent": list(member.hpo.absent)}}
        for member in members
        if member.hpo is not None and (member.hpo.present or member.hpo.absent)
    }
    if individuals:
        sections["individuals"] = individuals
    return sections


def package_manifest(package: CogaPackage, draft: dict[str, Any] | None) -> dict[str, Any]:
    """The manifest for the complete package ``package``. ``draft`` is Discover's manifest of the
    package's folder; it is read only for what the file leaves out (``proposed_sections``), and
    may be None when it leaves out nothing."""
    draft = draft or {}
    manifest: dict[str, Any] = {"schema_version": 1}
    if package.family is not None:
        manifest["family_id"] = package.family.family_id
    elif draft.get("family_id"):
        manifest["family_id"] = draft["family_id"]
    if package.analysis_type is not None:
        manifest["analysis_type"] = package.analysis_type
    roi = package.family.roi if package.family is not None else None
    if roi is not None:
        manifest["roi"] = roi
    elif draft.get("roi") is not None:
        manifest["roi"] = draft["roi"]

    metadata = dict(draft.get("metadata") or {}) if isinstance(draft.get("metadata"), dict) else {}
    pgt = package.family.pgt if package.family is not None else None
    if pgt is not None:
        given: dict[str, Any] = {}
        if pgt.inheritance_model is not None:
            given["inheritance_model"] = pgt.inheritance_model
        if pgt.affected_parents:
            given["affected_parents"] = list(pgt.affected_parents)
        if pgt.indexes:
            given["indexes"] = list(pgt.indexes)
        drafted = metadata.get("pgt")
        metadata["pgt"] = {**(drafted if isinstance(drafted, dict) else {}), **given}
    if metadata:
        manifest["metadata"] = metadata

    if package.members is not None:
        manifest.update(_member_sections(package, package.members))
    else:
        manifest.update({name: draft[name] for name in _MEMBER_SECTIONS if draft.get(name) is not None})

    if package.datasets is not None:
        manifest["datasets"] = {name: dict(entry) for name, entry in package.datasets.items()}
    else:
        manifest["datasets"] = draft.get("datasets") or {}
    return manifest
