"""A family made over the API with a VEP-annotated VCF, as the end-to-end filter tests make it.

The modules that compare what each view of the family page (and the Variant Explorer) keeps
for a filter or a gene panel create a family of their own through the PED upload, under a
new id each run so that the golden trio is never touched, and load a synthetic VCF as
DeepVariant writes it and VEP annotates it through the family small-variant upload, so that
its CSQ is parsed and stored as an import stores it. Each module keeps its own variants,
CSQ fields and expectations; this holds what they build alike.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def deepvariant_vep_header(csq_format: str, *info_lines: str) -> str:
    """The meta-information of a DeepVariant VCF that VEP annotated, its CSQ fields in
    ``csq_format``; ``info_lines`` are INFO lines of other fields, before the CSQ one."""
    return (
        "##fileformat=VCFv4.2\n"
        "##DeepVariant_version=1.10.0\n"
        "##reference=GRCh38\n"
        + "".join(f"{line}\n" for line in info_lines)
        + f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from Ensembl VEP. Format: {csq_format}">\n'
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
        '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
    )


def single_sample_vcf(header: str, sample: str, info_by_variant: Mapping[str, str], call: str) -> bytes:
    """A VCF of one sample: a record for each variant id (``chrom-pos-ref-alt``) with its
    INFO, the sample's ``call`` in GT:GQ:DP:AD."""
    lines = [header + f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}"]
    for variant_id, info in info_by_variant.items():
        chrom, pos, ref, alt = variant_id.split("-")
        lines.append(f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\t{info}\tGT:GQ:DP:AD\t{call}")
    return ("\n".join(lines) + "\n").encode()


async def upload_family(ac: Any, project_id: str, family_id: str, ped: str, vcf: bytes) -> dict[str, tuple[int, str]]:
    """Create the family by the PED upload, then load its VCF by the family small-variant
    upload (without overwrite); the status and text of each answer."""
    ped_answer = await ac.post(
        "/api/ped/upload",
        params={"project_id": project_id},
        files={"file": (f"{family_id}.ped", ped.encode(), "text/plain")},
    )
    upload = await ac.post(
        f"/api/families/{family_id}/small-variants/upload",
        params={"overwrite": "false"},
        files={"file": (f"{family_id}.vcf", vcf, "text/plain")},
    )
    return {"ped": (ped_answer.status_code, ped_answer.text), "upload": (upload.status_code, upload.text)}


def family_page_variant_ids(page: dict[str, Any]) -> set[str]:
    """The variants a family page holds: its single variants and those of its pairs."""
    ids = {variant["_id"] for variant in page["variants"]}
    for group in page.get("variant_groups") or []:
        ids.update(variant["_id"] for variant in group["variants"])
    return ids
