"""Extract annotation/tool provenance from VCF (and TRGT/SV) headers.

Per-family clinical traceability (see ``docs/annotation-provenance.md`` and
``docs/clinical-traceability.md``): every annotated input a family is built from
carries, in its ``##`` header lines, the versions of the tools and reference
databases that produced it — the variant caller (DeepVariant/GATK/Sniffles/
Spectre/TRGT), the annotation engine (VEP/snpEff/bcftools) and, embedded in the
VEP header, the upstream database releases (gnomAD, ClinVar, dbNSFP, SpliceAI,
dbSNP, COSMIC, …).

This module turns those header lines into a normalized ``modules`` map
(``{moduleKey: {"version": ..., "detail": ..., ...}}``) that feeds
``family_annotation_manifest`` (``source='vcf_header'``) and is rendered on the
report's provenance footer and the filter page. Parsing is **best-effort and
never raises**: an unrecognised or malformed header simply yields less, never an
error, so provenance capture can never fail an import.

Design notes
------------
* One canonical key per tool/database (``vep``, ``deepvariant``, ``gnomad`` …) so
  that provenance harvested from *different* modality VCFs (SNV, SV, TRGT) merges
  into a single per-family manifest without clobbering — each modality
  contributes the tools it names.
* The variant caller is additionally tagged with the modality it came from
  (``detail="snv caller"``) so the same family can record an SNV caller and an
  SV caller side by side.
* Raw captured lines are kept (truncated) under ``raw`` while a header is read, but
  ``as_modules`` stores neither them nor ``fileformat`` and ``file_date``: what reaches the
  manifest is the per-tool versions and details.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# Header lines longer than this are truncated when stored raw (command lines can
# be kilobytes); the parsed version fields are unaffected.
_MAX_RAW_VALUE = 600
# Stop scanning once we are clearly past the meta-header — the first data or
# column line. Callers usually pass only ``##`` lines, but this makes the parser
# safe to point at a whole file/stream too.
_HEADER_PREFIX = "##"
_COLUMN_PREFIX = "#CHROM"

_NULL = {"", ".", "na", "n/a", "null", "none", "unknown"}

# Canonical module keys we normalise tool/database names onto. Anything not in
# here is still captured under its lower-cased token, so new sources show up
# without a code change — they just render with a title-cased label.
_CANONICAL: dict[str, str] = {
    # annotation engines
    "vep": "vep",
    "ensembl-vep": "vep",
    "snpeff": "snpeff",
    "bcftools": "bcftools",
    "vcfanno": "vcfanno",
    "slivar": "slivar",
    # small-variant callers
    "deepvariant": "deepvariant",
    "gatk": "gatk",
    "haplotypecaller": "gatk",
    "dragen": "dragen",
    "clair3": "clair3",
    "octopus": "octopus",
    "glimpse": "glimpse",
    "glimpse2": "glimpse",
    # structural-variant callers
    "sniffles": "sniffles",
    "sniffles2": "sniffles",
    "spectre": "spectre",
    "needlr": "needlr",
    "manta": "manta",
    "cutesv": "cutesv",
    "delly": "delly",
    # repeat / other callers
    "trgt": "trgt",
    "paraphase": "paraphase",
    # reference databases (often embedded in the VEP header)
    "gnomad": "gnomad",
    "gnomade": "gnomad",
    "gnomadg": "gnomad",
    "clinvar": "clinvar",
    "dbnsfp": "dbnsfp",
    "spliceai": "spliceai",
    "dbsnp": "dbsnp",
    "cosmic": "cosmic",
    "sift": "sift",
    "polyphen": "polyphen",
    "alphamissense": "alphamissense",
    "revel": "revel",
    "cadd": "cadd",
    "gencode": "gencode",
    "mane": "mane",
    "hgmd": "hgmd",
    "gerp": "gerp",
}

# VEP embeds its database releases as ``key="value"`` pairs on the ``##VEP=`` line.
# Map the VEP key (lower-cased) -> our canonical module key.
_VEP_DB_KEYS: dict[str, str] = {
    "gnomade": "gnomad",
    "gnomadg": "gnomad",
    "gnomad": "gnomad",
    "clinvar": "clinvar",
    "dbnsfp": "dbnsfp",
    "spliceai": "spliceai",
    "dbsnp": "dbsnp",
    "cosmic": "cosmic",
    "sift": "sift",
    "polyphen": "polyphen",
    "gencode": "gencode",
    "assembly": "assembly",
    "hgmd-public": "hgmd",
    "mane": "mane",
    "mane_select": "mane",
}

# Tokens that mark a generic ``##key=…`` line as version/provenance-bearing.
_VERSION_TOKEN = re.compile(r"(?i)(version|_v$|cmd|command|commandline|pipeline)")
# A trailing version embedded in a "source"/name token, e.g. ``Sniffles2_2.2`` or
# ``DeepVariant-1.6.0`` or ``TRGT v0.7.0``.
_NAME_VERSION = re.compile(r"(?i)^(?P<name>[a-z][a-z0-9]*?)[ _v-]*v?(?P<version>\d[\w.+-]*)$")
# key="value" or key=value pairs (value may be quoted, may contain spaces if quoted)
_KV_PAIR = re.compile(r'([A-Za-z0-9_.:-]+)=("(?:[^"\\]|\\.)*"|\S+)')


@dataclass
class HeaderProvenance:
    """Normalised provenance harvested from one input's header."""

    modules: dict[str, dict[str, Any]] = field(default_factory=dict)
    caller: dict[str, str] | None = None
    reference: str | None = None
    fileformat: str | None = None
    file_date: str | None = None
    raw: dict[str, str] = field(default_factory=dict)

    def as_modules(self) -> dict[str, dict[str, Any]]:
        """Flatten into the manifest ``modules`` shape (one entry per tool/db)."""
        out: dict[str, dict[str, Any]] = {}
        for key, value in self.modules.items():
            out[key] = {k: v for k, v in value.items() if v not in (None, "")}
        if self.reference and "assembly" not in out:
            out["assembly"] = {"version": self.reference, "detail": "from VCF ##reference"}
        return out


def _clean(value: Any) -> str:
    text = str(value or "").strip().strip('"').strip()
    return "" if text.lower() in _NULL else text


def _truncate(value: str) -> str:
    value = value.strip()
    return value if len(value) <= _MAX_RAW_VALUE else value[: _MAX_RAW_VALUE - 1] + "…"


def _canonical_key(name: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "", name.lower())
    return _CANONICAL.get(token, token)


def _set_module(
    modules: dict[str, dict[str, Any]],
    key: str,
    *,
    version: str | None = None,
    detail: str | None = None,
    overwrite: bool = True,
) -> None:
    key = _canonical_key(key)
    if not key:
        return
    entry = modules.setdefault(key, {})
    version = _clean(version) if version is not None else ""
    detail = _clean(detail) if detail is not None else ""
    if version and (overwrite or not entry.get("version")):
        entry["version"] = version
    if detail and (overwrite or not entry.get("detail")):
        entry["detail"] = detail


def _iter_kv(text: str) -> Iterable[tuple[str, str]]:
    for match in _KV_PAIR.finditer(text):
        yield match.group(1), match.group(2).strip().strip('"')


def _parse_vep_line(value: str, prov: HeaderProvenance) -> None:
    """``##VEP="v110" cache="…" gnomADe="r2.1.1" ClinVar="202301" …`` — the
    richest single line: the VEP version plus every embedded database release."""
    pairs = list(_iter_kv(value))
    # The leading bare ``"v110"`` is VEP's own version (key is literally "VEP" or
    # the whole thing starts with a quoted token); capture it first.
    leading = re.match(r'^\s*"?v?([\w.+-]+)"?', value)
    if leading:
        _set_module(prov.modules, "vep", version=leading.group(1))
    for key, raw in pairs:
        low = key.lower()
        if low in {"vep", "ensembl-vep"}:
            _set_module(prov.modules, "vep", version=raw)
        elif low == "cache":
            _set_module(prov.modules, "vep", detail=raw, overwrite=False)
        elif low in {"ensembl", "ensembl-version"} and not prov.modules.get("vep", {}).get("version"):
            _set_module(prov.modules, "vep", version=raw, overwrite=False)
        elif low in _VEP_DB_KEYS:
            canonical = _VEP_DB_KEYS[low]
            if canonical == "assembly":
                prov.reference = prov.reference or raw
            else:
                _set_module(prov.modules, canonical, version=raw, overwrite=False)


def _parse_gatk_line(value: str, prov: HeaderProvenance) -> None:
    """``##GATKCommandLine=<ID=HaplotypeCaller,…,Version="4.4.0.0",…>``."""
    version = None
    for key, raw in _iter_kv(value):
        if key.lower() == "version":
            version = raw
            break
    _set_module(prov.modules, "gatk", version=version, detail="caller", overwrite=False)


def _parse_source(value: str, prov: HeaderProvenance) -> None:
    """``##source=Sniffles2_2.2`` / ``##source=DeepVariant`` / ``##source=TRGT``."""
    value = _clean(value)
    if not value:
        return
    token = value.split()[0]
    name, version = token, None
    # ``tool_version`` convention (Sniffles2_2.2): split on the last underscore
    # when the right side looks like a version.
    if "_" in token:
        left, _, right = token.rpartition("_")
        if left and right[:1].isdigit():
            name, version = left, right
    if version is None:
        match = _NAME_VERSION.match(token)
        if match:
            name, version = match.group("name"), match.group("version")
    key = _canonical_key(name)
    if key:
        _set_module(prov.modules, key, version=version, detail="caller", overwrite=False)
        if prov.caller is None:
            prov.caller = {"name": key, "version": version or ""}


def _parse_generic(key: str, value: str, prov: HeaderProvenance) -> None:
    """Catch-all for ``##<tool>Version=…`` / ``##<tool>_version=…`` / command lines."""
    low = key.lower()
    # e.g. bcftools_normVersion, trgtVersion, DeepVariant_version, SnpEffVersion
    m = re.match(r"^([a-z][a-z0-9]*)(?:[_-][a-z0-9]+)*?[_-]?(?:version|cmd|command)$", low)
    if m:
        tool = m.group(1)
        # value may be "1.17+htslib-1.17" or '5.1d (build …)'
        version = value.split()[0] if value else ""
        _set_module(prov.modules, tool, version=version, overwrite=False)
        return
    if low in {"source", "fileformat", "filedate", "reference"}:
        return  # handled by dedicated parsers
    if _VERSION_TOKEN.search(low):
        prov.raw[key] = _truncate(value)


def extract_header_provenance(
    lines: Iterable[str], *, modality: str | None = None
) -> HeaderProvenance:
    """Parse ``##`` header lines into normalised provenance.

    ``modality`` (``"snv"`` / ``"sv"`` / ``"repeats"`` …) tags the primary caller
    so the same family can record one caller per modality. Best-effort: malformed
    lines are skipped, never raised.
    """
    prov = HeaderProvenance()
    for raw_line in lines:
        line = (raw_line or "").rstrip("\n")
        if line.startswith(_COLUMN_PREFIX) or (line and not line.startswith(_HEADER_PREFIX)):
            break
        if not line.startswith(_HEADER_PREFIX):
            continue
        body = line[len(_HEADER_PREFIX):]
        key, sep, value = body.partition("=")
        key = key.strip()
        value = value.strip()
        if not sep:
            continue
        low = key.lower()
        try:
            if low == "fileformat":
                prov.fileformat = _clean(value)
            elif low in {"filedate", "file_date"}:
                prov.file_date = _clean(value)
            elif low == "reference":
                prov.reference = prov.reference or _clean(value).rsplit("/", 1)[-1]
            elif low == "source":
                _parse_source(value, prov)
            elif low == "vep":
                _parse_vep_line(value, prov)
            elif low.startswith("gatkcommandline"):
                _parse_gatk_line(value, prov)
            elif low == "snpeffversion":
                _set_module(prov.modules, "snpeff", version=value.split()[0] if value else "")
            elif low == "snpeffcmd":
                # genome db, e.g. SnpEff GRCh38.105
                gm = re.search(r"GRCh\d+\.\d+|GRCm\d+\.\d+", value)
                _set_module(prov.modules, "snpeff", detail=gm.group(0) if gm else None, overwrite=False)
            else:
                _parse_generic(key, value, prov)
        except Exception:  # noqa: BLE001 — provenance must never break ingestion
            continue

    # Tag the primary caller with its modality for a readable label, upgrading the
    # generic "caller" detail set during parsing.
    if modality and prov.caller:
        caller_key = prov.caller.get("name")
        if caller_key and caller_key in prov.modules:
            current = prov.modules[caller_key].get("detail")
            if not current or current == "caller":
                prov.modules[caller_key]["detail"] = f"{modality} caller"
    return prov


# --- VEP tab-output (TSV) headers ------------------------------------------- #
# VEP's ``--tab`` output states its versions as ``## <name> version <value>``
# lines (plus a ``## ENSEMBL VARIANT EFFECT PREDICTOR vX`` banner and a
# ``## Using cache in …`` line) — NOT the VCF ``##KEY=value`` form. For VCFs whose
# annotations were supplied as a separate VEP TSV, this is where the VEP / gnomAD /
# ClinVar / dbNSFP / dbSNP / … database releases live, so they need their own parser.
_VEP_TAB_TITLE = re.compile(r"(?i)^##\s*ENSEMBL VARIANT EFFECT PREDICTOR\s+v?([\w.]+)")
_VEP_TAB_VERSION = re.compile(r"^##\s+(?P<name>.+?)\s+version\s+(?P<value>.+?)\s*$")
_VEP_TAB_CACHE = re.compile(r"(?i)^##\s*Using cache in\s+(.+?)\s*$")
# Canonical keys we keep from the VEP tab header — drop ensembl-internal/build noise
# (ensembl-funcgen, genebuild, regbuild, "Using API", 1000genomes, …).
_VEP_TAB_ALLOW = {
    "vep", "clinvar", "gnomad", "dbsnp", "cosmic", "sift", "polyphen",
    "assembly", "gencode", "hgmd", "dbnsfp", "spliceai", "revel", "cadd", "mane",
}


def extract_vep_tab_provenance(lines: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Parse a VEP tab-output (``--tab``) header into the ``modules`` shape.

    Complements :func:`extract_header_provenance` (VCF ``##KEY=value`` headers).
    Best-effort: unrecognised lines are skipped, never raised.
    """
    modules: dict[str, dict[str, Any]] = {}
    for raw_line in lines:
        line = (raw_line or "").rstrip("\n")
        if not line.startswith("##"):
            # The column header (single '#') or first data row ends the meta-header.
            if line and not line.startswith("#"):
                break
            continue
        try:
            title = _VEP_TAB_TITLE.match(line)
            if title:
                _set_module(modules, "vep", version=title.group(1), overwrite=False)
                continue
            cache = _VEP_TAB_CACHE.match(line)
            if cache:
                _set_module(modules, "vep", detail=cache.group(1).rsplit("/", 1)[-1], overwrite=False)
                continue
            match = _VEP_TAB_VERSION.match(line)
            if not match:
                continue
            name, value = match.group("name").strip(), match.group("value").strip()
            if not value or "?" in value:
                continue
            key = _canonical_key(name)
            if key == "hgmdpublic":
                key = "hgmd"
            if key in _VEP_TAB_ALLOW:
                _set_module(modules, key, version=value, overwrite=False)
        except Exception:  # noqa: BLE001 — provenance must never break ingestion
            continue
    return modules


# --- INFO-description mining ------------------------------------------------- #
# Some annotation pipelines (notably the NeedlR SV annotator) state their database
# releases *inside* ``##INFO=<…Description="…">`` free text rather than in dedicated
# header lines — e.g. ``(gencode v45)``, ``(OMIM 20260129)``, ``(GenCC 20250510)``,
# ``gnomAD v4.1``, ``(GIAB v3.3)``. Mining free text is inherently more fragile than
# reading a clean ``##VEP=`` line, so this is a CONSERVATIVE, allowlisted fallback:
# only these named databases, each anchored to a version-shaped token.
_INFO_DESCRIPTION = re.compile(r'Description="((?:[^"\\]|\\.)*)"')
_INFO_DB_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\bgencode\s+(v?\d[\w.]*)"), "gencode"),
    (re.compile(r"(?i)\bgnomad\w*\s+(v?\d[\w.]*)"), "gnomad"),
    (re.compile(r"(?i)\bomim\s+(\d{6,8})\b"), "omim"),
    (re.compile(r"(?i)\bgencc\s+(\d{6,8})\b"), "gencc"),
    (re.compile(r"(?i)\bgiab\s+([\w.\-]*\d[\w.\-]*)"), "giab"),
    (re.compile(r"(?i)\bclinvar\s+(\d{6,8})\b"), "clinvar"),
    (re.compile(r"(?i)\bdbsnp\s+(v?\d[\w.]*)"), "dbsnp"),
    (re.compile(r"(?i)\bcosmic\s+(v?\d[\w.]*)"), "cosmic"),
]


def extract_info_description_provenance(lines: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Mine versioned database references out of ``##INFO`` descriptions.

    Allowlisted fallback for pipelines that put their database releases in INFO
    free text instead of header lines. First occurrence of each database wins
    (descriptions repeat the same version). Best-effort: never raises.
    """
    modules: dict[str, dict[str, Any]] = {}
    for raw_line in lines:
        line = (raw_line or "").rstrip("\n")
        if line.startswith("#CHROM") or (line and not line.startswith("#")):
            break
        if not line.startswith("##INFO"):
            continue
        described = _INFO_DESCRIPTION.search(line)
        if not described:
            continue
        text = described.group(1)
        for pattern, key in _INFO_DB_PATTERNS:
            match = pattern.search(text)
            if match:
                _set_module(modules, key, version=match.group(1).rstrip(".,);"), overwrite=False)
    return modules


def merge_module_maps(
    base: Mapping[str, Any] | None, new: Mapping[str, Any] | None
) -> dict[str, dict[str, Any]]:
    """Merge two ``modules`` maps. ``new`` fills gaps but does not overwrite a
    value ``base`` already has (so an explicit/manual entry wins over a parsed
    one when ``base`` is the curated side)."""
    out: dict[str, dict[str, Any]] = {}
    for source in (base or {}, new or {}):
        for key, value in source.items():
            entry = value if isinstance(value, dict) else {"version": str(value)}
            existing = out.setdefault(key, {})
            for field_name, field_value in entry.items():
                if field_value in (None, ""):
                    continue
                existing.setdefault(field_name, field_value)
    return {k: v for k, v in out.items() if v}
