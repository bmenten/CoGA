#!/usr/bin/env python3
"""
Build a clinically relevant CNV knowledgebase TSV.

Main output:
    clinical_cnv_knowledgebase.tsv

Sources:
    - ClinGen Dosage Sensitivity curated regions
    - ClinGen recurrent CNV regions, when available from downloads page
    - UCSC cytobands
    - ClinVar structural/copy-number variants
    - Optional OMIM API enrichment
    - Optional Orphanet XML enrichment
    - Optional DECIPHER manual mapping TSV

Install:
    pip install pandas requests beautifulsoup4 intervaltree tqdm

Usage:
    python build_clinical_cnv_kb.py \
        --assembly GRCh38 \
        --out clinical_cnv_knowledgebase.tsv

Optional:
    export OMIM_API_KEY=your_key
    python build_clinical_cnv_kb.py --orphanet-xml en_product6.xml --decipher-map decipher_map.tsv
"""

from __future__ import annotations

import argparse
import gzip
import io
import os
import re
import sys
import tempfile
import time
import hashlib
from pathlib import Path
from typing import Optional, Dict, List, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup
from intervaltree import Interval, IntervalTree
from tqdm import tqdm
import xml.etree.ElementTree as ET


CLINGEN_DOWNLOADS = "https://search.clinicalgenome.org/kb/downloads"

# Authoritative recurrent-CNV regions with inline syndrome names
# (TAR, Williams-Beuren, DiGeorge, ...). Complements the region curation list.
CLINGEN_RECURRENT_CNV = {
    "GRCh38": "https://ftp.clinicalgenome.org/ClinGen_recurrent_CNV_V2.1-hg38.bed",
    "GRCh37": "https://ftp.clinicalgenome.org/ClinGen_recurrent_CNV_V2.1-hg19.bed",
}

UCSC_CYTO = {
    "GRCh38": "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/database/cytoBand.txt.gz",
    "GRCh37": "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/database/cytoBand.txt.gz",
}

CLINVAR_VARIANT_SUMMARY = (
    "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/variant_summary.txt.gz"
)

OMIM_API = "https://api.omim.org/api"

# Orphanet rare-disease nomenclature (disorder names + OrphaCodes), used to
# match syndrome names. Auto-downloaded when no local XML is provided.
ORPHANET_NOMENCLATURE_URL = "https://www.orphadata.com/data/xml/en_product1.xml"


def log(msg: str) -> None:
    print(f"[build-cnv-kb] {msg}", file=sys.stderr)


def safe_get(url: str, timeout: int = 60) -> bytes:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content


def clean_chr(chrom: str) -> str:
    chrom = str(chrom).strip()
    chrom = chrom.replace("chr", "")
    chrom = chrom.replace("CHR", "")
    if chrom == "23":
        return "X"
    if chrom == "24":
        return "Y"
    if chrom in {"M", "MT"}:
        return "MT"
    return chrom


def canonical_chr_sort_key(chrom: str) -> int:
    chrom = clean_chr(chrom)
    if chrom.isdigit():
        return int(chrom)
    if chrom == "X":
        return 23
    if chrom == "Y":
        return 24
    if chrom == "MT":
        return 25
    return 99


def parse_interval(value: str) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    if pd.isna(value):
        return None, None, None

    s = str(value).replace(",", "").strip()

    patterns = [
        r"(?:chr)?([0-9XYM]+)\s*:\s*(\d+)\s*-\s*(\d+)",
        r"(?:chr)?([0-9XYM]+)\s+(\d+)\s+(\d+)",
    ]

    for pat in patterns:
        m = re.search(pat, s, flags=re.IGNORECASE)
        if m:
            chrom = clean_chr(m.group(1))
            start = int(m.group(2))
            end = int(m.group(3))
            if start > end:
                start, end = end, start
            return chrom, start, end

    return None, None, None


def first_existing_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]

    for c in df.columns:
        cl = c.lower()
        for cand in candidates:
            if cand.lower() in cl:
                return c

    return None


def extract_omim_ids(text: str) -> str:
    if pd.isna(text):
        return ""
    ids = sorted(set(re.findall(r"\b[1-6]\d{5}\b", str(text))))
    return ";".join(ids)


def make_id(prefix: str, chrom: str, start: int, end: int, name: str) -> str:
    raw = f"{prefix}|{chrom}|{start}|{end}|{name}"
    return prefix + "_" + hashlib.md5(raw.encode()).hexdigest()[:12]


def discover_clingen_tsv_urls(assembly: str) -> List[str]:
    """
    Scrape the ClinGen downloads page and keep likely region/recurrent CNV TSVs.
    This avoids hard-coding URLs that occasionally change.
    """
    html = safe_get(CLINGEN_DOWNLOADS).decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    urls = []
    wanted_asm = "GRCh38" if assembly == "GRCh38" else "GRCh37"

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = " ".join([a.get_text(" ", strip=True), href])

        if not re.search(r"\.(tsv|txt|bed)(\.gz)?$", href, re.I):
            continue

        if wanted_asm.lower() not in text.lower() and assembly.lower() not in text.lower():
            continue

        if not any(k in text.lower() for k in ["region", "recurrent", "cnv", "dosage"]):
            continue

        if href.startswith("ftp://"):
            # ClinGen links to its FTP host, which also serves over HTTPS.
            # `requests` can't fetch ftp:// URLs, so rewrite to https://.
            href = "https://" + href[len("ftp://"):]
        elif href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = "https://search.clinicalgenome.org" + href
        elif href.startswith("http"):
            pass
        else:
            href = "https://search.clinicalgenome.org/kb/" + href.lstrip("./")

        urls.append(href)

    return sorted(set(urls))


def load_table_from_url(url: str) -> pd.DataFrame:
    content = safe_get(url)
    if url.endswith(".gz"):
        content = gzip.decompress(content)

    text = content.decode("utf-8", errors="replace")
    lines = text.splitlines()

    # Some sources (e.g. ClinGen region curation lists) prefix BOTH the metadata
    # block AND the real column header with '#'. Detect the column header as the
    # last leading-'#' line that is tab-delimited, strip its '#', and parse from
    # there so the header is not silently dropped as a comment.
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("#"):
            if "\t" in line:
                header_idx = i
        else:
            break

    if header_idx is not None:
        body_lines = lines[header_idx:]
        body_lines[0] = body_lines[0].lstrip("#")
        return pd.read_csv(io.StringIO("\n".join(body_lines)), sep="\t", dtype=str)

    sep = "\t" if "\t" in text[:5000] else ","
    return pd.read_csv(io.StringIO(text), sep=sep, dtype=str, comment="#")


def normalize_clingen_table(df: pd.DataFrame, source_url: str, assembly: str) -> pd.DataFrame:
    # Note: do NOT include location columns here. ClinGen's "ISCA Region Name"
    # holds the real region name; including "Genomic Location" made the name
    # resolve to coordinates instead.
    name_col = first_existing_col(df, [
        "ISCA Region Name", "Region Name", "region_name", "Region", "Name",
        "Dosage Region", "Syndrome",
    ])

    location_col = first_existing_col(df, [
        "Genomic Location", "GRCh38 Location", "GRCh37 Location",
        "Location", "coordinates", "genomic_location"
    ])

    chrom_col = first_existing_col(df, ["Chromosome", "chr", "chrom"])
    start_col = first_existing_col(df, ["Start", "start", "Begin"])
    end_col = first_existing_col(df, ["End", "end", "Stop"])

    cyto_col = first_existing_col(df, [
        "Cytoband", "cytoband", "Cyto Band", "Cytogenetic Location"
    ])

    isca_col = first_existing_col(df, ["ISCA ID", "ISCA", "Region ID", "id"])
    disease_col = first_existing_col(df, [
        "Disease Name", "disease_name", "Phenotype", "Syndrome", "Disorder"
    ])

    hi_score_col = first_existing_col(df, [
        "Haploinsufficiency Score", "HI Score", "haploinsufficiency_score"
    ])
    ts_score_col = first_existing_col(df, [
        "Triplosensitivity Score", "TS Score", "triplosensitivity_score"
    ])

    hi_desc_col = first_existing_col(df, [
        "Haploinsufficiency Description", "HI Description"
    ])
    ts_desc_col = first_existing_col(df, [
        "Triplosensitivity Description", "TS Description"
    ])

    pmid_col = first_existing_col(df, ["PMID", "PMIDs", "PubMed"])
    comment_col = first_existing_col(df, ["Comments", "Comment", "Description", "Evidence"])

    records = []

    for _, row in df.iterrows():
        chrom = start = end = None

        if location_col:
            chrom, start, end = parse_interval(row.get(location_col, ""))

        if chrom is None and chrom_col and start_col and end_col:
            try:
                chrom = clean_chr(row[chrom_col])
                start = int(str(row[start_col]).replace(",", ""))
                end = int(str(row[end_col]).replace(",", ""))
            except Exception:  # noqa: BLE001 - a row whose coordinates cannot be read is skipped
                chrom = start = end = None

        if chrom is None or start is None or end is None:
            continue

        region_name = ""
        if disease_col and pd.notna(row.get(disease_col)):
            region_name = str(row.get(disease_col))
        elif name_col and pd.notna(row.get(name_col)):
            region_name = str(row.get(name_col))
        else:
            region_name = f"{chrom}:{start}-{end}"

        # Only mine OMIM IDs from descriptive text columns. Scanning the whole
        # row matched 6-digit genomic coordinates as bogus MIM numbers.
        omim_text = " ".join(
            str(row.get(c, ""))
            for c in (disease_col, comment_col, hi_desc_col, ts_desc_col, name_col)
            if c and pd.notna(row.get(c))
        )
        omim_ids = extract_omim_ids(omim_text)

        source_id = str(row.get(isca_col, "")).strip() if isca_col else ""

        records.append({
            "cnv_id": make_id("CLINGEN", chrom, start, end, region_name),
            "syndrome_name": region_name,
            "chromosome": chrom,
            "start": int(start),
            "end": int(end),
            "size_bp": int(end) - int(start) + 1,
            "assembly": assembly,
            "cytoband": str(row.get(cyto_col, "")).strip() if cyto_col else "",
            "source": "ClinGen",
            "source_id": source_id,
            "hi_score": str(row.get(hi_score_col, "")).strip() if hi_score_col else "",
            "ts_score": str(row.get(ts_score_col, "")).strip() if ts_score_col else "",
            "hi_description": str(row.get(hi_desc_col, "")).strip() if hi_desc_col else "",
            "ts_description": str(row.get(ts_desc_col, "")).strip() if ts_desc_col else "",
            "omim_id": omim_ids,
            "omim_title": "",
            "decipher_id": "",
            "decipher_url": "",
            "orpha_id": "",
            "orpha_name": "",
            "genes": "",
            "clinical_description": str(row.get(comment_col, "")).strip() if comment_col else "",
            "phenotypes": "",
            "references": str(row.get(pmid_col, "")).strip() if pmid_col else "",
            "clingen_url": make_clingen_url(source_id),
            "source_url": source_url,
            # Empty, not 0, until add_clinvar_overlap_support has counted: a knowledgebase
            # built without ClinVar must not read as a region without ClinVar support (#624).
            "clinvar_pathogenic_loss_count": "",
            "clinvar_pathogenic_gain_count": "",
            "clinvar_pathogenic_accessions": "",
        })

    return pd.DataFrame(records)


def make_clingen_url(source_id: str) -> str:
    if not source_id:
        return ""
    source_id = str(source_id).strip()
    return f"https://search.clinicalgenome.org/kb/gene-dosage/region/{source_id}"


def load_ucsc_cytobands(assembly: str) -> pd.DataFrame:
    content = gzip.decompress(safe_get(UCSC_CYTO[assembly]))
    cyto = pd.read_csv(
        io.BytesIO(content),
        sep="\t",
        header=None,
        names=["chromosome", "start", "end", "cytoband", "stain"],
        dtype={"chromosome": str, "start": int, "end": int, "cytoband": str, "stain": str},
    )
    cyto["chromosome"] = cyto["chromosome"].map(clean_chr)
    return cyto


def build_cytoband_trees(cyto: pd.DataFrame) -> Dict[str, IntervalTree]:
    trees: Dict[str, IntervalTree] = {}
    for chrom, sub in cyto.groupby("chromosome"):
        tree = IntervalTree()
        for _, r in sub.iterrows():
            tree.add(Interval(int(r["start"]), int(r["end"]), str(r["cytoband"])))
        trees[chrom] = tree
    return trees


def annotate_cytobands(df: pd.DataFrame, cyto: pd.DataFrame) -> pd.DataFrame:
    trees = build_cytoband_trees(cyto)
    out = df.copy()

    cytos = []
    for _, r in out.iterrows():
        existing = str(r.get("cytoband", "") or "").strip()
        if existing:
            cytos.append(existing)
            continue

        chrom = clean_chr(r["chromosome"])
        tree = trees.get(chrom)
        if not tree:
            cytos.append("")
            continue

        hits = sorted(tree.overlap(int(r["start"]), int(r["end"]) + 1), key=lambda h: h.begin)
        if not hits:
            cytos.append("")
            continue
        first = str(hits[0].data)
        last = str(hits[-1].data)
        # Clean cytoband range, e.g. "5p15.33-p15.1" instead of every band joined.
        cytos.append(f"{chrom}{first}" if first == last else f"{chrom}{first}-{last}")

    out["cytoband"] = cytos
    return out


# ClinVar's Type column states the side of a copy-number record directly (#566).
_CLINVAR_LOSS_TYPES = frozenset({"copy number loss", "deletion"})
_CLINVAR_GAIN_TYPES = frozenset({"copy number gain", "duplication", "tandem duplication"})

# The copy number of an ISCN-style array record, written after its coordinates:
# "GRCh38/hg38 7q11.23(chr7:73330452-74799773)x1".
_ISCN_COPY_NUMBER = re.compile(r"\)\s*x(\d+)\b", re.IGNORECASE)


def clinvar_cnv_side(type_value: object, name: object, chromosome: object) -> str:
    """The side of a ClinVar copy-number record: "loss", "gain" or "unknown" (#566).

    Read, in order, from ClinVar's ``Type`` column; from the copy number an array record
    carries in its name (``x1``, ``x3``), and only then from words in the name. The side
    used to come from the name's words only, so an array record, named in ISCN notation
    without them, counted toward neither side.

    On an autosome two copies are normal, so ``x0``/``x1`` is a loss and ``x3`` or more a
    gain. On chrX and chrY the normal count depends on the carrier's sex, which ClinVar's
    summary does not give: ``x1`` is a loss in a female and normal in a male, and ``x2``
    normal in a female and a gain in a male. There only ``x0`` (a loss) and ``x3`` or more
    (a gain) are read, and ``x1``/``x2`` fall through to the name's words.
    """
    typ = str(type_value or "").strip().lower()
    if typ in _CLINVAR_LOSS_TYPES:
        return "loss"
    if typ in _CLINVAR_GAIN_TYPES:
        return "gain"

    text = "" if name is None or pd.isna(name) else str(name)
    copy_numbers = _ISCN_COPY_NUMBER.findall(text)
    if copy_numbers:
        copies = int(copy_numbers[-1])
        sex_chromosome = clean_chr(str(chromosome or "")) in {"X", "Y"}
        if copies == 0 or (copies == 1 and not sex_chromosome):
            return "loss"
        if copies >= 3:
            return "gain"

    lower_name = text.lower()
    if "dup" in lower_name or "duplication" in lower_name or "gain" in lower_name:
        return "gain"
    if "del" in lower_name or "deletion" in lower_name or "loss" in lower_name:
        return "loss"
    return "unknown"


def load_clinvar_cnv_support(assembly: str) -> pd.DataFrame:
    """
    Loads ClinVar variant_summary and keeps likely pathogenic copy-number / structural CNVs.
    This is used only as supporting evidence, not as the backbone list.
    """
    log("Downloading ClinVar variant_summary.txt.gz; this can take a while.")
    # The archive is ~250 MB compressed and several GB uncompressed. Stream it
    # to disk and read in bounded chunks (only the columns we need), keeping just
    # the filtered CNV subset, so the build does not get OOM-killed (exit -9).
    keep_cols = {
        "Assembly", "Chromosome", "Start", "Stop", "ClinicalSignificance",
        "Type", "Name", "VariationID", "RCVaccession",
    }
    tmp = tempfile.NamedTemporaryFile(prefix="clinvar-", suffix=".txt.gz", delete=False)
    frames = []
    try:
        with requests.get(CLINVAR_VARIANT_SUMMARY, stream=True, timeout=600) as r:
            r.raise_for_status()
            for piece in r.iter_content(chunk_size=1 << 20):
                if piece:
                    tmp.write(piece)
        tmp.close()
        reader = pd.read_csv(
            tmp.name,
            sep="\t",
            dtype=str,
            compression="gzip",
            chunksize=100_000,
            low_memory=True,
            usecols=lambda c: c in keep_cols,
        )
        for chunk in reader:
            if "Assembly" not in chunk.columns:
                log("ClinVar missing expected columns; skipping ClinVar support.")
                return pd.DataFrame()
            chunk = chunk[chunk["Assembly"].fillna("") == assembly]
            if chunk.empty:
                continue
            sig = chunk["ClinicalSignificance"].fillna("").str.lower()
            chunk = chunk[sig.str.contains("pathogenic") & ~sig.str.contains("conflicting")]
            if chunk.empty:
                continue
            typ = (
                chunk["Type"].fillna("").str.lower()
                if "Type" in chunk.columns
                else chunk.get("Name", "").fillna("").str.lower()
            )
            chunk = chunk[
                typ.str.contains("copy number")
                | typ.str.contains("deletion")
                | typ.str.contains("duplication")
                | typ.str.contains("cnv")
            ]
            if not chunk.empty:
                frames.append(chunk)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return clinvar_support_rows(df)


def clinvar_support_rows(df: pd.DataFrame) -> pd.DataFrame:
    """The filtered ClinVar CNV records as support rows: locus, side, accession and name."""
    if df.empty:
        return pd.DataFrame()

    type_col = first_existing_col(df, ["Type", "VariationType", "variant_type"])
    name_col = first_existing_col(df, ["Name"])
    acc_col = first_existing_col(df, ["VariationID", "RCVaccession", "Accession"])

    rows = []
    for _, r in df.iterrows():
        try:
            chrom = clean_chr(r["Chromosome"])
            start = int(str(r["Start"]).replace(",", ""))
            end = int(str(r["Stop"]).replace(",", ""))
        except Exception:  # noqa: BLE001 - a row whose coordinates cannot be read is skipped
            continue

        if start <= 0 or end <= 0:
            continue

        name = str(r.get(name_col, "")) if name_col else ""
        cnv_type = clinvar_cnv_side(r.get(type_col) if type_col else None, name, chrom)
        accession = str(r.get(acc_col, "")) if acc_col else ""

        rows.append({
            "chromosome": chrom,
            "start": start,
            "end": end,
            "cnv_type": cnv_type,
            "accession": accession,
            "name": name,
        })

    return pd.DataFrame(rows)


def add_clinvar_overlap_support(kb: pd.DataFrame, clinvar: pd.DataFrame) -> pd.DataFrame:
    if clinvar.empty:
        return kb

    trees: Dict[str, IntervalTree] = {}
    for chrom, sub in clinvar.groupby("chromosome"):
        tree = IntervalTree()
        for _, r in sub.iterrows():
            tree.add(Interval(
                int(r["start"]),
                int(r["end"]) + 1,
                {
                    "type": r["cnv_type"],
                    "accession": r["accession"],
                    "name": r["name"],
                }
            ))
        trees[chrom] = tree

    out = kb.copy()
    loss_counts = []
    gain_counts = []
    accs = []

    for _, r in tqdm(out.iterrows(), total=len(out), desc="ClinVar overlaps"):
        chrom = clean_chr(r["chromosome"])
        tree = trees.get(chrom)
        if not tree:
            loss_counts.append(0)
            gain_counts.append(0)
            accs.append("")
            continue

        hits = tree.overlap(int(r["start"]), int(r["end"]) + 1)

        loss = 0
        gain = 0
        accessions = []

        for h in hits:
            # Require meaningful reciprocal overlap for supporting evidence.
            q_start, q_end = int(r["start"]), int(r["end"])
            h_start, h_end = int(h.begin), int(h.end)
            overlap = max(0, min(q_end, h_end) - max(q_start, h_start))
            q_len = max(1, q_end - q_start)
            h_len = max(1, h_end - h_start)
            reciprocal = min(overlap / q_len, overlap / h_len)

            if reciprocal < 0.3:
                continue

            if h.data["type"] == "loss":
                loss += 1
            elif h.data["type"] == "gain":
                gain += 1

            if h.data["accession"]:
                accessions.append(str(h.data["accession"]))

        loss_counts.append(loss)
        gain_counts.append(gain)
        accs.append(";".join(sorted(set(accessions))[:50]))

    out["clinvar_pathogenic_loss_count"] = loss_counts
    out["clinvar_pathogenic_gain_count"] = gain_counts
    out["clinvar_pathogenic_accessions"] = accs
    return out


def enrich_omim(kb: pd.DataFrame, api_key: Optional[str], sleep: float = 0.2) -> pd.DataFrame:
    """
    Optional OMIM enrichment.

    Requires OMIM_API_KEY. This script only fills titles for already detected MIM IDs.
    It does not scrape OMIM.
    """
    out = kb.copy()
    if not api_key:
        log("No OMIM_API_KEY set; keeping OMIM IDs extracted from source text only.")
        return out

    cache: Dict[str, str] = {}

    def fetch_title(mim_id: str) -> str:
        if mim_id in cache:
            return cache[mim_id]

        params = {
            "mimNumber": mim_id,
            "include": "titles",
            "format": "json",
            "apiKey": api_key,
        }
        try:
            r = requests.get(f"{OMIM_API}/entry", params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            entry = data["omim"]["entryList"][0]["entry"]
            titles = entry.get("titles", {})
            title = (
                titles.get("preferredTitle")
                or titles.get("alternativeTitles")
                or titles.get("includedTitles")
                or ""
            )
        except Exception as e:  # noqa: BLE001 - logged; the title stays empty
            log(f"OMIM lookup failed for {mim_id}: {e}")
            title = ""

        cache[mim_id] = title
        time.sleep(sleep)
        return title

    titles = []
    for ids in tqdm(out["omim_id"].fillna(""), desc="OMIM"):
        mim_ids = [x for x in str(ids).split(";") if x]
        title_list = [fetch_title(x) for x in mim_ids]
        titles.append("; ".join([t for t in title_list if t]))

    out["omim_title"] = titles
    return out


def parse_orphanet_xml(path: str) -> pd.DataFrame:
    """
    Very tolerant parser for Orphanet product XML.
    Different Orphadata products have slightly different XML shapes.
    """
    tree = ET.parse(path)
    root = tree.getroot()

    rows = []

    for disorder in root.iter():
        if not disorder.tag.lower().endswith("disorder"):
            continue

        orpha = ""
        name = ""

        for child in disorder:
            tag = child.tag.lower()
            if tag.endswith("orphacode") and child.text:
                orpha = child.text.strip()
            if tag.endswith("name") and child.text:
                name = child.text.strip()

        if orpha and name:
            rows.append({"orpha_id": orpha, "orpha_name": name})

    return pd.DataFrame(rows).drop_duplicates()


def add_orphanet_matches(kb: pd.DataFrame, orphanet_xml: Optional[str] = None) -> pd.DataFrame:
    out = kb.copy()
    xml_path = orphanet_xml
    tmp_path = None

    if xml_path:
        if not Path(xml_path).exists():
            log(f"Orphanet XML not found: {xml_path}")
            return out
    else:
        log("Downloading Orphanet nomenclature (en_product1.xml).")
        try:
            content = safe_get(ORPHANET_NOMENCLATURE_URL, timeout=180)
        except Exception as e:  # noqa: BLE001 - logged; the build continues without Orphanet
            log(f"Orphanet download failed: {e}")
            return out
        tmp = tempfile.NamedTemporaryFile(prefix="orphanet-", suffix=".xml", delete=False)
        tmp.write(content)
        tmp.close()
        xml_path = tmp.name
        tmp_path = tmp.name

    try:
        orpha = parse_orphanet_xml(str(xml_path))
    except Exception as e:  # noqa: BLE001 - logged; the build continues without Orphanet
        log(f"Orphanet parse failed: {e}")
        return out
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    if orpha.empty:
        return out

    lookup = {}
    for _, r in orpha.iterrows():
        key = normalize_name(r["orpha_name"])
        lookup[key] = (r["orpha_id"], r["orpha_name"])

    ids = []
    names = []

    for name in out["syndrome_name"].fillna(""):
        key = normalize_name(name)
        hit = lookup.get(key)

        if not hit and key:
            # Match only when a sufficiently specific Orphanet disease name
            # (multi-token, >= 8 chars) appears verbatim inside the region name.
            # Avoids over-generic single-token names matching everything.
            best = None
            for k, v in lookup.items():
                if len(k) >= 8 and " " in k and k in key:
                    if best is None or len(k) > len(best[0]):
                        best = (k, v)
            hit = best[1] if best else None

        if hit:
            ids.append(hit[0])
            names.append(hit[1])
        else:
            ids.append("")
            names.append("")

    out["orpha_id"] = ids
    out["orpha_name"] = names
    return out


def normalize_name(s: str) -> str:
    s = str(s).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\b(syndrome|disease|disorder|type)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def add_decipher_mapping(kb: pd.DataFrame, decipher_map: Optional[str]) -> pd.DataFrame:
    """
    Optional manual DECIPHER mapping file.

    decipher_map.tsv columns:
        syndrome_name    decipher_id    decipher_url

    A manual mapping is safer than scraping DECIPHER.
    """
    out = kb.copy()

    out["decipher_url"] = out["syndrome_name"].map(
        lambda x: "https://www.deciphergenomics.org/search?q=" + requests.utils.quote(str(x))
    )

    if not decipher_map:
        return out

    p = Path(decipher_map)
    if not p.exists():
        log(f"DECIPHER map not found: {decipher_map}")
        return out

    dm = pd.read_csv(p, sep="\t", dtype=str).fillna("")
    required = {"syndrome_name", "decipher_id", "decipher_url"}
    if not required.issubset(set(dm.columns)):
        log("DECIPHER map must contain syndrome_name, decipher_id, decipher_url")
        return out

    dm["key"] = dm["syndrome_name"].map(normalize_name)
    map_by_key = {
        r["key"]: (r["decipher_id"], r["decipher_url"])
        for _, r in dm.iterrows()
    }

    ids = []
    urls = []

    for _, r in out.iterrows():
        key = normalize_name(r["syndrome_name"])
        hit = map_by_key.get(key)

        if hit:
            ids.append(hit[0])
            urls.append(hit[1])
        else:
            ids.append(r.get("decipher_id", ""))
            urls.append(r.get("decipher_url", ""))

    out["decipher_id"] = ids
    out["decipher_url"] = urls
    return out


def collapse_duplicate_regions(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    key_cols = ["chromosome", "start", "end", "syndrome_name"]

    def join_unique(series):
        vals = []
        for x in series:
            if pd.isna(x):
                continue
            for part in str(x).split(";"):
                part = part.strip()
                if part:
                    vals.append(part)
        return ";".join(sorted(set(vals)))

    agg = {}
    for c in df.columns:
        if c in key_cols:
            continue
        if c in {"size_bp"}:
            agg[c] = "first"
        elif c.startswith("clinvar_pathogenic") and c.endswith("_count"):
            agg[c] = "max"
        else:
            agg[c] = join_unique

    out = df.groupby(key_cols, as_index=False).agg(agg)
    out["cnv_id"] = [
        make_id("CNV", r["chromosome"], int(r["start"]), int(r["end"]), r["syndrome_name"])
        for _, r in out.iterrows()
    ]
    return out


def load_clingen_recurrent_regions(assembly: str) -> pd.DataFrame:
    """ClinGen recurrent-CNV regions with authoritative inline syndrome names.

    These carry the recognizable names (TAR syndrome, Williams-Beuren, DiGeorge,
    etc.) with exact coordinates, so no fuzzy name matching is required.
    """
    url = CLINGEN_RECURRENT_CNV.get(assembly)
    if not url:
        return pd.DataFrame()
    try:
        text = safe_get(url).decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001 - logged; the build continues without this source
        log(f"  skipped recurrent CNV bed: {e}")
        return pd.DataFrame()

    records = []
    for line in text.splitlines():
        if not line or line.startswith("track") or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        name = cols[3].strip()
        # Keep curated syndrome regions; drop plain breakpoint markers (BP1, ...).
        if "region" not in name.lower() and "syndrome" not in name.lower():
            continue
        try:
            chrom = clean_chr(cols[0])
            start = int(cols[1])
            end = int(cols[2])
        except ValueError:
            continue
        records.append({
            "cnv_id": make_id("CLINGEN-RECURRENT", chrom, start, end, name),
            "syndrome_name": name,
            "chromosome": chrom,
            "start": start,
            "end": end,
            "size_bp": end - start + 1,
            "assembly": assembly,
            "cytoband": "",
            "source": "ClinGen recurrent CNV",
            "source_id": "",
            "hi_score": "",
            "ts_score": "",
            "hi_description": "",
            "ts_description": "",
            "omim_id": "",
            "omim_title": "",
            "decipher_id": "",
            "decipher_url": "",
            "orpha_id": "",
            "orpha_name": "",
            "genes": "",
            "clinical_description": "",
            "phenotypes": "",
            "references": "",
            "clingen_url": "",
            "source_url": url,
            "clinvar_pathogenic_loss_count": "",
            "clinvar_pathogenic_gain_count": "",
            "clinvar_pathogenic_accessions": "",
        })
    if records:
        log(f"  retained {len(records)} recurrent CNV regions")
    return pd.DataFrame(records)


def clean_syndrome_name(name: str) -> str:
    """Tidy a region/syndrome name for display.

    Drops trailing gene lists like "(includes RBM8A)" (not part of the name) and
    collapses whitespace, while keeping the distinguishing region descriptors.
    """
    s = str(name or "").strip()
    s = re.sub(r"\s*\(includes[^)]*\)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def build_kb(
    *,
    assembly: str,
    orphanet_xml: Optional[str] = None,
    decipher_map: Optional[str] = None,
    skip_clinvar: bool = False,
) -> pd.DataFrame:
    """Build the clinical CNV knowledgebase DataFrame.

    Importable so the backend can invoke the build programmatically (see the
    admin "rebuild clinical CNV knowledgebase" action) in addition to the CLI.
    """
    log("Discovering ClinGen download files.")
    urls = discover_clingen_tsv_urls(assembly)

    if not urls:
        raise RuntimeError(
            "Could not discover ClinGen TSV/BED files. "
            "Check ClinGen downloads page or pass fixed URLs by editing the script."
        )

    log(f"Found {len(urls)} candidate ClinGen files.")
    tables = []

    for url in urls:
        try:
            log(f"Loading {url}")
            raw = load_table_from_url(url)
            norm = normalize_clingen_table(raw, url, assembly)
            if not norm.empty:
                tables.append(norm)
                log(f"  retained {len(norm)} interval records")
        except Exception as e:  # noqa: BLE001 - logged; the build continues without this source
            log(f"  skipped {url}: {e}")

    log("Loading ClinGen recurrent CNV regions (named syndromes).")
    recurrent = load_clingen_recurrent_regions(assembly)
    if not recurrent.empty:
        tables.append(recurrent)

    if not tables:
        raise RuntimeError("No usable ClinGen interval records found.")

    kb = pd.concat(tables, ignore_index=True)
    kb["syndrome_name"] = kb["syndrome_name"].map(clean_syndrome_name)

    log("Collapsing duplicate ClinGen records.")
    kb = collapse_duplicate_regions(kb)

    log("Adding UCSC cytobands.")
    cyto = load_ucsc_cytobands(assembly)
    kb = annotate_cytobands(kb, cyto)

    if not skip_clinvar:
        clinvar = load_clinvar_cnv_support(assembly)
        kb = add_clinvar_overlap_support(kb, clinvar)

    kb = enrich_omim(kb, os.environ.get("OMIM_API_KEY"))
    kb = add_orphanet_matches(kb, orphanet_xml)
    kb = add_decipher_mapping(kb, decipher_map)

    kb["clinical_description"] = kb.apply(make_description, axis=1)

    preferred_cols = [
        "cnv_id",
        "syndrome_name",
        "chromosome",
        "start",
        "end",
        "size_bp",
        "cytoband",
        "assembly",
        "source",
        "source_id",
        "hi_score",
        "hi_description",
        "ts_score",
        "ts_description",
        "omim_id",
        "omim_title",
        "decipher_id",
        "decipher_url",
        "orpha_id",
        "orpha_name",
        "genes",
        "clinical_description",
        "phenotypes",
        "clingen_url",
        "references",
        "clinvar_pathogenic_loss_count",
        "clinvar_pathogenic_gain_count",
        "clinvar_pathogenic_accessions",
        "source_url",
    ]

    for c in preferred_cols:
        if c not in kb.columns:
            kb[c] = ""

    kb = kb[preferred_cols].copy()
    kb["chrom_sort"] = kb["chromosome"].map(canonical_chr_sort_key)
    kb = kb.sort_values(["chrom_sort", "start", "end"]).drop(columns=["chrom_sort"])

    return kb


def make_description(row) -> str:
    parts = []

    if row.get("clinical_description"):
        parts.append(str(row["clinical_description"]))

    if row.get("hi_description"):
        parts.append(f"HI: {row['hi_description']}")

    if row.get("ts_description"):
        parts.append(f"TS: {row['ts_description']}")

    if row.get("omim_title"):
        parts.append(f"OMIM: {row['omim_title']}")

    if row.get("orpha_name"):
        parts.append(f"Orphanet: {row['orpha_name']}")

    return " | ".join([p for p in parts if p and p != "nan"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--assembly",
        choices=["GRCh38", "GRCh37"],
        default="GRCh38",
    )
    parser.add_argument(
        "--out",
        default="clinical_cnv_knowledgebase.tsv",
    )
    parser.add_argument(
        "--orphanet-xml",
        default=None,
        help="Optional local Orphanet XML, e.g. en_product6.xml",
    )
    parser.add_argument(
        "--decipher-map",
        default=None,
        help="Optional TSV: syndrome_name, decipher_id, decipher_url",
    )
    parser.add_argument(
        "--skip-clinvar",
        action="store_true",
        help="Skip ClinVar support counts.",
    )

    args = parser.parse_args()

    kb = build_kb(
        assembly=args.assembly,
        orphanet_xml=args.orphanet_xml,
        decipher_map=args.decipher_map,
        skip_clinvar=args.skip_clinvar,
    )
    kb.to_csv(args.out, sep="\t", index=False)

    log(f"Wrote {len(kb):,} CNV records to {args.out}")


if __name__ == "__main__":
    main()