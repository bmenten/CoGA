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
* A version is read only where a header states one: a version line
  (``##<tool>Version=…``, the ``Version`` field of a structured value, the
  ``##VEP=`` line) or a version that ``##source`` states apart from the tool's
  name (``Sniffles2_2.2``, ``pbsv 2.9.0``). A digit of a name (``Clair3``,
  ``Mutect2``, ``SHAPEIT5``) is part of the name, never a version. A command line
  (``##<tool>Command=…``, ``##<tool>Cmd=…``) names the program and its arguments,
  so it records the tool without a version and never stands in for the version
  line before or after it. The manifest is frozen into every sign-out, so a word
  that is not a version must never be recorded as one.
* Several versions of one tool in one header are all kept, joined with ``, `` as the
  pipeline record joins them (``extract_pipeline_versions``): a header that a merge or
  a second annotation left with two version lines of a tool names both, never only the
  first or the last (TRACE-8).
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
    "dragensnv": "dragen",
    "clair3": "clair3",
    "octopus": "octopus",
    "glimpse": "glimpse",
    "glimpse2": "glimpse",
    # GLIMPSE names the program in ``##source`` (``GLIMPSE_phase v2.0.0``).
    "glimpsephase": "glimpse",
    "glimpse2phase": "glimpse",
    "glimpseligate": "glimpse",
    "glimpse2ligate": "glimpse",
    # GATK's Mutect2 names itself in ``##source=Mutect2`` and states its version on
    # ``##MutectVersion=2.2``: one module.
    "mutect2": "mutect2",
    "mutect": "mutect2",
    # structural-variant callers
    "sniffles": "sniffles",
    "sniffles2": "sniffles",
    "spectre": "spectre",
    "needlr": "needlr",
    "manta": "manta",
    # Manta names its candidate-generation program (``GenerateSVCandidates 1.6.0``).
    "generatesvcandidates": "manta",
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
# A tool's version line (``##bcftools_normVersion``, ``##trgtVersion``,
# ``##DeepVariant_version``) or command line (``##bcftools_normCommand``,
# ``##trgtCommand``, ``##SnpSiftCmd``), matched on the lower-cased key: the tool, and
# which of the two lines it is.
_TOOL_LINE = re.compile(r"^([a-z][a-z0-9]*)(?:[_-][a-z0-9]+)*?[_-]?(version|cmd|command)$")
# A version-shaped word: a digit first, or a ``v`` and then a digit — ``1.21``,
# ``v4.6.1.0``, ``5.1d``, ``1.21+htslib-1.21``, ``0.7.0-a1b2c3d``. A program, a
# subcommand or a tool's name (``view``, ``trgt``, ``SnpSift``) never is one.
_VERSION_SHAPED = re.compile(r"(?i)^v?\d[\w.+-]*$")
# The fields of a structured value, ``<ID=dragen,Version="SW: …, HW: …">``: each field
# starts after the ``<`` or a comma, and a quoted value may hold commas.
_STRUCTURED_FIELD = re.compile(r'[<,]\s*([A-Za-z0-9_.:-]+)=("(?:[^"\\]|\\.)*"|[^,>]*)')
# A number glued to a ``##source`` name that ends in a digit, after a dot: ``shapeit4.1.3``.
# Where the name ends is not stated, and SHAPEIT4 4.2.2 still writes ``shapeit4.1.3``.
_GLUED_NUMBER = re.compile(r"^([A-Za-z][A-Za-z0-9]*\d)\.\d")
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
    join: bool = False,
) -> None:
    """Record ``version`` and ``detail`` for the tool ``key`` names. With ``join`` a version
    the entry does not list yet is added after the ones it holds, joined with ``, ``;
    otherwise ``overwrite`` decides whether a later value replaces an earlier one."""
    key = _canonical_key(key)
    if not key:
        return
    entry = modules.setdefault(key, {})
    version = _clean(version) if version is not None else ""
    detail = _clean(detail) if detail is not None else ""
    current = entry.get("version")
    if version and join and current:
        if not _lists_version(str(current), version):
            entry["version"] = f"{current}, {version}"
    elif version and (overwrite or not current):
        entry["version"] = version
    if detail and (overwrite or not entry.get("detail")):
        entry["detail"] = detail


def _lists_version(joined: str, version: str) -> bool:
    """Whether the versions ``joined`` holds include ``version``. A version may itself hold
    a comma (DRAGEN's ``SW: 07.021.624.3.10.4, HW: 07.021.624``), so it is looked for as
    a whole entry of the list, never by splitting it."""
    return (
        joined == version
        or joined.startswith(f"{version}, ")
        or joined.endswith(f", {version}")
        or f", {version}, " in joined
    )


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
        _set_module(prov.modules, "vep", version=leading.group(1), join=True)
    for key, raw in pairs:
        low = key.lower()
        if low in {"vep", "ensembl-vep"}:
            _set_module(prov.modules, "vep", version=raw, join=True)
        elif low == "cache":
            _set_module(prov.modules, "vep", detail=raw, overwrite=False)
        elif low in {"ensembl", "ensembl-version"} and not prov.modules.get("vep", {}).get("version"):
            _set_module(prov.modules, "vep", version=raw, overwrite=False)
        elif low in _VEP_DB_KEYS:
            canonical = _VEP_DB_KEYS[low]
            if canonical == "assembly":
                prov.reference = prov.reference or raw
            else:
                _set_module(prov.modules, canonical, version=raw, join=True)


def _parse_command_line_record(tool: str, value: str, prov: HeaderProvenance) -> None:
    """A caller's structured record of its run: the ``Version`` field gives the version.

    ``##GATKCommandLine=<ID=HaplotypeCaller,CommandLine="…",Version="4.4.0.0",…>`` (GATK 4),
    ``##GATKCommandLine.HaplotypeCaller=<ID=HaplotypeCaller,Version=3.8-0-ge9d806836,…>``
    (GATK 3, unquoted) and ``##SentieonCommandLine.Haplotyper=<ID=Haplotyper,
    Version="sentieon-genomics-202308",…>``. The command line inside is not read: GATK 3's
    ``CommandLineOptions`` lists ``version=false`` among the tool's arguments.
    """
    version = _stated_version(tool, value) if _clean(value).startswith("<") else None
    _set_module(prov.modules, tool, version=version, detail="caller", overwrite=False, join=True)


def _source_name_and_version(word: str) -> tuple[str, str | None]:
    """The tool the first word of ``##source`` names, and the version it states after a
    ``_`` or ``-``: ``Sniffles2_2.2``, ``cuteSV-1.0.13``.

    A name keeps its digits: ``Clair3``, ``GLIMPSE2``, ``Mutect2`` and ``SHAPEIT5`` state no
    version. Nor does a number glued to the name after a dot (``shapeit4.1.3``), which is
    left out of the name: SHAPEIT4 4.2.2 still writes ``shapeit4.1.3``.
    """
    if word[:1].isalpha():
        for index, char in enumerate(word):
            if char in "_-" and _VERSION_SHAPED.match(word[index + 1 :]):
                return word[:index], word[index + 1 :]
    glued = _GLUED_NUMBER.match(word)
    return (glued.group(1) if glued else word), None


def _parse_source(value: str, prov: HeaderProvenance) -> str | None:
    """``##source=Sniffles2_2.2`` / ``##source=pbsv 2.9.0 (commit …)`` / ``##source=Clair3``.

    Records the tool the first word names, with a version only where the line states one
    apart from the name, as a version-shaped word (a digit first, or a ``v`` and a digit):

    * after a ``_`` or ``-`` in the first word (``Sniffles2_2.2``, ``cuteSV-1.0.13``);
    * or as the second word (``pbsv 2.9.0 (commit …)``, ``freeBayes v1.3.6``,
      ``hificnv v1.0.1-4de7d40``, ``GLIMPSE_phase v2.0.0``).

    A later word is not read. In ``SHAPEIT5 phase_common 1.1`` it is phase_common's own
    number, and SHAPEIT5 reports that release as 5.1.1; LoFreq writes its command line
    there (``lofreq call --min-cov 10 -f ref.fa …``). Returns the tool's module key.
    """
    value = _clean(value)
    if not value:
        return None
    words = [word.strip("\"'").rstrip(",;") for word in value.split()]
    name, version = _source_name_and_version(words[0])
    if version is None and len(words) > 1 and _VERSION_SHAPED.match(words[1]):
        version = words[1]
    key = _canonical_key(name)
    if not key:
        return None
    _set_module(prov.modules, key, version=version, detail="caller", overwrite=False, join=True)
    if prov.caller is None:
        prov.caller = {"name": key, "version": version or ""}
    return key


def _stated_version(tool: str, value: str) -> str | None:
    """The version a ``##<tool>Version=…`` line states, or None when it states none.

    * A structured value states it in its ``Version`` field:
      ``<ID=dragen,Version="SW: 07.021.624.3.10.4, HW: 07.021.624">``.
    * Otherwise it is the value's first version-shaped word: ``1.21+htslib-1.21``, and
      ``5.1d`` both from snpEff's ``"5.1d (build …), by …"`` and from SnpSift's
      ``"SnpSift 5.1d (build …), by …"``, which names the tool first.
    * A value without a version-shaped word is a version only when it is a single word
      with a digit that is not the tool's own name: a release tag or a commit, such as
      ``r2.1.1``.
    """
    text = _clean(value)
    if text.startswith("<"):
        for field_match in _STRUCTURED_FIELD.finditer(text):
            if field_match.group(1).lower() == "version":
                return _clean(field_match.group(2)) or None
        return None
    words = [word.strip("\"'").rstrip(",;") for word in text.split()]
    for word in words:
        if _VERSION_SHAPED.match(word):
            return word
    if (
        len(words) == 1
        and any(char.isdigit() for char in words[0])
        and _canonical_key(words[0]) != _canonical_key(tool)
    ):
        return words[0]
    return None


def _parse_generic(key: str, value: str, prov: HeaderProvenance) -> None:
    """Catch-all for ``##<tool>Version=…`` / ``##<tool>_version=…`` / command lines.

    Only the version line gives the tool's version. A command line
    (``##bcftools_viewCommand=view …``, ``##SnpSiftCmd="SnpSift annotate …"``,
    ``##trgtCommand=trgt genotype …``) starts with the program or subcommand, not a
    version: it records only that the tool ran, so the version line, before it or after
    it, gives the version.
    """
    low = key.lower()
    m = _TOOL_LINE.match(low)
    if m:
        tool, line_kind = m.group(1), m.group(2)
        version = _stated_version(tool, value) if line_kind == "version" else None
        _set_module(prov.modules, tool, version=version, overwrite=False, join=True)
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
    # The tools the ``##source`` lines name, and the first version a ``##source_version``
    # line states (Strelka).
    source_tools: list[str] = []
    source_version: str | None = None
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
                tool = _parse_source(value, prov)
                if tool and tool not in source_tools:
                    source_tools.append(tool)
            elif low in {"source_version", "sourceversion"}:
                source_version = source_version or _stated_version("source", value)
            elif low == "vep":
                _parse_vep_line(value, prov)
            elif low.startswith("gatkcommandline"):
                _parse_command_line_record("gatk", value, prov)
            elif low.startswith("sentieoncommandline"):
                _parse_command_line_record("sentieon", value, prov)
            elif low == "snpeffversion":
                _set_module(prov.modules, "snpeff", version=_stated_version("snpeff", value), join=True)
            elif low == "snpeffcmd":
                # genome db, e.g. SnpEff GRCh38.105
                gm = re.search(r"GRCh\d+\.\d+|GRCm\d+\.\d+", value)
                _set_module(prov.modules, "snpeff", detail=gm.group(0) if gm else None, overwrite=False)
            else:
                _parse_generic(key, value, prov)
        except Exception:  # noqa: BLE001 — provenance must never break ingestion
            continue

    # Strelka states its version on a line of its own (``##source=strelka``,
    # ``##source_version=2.9.10``): the version of the tool ``##source`` names, when the
    # header names one tool there. With several, which one it belongs to is not stated.
    if source_version and len(source_tools) == 1:
        named = source_tools[0]
        _set_module(prov.modules, named, version=source_version, overwrite=False)
        if prov.caller is not None and not prov.caller.get("version"):
            prov.caller["version"] = prov.modules.get(named, {}).get("version", "")

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
