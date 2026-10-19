# ACMG secondary-findings gene list — vendored reference (version-pinned)

`acmg-sf-genes.json` is the ACMG SF list: the genes the American College of Medical Genetics
and Genomics (ACMG) recommends for return as secondary findings from clinical exome and
genome sequencing. It is **vendored** (committed) so that every deployment shows the same,
reviewed list, offline too. The gene page reads it through the gene reference sync and
says, for each gene, whether it is on this list.

## Pinned version

| Field | Value |
| --- | --- |
| List | **ACMG SF v3.3** |
| Publication | Lee K, Abul-Husn NS, Amendola LM, et al.; ACMG Secondary Findings Working Group. ACMG SF v3.3 list for reporting of secondary findings in clinical exome and genome sequencing: A policy statement of the American College of Medical Genetics and Genomics (ACMG). *Genet Med.* 2025;27(8):101454 |
| Published | Online 2025-06-23 (issue August 2025) |
| DOI · PMID | [10.1016/j.gim.2025.101454](https://doi.org/10.1016/j.gim.2025.101454) · [40568962](https://pubmed.ncbi.nlm.nih.gov/40568962/) |
| Transcribed from | Table 1 of the version of record (<https://www.gimjournal.org/article/S1098-3600(25)00101-7/fulltext>), read 2026-10-14 |
| Genes | 84 (v3.2's 81, plus ABCD1, CYP27A1 and PLN; none removed) |
| SHA-256 of `acmg-sf-genes.json` | `5b10d64b5f10653171ebdbc8ee6e65e1c2f28ba0ed437619cd575c9bfc486a6e` |

On 2026-10-14 v3.3 was the latest list ClinGen's ACMG SF page names
(<https://search.clinicalgenome.org/kb/genes/acmgsf>). NCBI's ClinVar page on the ACMG
recommendations (<https://www.ncbi.nlm.nih.gov/clinvar/docs/acmg/>) still listed v3.2.

## What the file holds

The list's name and version, the publication, `gene_count`, and one entry per gene, in the
order of Table 1:

| Field | From Table 1 |
| --- | --- |
| `gene`, `hgnc_id` | the gene symbol; its HGNC ID (from ClinGen's ACMG SF gene list) |
| `phenotype`, `omim_disorders` | the *Disease* column and the gene's *OMIM Disorder* numbers |
| `category` | the table section the gene is in, without "Genes Related to": *Cancer Predisposition*, *Cardiovascular Disease Predisposition*, *Inborn Errors of Metabolism* or *Other Disease Phenotypes* |
| `inheritance` | AD, AR, XL or SD (semidominant) |
| `variants_to_report` | the *Variants to Report* column (P pathogenic, LP likely pathogenic) |
| `added_in` | the *ACMG SF List Version* column: the list version that added the gene |
| `notes` | the table footnotes that name the gene, with their letter |

A blank cell in Table 1 repeats the cell above it, as the table lays out its groups: TRDN's
*Variants to Report* is CASQ2's "P and LP (2 variants)", as its AR inheritance requires.
Footnotes b, c, d, f, g, h, i, j and k are given as the table states them. Footnote e (LMNA)
is condensed and says so; read the publication for it in full. The general
footnote a (how AD, AR and X-linked genes are reported; never a variant of uncertain
significance) applies to every gene and is not repeated per gene.

The transcription was checked against three other sources, downloaded on 2026-10-14: the
author manuscript of the same article in PMC (PMC12318660), ClinGen's ACMG SF gene list (the
84 genes and their HGNC IDs) and ClinGen's "ACMG SF v3.3 List" spreadsheet (linked from its
ACMG SF page). Every gene, version, OMIM number and inheritance agrees with the manuscript,
except that the manuscript prints CALM1–3's version as "32" where the version of record has
3.2; the disease names follow the version of record, which differs from the manuscript in
capitals and small wording. The gene set also matches the v3.2 policy statement's 81 genes
plus the three v3.3 added.

ClinGen's spreadsheet is laid out differently: one row per gene–disease pair, with Table 1's
footnotes folded into those rows (DSP, MYH7 and SCN5A also with dilated cardiomyopathy, TTN
"truncating variants only", and so on); this file keeps Table 1's one row per gene, the
footnotes as notes. Where the spreadsheet disagrees with Table 1 itself, this file follows the
publication: BMPR1A and SMAD4 were added in 2.0 (the spreadsheet: 1), LDLR is semidominant
(the spreadsheet: AD), TRDN reports "P and LP (2 variants)" (the spreadsheet: "All P and
LP"), HFE is in *Inborn Errors of Metabolism* (the spreadsheet: other; v3.2's table had it
under miscellaneous phenotypes), and PRKAG2 is in the cardiovascular category only (the
spreadsheet: also metabolic).

## How CoGA uses it

The gene reference sync (`gene_info_bulk_sources.py`, source `acmg_sf`) reads the file for
every gene, next to dbNSFP, HGNC, ClinGen, GenCC and ClinVar. A listed gene gets its entry in
`gene_info.extra.acmg_secondary_finding`; every gene gets the source status: `success` (on
the list) or `missing` (not on it), with the release `v3.3` and the file's SHA-256, which the
gene page and **Administration → Gene reference sync** show. Genes are matched by HGNC ID,
so a gene HGNC renamed after the list was published is still found; without the HGNC set the
list's symbols are used.

A file that is missing, unreadable or inconsistent (no version, a gene without its symbol,
HGNC ID, phenotype or category, a repeated gene or HGNC ID, or a gene count other than
`gene_count`) is refused whole: every gene then gets the status `error`, and the gene page
says the list could not be read. It never says "not on the list" then.

The list is shown on the gene page only. No filter, ranking, ACMG suggestion or report reads
it, and a sign-out does not record it.

## Updating (controlled change)

Replacing the list is a **TF-18 controlled change**:

1. Take the new list from its ACMG policy statement in *Genetics in Medicine* (Table 1),
   and check ClinGen's ACMG SF page for the version it names as current.
2. Update `acmg-sf-genes.json`: `version`, `source`, `gene_count` and the genes, one line per
   gene in the order of Table 1, with the HGNC ID of each new gene from ClinGen's list or
   HGNC. Check every symbol against the HGNC complete set.
3. Update the **Pinned version** table above, including the SHA-256
   (`shasum -a 256 acmg-sf-genes.json`), and the differences between sources, then run
   `backend/tests/test_acmg_sf_reference_list.py`, which fails until the file, this README
   and the loader agree.
4. Deploy the file where the backend reads it (`/data/ref-data/acmg-sf/`; a Cloud Run
   deployment uploads `data/ref-data/` to its reference-data bucket) and run a full gene
   reference sync. Cached genes keep the list they were synced with until then; the gene
   page names the version each gene was checked against.
5. Record it: before the first release candidate the pull request is the record; from the
   release candidate on, a `CHANGELOG.md` entry and a TF-18 §8 change record.
