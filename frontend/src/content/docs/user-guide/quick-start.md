### Getting started

Choose **Sign in with Microsoft** and sign in with the Microsoft account of your hospital or university.
Your first sign-in makes your CoGA account; you see a project's data once an administrator has given
you access to it. Where CoGA runs without Microsoft sign-in, choose **Sign up** on the sign-in page and
fill in **Create account** instead: you can sign in once an administrator has activated the account and
given you access to your projects. **Logout** signs you out of CoGA; on a shared computer, then choose
**Sign out of Microsoft** on the sign-in page too. The arrow at
the right of the top bar opens a menu of the main sections: the projects, the explorers, the gene panels,
this guide and, for administrators, **Admin**. **Settings** in the same bar holds your display
preferences.

CoGA keeps the pedigree, the assay data and your interpretation together, so a case is reviewed as a
whole:

- A **project** decides who sees the data and which reference assembly applies. You see only the
  projects you belong to; administrators see everything and create the projects. In each of your
  projects you are an **analyst**, who reads and changes its records, or a **reader**, who only reads
  them; a family page says *Read-only access (reader)* where you are a reader.
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
> - **Sign-out closes the analysis.** It is the analyst's sign-off, not the medical one: the clinical
>   report is drafted and medically signed in the EPD and refers to the signed version. Only people
>   the laboratory authorises sign out; CoGA refuses it to a reader, but does not check which
>   analyst may.

### Reviewing a case

1. On the **dashboard**, search for a project, family or sample, and open the family.
2. On the family page, pick the analysis that fits the question. A button appears only when the family
   has that kind of data.
3. Check **Sample QC** before you interpret.
4. Build a shortlist in the variant list before you open the viewers.
5. Record your interpretation as you go: classification, tags and notes.
6. Tag the variants to report with **Report**, check the drafted report, and have someone the
   laboratory authorises sign out the analysis. The clinical report is then drafted and medically
   signed in the EPD, referring to that signed version.

To set up a new case, see [Case setup and data import](#case-setup).

> **Not finding a variant you expect?** The **Small variants** page opens with a default filter: the
> *Phenotype priority* preset on the Mendeliome panel (rare, HIGH or MODERATE impact, carried by the
> affected members). Press **Clear all filters** before you conclude that a variant is missing. The
> **Variant explorer** leaves out imputed calls unless you tick *Include imputed variants*.
