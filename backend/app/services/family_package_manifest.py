from __future__ import annotations

import logging
import re
from typing import Any


from ..schemas import (
    FamilyImportValidationIssue,
)

from .family_package_common import PackageManifest, ParsedPed, PedMember, _issue, _metadata_dict, _normalize_header_key


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
    metadata: dict[str, Any] = {}
    if inheritance_model:
        metadata["inheritance_model"] = inheritance_model
    if obligate_carriers:
        metadata["obligate_carriers"] = obligate_carriers
    if proven_carriers:
        metadata["proven_carriers"] = proven_carriers
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
    return result


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
