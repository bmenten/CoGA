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
- The reference genome the run used (``reference``, its FASTA's name) becomes the manifest's
  ``reference``, which the import records in the family's annotation manifest.
- ``proposed_package`` writes the draft into the package itself: the members (as the import reads
  Discover's draft: from the PED, with Discover's roles and carrier fields), the couples and
  relatives, assays, HPO terms, the PGT details and the datasets. The package then says
  everything, so a person can correct what was proposed as they correct what the pipeline gave.
"""

from __future__ import annotations

from typing import Any

from .coga_package import CogaPackage, PackageMember, package_document

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
    if package.reference is not None:
        manifest["reference"] = package.reference
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


_SEX = {"male": "male", "female": "female"}


def _object(value: Any) -> dict[str, Any]:
    """``value`` when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}


def _drafted_member(member: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    """A member as the import reads Discover's draft (``_ped_members_for_import``), in the package
    format: what is unknown left out."""
    sample_id = str(member["sample_id"])
    entry: dict[str, Any] = {"sample_id": sample_id, "sex": _SEX.get(str(member.get("sex")), "unknown")}
    for name, key in (("father", "father_id"), ("mother", "mother_id"), ("role", "role")):
        if member.get(key):
            entry[name] = str(member[key])
    if member.get("clinical_status") in ("affected", "unaffected"):
        entry["clinical_status"] = member["clinical_status"]
    if member.get("carrier_status") in ("carrier", "not_carrier"):
        entry["carrier_status"] = member["carrier_status"]
    if member.get("carrier_type"):
        entry["carrier_type"] = member["carrier_type"]
    samples = _object(draft.get("samples"))
    sample = _object(samples.get(sample_id))
    for name in ("assay", "assay_panel"):
        if isinstance(sample.get(name), str) and sample[name]:
            entry[name] = sample[name]
    individuals = _object(draft.get("individuals"))
    individual = _object(individuals.get(sample_id))
    hpo = _object(individual.get("hpo"))
    if hpo.get("present") or hpo.get("absent"):
        entry["hpo"] = {"present": list(hpo.get("present") or []), "absent": list(hpo.get("absent") or [])}
    return entry


def proposed_package(package: CogaPackage, draft: dict[str, Any], members: list[dict[str, Any]]) -> CogaPackage:
    """The package ``package`` with what it leaves out (``proposed_sections``) taken from
    Discover's draft ``draft`` of its folder, written into it. ``members`` are the draft's members
    as the import reads them (``_ped_members_for_import`` over the draft's PED, with its explicit
    ``family.members``, not the statuses the import derives from the PGT model, which it derives
    again). What the file gives is kept as it is."""
    document = package_document(package)
    proposed = proposed_sections(package)
    if document.get("analysis_type") is None and isinstance(draft.get("analysis_type"), str):
        # Discover names the analysis where the folder shows it (a NIPT pair, a PGT run).
        document["analysis_type"] = draft["analysis_type"]
    family = document.setdefault("family", {})
    if family.get("roi") is None and draft.get("roi") is not None and isinstance(draft.get("roi"), str):
        family["roi"] = draft["roi"]
    metadata = _object(draft.get("metadata"))
    drafted_pgt = _object(metadata.get("pgt"))
    if drafted_pgt:
        pgt = family.setdefault("pgt", {})
        for name in ("inheritance_model", "affected_parents", "indexes"):
            if pgt.get(name) in (None, []) and drafted_pgt.get(name):
                pgt[name] = drafted_pgt[name]
    if "members" in proposed:
        entries = [_drafted_member(member, draft) for member in members]
        drafted_family = _object(draft.get("family"))
        relationships = _object(drafted_family.get("relationships"))
        related: dict[str, list[str]] = {}
        for relative in relationships.get("relatives") or []:
            if isinstance(relative, dict) and relative.get("member"):
                related.setdefault(str(relative["member"]), []).extend(str(item) for item in relative.get("related_to") or [])
        for entry in entries:
            if related.get(entry["sample_id"]):
                entry["related_to"] = related[entry["sample_id"]]
        document["members"] = entries
        couples = [
            {"partners": [str(partner) for partner in couple.get("partners") or []], **({"context": str(couple["context"])} if couple.get("context") else {})}
            for couple in relationships.get("couples") or []
            if isinstance(couple, dict)
        ]
        if couples and not document.get("couples"):
            document["couples"] = couples
    if "datasets" in proposed:
        drafted = _object(draft.get("datasets"))
        document["datasets"] = {
            name: dict(entry) for name, entry in drafted.items() if isinstance(entry, dict) and entry.get("enabled", True) is not False
        }
    return CogaPackage.model_validate(document)
