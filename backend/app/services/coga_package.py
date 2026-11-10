"""The package file a pipeline writes into a package folder: ``coga-package.json``.

One file describes a delivered package: the run that made it, where it belongs (the project, the
assembly, the analysis), the family and its members (the pedigree), the datasets the import reads,
every file with its checksum, and the pipeline's QC. It takes the place of three files: the PED,
the manifest and the ready marker ``coga-ready.json`` (format ``coga-package/1``,
docs/coga-package.md).

The pipeline writes the file whole each time, under a temporary name renamed over it, and may write
it more than once. Its ``status`` says whether the package is there: ``running`` while the run is
under way (written when it starts, so that a run into a folder delivered before tells CoGA that
folder is changing), ``failed`` when it stopped, and ``complete`` as its last step. Only a
``complete`` file is offered for import; the others are read for their status and run only.

A field the format does not know is refused, never kept unread: a misspelt field
(``clinical_staus``) would otherwise leave its member's status unset without a word. Free
information goes under ``extra``, which CoGA keeps and never reads.

- ``read_coga_package`` reads the file's bytes: UTF-8 JSON, one object, no key twice, no NaN or
  Infinity, no NUL or lone surrogate, at most ``PACKAGE_MAX_BYTES``.
- ``CogaPackage`` is the format; ``coga_package_json_schema`` is its JSON Schema, which a
  pipeline can check the file against before writing it
  (``docs/schemas/coga-package-1.schema.json``, written by
  ``scripts/generate-coga-package-schema.py``).
- ``package_issues`` checks what the schema cannot say: the fields a complete file needs, and that
  every member, file and dataset named is the one meant.

What the package says against the folder and CoGA (the files there, the project, the family) is
checked where the package is read for import.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Annotated, Any, Literal, get_args

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from .family_identifiers import identifier_problem
from .family_package_common import manifest_text_problem

PACKAGE_FILE_NAME = "coga-package.json"
PACKAGE_SCHEMA = "coga-package/1"
# A package lists its files: a few hundred, at most some thousands.
PACKAGE_MAX_BYTES = 20 * 1024 * 1024

PackageStatus = Literal["running", "complete", "failed"]
# The analyses a package may name (as import_ready_marker.ANALYSIS_TYPES).
AnalysisType = Literal["wgs", "wgs_trio", "pgt_m", "monogenic_nipt", "long_read_wgs", "long_read_couple"]
# The datasets the import reads (family_package_common.SUPPORTED_DATASETS).
DatasetName = Literal[
    "snv",
    "sv_needlr",
    "repeats_trgt",
    "wisecondorx",
    "qdnaseq",
    "apcad",
    "pcf",
    "haplotypes",
    "paraphase",
    "coverage",
    "cnv",
    "mito",
    "alignments",
    "qc",
    "pipeline_info",
]
Sex = Literal["male", "female", "unknown"]
# A member's role in the family (family_package_manifest._PED_ROLE_VALUES).
Role = Literal["proband", "father", "mother", "sibling", "embryo", "relative"]
ClinicalStatus = Literal["affected", "unaffected", "unknown"]
CarrierStatus = Literal["carrier", "not_carrier", "unknown"]
CarrierType = Literal["obligate", "proven", "reported", "inferred"]
InheritanceModel = Literal["AD", "AR", "XLD", "XLR", "mitochondrial"]
QcStatus = Literal["pass", "warn", "fail"]

# What a complete file must hold beyond what every file holds (``schema``, ``status``,
# ``written_at``, ``pipeline``): where the package belongs and what is in it.
COMPLETE_REQUIRED_FIELDS = ("project", "assembly", "analysis_type", "family", "files")

# The JSON Schema's approximation of the ID rule (family_identifiers.identifier_problem, which
# CoGA applies): printable, no whitespace, no '/' or '\', not '.' or '..'.
IDENTIFIER_PATTERN = r"^(?!\.\.?$)[^\s/\\\u0000-\u001f\u007f-\u009f]+$"
# A path in the package folder: relative, '/' between its parts, none of them empty, '.' or '..',
# no '\', no leading '~', no control character.
PACKAGE_PATH_PATTERN = r"^(?![/~])(?!.*//)(?!.*/$)(?!(?:.*/)?\.\.?(?:/|$))[^\\\u0000-\u001f\u007f]+$"
HPO_PATTERN = r"^HP:[0-9]{7}$"


def _identifier(value: str) -> str:
    problem = identifier_problem(value)
    if problem is not None:
        raise ValueError(f"is no family or sample ID: it {problem}")
    return value


def _package_path(value: str) -> str:
    if not value:
        raise ValueError("is empty")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError("holds a control character")
    if "\\" in value:
        raise ValueError("holds a backslash: write '/' between a path's parts")
    if value.startswith("/"):
        raise ValueError("is an absolute path: write it relative to the package folder")
    if value.startswith("~"):
        raise ValueError("starts with '~': write it relative to the package folder")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("has an empty, '.' or '..' part: name the file in the package folder itself")
    return value


Identifier = Annotated[
    str, AfterValidator(_identifier), Field(min_length=1, json_schema_extra={"pattern": IDENTIFIER_PATTERN})
]
PackagePath = Annotated[
    str, AfterValidator(_package_path), Field(min_length=1, json_schema_extra={"pattern": PACKAGE_PATH_PATTERN})
]
Text = Annotated[str, Field(min_length=1)]
HpoTerm = Annotated[str, Field(pattern=HPO_PATTERN)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PackagePipeline(_Strict):
    """The pipeline and its run. ``run_id`` is one per run: a run that writes the file again (as
    ``running``, then ``complete``) keeps it."""

    name: Text
    version: Text
    run_id: Text


class PackagePgt(_Strict):
    """What a PGT-M analysis needs beyond the pedigree."""

    inheritance_model: InheritanceModel | None = None
    # The parent(s) whose condition the PGT tests for.
    affected_parents: list[Identifier] = Field(default_factory=list)
    # The relative(s) the risk haplotype is read from.
    indexes: list[Identifier] = Field(default_factory=list)


class PackageFamily(_Strict):
    family_id: Identifier
    # The region of interest: a gene (symbol, Ensembl or RefSeq ID) or a region (chr7:1000-2000).
    roi: Text | None = None
    pgt: PackagePgt | None = None


class PackageHpo(_Strict):
    present: list[HpoTerm] = Field(default_factory=list)
    absent: list[HpoTerm] = Field(default_factory=list)


class PackageMember(_Strict):
    """One member of the family: the only place the package describes them. ``father`` and
    ``mother`` are members; a member without data (a fetus) is listed too."""

    sample_id: Identifier
    sex: Sex
    father: Identifier | None = None
    mother: Identifier | None = None
    role: Role | None = None
    # Not given: unknown.
    clinical_status: ClinicalStatus | None = None
    carrier_status: CarrierStatus | None = None
    carrier_type: CarrierType | None = None
    # The sample's assay where it is not the family's (``nipt_cfdna`` for a maternal plasma) and
    # its capture panel.
    assay: Text | None = None
    assay_panel: Text | None = None
    hpo: PackageHpo | None = None
    # Members this one is related to by an unknown degree (a PGT index on the mother's side).
    related_to: list[Identifier] = Field(default_factory=list)


class PackageCouple(_Strict):
    partners: list[Identifier] = Field(min_length=2, max_length=2)
    # Why they are a couple: ``reproductive``, ``carrier screening``.
    context: Text | None = None


class PackageFile(_Strict):
    """A file of the package: its path in the folder, its size in bytes and its checksum, as the
    pipeline made it. SHA-256 is preferred; MD5 is read where a pipeline has only that."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "anyOf": [
                {"required": ["sha256"], "properties": {"sha256": {"type": "string"}}},
                {"required": ["md5"], "properties": {"md5": {"type": "string"}}},
            ]
        },
    )

    path: PackagePath
    size: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    md5: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @model_validator(mode="after")
    def _a_checksum(self) -> PackageFile:
        if self.sha256 is None and self.md5 is None:
            raise ValueError("a file needs its sha256 or its md5")
        return self


class PackageQc(_Strict):
    status: QcStatus
    # The pipeline's QC report, a file of the package.
    report: PackagePath | None = None


class CogaPackage(_Strict):
    """``coga-package/1``. Every file holds ``schema``, ``status``, ``written_at`` and
    ``pipeline``; a complete one also ``COMPLETE_REQUIRED_FIELDS``. ``members``, ``couples``,
    ``datasets`` and ``pipeline_used_pedigree`` may be left out: CoGA then proposes them from the
    folder, as Discover does, and a person checks them before the import."""

    schema_: Literal["coga-package/1"] = Field(alias="schema")
    status: PackageStatus
    written_at: AwareDatetime
    pipeline: PackagePipeline
    project: Text | None = None
    assembly: Text | None = None
    analysis_type: AnalysisType | None = None
    family: PackageFamily | None = None
    members: list[PackageMember] | None = None
    couples: list[PackageCouple] = Field(default_factory=list)
    # Each dataset's entry, as the manifest's ``datasets`` block holds it (docs/data-import.md,
    # "The manifest"); its paths are files of the package.
    datasets: dict[DatasetName, dict[str, Any]] | None = None
    # The datasets the pipeline computed with the pedigree or the members' sex (phasing with the
    # PED, a trio filter, APCAD): a change of either in CoGA does not change them.
    pipeline_used_pedigree: list[DatasetName] = Field(default_factory=list)
    files: list[PackageFile] | None = None
    qc: PackageQc | None = None
    request_id: Text | None = None
    # The reference genome the run used (its FASTA's name).
    reference: Text | None = None
    notes: str | None = None
    # Kept with the package, never read.
    extra: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PackageIssue:
    """What is wrong with a package file: a code, a sentence, and where (``members[2].sex``)."""

    code: str
    message: str
    location: str | None = None


@dataclass(frozen=True, slots=True)
class PackageRead:
    """A package file as read: its bytes' SHA-256 (None when there were none to read), the package
    (None when it is no ``coga-package/1`` file) and what is wrong with it."""

    sha256: str | None
    package: CogaPackage | None
    issues: list[PackageIssue] = field(default_factory=list)


class _DuplicateKey(ValueError):
    pass


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = Counter(key for key, _ in pairs)
    twice = sorted(key for key, count in keys.items() if count > 1)
    if twice:
        raise _DuplicateKey(twice[0])
    return dict(pairs)


def _refuse_constant(name: str) -> Any:
    raise ValueError(name)


def _unstorable(value: Any) -> bool:
    """Whether a key or a string anywhere in the parsed file holds a NUL or a lone surrogate."""
    if isinstance(value, str):
        return manifest_text_problem(value) is not None
    if isinstance(value, dict):
        return any(_unstorable(key) or _unstorable(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_unstorable(item) for item in value)
    return False


def _non_finite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_non_finite(item) for item in value.values())
    if isinstance(value, list):
        return any(_non_finite(item) for item in value)
    return False


def _location(loc: tuple[int | str, ...]) -> str:
    text = ""
    for part in loc:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += f".{part}" if text else str(part)
    return text


def _validation_issues(error: ValidationError) -> list[PackageIssue]:
    issues: list[PackageIssue] = []
    for item in error.errors(include_url=False, include_input=False):
        location = _location(tuple(part for part in item["loc"] if part != "[key]"))
        message = str(item["msg"])
        if message.startswith("Value error, "):
            message = message[len("Value error, ") :]
        code = {
            "extra_forbidden": "package_field_unknown",
            "missing": "package_field_missing",
        }.get(str(item["type"]), "package_field_invalid")
        if code == "package_field_unknown":
            message = "the format has no such field; free information goes under extra"
        issues.append(PackageIssue(code, f"{location}: {message}", location))
    return issues


def read_coga_package(raw: bytes) -> PackageRead:
    """Read a package file's bytes. A file of another format, or one that is no JSON object, is
    no package; one that is but breaks the format is read with its issues."""
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) > PACKAGE_MAX_BYTES:
        return PackageRead(
            digest,
            None,
            [PackageIssue("package_too_large", f"{PACKAGE_FILE_NAME} is larger than {PACKAGE_MAX_BYTES // (1024 * 1024)} MB.")],
        )
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_refuse_constant,
        )
    except _DuplicateKey:
        return PackageRead(
            digest, None, [PackageIssue("package_duplicate_key", f"{PACKAGE_FILE_NAME} gives a key twice in one object.")]
        )
    except (UnicodeDecodeError, ValueError):
        return PackageRead(digest, None, [PackageIssue("package_not_json", f"{PACKAGE_FILE_NAME} is no JSON text.")])
    if not isinstance(payload, dict):
        return PackageRead(digest, None, [PackageIssue("package_not_json", f"{PACKAGE_FILE_NAME} is no JSON object.")])
    # A NUL or a lone surrogate (JSON escapes can carry both) is no text Postgres stores.
    if _unstorable(payload):
        return PackageRead(
            digest,
            None,
            [PackageIssue("package_unstorable_text", f"{PACKAGE_FILE_NAME} holds a NUL or a lone surrogate.")],
        )
    # A number too large for a float reads as infinity.
    if _non_finite(payload):
        return PackageRead(
            digest, None, [PackageIssue("package_number_invalid", f"{PACKAGE_FILE_NAME} holds a number out of range.")]
        )
    if payload.get("schema") != PACKAGE_SCHEMA:
        return PackageRead(
            digest, None, [PackageIssue("package_schema_unknown", f"{PACKAGE_FILE_NAME} is not of the format {PACKAGE_SCHEMA}.")]
        )
    try:
        package = CogaPackage.model_validate(payload)
    except ValidationError as error:
        return PackageRead(digest, None, _validation_issues(error))
    return PackageRead(digest, package, package_issues(package))


def _duplicates(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def package_issues(package: CogaPackage) -> list[PackageIssue]:
    """What the schema cannot say: the fields a complete file needs, and that every member,
    file and dataset the file names is one it describes."""
    issues: list[PackageIssue] = []
    if package.status == "complete":
        for name in COMPLETE_REQUIRED_FIELDS:
            if getattr(package, name) in (None, []):
                issues.append(PackageIssue("package_field_missing", f"{name}: a complete package needs it", name))

    if package.files is not None:
        paths = [entry.path for entry in package.files]
        for path in _duplicates(paths):
            issues.append(PackageIssue("package_file_listed_twice", f"files: {path} is listed twice", "files"))
        if PACKAGE_FILE_NAME in paths:
            issues.append(
                PackageIssue("package_lists_itself", f"files: {PACKAGE_FILE_NAME} cannot list itself", "files")
            )

    members = package.members
    if members is None:
        if package.couples:
            issues.append(PackageIssue("package_members_missing", "couples: name the members to link them", "couples"))
    else:
        issues.extend(_member_issues(package, members))

    if package.datasets is not None:
        for name in package.pipeline_used_pedigree:
            if name not in package.datasets:
                issues.append(
                    PackageIssue(
                        "package_dataset_unknown",
                        f"pipeline_used_pedigree: {name} is no dataset of the package",
                        "pipeline_used_pedigree",
                    )
                )
    return issues


def _member_issues(package: CogaPackage, members: list[PackageMember]) -> list[PackageIssue]:
    issues: list[PackageIssue] = []
    sample_ids = [member.sample_id for member in members]
    for sample_id in _duplicates(sample_ids):
        issues.append(PackageIssue("package_member_twice", f"members: {sample_id} is listed twice", "members"))
    known = {member.sample_id: member for member in members}

    def unknown(location: str, sample_id: str) -> None:
        issues.append(PackageIssue("package_member_unknown", f"{location}: {sample_id} is no member", location))

    for position, member in enumerate(members):
        here = f"members[{position}]"
        for parent, sex in (("father", "female"), ("mother", "male")):
            parent_id = getattr(member, parent)
            if parent_id is None:
                continue
            if parent_id == member.sample_id:
                issues.append(
                    PackageIssue("package_member_own_parent", f"{here}.{parent}: a member is not its own parent", f"{here}.{parent}")
                )
            elif parent_id not in known:
                unknown(f"{here}.{parent}", parent_id)
            elif known[parent_id].sex == sex:
                issues.append(
                    PackageIssue(
                        "package_parent_sex",
                        f"{here}.{parent}: {parent_id} is {sex}",
                        f"{here}.{parent}",
                    )
                )
        for related in member.related_to:
            if related == member.sample_id:
                issues.append(
                    PackageIssue(
                        "package_member_own_relative", f"{here}.related_to: a member is not its own relative", f"{here}.related_to"
                    )
                )
            elif related not in known:
                unknown(f"{here}.related_to", related)

    for position, couple in enumerate(package.couples):
        here = f"couples[{position}]"
        if couple.partners[0] == couple.partners[1]:
            issues.append(PackageIssue("package_couple_invalid", f"{here}: a couple is two members", here))
        for partner in couple.partners:
            if partner not in known:
                unknown(f"{here}.partners", partner)

    pgt = package.family.pgt if package.family is not None else None
    if pgt is not None:
        for name in ("affected_parents", "indexes"):
            for sample_id in getattr(pgt, name):
                if sample_id not in known:
                    unknown(f"family.pgt.{name}", sample_id)

    for name, entry in (package.datasets or {}).items():
        per_sample = entry.get("per_sample")
        if isinstance(per_sample, dict):
            for sample_id in per_sample:
                if sample_id not in known:
                    unknown(f"datasets.{name}.per_sample", str(sample_id))
    return issues


def coga_package_json_schema() -> dict[str, Any]:
    """The JSON Schema of ``coga-package/1`` (draft 2020-12), with what a complete file needs."""
    schema = CogaPackage.model_json_schema(by_alias=True, mode="validation")
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "coga-package-1.schema.json",
        **schema,
    }
    schema["title"] = PACKAGE_SCHEMA
    schema["description"] = (
        "The package file a pipeline writes into a package folder for CoGA to import "
        "(docs/coga-package.md). Only a file whose status is complete is offered for import. "
        "CoGA also checks what a schema cannot say: that every member, file and dataset named "
        "is one the file describes, and the ID rule in full."
    )
    schema["allOf"] = [
        {
            "if": {"properties": {"status": {"const": "complete"}}, "required": ["status"]},
            "then": {
                "required": list(COMPLETE_REQUIRED_FIELDS),
                "properties": {
                    name: {"not": {"type": "null"}} for name in COMPLETE_REQUIRED_FIELDS
                }
                | {"files": {"type": "array", "minItems": 1}},
            },
        }
    ]
    return schema


def coga_package_json_schema_text() -> str:
    """The JSON Schema as docs/schemas/coga-package-1.schema.json holds it."""
    return json.dumps(coga_package_json_schema(), indent=2, ensure_ascii=False) + "\n"


def literal_values(alias: Any) -> tuple[str, ...]:
    """The values of one of the ``Literal`` aliases above, for the tests that hold them to the
    sets the import reads."""
    return tuple(get_args(alias))
