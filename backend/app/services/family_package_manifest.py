from __future__ import annotations

import logging
import re
from typing import Any


from ..schemas import (
    FamilyImportValidationIssue,
)

from .family_package_common import PackageManifest, ParsedPed, PedMember, _issue, _metadata_dict, _normalize_header_key, sample_id_issues


logger = logging.getLogger(__name__)


_PED_SEX_CODES = {
    "0": "0",
    "unknown": "0",
    "und": "0",
    "u": "0",
    "1": "1",
    "male": "1",
    "m": "1",
    "2": "2",
    "female": "2",
    "f": "2",
}


_PED_STATUS_VALUES = {
    "unknown": "unknown",
    "unk": "unknown",
    "normal": "unaffected",
    "unaffected": "unaffected",
    "healthy": "unaffected",
    "control": "unaffected",
    "affected": "affected",
    "case": "affected",
}


_PED_NUMERIC_STATUS_VALUES = {
    "-9": "unknown",
    "0": "unknown",
    "1": "unaffected",
    "2": "affected",
}


_PED_ROLE_VALUES = {"proband", "father", "mother", "sibling", "embryo", "relative"}


_TRUE_VALUES = {"1", "true", "yes", "y", "carrier"}
# A PED carrier column that says "not a carrier".
_NOT_CARRIER_VALUES = {"0", "false", "no", "n", "not_carrier", "notcarrier", "non_carrier", "noncarrier", "non-carrier"}


_INHERITANCE_MODELS = {"AD", "AR", "XLD", "XLR", "mitochondrial"}


def _normalize_ped_sex(value: str) -> str | None:
    return _PED_SEX_CODES.get(value.strip().lower())


def _parse_ped_annotations(extra_columns: list[str]) -> tuple[dict[str, str], set[str]]:
    annotations: dict[str, str] = {}
    flags: set[str] = set()
    for raw_token in extra_columns:
        token = raw_token.strip()
        if not token:
            continue
        if "=" in token:
            key, value = token.split("=", 1)
            annotations[_normalize_header_key(key)] = value.strip()
        else:
            flags.add(token.lower())
    return annotations, flags


def _ped_clinical_status(
    phenotype: str,
    *,
    annotations: dict[str, str],
    flags: set[str],
    numeric_status_values: dict[str, str],
) -> str | None:
    for key in ("clinicalstatus", "status", "phenotype"):
        value = annotations.get(key)
        if value is None:
            continue
        normalized = _normalize_ped_status(value, numeric_status_values)
        if normalized is not None:
            return normalized
    for flag in flags:
        normalized = _normalize_ped_status(flag, numeric_status_values)
        if normalized is not None:
            return normalized
    return _normalize_ped_status(phenotype, numeric_status_values)


def _normalize_ped_status(value: str, numeric_status_values: dict[str, str]) -> str | None:
    token = value.strip().lower()
    return numeric_status_values.get(token) or _PED_STATUS_VALUES.get(token)


def _ped_numeric_status_values() -> dict[str, str]:
    return _PED_NUMERIC_STATUS_VALUES


def _ped_role_hint(
    *,
    annotations: dict[str, str],
    flags: set[str],
) -> str | None:
    for key in ("role", "sampletype", "type"):
        value = annotations.get(key)
        if value is None:
            continue
        normalized = value.strip().lower()
        if normalized in _PED_ROLE_VALUES:
            return normalized
    for flag in flags:
        if flag in _PED_ROLE_VALUES:
            return flag
    return None


def _ped_carrier_type(member: PedMember) -> str | None:
    for key in ("carriertype", "carrierkind", "carrierstatus"):
        value = member.extra.get(key)
        if value is None:
            continue
        normalized = str(value).strip().lower()
        if normalized in {"obligate", "proven"}:
            return normalized
    flags = {flag.lower() for flag in member.extra_columns}
    if {"obligatecarrier", "obligate_carrier", "obligate-carrier"}.intersection(flags):
        return "obligate"
    if {"provencarrier", "proven_carrier", "proven-carrier"}.intersection(flags):
        return "proven"
    return None


def _ped_is_carrier(member: PedMember) -> bool:
    if _ped_carrier_type(member) is not None:
        return True
    for key in ("carrier", "carrierstatus"):
        value = member.extra.get(key)
        if value is None:
            continue
        normalized = str(value).strip().lower()
        if normalized in _TRUE_VALUES or normalized in {"obligate", "proven"}:
            return True
    flags = {flag.lower() for flag in member.extra_columns}
    return bool({"carrier", "obligatecarrier", "provencarrier"}.intersection(flags))


def _ped_records_not_carrier(member: PedMember) -> bool:
    """A PED carrier column that says the member is not a carrier (as opposed to one
    that says nothing)."""
    for key in ("carrier", "carrierstatus"):
        value = member.extra.get(key)
        if value is not None and str(value).strip().lower() in _NOT_CARRIER_VALUES:
            return True
    return False


def _lookup_normalized_key(payload: dict[str, Any], *keys: str) -> Any:
    normalized_keys = {_normalize_header_key(key) for key in keys}
    for key, value in payload.items():
        if _normalize_header_key(str(key)) in normalized_keys:
            return value
    return None


def _manifest_pgt_source(manifest: PackageManifest) -> dict[str, Any]:
    metadata = _metadata_dict(manifest.metadata)
    pgt_metadata = _metadata_dict(metadata.get("pgt"))
    extras = _metadata_dict(getattr(manifest, "model_extra", None))
    return {
        **extras,
        **metadata,
        **pgt_metadata,
    }


def _manifest_sample_id_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item for item in re.split(r"[\s,;]+", value.strip()) if item]
    if isinstance(value, (list, tuple, set)):
        sample_ids: list[str] = []
        for item in value:
            sample_ids.extend(_manifest_sample_id_list(item))
        return sample_ids
    return [str(value).strip()] if str(value).strip() else []


def _normalize_manifest_inheritance_model(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    for model in _INHERITANCE_MODELS:
        if normalized.lower() == model.lower():
            return model
    return None


def _manifest_pgt_metadata(manifest: PackageManifest) -> dict[str, Any]:
    source = _manifest_pgt_source(manifest)
    inheritance_model = _normalize_manifest_inheritance_model(
        _lookup_normalized_key(source, "inheritance_model", "inheritanceModel", "inheritance", "model")
    )
    obligate_carriers = sorted(
        set(_manifest_sample_id_list(_lookup_normalized_key(source, "obligate_carriers", "obligateCarriers")))
    )
    proven_carriers = sorted(
        set(_manifest_sample_id_list(_lookup_normalized_key(source, "proven_carriers", "provenCarriers")))
    )
    affected_parents = _manifest_affected_parents(manifest)
    indexes = _manifest_indexes(manifest)
    metadata: dict[str, Any] = {}
    if inheritance_model:
        metadata["inheritance_model"] = inheritance_model
    if obligate_carriers:
        metadata["obligate_carriers"] = obligate_carriers
    if proven_carriers:
        metadata["proven_carriers"] = proven_carriers
    if affected_parents:
        metadata["affected_parents"] = affected_parents
    if indexes:
        metadata["indexes"] = indexes
    return metadata


def _manifest_carrier_types(manifest: PackageManifest) -> dict[str, str]:
    pgt_metadata = _manifest_pgt_metadata(manifest)
    carrier_types: dict[str, str] = {}
    for sample_id in pgt_metadata.get("obligate_carriers", []):
        carrier_types[str(sample_id)] = "obligate"
    for sample_id in pgt_metadata.get("proven_carriers", []):
        carrier_types[str(sample_id)] = "proven"
    return carrier_types


def _manifest_family_payload(manifest: PackageManifest) -> dict[str, Any]:
    extras = _metadata_dict(getattr(manifest, "model_extra", None))
    return _metadata_dict(extras.get("family"))


def _normalize_manifest_clinical_status(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"unknown", "unaffected", "affected"}:
        return normalized
    if normalized in {"0", "-9"}:
        return "unknown"
    if normalized == "1":
        return "unaffected"
    if normalized == "2":
        return "affected"
    return None


def _normalize_manifest_carrier_status(value: Any, carrier_type: str | None = None) -> str | None:
    if value is None:
        return "carrier" if carrier_type else None
    normalized = str(value).strip().lower()
    if normalized in {"unknown", "not_carrier", "carrier"}:
        return "carrier" if carrier_type and normalized != "carrier" else normalized
    if normalized in {"1", "true", "yes", "y"}:
        return "carrier"
    if normalized in {"0", "false", "no", "n"}:
        return "not_carrier"
    return "carrier" if carrier_type else None


def _normalize_manifest_carrier_type(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized if normalized in {"obligate", "proven", "reported", "inferred"} else None


def _manifest_member_overrides(manifest: PackageManifest) -> dict[str, dict[str, Any]]:
    members = _manifest_family_payload(manifest).get("members")
    if not isinstance(members, dict):
        return {}
    overrides: dict[str, dict[str, Any]] = {}
    for sample_id, payload in members.items():
        if not isinstance(payload, dict):
            continue
        carrier_type = _normalize_manifest_carrier_type(
            _lookup_normalized_key(payload, "carrier_type", "carrierType")
        )
        carrier_status = _normalize_manifest_carrier_status(
            _lookup_normalized_key(payload, "carrier_status", "carrierStatus", "carrier"),
            carrier_type,
        )
        override: dict[str, Any] = {}
        clinical_status = _normalize_manifest_clinical_status(
            _lookup_normalized_key(payload, "clinical_status", "clinicalStatus", "phenotype")
        )
        if clinical_status:
            override["clinical_status"] = clinical_status
        if carrier_status:
            override["carrier_status"] = carrier_status
        if carrier_type:
            override["carrier_type"] = carrier_type
        evidence = _metadata_dict(_lookup_normalized_key(payload, "carrier_evidence", "carrierEvidence"))
        if evidence:
            override["carrier_evidence"] = evidence
        role = _lookup_normalized_key(payload, "role")
        if isinstance(role, str) and role.strip().lower() in _PED_ROLE_VALUES:
            override["role"] = role.strip().lower()
        overrides[str(sample_id)] = override
    return overrides


def _manifest_affected_parents(manifest: PackageManifest) -> list[str]:
    """The parents ``metadata.pgt.affected_parents`` names: those whose condition the PGT
    tests for. ``affected_parent``, the PGT pipeline's name for it, is read too."""
    value = _lookup_normalized_key(
        _manifest_pgt_source(manifest),
        "affected_parents",
        "affectedParents",
        "affected_parent",
        "affectedParent",
    )
    return sorted(set(_manifest_sample_id_list(value)))


def _manifest_indexes(manifest: PackageManifest) -> list[str]:
    """The members ``metadata.pgt.indexes`` names: the affected relatives (or, under X-linked
    recessive inheritance, the carriers) whose haplotypes identify the risk haplotype."""
    value = _lookup_normalized_key(_manifest_pgt_source(manifest), "indexes")
    return sorted(set(_manifest_sample_id_list(value)))


def _affected_parent_status(inheritance_model: str, *, is_father: bool) -> dict[str, str] | None:
    """The status an affected parent has under an inheritance model, as the haplotype
    analysis reads it: affected under a dominant model, and under X-linked recessive
    inheritance a father; a proven carrier under a recessive model, and under X-linked
    recessive inheritance a mother. Mitochondrial inheritance is not traced through
    haplotypes, so it gives none."""
    if inheritance_model in {"AD", "XLD"} or (inheritance_model == "XLR" and is_father):
        return {"clinical_status": "affected"}
    if inheritance_model in {"AR", "XLR"}:
        return {"carrier_status": "carrier", "carrier_type": "proven"}
    return None


def _index_status(inheritance_model: str, *, sex: str) -> dict[str, str] | None:
    """The status an index has under an inheritance model: affected, as the relative whose
    haplotypes the risk haplotype is read from (under a recessive model, the affected child
    or relative); under X-linked recessive inheritance affected if male and a proven carrier
    if female, and none while its sex is not recorded. Mitochondrial inheritance gives none."""
    if inheritance_model in {"AD", "XLD", "AR"}:
        return {"clinical_status": "affected"}
    if inheritance_model == "XLR":
        if sex == "1":
            return {"clinical_status": "affected"}
        if sex == "2":
            return {"carrier_status": "carrier", "carrier_type": "proven"}
    return None


def _manifest_derived_statuses(
    manifest: PackageManifest, ped: ParsedPed
) -> tuple[dict[str, dict[str, Any]], list[FamilyImportValidationIssue], list[FamilyImportValidationIssue]]:
    """The status each affected parent (``metadata.pgt.affected_parents``) and each index
    (``metadata.pgt.indexes``) gets from ``metadata.pgt.inheritance_model``, with the errors
    and warnings that explain it.

    A status recorded for the member wins: under ``family.members`` (or, for an added
    member, ``family.add_members``), in the carrier lists, or in the PED. A derived value
    only fills what none of them states, and where a recorded status contradicts the model,
    a warning says so. Each status derived is reported as a warning too, so the user sees it
    before the import."""
    parents = _manifest_affected_parents(manifest)
    indexes = _manifest_indexes(manifest)
    if not parents and not indexes:
        return {}, [], []
    members = {member.iid: member for member in ped.members}
    fathers = {member.pid for member in ped.members if member.pid not in {"", "0"}}
    mothers = {member.mid for member in ped.members if member.mid not in {"", "0"}}
    inheritance_model = _manifest_pgt_metadata(manifest).get("inheritance_model")
    explicit = _manifest_member_overrides(manifest)
    listed_carriers = _manifest_carrier_types(manifest)
    derived: dict[str, dict[str, Any]] = {}
    errors: list[FamilyImportValidationIssue] = []
    warnings: list[FamilyImportValidationIssue] = []

    def derive(
        sample_id: str, member: PedMember, status: dict[str, str], *, role: str, code: str, key: str, hint: str = ""
    ) -> None:
        override = explicit.get(sample_id, {})
        kept: dict[str, Any] = {}
        if "clinical_status" in status:
            recorded = override.get("clinical_status") or (
                member.clinical_status if member.clinical_status != "unknown" else None
            )
            if recorded is None:
                kept["clinical_status"] = status["clinical_status"]
            elif recorded != status["clinical_status"]:
                warnings.append(
                    _issue(
                        f"{code}_status_conflict",
                        f"{sample_id} is recorded as {recorded}, though the {role} is affected under "
                        f"{inheritance_model} inheritance; the recorded status is kept.",
                        sample_id=sample_id,
                    )
                )
        else:
            recorded_carrier = override.get("carrier_status") or (
                "carrier"
                if override.get("carrier_type") or sample_id in listed_carriers or _ped_is_carrier(member)
                else "not_carrier"
                if _ped_records_not_carrier(member)
                else None
            )
            if recorded_carrier is None:
                kept.update(status)
                kept["carrier_evidence"] = {"derived_from": key, "inheritance_model": inheritance_model}
            elif recorded_carrier != "carrier":
                warnings.append(
                    _issue(
                        f"{code}_status_conflict",
                        f"{sample_id} is recorded as {recorded_carrier.replace('_', ' ')}, though the "
                        f"{role} is a carrier under {inheritance_model} inheritance; the recorded "
                        "status is kept.",
                        sample_id=sample_id,
                    )
                )
        if kept:
            derived[sample_id] = kept
            description = "affected" if "clinical_status" in kept else "a proven carrier"
            warnings.append(
                _issue(
                    f"{code}_status",
                    f"{sample_id} is recorded as {description}: the {role} under {inheritance_model} inheritance.{hint}",
                    sample_id=sample_id,
                )
            )

    for sample_id in parents:
        member = members.get(sample_id)
        if member is None:
            errors.append(
                _issue(
                    "manifest_affected_parent_unknown",
                    f"metadata.pgt.affected_parents names '{sample_id}', which is not a member of the family",
                    sample_id=sample_id,
                )
            )
            continue
        if sample_id not in fathers and sample_id not in mothers:
            errors.append(
                _issue(
                    "manifest_affected_parent_not_parent",
                    f"metadata.pgt.affected_parents names '{sample_id}', who is no one's parent in the PED",
                    sample_id=sample_id,
                )
            )
            continue
        if inheritance_model is None:
            warnings.append(
                _issue(
                    "pgt_affected_parent_needs_model",
                    f"{sample_id} is the affected parent, but metadata.pgt.inheritance_model is not "
                    "set, so its clinical and carrier status stay as recorded. With the model set "
                    "(AD, AR, XLD or XLR), the affected parent is recorded as affected or as a "
                    "proven carrier, as the model asks.",
                    sample_id=sample_id,
                )
            )
            continue
        status = _affected_parent_status(inheritance_model, is_father=sample_id in fathers)
        if status is None:
            warnings.append(
                _issue(
                    "pgt_affected_parent_no_status",
                    f"No status is derived for the affected parent {sample_id} under "
                    f"{inheritance_model} inheritance; record it under family.members.",
                    sample_id=sample_id,
                )
            )
            continue
        derive(sample_id, member, status, role="affected parent", code="pgt_affected_parent", key="metadata.pgt.affected_parents")

    for sample_id in indexes:
        member = members.get(sample_id)
        if member is None:
            errors.append(
                _issue(
                    "manifest_index_unknown",
                    f"metadata.pgt.indexes names '{sample_id}', which is not a member of the family",
                    sample_id=sample_id,
                )
            )
            continue
        if sample_id in parents:
            errors.append(
                _issue(
                    "manifest_index_is_affected_parent",
                    f"metadata.pgt.indexes names '{sample_id}', which is also the affected parent",
                    sample_id=sample_id,
                )
            )
            continue
        if inheritance_model is None:
            warnings.append(
                _issue(
                    "pgt_index_needs_model",
                    f"{sample_id} is the index, but metadata.pgt.inheritance_model is not set, so its "
                    "clinical and carrier status stay as recorded. With the model set, the index is "
                    "recorded as affected (or, under XLR, a female index as a proven carrier).",
                    sample_id=sample_id,
                )
            )
            continue
        status = _index_status(inheritance_model, sex=member.sex)
        if status is None:
            reason = (
                "its sex is not recorded, and under XLR it decides"
                if inheritance_model == "XLR"
                else f"{inheritance_model} inheritance gives none"
            )
            warnings.append(
                _issue(
                    "pgt_index_no_status",
                    f"No status is derived for the index {sample_id}: {reason}. Record it under "
                    "family.members or family.add_members.",
                    sample_id=sample_id,
                )
            )
            continue
        derive(
            sample_id,
            member,
            status,
            role="index",
            code="pgt_index",
            key="metadata.pgt.indexes",
            hint=(
                " If the index is an unaffected relative, record its status under family.members "
                "(or family.add_members)."
            ),
        )
    return derived, errors, warnings


def _manifest_member_status_overrides(
    manifest: PackageManifest, ped: ParsedPed
) -> dict[str, dict[str, Any]]:
    """``family.members`` overrides, with the affected parent's and the index's derived
    statuses beneath them (see :func:`_manifest_derived_statuses`)."""
    overrides = {sample_id: dict(override) for sample_id, override in _manifest_member_overrides(manifest).items()}
    derived, _errors, _warnings = _manifest_derived_statuses(manifest, ped)
    for sample_id, fields in derived.items():
        target = overrides.setdefault(sample_id, {})
        for key, value in fields.items():
            target.setdefault(key, value)
    return overrides


def _manifest_added_member_entries(manifest: PackageManifest) -> list[tuple[str, dict[str, Any]]]:
    """``family.add_members`` as ``(sample_id, payload)`` pairs, a list or a mapping."""
    raw = _manifest_family_payload(manifest).get("add_members")
    entries: list[tuple[str, dict[str, Any]]] = []
    if isinstance(raw, dict):
        for sample_id, payload in raw.items():
            entries.append((str(sample_id).strip(), payload if isinstance(payload, dict) else {}))
    elif isinstance(raw, list):
        for payload in raw:
            if isinstance(payload, dict):
                sample_id = payload.get("sample_id") or payload.get("id")
                entries.append((str(sample_id or "").strip(), payload))
            else:
                entries.append((str(payload or "").strip(), {}))
    return entries


_ADDED_MEMBER_PHENOTYPE_CODES = {"unknown": "0", "unaffected": "1", "affected": "2"}


def _manifest_added_ped_rows(
    manifest: PackageManifest,
    *,
    family_id: str,
    ped_sample_ids: set[str],
    ped_from_database: bool = False,
) -> tuple[list[str], list[FamilyImportValidationIssue]]:
    """PED rows for the members ``family.add_members`` adds to the PED's.

    A pipeline PED may lack a member the analysis uses: the PGT pipeline's PED holds the
    couple and the embryos, not the index whose haplotypes identify the affected one.
    Each added member becomes one more PED row, so every later step (validation, the
    family's members, the stored pedigree) sees it like any other member; its role is
    written as a ``role=`` column. Its ``father`` and ``mother``, when given, go into the
    row, so a child of the couple is one in the PED too; the PED checks then see that
    they are members of the right sex. Other links go under ``family.relationships``.

    A member already in the PED is an error, except when the PED is the stored pedigree
    of an existing family, which holds the members an earlier import added. So is an ID,
    the member's or a parent's, that a sample cannot be stored under (``sample_id_invalid``).
    """
    rows: list[str] = []
    errors: list[FamilyImportValidationIssue] = []
    seen: set[str] = set()
    for sample_id, payload in _manifest_added_member_entries(manifest):
        if not sample_id:
            errors.append(_issue("manifest_added_member_invalid", "family.add_members entries need a sample_id"))
            continue
        id_issues = sample_id_issues([sample_id], source="under family.add_members")
        if id_issues:
            errors.extend(id_issues)
            continue
        if sample_id in ped_sample_ids:
            if ped_from_database:
                continue
            errors.append(
                _issue(
                    "manifest_added_member_in_ped",
                    f"family.add_members adds '{sample_id}', which is already in the PED; "
                    "change it under family.members instead",
                    sample_id=sample_id,
                )
            )
            continue
        if sample_id in seen:
            errors.append(
                _issue(
                    "manifest_added_member_duplicate",
                    f"family.add_members lists '{sample_id}' twice",
                    sample_id=sample_id,
                )
            )
            continue
        seen.add(sample_id)
        sex_value = _lookup_normalized_key(payload, "sex")
        sex = _normalize_ped_sex(str(sex_value)) if sex_value is not None else "0"
        if sex is None:
            errors.append(
                _issue(
                    "manifest_added_member_invalid",
                    f"family.add_members gives '{sample_id}' the unsupported sex '{sex_value}'",
                    sample_id=sample_id,
                )
            )
            continue
        role_value = _lookup_normalized_key(payload, "role")
        role = str(role_value).strip().lower() if role_value is not None else "relative"
        if role not in _PED_ROLE_VALUES:
            errors.append(
                _issue(
                    "manifest_added_member_invalid",
                    f"family.add_members gives '{sample_id}' the unsupported role '{role_value}'",
                    sample_id=sample_id,
                )
            )
            continue
        status_value = _lookup_normalized_key(payload, "clinical_status", "clinicalStatus", "phenotype")
        clinical_status = (
            _normalize_manifest_clinical_status(status_value) if status_value is not None else "unknown"
        )
        if clinical_status is None:
            errors.append(
                _issue(
                    "manifest_added_member_invalid",
                    f"family.add_members gives '{sample_id}' the unsupported clinical status '{status_value}'",
                    sample_id=sample_id,
                )
            )
            continue
        phenotype = _ADDED_MEMBER_PHENOTYPE_CODES[clinical_status]
        parents = [
            str(value).strip() if value is not None and str(value).strip() else "0"
            for value in (_lookup_normalized_key(payload, "father"), _lookup_normalized_key(payload, "mother"))
        ]
        parent_issues = sample_id_issues(
            [parent for parent in parents if parent != "0"],
            source=f"a parent of {sample_id} under family.add_members",
        )
        if parent_issues:
            errors.extend(parent_issues)
            continue
        rows.append(f"{family_id} {sample_id} {parents[0]} {parents[1]} {sex} {phenotype} role={role}")
    return rows, errors


def _manifest_relationships(manifest: PackageManifest) -> list[dict[str, Any]]:
    relationships = _metadata_dict(_manifest_family_payload(manifest).get("relationships"))
    result: list[dict[str, Any]] = []
    couples = relationships.get("couples")
    if isinstance(couples, list):
        for couple in couples:
            if not isinstance(couple, dict):
                continue
            partners = couple.get("partners")
            if not isinstance(partners, list) or len(partners) != 2:
                continue
            metadata = _metadata_dict(couple.get("metadata"))
            if couple.get("context"):
                metadata["context"] = str(couple["context"])
            result.append(
                {
                    "relationship_type": "couple",
                    "sample_id_a": str(partners[0]),
                    "sample_id_b": str(partners[1]),
                    "role_a": "partner",
                    "role_b": "partner",
                    "source": "manifest",
                    "metadata": metadata,
                }
            )
    parent_child = relationships.get("parent_child")
    if isinstance(parent_child, list):
        for relationship in parent_child:
            if not isinstance(relationship, dict):
                continue
            child = relationship.get("child")
            parents = relationship.get("parents")
            if child is None or not isinstance(parents, list):
                continue
            for index, parent in enumerate(parents[:2]):
                if parent in {None, "", "0"}:
                    continue
                role = "father" if index == 0 else "mother"
                result.append(
                    {
                        "relationship_type": "parent_child",
                        "sample_id_a": str(parent),
                        "sample_id_b": str(child),
                        "role_a": role,
                        "role_b": "child",
                        "source": "manifest",
                        "metadata": {},
                    }
                )
    # A member related to the family through another, by an unknown degree: a PGT index
    # known to be on the mother's side, say. sample_id_a is the member the link goes
    # through.
    relatives = relationships.get("relatives")
    if isinstance(relatives, list):
        for relative in relatives:
            if not isinstance(relative, dict):
                continue
            member = relative.get("member") or relative.get("sample_id")
            if member is None or not str(member).strip():
                continue
            for related_to in _manifest_sample_id_list(relative.get("related_to")):
                result.append(
                    {
                        "relationship_type": "relative",
                        "sample_id_a": related_to,
                        "sample_id_b": str(member).strip(),
                        "role_a": "relative",
                        "role_b": "relative",
                        "source": "manifest",
                        "metadata": _metadata_dict(relative.get("metadata")),
                    }
                )
    return result


def _manifest_relationship_issues(
    manifest: PackageManifest, sample_ids: set[str], ped: ParsedPed | None = None
) -> list[FamilyImportValidationIssue]:
    """Errors for ``family.relationships`` entries that name someone who is not a member,
    and for a link of unknown degree (``relatives``) that links a member to itself or
    repeats a relationship the PED or the manifest records.

    Without this check a typo in a manifest relationship surfaced only when the family
    was written, after the package had validated."""
    errors: list[FamilyImportValidationIssue] = []
    relationships = _manifest_relationships(manifest)
    for relationship in relationships:
        for key in ("sample_id_a", "sample_id_b"):
            sample_id = relationship[key]
            if sample_id not in sample_ids:
                errors.append(
                    _issue(
                        "manifest_relationship_unknown_member",
                        f"family.relationships names '{sample_id}', which is not a member of the family",
                        sample_id=sample_id,
                    )
                )
    # The pairs the family will record otherwise: the manifest's own links, each PED
    # member with its parents, and the parents of a child, whom the import records as
    # a couple. The family editor refuses a link of unknown degree between any of these,
    # and so every later edit of the family would fail.
    known_pairs = {
        frozenset((relationship["sample_id_a"], relationship["sample_id_b"]))
        for relationship in relationships
        if relationship["relationship_type"] != "relative"
    }
    for member in ped.members if ped is not None else []:
        parents = [parent for parent in (member.pid, member.mid) if parent not in {"", "0"}]
        known_pairs.update(frozenset((member.iid, parent)) for parent in parents)
        if len(parents) == 2:
            known_pairs.add(frozenset(parents))
    seen_relatives: set[frozenset[str]] = set()
    for relationship in relationships:
        if relationship["relationship_type"] != "relative":
            continue
        member, related_to = relationship["sample_id_b"], relationship["sample_id_a"]
        pair = frozenset((member, related_to))
        if member == related_to:
            message = f"family.relationships.relatives links '{member}' to itself"
        elif pair in known_pairs:
            message = (
                f"family.relationships.relatives links '{member}' to '{related_to}', who are "
                "already recorded as parent and child or as a couple"
            )
        elif pair in seen_relatives:
            message = f"family.relationships.relatives links '{member}' and '{related_to}' twice"
        else:
            seen_relatives.add(pair)
            continue
        errors.append(_issue("manifest_relative_link_invalid", message, sample_id=member))
    return errors


def _parse_ped_text_strict(text_value: str) -> tuple[ParsedPed | None, list[FamilyImportValidationIssue]]:
    errors: list[FamilyImportValidationIssue] = []
    members: list[PedMember] = []
    seen_samples: set[str] = set()
    duplicate_samples: set[str] = set()
    rows: list[tuple[int, list[str]]] = []
    for line_no, line in enumerate(text_value.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 6:
            errors.append(
                _issue(
                    "ped_malformed_row",
                    f"PED row {line_no} has {len(parts)} columns; expected at least 6",
                )
            )
            continue
        rows.append((line_no, parts))

    numeric_status_values = _ped_numeric_status_values()
    for line_no, parts in rows:
        family_id, individual_id, father_id, mother_id, sex, phenotype = parts[:6]
        extra_columns = parts[6:]
        annotations, flags = _parse_ped_annotations(extra_columns)
        normalized_sex = _normalize_ped_sex(sex)
        clinical_status = _ped_clinical_status(
            phenotype,
            annotations=annotations,
            flags=flags,
            numeric_status_values=numeric_status_values,
        )
        role_hint = _ped_role_hint(annotations=annotations, flags=flags)
        if individual_id in seen_samples:
            duplicate_samples.add(individual_id)
        seen_samples.add(individual_id)
        if normalized_sex is None:
            errors.append(
                _issue(
                    "ped_invalid_sex",
                    f"PED row {line_no} has unsupported sex code '{sex}'",
                    sample_id=individual_id,
                )
            )
            normalized_sex = sex
        if clinical_status is None:
            errors.append(
                _issue(
                    "ped_invalid_phenotype",
                    f"PED row {line_no} has unsupported phenotype/status '{phenotype}'",
                    sample_id=individual_id,
                )
            )
            clinical_status = "unknown"
        members.append(
            PedMember(
                family_id=family_id,
                iid=individual_id,
                pid=father_id,
                mid=mother_id,
                sex=normalized_sex,
                phen=phenotype,
                line_no=line_no,
                clinical_status=clinical_status,
                role_hint=role_hint,
                extra=dict(annotations),
                extra_columns=extra_columns,
            )
        )

    if not members:
        errors.append(_issue("ped_empty", "PED file does not contain any sample rows"))
        return None, errors
    for sample_id in sorted(duplicate_samples):
        errors.append(_issue("ped_duplicate_sample", f"PED sample ID is duplicated: {sample_id}", sample_id=sample_id))

    sample_ids = [member.iid for member in members]
    sample_id_set = set(sample_ids)
    member_by_id = {member.iid: member for member in members}
    for member in members:
        if member.pid not in {"", "0"} and member.pid not in sample_id_set:
            errors.append(
                _issue(
                    "ped_missing_father",
                    f"Father ID '{member.pid}' for sample '{member.iid}' is not present in the PED",
                    sample_id=member.iid,
                )
            )
        if member.mid not in {"", "0"} and member.mid not in sample_id_set:
            errors.append(
                _issue(
                    "ped_missing_mother",
                    f"Mother ID '{member.mid}' for sample '{member.iid}' is not present in the PED",
                    sample_id=member.iid,
                )
            )
        father = member_by_id.get(member.pid)
        mother = member_by_id.get(member.mid)
        if father is not None and father.sex == "2":
            errors.append(
                _issue(
                    "ped_father_sex_mismatch",
                    f"Father ID '{member.pid}' for sample '{member.iid}' has female sex in the PED",
                    sample_id=member.iid,
                )
            )
        if mother is not None and mother.sex == "1":
            errors.append(
                _issue(
                    "ped_mother_sex_mismatch",
                    f"Mother ID '{member.mid}' for sample '{member.iid}' has male sex in the PED",
                    sample_id=member.iid,
                )
            )

    family_ids = list(dict.fromkeys(member.family_id for member in members))
    return ParsedPed(
        family_ids=family_ids,
        members=members,
        sample_ids=sample_ids,
        text="\n".join(
            " ".join(
                [
                    member.family_id,
                    member.iid,
                    member.pid,
                    member.mid,
                    member.sex,
                    member.phen,
                    *member.extra_columns,
                ]
            )
            for member in members
        ),
    ), errors


def _normalize_manifest_samples(samples: dict[str, Any] | list[Any] | None) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    if samples is None:
        return normalized
    if isinstance(samples, dict):
        for sample_id, payload in samples.items():
            normalized[str(sample_id)] = payload if isinstance(payload, dict) else {"value": payload}
        return normalized
    for entry in samples:
        if isinstance(entry, str):
            normalized[entry] = {}
            continue
        if not isinstance(entry, dict):
            continue
        sample_id = entry.get("sample_id") or entry.get("id")
        if sample_id:
            normalized[str(sample_id)] = dict(entry)
    return normalized


def _is_ped_embryo(member: PedMember, *, fathers: set[str], mothers: set[str]) -> bool:
    if member.role_hint == "embryo":
        return True
    if member.iid in fathers or member.iid in mothers:
        return False
    has_recorded_parents = member.pid not in {"", "0"} and member.mid not in {"", "0"}
    return has_recorded_parents and member.sex == "0" and member.clinical_status in {"unknown", "unaffected"}


def _ped_embryo_sample_ids(ped: ParsedPed) -> set[str]:
    fathers = {member.pid for member in ped.members if member.pid not in {"", "0"}}
    mothers = {member.mid for member in ped.members if member.mid not in {"", "0"}}
    return {
        member.iid
        for member in ped.members
        if _is_ped_embryo(member, fathers=fathers, mothers=mothers)
    }


def _ped_members_for_import(
    ped: ParsedPed,
    *,
    carrier_types: dict[str, str] | None = None,
    member_overrides: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    fathers = {member.pid for member in ped.members if member.pid not in {"", "0"}}
    mothers = {member.mid for member in ped.members if member.mid not in {"", "0"}}
    carrier_types = carrier_types or {}
    member_overrides = member_overrides or {}
    family_members: list[dict[str, Any]] = []
    assigned_proband = False
    for member in ped.members:
        role = member.role_hint if member.role_hint in _PED_ROLE_VALUES else None
        if member.iid in fathers:
            role = "father"
        elif member.iid in mothers:
            role = "mother"
        elif role is None and _is_ped_embryo(member, fathers=fathers, mothers=mothers):
            role = "embryo"
        elif role is None and member.clinical_status == "affected" and not assigned_proband:
            role = "proband"
        elif role is None and family_members:
            role = "sibling"
        elif role is None:
            role = "proband"
        if role == "proband":
            assigned_proband = True
        carrier_type = carrier_types.get(member.iid) or _ped_carrier_type(member)
        carrier_status = "carrier" if member.iid in carrier_types or _ped_is_carrier(member) else "unknown"
        override = member_overrides.get(member.iid, {})
        clinical_status = override.get("clinical_status") or member.clinical_status
        carrier_type = override.get("carrier_type") or carrier_type
        carrier_status = override.get("carrier_status") or (
            "carrier" if carrier_type else carrier_status
        )
        role = override.get("role") or role
        metadata: dict[str, Any] = {}
        if carrier_status == "carrier":
            metadata["carrier_status"] = True
        if carrier_type:
            metadata["carrier_type"] = carrier_type
        family_members.append(
            {
                "sample_id": member.iid,
                "father_id": member.pid if member.pid not in {"", "0"} else None,
                "mother_id": member.mid if member.mid not in {"", "0"} else None,
                "sex": {"1": "male", "2": "female"}.get(member.sex, "und"),
                "role": role,
                "clinical_status": clinical_status,
                "carrier_status": carrier_status,
                "carrier_type": carrier_type,
                "carrier_evidence": override.get("carrier_evidence") or {},
                "affected": clinical_status == "affected",
                "metadata": metadata,
            }
        )
    return family_members


def _manifest_roi_value(manifest: PackageManifest) -> str | None:
    raw_roi = manifest.roi if manifest.roi is not None else manifest.metadata.get("roi")
    if raw_roi is None:
        return None
    if isinstance(raw_roi, str):
        return raw_roi.strip() or None
    if isinstance(raw_roi, dict):
        for key in ("query", "gene", "region", "label"):
            value = raw_roi.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return None
