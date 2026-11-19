A conventional NIPT screens a pregnancy for aneuploidies and copy-number changes from the genome-wide
copy number of the **maternal plasma**. The laboratory's tool, ccnv, measures it; CoGA draws it and gives
an **automatic screening result per target**. A high-risk result calls for confirmatory diagnostic
testing.

### Setting up a conventional NIPT family

A package import makes the family when its package says `analysis_type: conventional_nipt` (**cNIPT**
in *Deliveries*), with ccnv's bins, segments, calls and QC file for the plasma. A folder of ccnv output
added by hand is read the same way: CoGA proposes the plasma as the mother of the pregnancy. The family
page then shows a **cNIPT** tab.

### The cNIPT page

- **The result** lists chromosomes 21, 18 and 13, the sex chromosomes and the segmental CNVs first, each
  **High risk**, **Inconclusive**, **Low risk**, **No result** or **Not assessed**, with why. Another
  autosome is listed with them when it is not low risk; every autosome is under *Every other autosome*.
- **The maternal plasma** gives the fetal fraction the result uses (ccnv's effective one) and every other
  estimate, the fetal sex and the sex-chromosome call, the resolution (from which size segmental CNVs are
  assessed at this fetal fraction), and ccnv's warnings.
- **The findings** list every segment ccnv called and every segment in the grey zone, each with its size,
  log2 ratio, z-score, implied fetal fraction, origin and result. Click a region to open it in the
  chromosome view.
- **The bounds of the rule** show the values the result applied. A bound QA has not set yet is marked
  **provisional**: provisional values are not validated.

In the genome and chromosome views the plasma's track has an axis of its own. Each segment takes the
colour of ccnv's call (purple where ccnv calls it discordant with the fetal fraction), and dashed lines
mark where a fetal trisomy and monosomy lie at the fetal fraction.

### Signing out

Sign the family out from its **Report** page, which shows the result above the reported findings. The
signed version keeps the result as the cNIPT page showed it, with the bounds and the files it was read
from. A plasma without a result, or a result on provisional bounds, asks for an acknowledgement with a
reason; so does the plasma's sex check, which cannot run on ccnv's output.

[Conventional NIPT reference (the fetal fraction, the rule, the targets and the bounds)](/docs/reference/conventional-nipt "further-reading")
