"""Conventional NIPT packages: the ``ccnv`` dataset, ccnv's prenatal output for a maternal
plasma (ccnv_output.py), as a package names it and Discover finds it.

A dataset entry names, per sample, the four files the import reads:

```yaml
analysis_type: conventional_nipt
datasets:
  ccnv:
    per_sample:
      CFDNA1:
        bins: CFDNA1_bins.bed.gz
        segments: CFDNA1_segments.bed.gz
        calls: CFDNA1_calls.vcf.gz
        qc: CFDNA1_qc.json
```

The files are one run's result for one sample. The QC file and the calls VCF each name the
sample and state the run's fetal sex, fetal fraction and sex-chromosome call: two files that
name different samples, or state other results, are no one result and are refused. A name that
differs from the member's is read as the member's all the same, with a warning, as a monogenic
NIPT's per-sample file is (the laboratory maps its verified tubes to the files they are in;
the owner's decision of 2026-10-28 for the monogenic NIPT): the entry's ``vcf_sample`` records a
name the laboratory verified.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from .ccnv_output import CCNV_QC_MAX_BYTES, CCNV_ROLES, CcnvOutputError, CcnvQc, ccnv_track_rows, read_ccnv_qc
from .family_identifiers import visible
from .family_package_common import UNREADABLE_FILE_ERRORS, _issue, package_text_handle, resolve_vcf_sample_id
from .nipt import CNIPT_CFDNA_ASSAY, CONVENTIONAL_NIPT_ANALYSIS_TYPE

if TYPE_CHECKING:
    from ..schemas import FamilyImportValidationIssue


#: The dataset's name in a manifest.
CCNV_DATASET = "ccnv"

#: The issue codes of the checks of a ccnv entry.
CCNV_SAMPLE_NAMED_OTHERWISE = "ccnv_sample_named_otherwise"

#: A calls VCF's header is some dozens of lines; one longer than this has no ``#CHROM`` line
#: where a header ends.
_VCF_HEADER_MAX_LINES = 10_000


def ccnv_entry_paths(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The four files a ccnv entry names, by role (``CCNV_ROLES``)."""
    return {role: entry.get(role) for role in CCNV_ROLES}


def read_ccnv_qc_file(path: Path, *, file: str) -> CcnvQc:
    """The ccnv QC file at ``path`` (shown as ``file``), read as ``read_ccnv_qc`` reads it. One
    larger than a QC file is, or one that cannot be read, raises ``CcnvOutputError``."""
    try:
        if path.stat().st_size > CCNV_QC_MAX_BYTES:
            raise CcnvOutputError(f"The ccnv QC file {file} is larger than a ccnv QC file is")
        raw = path.read_bytes()
    except OSError as exc:
        raise CcnvOutputError(f"The ccnv QC file {file} cannot be read") from exc
    return read_ccnv_qc(raw, file=file)


def read_ccnv_vcf_header(path: Path, *, file: str) -> tuple[list[str], list[str]]:
    """A ccnv calls VCF's ``##`` lines and its sample columns, read only as far as its
    ``#CHROM`` line. A file that cannot be read, or has no ``#CHROM`` line where its header
    ends, raises ``CcnvOutputError``."""
    meta: list[str] = []
    try:
        with package_text_handle(path) as handle:
            for line_no, line in enumerate(handle, start=1):
                if line.startswith("#CHROM"):
                    return meta, line.rstrip("\r\n").split("\t")[9:]
                if not line.startswith("##") or line_no > _VCF_HEADER_MAX_LINES:
                    break
                meta.append(line.rstrip("\r\n"))
    except UNREADABLE_FILE_ERRORS as exc:
        raise CcnvOutputError(f"The ccnv calls VCF {file} cannot be read") from exc
    raise CcnvOutputError(f"The ccnv calls VCF {file} has no #CHROM line")


def check_ccnv_track_header(path: Path, *, track_type: str, file: str) -> None:
    """Read a ccnv bins (``coverage``) or segments (``segments``) file up to its first data line,
    which checks its header (``ccnv_track_rows``); raises ``CcnvOutputError`` for a file that is
    no ccnv prenatal output or cannot be read. Whether it holds a row the import stores is the
    import's check, which reads it before it replaces the track."""
    try:
        with package_text_handle(path) as handle:
            head: list[str] = []
            for line in handle:
                head.append(line)
                if line.strip() and not line.startswith("#"):
                    break
    except UNREADABLE_FILE_ERRORS as exc:
        raise CcnvOutputError(f"The ccnv file {file} cannot be read") from exc
    for _row in ccnv_track_rows(head, track_type=track_type, file=file):
        pass


def ccnv_sample_issues(
    *,
    sample_id: str,
    entry: Mapping[str, Any],
    qc: CcnvQc,
    vcf_columns: list[str],
    family_sample_ids: Iterable[str],
    qc_file: str,
    vcf_file: str,
    path: Path | None = None,
) -> tuple[list[FamilyImportValidationIssue], list[FamilyImportValidationIssue]]:
    """The errors and warnings of the sample names in ``sample_id``'s ccnv entry: the calls VCF
    must hold one sample column, and it and the QC file's ``sample`` must name one sample; a
    name other than the member's (or its entry's ``vcf_sample``) is warned of, and read as the
    member's (``CCNV_SAMPLE_NAMED_OTHERWISE``)."""
    errors: list[FamilyImportValidationIssue] = []
    warnings: list[FamilyImportValidationIssue] = []
    if len(vcf_columns) != 1:
        errors.append(
            _issue(
                "ccnv_calls_columns",
                f"The ccnv calls VCF of {sample_id} ({vcf_file}) holds {len(vcf_columns)} sample "
                "columns; ccnv writes one, its sample's",
                dataset=CCNV_DATASET,
                sample_id=sample_id,
                path=path,
            )
        )
        return errors, warnings
    column = vcf_columns[0]
    if column.strip() != qc.sample:
        errors.append(
            _issue(
                "ccnv_samples_disagree",
                f"The ccnv QC file of {sample_id} ({qc_file}) and its calls VCF ({vcf_file}) name "
                "different samples: they are no one run's result",
                dataset=CCNV_DATASET,
                sample_id=sample_id,
                path=path,
            )
        )
        return errors, warnings
    declared = entry.get("vcf_sample") or entry.get("sample_name")
    vcf_sample = str(declared).strip() if declared is not None else ""
    if resolve_vcf_sample_id(qc.sample, {sample_id}) == sample_id or (vcf_sample and vcf_sample == qc.sample):
        return errors, warnings
    shown = f"'{visible(qc.sample)}'"
    other = resolve_vcf_sample_id(qc.sample, set(family_sample_ids) - {sample_id})
    named = f"{shown} after {other}, another member of the family," if other is not None else shown
    warnings.append(
        _issue(
            CCNV_SAMPLE_NAMED_OTHERWISE,
            f"The ccnv output of {sample_id} ({qc_file}, {vcf_file}) names its sample {named} not "
            f"{sample_id}: the import reads it as {sample_id}'s result. If the laboratory verified "
            f"that these files hold {sample_id}'s tube, set the entry's vcf_sample to {shown}; "
            "otherwise correct the manifest",
            dataset=CCNV_DATASET,
            sample_id=sample_id,
            path=path,
        )
    )
    return errors, warnings


# Where Discover looks for a sample's ccnv files: beside the package file, in a folder named
# after the sample, or under ``ccnv/``. ``{sample_id}`` is the sample's ID.
CCNV_FILE_PATTERNS: dict[str, list[str]] = {
    role: [
        f"{{sample_id}}{suffix}",
        f"{{sample_id}}/{{sample_id}}{suffix}",
        f"ccnv/{{sample_id}}{suffix}",
        f"ccnv/{{sample_id}}/{{sample_id}}{suffix}",
    ]
    for role, suffix in (
        ("bins", "_bins.bed.gz"),
        ("segments", "_segments.bed.gz"),
        ("calls", "_calls.vcf.gz"),
        ("qc", "_qc.json"),
    )
}

_QC_SUFFIX = "_qc.json"


def ccnv_sample_ids(root: Path) -> list[str]:
    """The samples of a package laid out as ccnv writes its output, in name order: each
    ``<sample>_qc.json`` in a place ``CCNV_FILE_PATTERNS`` names, with the sample's bins file
    beside it. Empty for any other layout. A name no sample can be stored under is listed too:
    Discover reports it, rather than leave a member out."""
    samples: set[str] = set()
    for folder in (root, root / "ccnv"):
        try:
            children = list(folder.iterdir()) if folder.is_dir() else []
        except OSError:
            continue
        for child in children:
            name = child.name
            if name.startswith("."):
                continue
            if name.endswith(_QC_SUFFIX) and child.is_file():
                sample_id = name[: -len(_QC_SUFFIX)]
                if sample_id and (folder / f"{sample_id}_bins.bed.gz").is_file():
                    samples.add(sample_id)
            elif child.is_dir() and (child / f"{name}{_QC_SUFFIX}").is_file() and (child / f"{name}_bins.bed.gz").is_file():
                samples.add(name)
    return sorted(samples)


def ccnv_family_block(sample_ids: list[str]) -> tuple[dict[str, Any], list[FamilyImportValidationIssue]]:
    """The manifest ``family`` block for a package of ccnv output without a PED: each sample a
    maternal plasma, the mother of the pregnancy, and the warning that says so. No fetus is
    proposed: its ID would be invented, and a package file names it where the laboratory
    wants one."""
    block = {
        "add_members": [{"sample_id": sample_id, "sex": "female", "role": "mother"} for sample_id in sample_ids]
    }
    message = (
        "The package has no PED. Discover took "
        + ", ".join(sample_ids)
        + " for the maternal plasma of a conventional NIPT, from its ccnv output: the mother of "
        "the pregnancy, female. Check before importing."
    )
    return block, [_issue("ccnv_plasma_proposed", message, dataset=CCNV_DATASET)]


def apply_ccnv_package(payload: dict[str, Any]) -> bool:
    """Make a drafted manifest a conventional NIPT one when Discover found ccnv output for one of
    its samples: the analysis type, and each such sample's assay tag (the maternal plasma of a
    conventional NIPT). Returns whether it did."""
    ccnv = payload.get("datasets", {}).get(CCNV_DATASET)
    if not isinstance(ccnv, dict) or not ccnv.get("enabled"):
        return False
    payload["analysis_type"] = CONVENTIONAL_NIPT_ANALYSIS_TYPE
    samples = payload.setdefault("samples", {})
    for sample_id in ccnv.get("per_sample") or {}:
        samples[sample_id] = {**samples.get(sample_id, {}), "assay": CNIPT_CFDNA_ASSAY}
    return True

