### Getting started

To get an account, choose **Sign up** on the sign-in page and fill in **Create account**. You can sign
in once an administrator has activated the account and given you access to your projects. **Settings**
in the page header holds your display preferences.

CoGA keeps the pedigree, the assay data and your interpretation together, so a case is reviewed as a
whole:

- A **project** decides who sees the data and which reference assembly applies. You see only the
  projects you belong to; administrators see everything and create the projects.
- A **family** is the case: related samples with their pedigree, roles and affected status.
- A **sample** carries the assay data: variants, coverage, segments, repeats and more.
- **Reference data** (genes, cytobands, ClinVar, gnomAD, clinical CNVs and more) belongs to an assembly
  and is shared by all projects on it.
- **Review state** — classifications, tags and notes — is your interpretation, kept per family.

> **Use CoGA safely.**
>
> - **Decision support only.** Review every suggestion, ranking and call; CoGA makes no diagnosis.
> - **Screening needs confirmation.** Confirm an at-risk NIPT or carrier-screening finding with a
>   diagnostic test.
> - **Validated scope only.** A family marked *Not validated for clinical use* is outside the validated
>   assembly (GRCh38) and cannot be signed out.
> - **Check Sample QC** before sign-out: it confirms that the samples are who the pedigree says.
> - **Only authorised signatories sign out.** CoGA does not check who may sign.

### Reviewing a case

1. On the **dashboard**, search for a project, family or sample, and open the family.
2. On the family page, pick the analysis that fits the question. A button appears only when the family
   has that kind of data.
3. Check **Sample QC** before you interpret.
4. Build a shortlist in the variant list before you open the viewers.
5. Record your interpretation as you go: classification, tags and notes.
6. Tag the variants to report with **Report**, check the drafted report, and have an authorised
   signatory sign it out.

To set up a new case, see [Case setup and data import](#case-setup).

> **Not finding a variant you expect?** The **Small variants** page opens with a default filter: the
> *Phenotype priority* preset on the Mendeliome panel (rare, HIGH or MODERATE impact, carried by the
> affected members). Press **Clear all filters** before you conclude that a variant is missing. The
> **Variant Explorer** leaves out imputed calls unless you tick *Include imputed variants*.
