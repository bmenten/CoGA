### Getting started

Choose **Sign in with Microsoft** and use the Microsoft account of your hospital or university. Your
first sign-in creates your CoGA account, with the role *viewer*: you read and change nothing. You see
a project's data once an administrator gives you access to it, and you analyse once they give you a
higher role.

Where CoGA runs without Microsoft sign-in, choose **Sign up** on the sign-in page and fill in
**Create account**. You can sign in once an administrator has activated the account and given you
access to your projects.

**Logout** signs you out of CoGA, on every computer and in every browser where you are signed in:
each of them asks you to sign in again. On a shared computer, then also choose **Sign out of
Microsoft** on the sign-in page.

The arrow at the right of the top bar opens a menu of the main sections: the projects, the explorers,
the gene panels, this guide and, for superusers and administrators, **Admin**. **Settings** in the same bar holds your
display preferences.

CoGA keeps the pedigree, the assay data and your interpretation together, so you review a case as a
whole:

- Your **role** decides what you may do, lowest first: a **viewer** reads; an **analyst** also
  analyses (reviews, tags, classifications, HPO terms, the case's workflow); a **supervisor** also signs
  an analysis out; a **superuser** also has every project and the configuration the analyses read (gene
  panels, tags, preset filters, QC thresholds) and package import; an **admin** has the whole
  administration.
- A **project** decides who sees the data and which reference assembly applies. You see only the
  projects you belong to; superusers and administrators see everything, and administrators create the
  projects. In each of your projects you are an **analyst**, who reads and changes its records as far
  as your role allows, or a **reader**, who only reads them. A family page says *Read-only access*
  where you only read: your role is viewer, or you are a reader there.
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
> - **Sign-out closes the analysis.** It is the analytical sign-off, not the medical one: the clinical
>   report is drafted and medically signed in the EPD and refers to the signed version. Only a
>   supervisor of the family's project, a superuser or an administrator signs out; the laboratory
>   decides who gets the supervisor role.

### Reviewing a case

1. On the **dashboard**, search for a project, family or sample, and open the family.
2. On the family page, pick the analysis that fits the question. A button appears only when the family
   has that kind of data.
3. Check **Sample QC** before you interpret.
4. Build a shortlist in the variant list before you open the viewers.
5. Record your interpretation as you go: classification, tags and notes.
6. Tag the variants to report with **Report**, check the drafted report, and have a supervisor sign
   out the analysis. The clinical report follows in the EPD.

To set up a new case, see [Case setup and data import](#case-setup).

> **Not finding a variant you expect?** The **Small variants** page opens with a default filter: the
> *Phenotype priority* preset on the Mendeliome panel (rare, HIGH or MODERATE impact, carried by the
> affected members). Press **Clear all filters** before you conclude that a variant is missing. The
> **Variant explorer** leaves out imputed calls unless you tick *Include imputed variants*.
