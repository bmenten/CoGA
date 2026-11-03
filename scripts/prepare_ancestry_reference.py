#!/usr/bin/env python3
"""Rebuild CoGA's bundled PCA model from pinned public peddy/1000 Genomes data.

Run with the backend Python environment. No network or family-data access:
download the four documented, checksummed public files first and pass --source.
"""
from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

REVISION = "1d340cd3e87623c8c58b4a82b18f33ba532101aa"
INPUTS = {
    "GRCH38.sites": "f6647779e9195ed3686fe34f705dcb6af2f658cafd1839fd93e1fd71c5e86e74",
    "GRCH38.sites.bin.gz": "61cf27b489292bc8c5670199845f690667b64bfa58035aa3a67d4474c7c1e0ef",
    "pca.py": "a74e84ca783ca680bbe10a85d76d7ad36ec88e8d46e5ba01ff0dfc11226e283f",
    "LICENSE": "b60a9efdae9f210701e357c747fdc3c492fa637c49ed462a73e26c40ac68e34d",
}


def prepare(source: Path, output: Path) -> dict:
    for name, digest in INPUTS.items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Pinned public input checksum failed: {name}")
    loci = (source / "GRCH38.sites").read_text().splitlines()
    # peddy's gts012=True encoding: 0/1/2 ALT copies; 3 means missing. SNP-major file.
    raw = np.frombuffer(gzip.decompress((source / "GRCH38.sites.bin.gz").read_bytes()), dtype=np.uint8)
    genotypes = raw.reshape((len(loci), 2504)).T.astype(np.float64)
    if (genotypes > 3).any():
        raise ValueError("Unexpected public genotype encoding")
    tree = ast.parse((source / "pca.py").read_text())
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "_str" for t in node.targets))
    labels = ast.literal_eval(assignment.value).split("|")
    if len(labels) != genotypes.shape[0]:
        raise ValueError("Public reference labels and genotypes disagree")
    codes = np.array(["AFR", "AMR", "EAS", "EUR", "SAS"])[np.array(labels, dtype=int)]
    missing = genotypes == 3
    counts = (~missing).sum(axis=0)
    frequencies = np.divide(np.where(missing, 0, genotypes).sum(axis=0), 2 * counts,
                            out=np.zeros(len(loci)), where=counts > 0)
    means = 2 * frequencies
    genotypes = np.where(missing, means, genotypes)
    centered = genotypes - means
    norms = np.sqrt((centered ** 2).sum(axis=0))
    # Greedy deterministic LD pruning: r² <= .2 in a 500-kb window. Exclude the
    # MHC and common inversion regions to keep PCs from representing one LD block.
    excluded = {"6": (25000000, 35000000), "8": (7000000, 13000000), "17": (40000000, 45000000)}
    parsed = [locus.split(":") for locus in loci]
    order = sorted(range(len(loci)), key=lambda i: (int(parsed[i][0]), int(parsed[i][1])))
    kept: list[int] = []
    window: list[int] = []
    for i in order:
        chrom, position, ref, alt = parsed[i]
        pos = int(position)
        if (not 1 <= int(chrom) <= 22 or not 0.05 <= frequencies[i] <= 0.95
                or missing[:, i].mean() > 0.02 or norms[i] == 0
                or (chrom in excluded and excluded[chrom][0] <= pos <= excluded[chrom][1])):
            continue
        window = [j for j in window if parsed[j][0] == chrom and pos - int(parsed[j][1]) <= 500000]
        if window:
            correlations = (centered[:, window].T @ centered[:, i]) / (norms[window] * norms[i])
            if (correlations ** 2 > 0.2).any():
                continue
        kept.append(i)
        window.append(i)
    scales = np.sqrt(2 * frequencies[kept] * (1 - frequencies[kept]))
    matrix = centered[:, kept] / scales
    # Seeded randomized SVD, QR stabilized at each power iteration. Only public
    # unrelated reference samples determine the axes. No sklearn/pickle is needed.
    rng = np.random.default_rng(20261101)
    q, _ = np.linalg.qr(matrix @ rng.normal(size=(len(kept), 20)), mode="reduced")
    for _ in range(5):
        z, _ = np.linalg.qr(matrix.T @ q, mode="reduced")
        q, _ = np.linalg.qr(matrix @ z, mode="reduced")
    _, singular, vt = np.linalg.svd(q.T @ matrix, full_matrices=False)
    loadings = vt[:4].T.copy()
    for component in range(4):
        if loadings[np.abs(loadings[:, component]).argmax(), component] < 0:
            loadings[:, component] *= -1
    coordinates = matrix @ loadings
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, loci=np.array(loci)[kept], means=means[kept], scales=scales,
                        loadings=loadings, coordinates=coordinates, populations=codes)
    metadata = {
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "name": "1000 Genomes phase 3 · GRCh38 · CoGA PCA v1",
        "genome_build": "GRCh38", "source_url": f"https://github.com/brentp/peddy/tree/{REVISION}/peddy",
        "source_revision": REVISION, "input_sha256": INPUTS,
        "method": "Reference-only variance-standardized PCA; MAF ≥ 5%; ≤ 2% reference missingness; r² ≤ 0.2 / 500 kb; long-range LD regions excluded; 4 PCs",
        "variant_count": len(kept), "sample_count": 2504,
        "explained_variance": (singular[:4] ** 2 / (matrix ** 2).sum()).tolist(),
        "populations": {"AFR": "African", "AMR": "Admixed American", "EAS": "East Asian", "EUR": "European", "SAS": "South Asian"},
        "numpy_version": np.__version__, "seed": 20261101,
        "reference_ids": "1KG-NNNN identifies a public matrix row, not an original participant identifier.",
        "license": "Public peddy-distributed reference data; MIT attribution retained in PEDDY-LICENSE.txt",
    }
    output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")
    (output.parent / "PEDDY-LICENSE.txt").write_bytes((source / "LICENSE").read_bytes().rstrip() + b"\n")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("backend/app/data/ancestry/1000g-grch38-v1.npz"))
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output), indent=2))
