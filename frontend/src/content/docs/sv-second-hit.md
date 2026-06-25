# SNV + SV compound heterozygosity (cross-type second hit) — reference

Recessive disease is frequently caused by **two different kinds of variant in the same gene**:
a small variant (SNV/indel) on one allele and a **structural variant** (deletion, duplication,
insertion, …) on the other. The most important version is a **heterozygous SNV plus an
overlapping deletion** — the deletion removes the second copy, so a variant that *looks*
heterozygous is functionally **biallelic** and can be pathogenic.

Because CoGA filters small variants and structural variants in separate workspaces, these
cross-type pairs are easy to miss. CoGA surfaces them automatically on the small-variant page.

This is the in-depth reference. For the workflow-level overview see the
[in-app user guide](/docs) section *Small-variant prioritisation*.

---

## What you'll see

On the small-variant results, any variant whose gene is **also hit by a structural variant**
carries an **SV badge** next to the gene name:

- **SV type and count** — e.g. `SV: DEL` or `SV: DEL, INS ×2`.
- **Zygosity** of the overlapping SV in affected individuals (`het` / `hom`).
- A **phase chip** — `trans` or `cis` — when the phase can be determined (see below).
- **Red, emphasised styling** when it is a **deletion in trans with a heterozygous SNV**, i.e.
  effectively biallelic — the highest-yield case.

Hover (or focus) the badge for a plain-language explanation of what was found and how the
phase was decided.

## Finding the pattern: the "second hit" filter

In the small-variant filters, the **Structural second hit** section has an **"Also hit by an
SV"** toggle. Turn it on to **restrict the results to genes that also carry a structural
variant** — so instead of stumbling on the badge, you can pull the whole candidate list in one
pass. It composes with everything else (panel, frequency, consequence, …): for example,
*Mendeliome + rare + also hit by an SV* in one search.

## trans vs cis — is it really biallelic?

A second hit only completes a recessive genotype if the two variants are on **opposite
alleles** (*in trans*). CoGA reports a phase verdict with two levels of evidence:

- **By segregation** (always available). Using the family, a pair is called **trans** when the
  SNV is heterozygous in every affected individual, the SV is present in every affected
  individual, and **no unaffected individual carries both** — the same rule CoGA uses for
  SNV+SNV compound heterozygotes. If an unaffected relative carries both, it is called **cis**.
  Without informative relatives the phase is **unknown**.
- **By read phasing** (when the SVs are phased). If the SNV and the SV share a **phase set**
  (`PS`) in an affected sample, CoGA compares the haplotype each one sits on and reads
  **trans/cis directly**. This is shown as a distinct, accent-coloured phase chip and noted in
  the tooltip. Read-based phasing requires the structural-variant VCF to be phased upstream
  (e.g. long-read calling with Sniffles2 + HiPhase / LongPhase); CoGA captures the phase set on
  import. Segregation is always applied as the fallback, so short-read and non-trio cases still
  get a verdict.

A **deletion in trans with a heterozygous SNV** is flagged as **effectively biallelic** — the
deletion removes the allele the SNV is *not* on, leaving no intact copy of the gene.

## How matching works

- A small variant is matched against **every gene it overlaps**, not just its primary
  annotation — so the badge and the filter always agree, including when the SV sits on a
  secondary overlapping gene.
- Matching is at the **gene** level (any overlap), so a whole-gene deletion — which has no
  per-base consequence but is the most important case — is included.
- The per-family map of *which genes the structural variants hit* is **precomputed** the first
  time you open the family's small variants (one quick scan, then cached) and **rebuilt
  automatically when the family's structural variants are re-imported**.

## Caveats

- The SV badge tells you a structural variant overlaps the gene; **review the SV itself**
  (type, size, quality, breakpoints) on the structural-variant page before acting.
- A `cis` or `unknown` verdict does not rule a pair out on its own — it reflects the available
  segregation/phasing evidence, which may be limited in small or singleton families.
- Read-based `trans`/`cis` only appears when the structural variants were phased upstream;
  otherwise the verdict is segregation-based.
