A conventional NIPT screens a pregnancy for aneuploidies and copy-number changes from the genome-wide
copy number of the **maternal plasma**. The laboratory's tool, ccnv, measures it; CoGA draws it and gives
an **automatic screening result per target**. A high-risk result calls for confirmatory diagnostic
testing; a low-risk result does not exclude an aneuploidy.

The validated claim covers **trisomies 21, 18 and 13 and the sex-chromosome aneuploidies**, in
pregnancies of more than 12 weeks, singleton or twin. Triplets or more, a vanishing twin, a maternal
organ or bone-marrow transplant or a blood transfusion in the months before sampling, and a known
maternal malignancy are outside the validated scope. A twin pregnancy gets one result, which does not
say which fetus a finding concerns.

### Setting up a conventional NIPT family

A package import makes the family when its package says `analysis_type: conventional_nipt` (**cNIPT**
in *Deliveries*), with ccnv's bins, segments, calls and QC file for the plasma. A folder of ccnv output
added by hand is read the same way: CoGA proposes the plasma as the mother of the pregnancy. The family
page then shows a **cNIPT** tab.

### The cNIPT page

- **The result** lists chromosomes 21, 18 and 13, the sex chromosomes and the segmental CNVs first, each
  **High risk**, **Inconclusive**, **Low risk**, **No result** or **Not assessed**, with why. Another
  autosome is listed with them when it is not low risk; every autosome is under *Every other autosome*.
  The segmental CNVs and every autosome other than 21, 18 and 13 are marked **outside the validated
  claim**, here, on the report page and in the signed version: screen them as findings outside the
  claim.
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
reason. When the family also holds the monogenic NIPT of the pregnancy, each NIPT is signed out on its
own: the **Report** page signs out the conventional NIPT (**Sign out the conventional NIPT**), and its
record freezes and checks the conventional NIPT's data only.

[Conventional NIPT reference (the fetal fraction, the rule, the targets and the bounds)](/docs/reference/conventional-nipt "further-reading")
