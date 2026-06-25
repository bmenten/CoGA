import React from 'react';
import { Link } from 'react-router-dom';

type GuideLink = {
  label: string;
  to: string;
  note?: string;
};

type GuideSection = {
  id: string;
  title: string;
  summary: string;
  quickLinks?: GuideLink[];
  content: React.ReactNode;
};

// Links a guide section to an in-depth, in-app reference document (rendered from a
// bundled Markdown file at /docs/reference/:slug).
const FurtherReading: React.FC<{ to: string; label: string }> = ({ to, label }) => (
  <p className="user-guide-further-reading">
    <span className="user-guide-further-reading-label">In-depth reference</span>
    <Link to={to} className="user-guide-doc-link">
      {label} →
    </Link>
  </p>
);

const guideSections: GuideSection[] = [
  {
    id: 'orientation',
    title: 'How CoGA is organised',
    summary:
      'Understand the scoped data model — projects, families, samples, assemblies, and review state — before you start interpreting.',
    quickLinks: [
      { label: 'Dashboard', to: '/dashboard', note: 'Start here' },
      { label: 'Families', to: '/families', note: 'Case catalog' },
    ],
    content: (
      <>
        <p>
          CoGA is an integrated review environment for clinical genomic analysis. Like other family-based
          platforms, it keeps the pedigree, the assay layers, and your interpretation together so a
          case is reviewed as a whole rather than as a set of disconnected tests.
        </p>
        <div className="user-guide-callout">
          <strong>Mental model:</strong> a <em>project</em> controls access and assembly, a
          <em> family</em> is the case, <em>samples</em> carry the assay data, and
          <em> review state</em> (classifications, tags, notes) is the interpretation layer on top.
        </div>

        <h3>The objects you work with</h3>
        <ul>
          <li>
            <strong>Projects</strong> define who can see the data and which reference assembly
            applies. Your access — and every cohort count you see — is scoped to the projects you
            belong to (administrators see everything).
          </li>
          <li>
            <strong>Families</strong> are the unit of case review. They group related samples and
            carry pedigree meaning (relationships, roles, affected status).
          </li>
          <li>
            <strong>Samples</strong> hold per-sample assay layers: genotypes, coverage, segments,
            repeat expansions, Paraphase, and more.
          </li>
          <li>
            <strong>Reference data</strong> (genes, transcripts, cytobands, ClinVar, gnomAD,
            blacklist, clinical CNVs, DGV) is assembly-scoped and shared across projects on that assembly.
          </li>
          <li>
            <strong>Review state</strong> — ACMG classifications, tags, notes, and saved filter
            presets — is layered on top of the raw data and is what survives between sessions.
          </li>
        </ul>

        <h3>The analyst journey</h3>
        <ol>
          <li>Search for families, cases, or individuals through the dashboard.</li>
          <li>Capture or review the pedigree and associated phenotype (HPO terms).</li>
          <li>Examine small variants, structural variants, recombinations, mtDNA variants, (triplet) repeat expansions, and complex genomic regions through one family-based workspace.</li>
          <li>Prioritise candidates with inheritance-aware, evidence-rich filtering.</li>
          <li>Interpret each candidate and record an ACMG class, tags, and notes.</li>
          <li>Put findings in cohort context and follow up visually (genome views, IGV).</li>
          <li>Automatically report interesting variants.</li>
        </ol>
      </>
    ),
  },
  {
    id: 'quick-start',
    title: 'Quick start',
    summary: 'Two common entry points: open an existing case, or set up a new one.',
    quickLinks: [
      { label: 'Dashboard', to: '/dashboard', note: 'Search' },
      { label: 'Family Builder', to: '/family-builder', note: 'New case' },
      { label: 'Package Import', to: '/package-import', note: 'Bulk import' },
      { label: 'Gene explorer', to: '/genes', note: 'Locus-first' },
      { label: 'Variant explorer', to: '/variant-explorer', note: 'Cross-cohort' },
    ],
    content: (
      <>
        <h3>Reviewing an existing case</h3>
        <ol>
          <li>From the dashboard, search by project, family ID, or sample ID.</li>
          <li>Open the family to land on its workspace.</li>
          <li>
            Pick the analysis that matches the question — small variants, structural variants,
            repeats, and so on. Buttons appear only for data types the family actually has.
          </li>
          <li>Build a candidate shortlist in the variant table before opening dense viewers.</li>
          <li>Record interpretation as you go with classifications, tags, and notes.</li>
        </ol>

        <h3>Setting up a new case</h3>
        <ol>
          <li>Confirm (or create) the target project and its assembly.</li>
          <li>
            Build the family and samples in <strong>Family Builder</strong>, or bulk-import a
            prepared folder package via <strong>Package Import</strong> (admin).
          </li>
          <li>Import or attach the assay layers for the family.</li>
          <li>Open the family workspace to confirm the expected tables and tracks appear.</li>
        </ol>

        <div className="user-guide-callout">
          <strong>Not finding a variant you expect?</strong> The most common causes are a frequency or quality filter that is too strict, or imputed calls being
          hidden. Clear filters and re-check scope before assuming the data is missing.
        </div>
      </>
    ),
  },
  {
    id: 'case-setup',
    title: 'Case setup and data import',
    summary:
      'Create families and samples, import data packages, and understand what each assay layer unlocks.',
    quickLinks: [
      { label: 'Family Builder', to: '/family-builder', note: 'Manual pedigree' },
      { label: 'Package Import', to: '/package-import', note: 'Folder packages' },
      { label: 'Upload sample data', to: '/upload-data', note: 'Assays' },
      { label: 'Reference data', to: '/reference-data', note: 'Assembly layers' },
    ],
    content: (
      <>
        <p>
          Intake is split into two focused pages. Use whichever matches how your data arrives.
        </p>
        <div className="user-guide-mini-grid">
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Family Builder</p>
            <p className="user-guide-mini-card-copy">
              Build a pedigree by hand or from a PED file: add samples, set sex and roles, assign
              parents and couples, and set phenotype and carrier status. Available to all users.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Package Import</p>
            <p className="user-guide-mini-card-copy">
              Admins point at a backend-visible folder, discover a manifest, run a dry-run
              validation, then import the family and all of its assay layers in one job.
            </p>
          </div>
        </div>

        <h3>What each data type unlocks</h3>
        <ul>
          <li><strong>Small variants (SNV/indel)</strong> enable the small-variant workbench and review.</li>
          <li><strong>Structural variants</strong> enable the SV table, review, and CNV detail.</li>
          <li><strong>Repeat expansions (TRGT)</strong> enable the repeat-expansion view.</li>
          <li><strong>Paraphase</strong> enables segmental-duplication / paralogue resolution.</li>
          <li><strong>Mitochondrial calls</strong> enable the mtDNA homo- and heteroplasmy analysis.</li>
          <li><strong>Coverage, segments, APCAD, haplotypes, and recombination</strong> populate the genome and chromosome track viewers.</li>
        </ul>

        <div className="user-guide-callout">
          <strong>Assembly and reference first.</strong> Genes, cytobands, ClinVar, DGV, and gnomAD annotations are
          loaded per assembly. If a viewer cannot resolve coordinates or a gene lookup is empty, check
          that the reference layers for that assembly are present.
        </div>

        <FurtherReading
          to="/docs/reference/data-import"
          label="Data import reference (intake paths, assay layers, the manifest/dry-run flow)"
        />
      </>
    ),
  },
  {
    id: 'phenotypes-and-panels',
    title: 'Phenotypes and gene panels',
    summary:
      'Anchor interpretation in the patient phenotype with HPO, and constrain searches with reusable gene panels.',
    quickLinks: [
      { label: 'HPO browser', to: '/hpo', note: 'Phenotype terms' },
      { label: 'Panel catalog', to: '/panels', note: 'Gene sets' },
    ],
    content: (
      <>
        <p>
          Phenotype-led analysis is central to modern interpretation. Capture the patient&apos;s
          clinical features as HPO terms and curate gene sets so the same prioritisation can be
          reused across cases.
        </p>

        <h3>HPO phenotypes</h3>
        <ul>
          <li>Browse and search the HPO ontology from the HPO page.</li>
          <li>
            Annotate individuals with HPO terms in the family workspace; phenotype annotations are
            stored per individual and travel with the family.
          </li>
          <li>Use the phenotype to decide which genes and inheritance models to prioritise.</li>
        </ul>

        <h3>Gene panels</h3>
        <ul>
          <li>The gene panel catalog holds reusable gene lists for recurring indications.</li>
          <li>
            Apply a panel directly in the small-variant or structural-variant filter to restrict a search to a curated set
            of genes — a fast way to focus a broad genome on the clinically relevant loci.
          </li>
          <li>Panels can be imported automatically from PanelApp or custom-made, and are managed centrally so a team shares the same definitions.</li>
        </ul>
      </>
    ),
  },
  {
    id: 'phenotype-matching',
    title: 'Phenotype matching (Monarch Initiative)',
    summary:
      'Connect the patient’s HPO phenotypes to genes and diseases through the Monarch knowledge graph — on the gene profile and as a ranked “candidate genes” panel in the family.',
    quickLinks: [
      { label: 'Gene explorer', to: '/genes', note: 'Gene–disease & phenotypes' },
      { label: 'Families', to: '/families', note: 'Candidate-gene panel' },
    ],
    content: (
      <>
        <p>
          CoGA integrates the{' '}
          <a href="https://monarchinitiative.org/" target="_blank" rel="noreferrer">
            Monarch Initiative
          </a>{' '}
          knowledge graph, which links <strong>genes ↔ diseases ↔ HPO phenotypes</strong> from
          curated sources (OMIM, ClinGen, Orphanet, and the Human Phenotype Ontology Annotations).
          This turns the patient’s recorded phenotype into an active signal: it tells you which genes
          and diseases are phenotypically relevant, and it powers the automatic variant ranking
          described in the next section.
        </p>
        <div className="user-guide-callout">
          <strong>The loop in one sentence.</strong> Observed phenotypes → candidate genes → each
          gene’s diseases → which of the patient’s phenotypes those diseases explain → ranked
          variants.
        </div>

        <h3>On the gene profile</h3>
        <p>The Gene Explorer profile gains two phenotype-aware blocks:</p>
        <ul>
          <li>
            <strong>Monarch gene–disease associations</strong> — the curated diseases linked to the
            gene, each labelled with the relationship (<em>causes</em>, <em>associated</em>,{' '}
            <em>contributes&nbsp;to</em>) and its source(s), and linking out to the Monarch page for
            the disease. Causal associations are listed first.
          </li>
          <li>
            <strong>Expected phenotypes and patient overlap</strong> — each disease carries the HPO
            phenotypes expected for it. When you open a gene <em>in the context of a family</em>{' '}
            (for example by clicking a gene from a variant), CoGA highlights the expected phenotypes
            the family actually exhibits. Matching is <strong>ancestor-aware</strong>: a patient’s
            specific term (e.g. a particular seizure type) still matches a disease’s more general
            expected term (e.g. <em>Seizure</em>) through the HPO hierarchy.
          </li>
        </ul>

        <h3>In the family: ranked candidate genes</h3>
        <p>
          The family workspace has a <strong>Phenotype match (Monarch)</strong> panel. Press{' '}
          <em>Find candidate genes</em> and CoGA sends the affected individuals’ observed HPO terms
          to Monarch’s semantic-similarity service, which returns a list of genes ranked by how well
          their phenotype profile matches the patient. Genes that exist in your platform link
          straight to the gene profile (where the gene–disease and overlap blocks above are waiting),
          so you can move from “these phenotypes” to “this gene” to “this variant” without leaving
          the case.
        </p>
        <div className="user-guide-callout">
          <strong>It runs on demand.</strong> The candidate-gene panel calls Monarch only when you
          press the button, so it never slows down opening a family. Results are cached briefly.
        </div>

        <h3>How the ranking works — and why specific phenotypes matter</h3>
        <p>
          The ranking is <strong>not</strong> a yes/no “is this gene linked to this term” lookup —
          if it were, a broad term such as <em>Intellectual disability</em> (linked to well over a
          thousand genes) would return a flat, unordered list. Instead, each candidate gene gets a{' '}
          <strong>phenotypic-similarity score</strong>: CoGA compares the gene’s expected phenotype
          profile (the HPO terms of all the diseases Monarch links to it) against the patient’s{' '}
          <em>whole</em> set of observed terms, and walks the HPO hierarchy so a specific patient
          term still matches a more general expected one (and vice versa).
        </p>
        <p>
          The decisive ingredient is <strong>information content</strong>: a phenotype that occurs
          in only a handful of diseases is highly informative and counts heavily, while a phenotype
          shared by a large fraction of diseases carries almost no weight. <em>Intellectual
          disability</em> is one of the broadest terms in the ontology, so on its own it barely
          separates those thousand-plus genes — they all look about equally good, the score
          differences are tiny, and the order you see is close to arbitrary (and capped at the top
          ~50 genes Monarch returns). This is expected, not a fault: a single very general term
          simply does not carry enough information to rank on.
        </p>
        <p>
          The ranking sharpens as you add the patient’s <strong>more specific, co-occurring
          features</strong> — a particular seizure type, a dysmorphic feature, a metabolic finding,
          an abnormal MRI. Each specific term is highly informative, so the genes whose diseases
          actually explain those features rise to the top, while the genes that only share the
          generic <em>Intellectual disability</em> term fall away. The same principle drives the
          local phenotype score used for{' '}
          <a href="#variant-prioritisation">variant prioritisation</a> (rarer, more specific shared
          phenotypes count more).
        </p>
        <div className="user-guide-callout">
          <strong>Practical takeaway.</strong> One broad term gives a weak ranking by design.
          Record the affected individual’s distinctive phenotypes alongside the umbrella term — the
          more specific HPO you provide, the more meaningful (and trustworthy) the gene ranking
          becomes.
        </div>

        <h3>Where the data comes from</h3>
        <p>
          The gene–disease and disease–phenotype tables are loaded by an administrator from the
          monthly Monarch release (see <a href="#administration">Administration</a>); the
          candidate-gene panel is a live call to Monarch. If the tables have not been loaded yet, the
          profile blocks show an empty state rather than an error.
        </p>

        <FurtherReading
          to="/docs/reference/monarch-integration"
          label="Phenotype matching with Monarch reference (the graph, ranking, and PP4)"
        />
      </>
    ),
  },
  {
    id: 'family-workspace',
    title: 'The family workspace',
    summary:
      'The case dashboard: pedigree, review summaries, an editable region of interest, and one entry point per analysis.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Open a family' }],
    content: (
      <>
        <p>
          The family page is the hub of interpretation. It shows the pedigree, curation summaries,
          and a set of analysis buttons that link out to each review surface.
        </p>

        <h3>What you will find there</h3>
        <ul>
          <li>
            <strong>Analysis buttons that reflect the data.</strong> Small variants, structural
            variants, variant summary, repeat expansions, Paraphase, and mtDNA only appear when that
            data type is actually loaded for the family — so an empty button never sends you to an
            empty page.
          </li>
          <li>
            <strong>Visualisation buttons</strong> for the genome overview, chromosome view, Circos
            plot, and IGV.
          </li>
          <li>
            <strong>Review summaries</strong> for small and structural variants: how many are
            reviewed, noted, and tagged.
          </li>
          <li>
            <strong>Region of interest (ROI).</strong> Users can set a gene or locus of interest
            directly from the family dashboard and open it in the chromosome view.
          </li>
        </ul>

        <h3>Pedigree and member management</h3>
        <p>
          Sex, role, parentage, phenotype, and carrier status are edited per member. Phenotype
          (clinical status: unknown / unaffected / affected) and carrier status (unknown / carrier /
          non-carrier) are <strong>independent axes</strong> — an individual can be unaffected but
          still a carrier — and are set with separate controls. These edits are metadata-only: they update
          the pedigree and mark phenotype-dependent views as needing recomputation, but they never
          delete or reimport raw data.
        </p>
      </>
    ),
  },
  {
    id: 'sample-qc',
    title: 'Sample-integrity QC',
    summary:
      'An automated check — before you interpret — that a family’s samples are who the pedigree says: catches swaps, mislabelled relationships, wrong-sex labels, contamination and consanguinity.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Sample QC button' }],
    content: (
      <>
        <p>
          Open a family and press <strong>Sample QC</strong> to run an automated
          sample-integrity check. It verifies that the samples are who the pedigree says they are{' '}
          <em>before</em> any variant call, segregation analysis, or report is trusted — the failure
          modes it catches (a swapped tube, a wrong parent, a mislabelled sex, contamination,
          unexpected relatedness) quietly invalidate everything downstream.
        </p>
        <div className="user-guide-callout">
          <strong>It adapts to the application.</strong> CoGA runs different assays with different
          notions of integrity, so the page resolves the application first and runs only the
          meaningful checks:
        </div>
        <ul>
          <li>
            <strong>Long-read WGS family</strong> — sex concordance, relatedness vs the pedigree, and
            the Mendelian-error rate.
          </li>
          <li>
            <strong>Shallow-WGS PGT</strong> — embryo sex and <em>parentage</em> (each embryo a true
            child of both parents — no switch), plus Mendelian.
          </li>
          <li>
            <strong>Monogenic NIPT (cfDNA)</strong> — paternity (categories 7/8), fetal sex (paternal
            X transmission), germline parent sex, and a cfDNA category-distribution QC, instead of
            genotype relatedness.
          </li>
          <li>
            <strong>Carrier couple</strong> — sex per partner and a confirmation that the two are
            unrelated.
          </li>
          <li>
            <strong>Single targeted sample</strong> — sex only.
          </li>
        </ul>

        <h3>Reading the page</h3>
        <ul>
          <li>
            <strong>Pedigree with QC overlay.</strong> Each individual’s symbol carries its roll-up
            verdict — the outline and any filled region turn <strong>green</strong> (pass),{' '}
            <strong>amber</strong> (warning) or <strong>red</strong> (fail). Hover a symbol for why.
          </li>
          <li>
            <strong>Per-sample table.</strong> Recorded sex vs genotype sex (green when concordant,
            red on mismatch) and the Mendelian-error rate, colour-coded by status.
          </li>
          <li>
            <strong>Relatedness matrix.</strong> A sample × sample grid (lower triangle — it is
            symmetric) coloured by the inferred relationship, with kinship (φ) and IBS0 per cell. A
            pair that contradicts the pedigree — including co-parents who look related
            (consanguinity) — is outlined in red.
          </li>
          <li>
            <strong>NIPT cards.</strong> For a cfDNA family, dedicated paternity, fetal-sex and
            cfDNA-category-QC cards.
          </li>
        </ul>

        <div className="user-guide-callout">
          <strong>What to do with a warning or fail.</strong> A fail points to a real integrity
          problem — resolve it (re-check the sample sheet, the pedigree, or the genotypes) before
          interpreting. A warning usually means too little data to call confidently (few informative
          sites, low fetal fraction); it weakens, but does not invalidate, the downstream analysis.
          On partial or mock data the page degrades to warnings rather than failing.
        </div>

        <FurtherReading to="/docs/reference/sample-qc" label="Sample-integrity QC reference (checks, thresholds, data sources)" />
      </>
    ),
  },
  {
    id: 'small-variant-filtering',
    title: 'Small-variant prioritisation',
    summary:
      'The filter workbench: location, inheritance, consequence, ClinVar, frequency, in-silico scores, transcripts, and tags — with reusable presets.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Per-family search' }],
    content: (
      <>
        <p>
          The small-variant page is where most candidate-finding happens. Filters are grouped so you
          can move from a broad genome to a short candidate list quickly, then save the recipe as a
          preset.
        </p>

        <h3>Filter dimensions</h3>
        <ul>
          <li><strong>Location</strong> — gene symbols, genomic regions/intervals, or a gene panel.</li>
          <li>
            <strong>Inheritance</strong> — de novo / dominant, recessive (homozygous), compound
            heterozygous, and X-linked models, plus expanded carrier screening for couples.
          </li>
          <li><strong>Variant type</strong> — SNV, indel, or MNV.</li>
          <li>
            <strong>Consequence and impact</strong> — HIGH / MODERATE / LOW / MODIFIER and specific
            effects (missense, frameshift, stop gained, splice, and so on).
          </li>
          <li>
            <strong>ClinVar and classification</strong> — pathogenic through benign, conflicting
            interpretations, and your own ACMG classifications.
          </li>
          <li>
            <strong>Population frequency</strong> — gnomAD exomes/genomes/popmax and TOPMed
            allele frequencies, allele counts, and homozygote/hemizygote caps.
          </li>
          <li>
            <strong>In-silico evidence</strong> — CADD, REVEL, SpliceAI, SIFT, and PolyPhen
            thresholds, amongst others.
          </li>
          <li>
            <strong>Transcript scope</strong> — restrict to canonical, MANE, or loss-of-function
            transcripts.
          </li>
          <li><strong>Tags and notes</strong> — include or exclude review tags, or require notes.</li>
        </ul>

        <h3>Compound heterozygotes and per-sample genotypes</h3>
        <p>
          Compound-heterozygous candidates are grouped as pairs so you can assess both hits in a gene
          together. Per-sample genotype and quality thresholds (genotype, QUAL, DP, AF, AD) let you
          encode segregation expectations across the family directly in the search.
        </p>

        <h3>Cross-type &ldquo;second hit&rdquo;: a gene also hit by a structural variant</h3>
        <p>
          Recessive disease is often caused by a small variant on one allele and a{' '}
          <em>structural</em> variant on the other — most importantly an SNV plus an overlapping
          deletion, which removes the second copy and makes a &ldquo;heterozygous&rdquo; SNV
          effectively biallelic. Because small variants and structural variants are filtered in
          separate workspaces, these pairs are easy to miss, so CoGA flags them for you.
        </p>
        <ul>
          <li>
            Any small variant whose gene is <strong>also hit by a structural variant</strong> carries
            an <strong>SV badge</strong> showing the SV type and the zygosity in affected individuals.
          </li>
          <li>
            A <strong>trans / cis</strong> verdict says whether the two hits sit on opposite alleles
            (a compound-heterozygous candidate) or the same one — inferred from family segregation, and
            read directly from the haplotypes when the SVs are phased (shown as a distinct badge).
          </li>
          <li>
            A deletion in trans with a heterozygous SNV is highlighted as <strong>effectively
            biallelic</strong> — the highest-yield case.
          </li>
          <li>
            The <strong>Structural second hit</strong> filter (<em>&ldquo;Also hit by an SV&rdquo;</em>)
            restricts the results to just these genes, so you can screen for the pattern in one pass.
          </li>
        </ul>
        <FurtherReading
          to="/docs/reference/sv-second-hit"
          label="SNV + SV compound heterozygosity (cross-type second hit, trans/cis phasing)"
        />

        <h3>Bi-sample partner analysis</h3>
        <p>
          A special case is the coupled partner analysis — a dedicated filter for (expanded) preconception
          carrier screening. Only genes where both partners carry a variant meeting the filter criteria are returned.
        </p>

        <div className="user-guide-callout">
          <strong>Presets save the recipe, not the result.</strong> Built-in presets cover common
          patterns (dominant, recessive, compound het, carrier screening, ClinVar review, and{' '}
          <em>phenotype priority</em> — see the next section); save your own to standardise variant
          filtration.
        </div>
      </>
    ),
  },
  {
    id: 'variant-prioritisation',
    title: 'Phenotype-driven variant prioritisation (Exomiser-style)',
    summary:
      'One click ranks a family’s rare, impactful, segregating variants by how well each gene matches the patient’s phenotypes — with a transparent, explainable score.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Apply the preset' }],
    content: (
      <>
        <p>
          Filtering narrows the genome to a candidate list; prioritisation puts that list in order.
          The <strong>Phenotype priority (Exomiser-style)</strong> preset combines the Monarch
          phenotype signal with the usual variant evidence to rank candidates the way tools such as
          Exomiser do — so the most likely causal variant tends to rise to the top instead of being
          buried in a long, position-sorted list.
        </p>

        <h3>Turning it on</h3>
        <p>
          Apply the <strong>Phenotype priority (Exomiser-style)</strong> preset. It sets a sensible
          starting point — rare (gnomAD popmax &lt; 0.1%) and HIGH/MODERATE impact — and switches the
          result list into ranked mode. A <strong>Score</strong> column appears (sortable), the rows
          come back ordered best-first, and a small <span aria-hidden>✦</span> marks variants whose
          gene matches the patient’s phenotypes. You can still adjust any filter; prioritisation
          re-ranks whatever passes.
        </p>

        <h3>What the score blends</h3>
        <p>Each variant gets a single priority score in <code>[0, 1]</code> built from four parts:</p>
        <ul>
          <li>
            <strong>Pathogenicity</strong> — variant impact and loss-of-function, ClinVar
            assertions (a pathogenic ClinVar record dominates), and the in-silico predictors
            (CADD, REVEL, SpliceAI, and AlphaMissense). Gene constraint (gnomAD pLI for
            loss-of-function, missense-Z for missense) raises confidence for the relevant variant
            class.
          </li>
          <li>
            <strong>Rarity</strong> — rarer variants score higher, using the gnomAD population-max
            frequency.
          </li>
          <li>
            <strong>Segregation</strong> — whether the variant fits an inheritance pattern in the
            pedigree (see below).
          </li>
          <li>
            <strong>Phenotype fit</strong> — how well the gene’s Monarch phenotype profile matches
            the affected individuals’ HPO, computed locally with a Phenomizer-style
            information-content similarity (rarer, more specific shared phenotypes count more).
          </li>
        </ul>
        <p>
          Pathogenicity, rarity, and segregation form a <em>variant score</em>; phenotype fit then{' '}
          <strong>reorders</strong> candidates so a gene that matches the patient can rise above an
          equally damaging variant in an unrelated gene. Genes Monarch knows nothing about are not
          penalised on the variant axis — their raw variant score is shown separately, so a strong
          candidate in a novel gene stays findable (sort by the variant score to surface them).
        </p>

        <h3>Segregation modes</h3>
        <p>Using the pedigree (affected status, sex, and parent–child links), each variant is tagged with the inheritance patterns it is compatible with:</p>
        <ul>
          <li>
            <strong>De novo</strong> — heterozygous in an affected child and confidently absent in{' '}
            <em>both</em> genotyped parents (a true trio is required; a parent with a missing or
            low-coverage genotype does not qualify).
          </li>
          <li><strong>Homozygous recessive</strong> — affected individuals homozygous-alt, no unaffected homozygous-alt.</li>
          <li><strong>Compound heterozygous</strong> — two heterozygous hits in the same gene that segregate as a pair.</li>
          <li><strong>X-linked recessive</strong> — sex-aware X-chromosome pattern.</li>
          <li><strong>Dominant</strong> — affected carry the variant, unaffected do not (covers inherited-dominant and no-trio cases).</li>
        </ul>

        <h3>The score breakdown</h3>
        <p>
          Open a variant’s review dialog to see exactly <em>why</em> it ranked where it did: the
          combined score and rank, the variant / pathogenicity / rarity / phenotype sub-scores, the
          compatible inheritance modes, the gene constraint values, and — most usefully — the
          specific patient phenotypes that drove the gene’s phenotype match. This explainability is
          the point: the ranking is a transparent aid, not a black box.
        </p>

        <h3>Export</h3>
        <p>
          With the preset active, <em>Download CSV</em> exports the variants in ranked order with{' '}
          <strong>Priority</strong> and <strong>Rank</strong> columns, so the file matches what you
          see on screen.
        </p>

        <div className="user-guide-callout">
          <strong>Read the scores as a ranking, not a verdict.</strong> Priority scores order
          candidates <em>within a family</em>; they are not calibrated probabilities of
          pathogenicity. Phenotype matching needs HPO recorded on the affected individuals — without
          it, ranking falls back to impact/rarity/segregation only. If the result list shows a{' '}
          <em>“ranking is incomplete”</em> warning, more candidates matched than the ranker handles
          at once: tighten the filters (frequency, impact, a gene panel, or an inheritance mode) so
          the full set is ranked.
        </div>

        <div className="user-guide-callout">
          <strong>It&rsquo;s cached, and stays fresh.</strong> The ranking is relatively expensive to
          compute, so CoGA caches it: the first open computes it, every open after is near-instant
          (a <em>“served from cache”</em> note shows when), and it refreshes automatically whenever
          phenotypes, the pedigree, the panel, the filters or the annotations change. Narrowing from
          the Mendeliome to a smaller gene panel is instant — it&rsquo;s served from the broader
          ranking, since the scores don&rsquo;t depend on the panel.
        </div>

        <FurtherReading
          to="/docs/reference/variant-ranking-cache"
          label="Prioritised ranking & caching reference (invalidation, warming, sub-panel serving)"
        />
      </>
    ),
  },
  {
    id: 'interpretation-and-review',
    title: 'Interpretation and review state',
    summary:
      'Record an ACMG classification, tags, and notes per variant, and keep that review state consistent across the team.',
    content: (
      <>
        <p>
          Once a candidate is in focus, open its review dialog to record an interpretation. Review
          state is stored per family and is what makes a case auditable and resumable.
        </p>

        <h3>What you can record</h3>
        <ul>
          <li>
            <strong>ACMG classification</strong> — benign, likely benign, VUS, likely pathogenic, or
            pathogenic (classes 1–5).
          </li>
          <li>
            <strong>Tags</strong> — collaboration tags (e.g. for review, send for validation,
            validated, excluded), classification tags, and project- or globally-defined custom tags.
            Tags are how you flag reported, candidate, research, or solved variants.
          </li>
          <li><strong>Notes</strong> — free-text rationale that stays attached to the variant.</li>
        </ul>

        <h3>Evidence at hand</h3>
        <p>
          Each variant row surfaces the context you need to classify: gene and consequence, the most
          relevant transcript, ClinVar status, population frequency, and in-silico scores. Use the
          Gene Explorer for deeper gene-level context and the Variant Explorer for cohort context.
        </p>

        <h3>Structural-variant review</h3>
        <p>
          Structural variants have their own review surface with the same idea: classify, tag, and
          note, with a dedicated tag for segmentation review. SV and small-variant review state are
          tracked separately and summarised on the family page.
        </p>

        <div className="user-guide-callout">
          <strong>Review state is scoped to the family</strong> and preserved through metadata edits.
          The same variant interpreted in two families carries two independent review records — which
          is exactly what the cohort views aggregate.
        </div>
      </>
    ),
  },
  {
    id: 'acmg-classification',
    title: 'Semi-automatic ACMG classification',
    summary:
      'A guided ACMG/AMP classifier that pre-evaluates criteria from the variant, trio and gene data, scores them on a points scale, and stays fully overridable.',
    content: (
      <>
        <p>
          From any small-variant card or table row, <strong>ACMG classify</strong> opens a dedicated
          modal that helps you apply the ACMG/AMP 2015 criteria. It reads the data already attached to
          the variant — molecular consequence, gnomAD frequency, in-silico predictions, ClinVar — plus
          the gene profile (ClinGen dosage, GenCC inheritance, gene–phenotype HPO terms) and the family
          genotypes, and pre-positions each criterion accordingly. Nothing is final: every criterion
          can be toggled and re-graded by you.
        </p>

        <h3>How the score and class are computed</h3>
        <p>
          Scoring follows the Tavtigian/ClinGen Bayesian <strong>points</strong> system. Each
          <em> applied</em> criterion contributes points by its strength; the signed total maps onto
          the five ACMG classes and drives the green→red scale bar and arrow at the top of the modal.
        </p>
        <div className="content-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Strength</th>
                <th>Pathogenic</th>
                <th>Benign</th>
              </tr>
            </thead>
            <tbody>
              <tr><td>Supporting (PP/BP)</td><td>+1</td><td>−1</td></tr>
              <tr><td>Moderate (PM)</td><td>+2</td><td>−2</td></tr>
              <tr><td>Strong (PS/BS)</td><td>+4</td><td>−4</td></tr>
              <tr><td>Very strong (PVS1)</td><td>+8</td><td>—</td></tr>
            </tbody>
          </table>
        </div>
        <p>
          Class bands over the point total: <strong>≥ 10</strong> Pathogenic ·{' '}
          <strong>6–9</strong> Likely Pathogenic · <strong>0–5</strong> VUS ·{' '}
          <strong>−1…−6</strong> Likely Benign · <strong>≤ −7</strong> Benign. <code>BA1</code>
          {' '}(allele frequency ≥ 5%) is a stand-alone override that classifies the variant Benign
          regardless of any other evidence. The class is also recomputed on the server when you save,
          so a stored classification never depends on the browser.
        </p>

        <h3>VUS sub-tiers: hot, warm, cold</h3>
        <p>
          A variant of uncertain significance is not a single bucket. Following the MAGI-ACMG
          approach, CoGA splits the VUS point band (0–5) into three tiers by how close the evidence
          sits to the Likely-Pathogenic threshold, so the clinically interesting “hot” VUS stand
          apart from the rest:
        </p>
        <div className="content-table-wrap">
          <table>
            <thead>
              <tr>
                <th>VUS sub-tier</th>
                <th>Points</th>
                <th>Reading</th>
              </tr>
            </thead>
            <tbody>
              <tr><td><strong>Hot</strong></td><td>4–5</td><td>Leans pathogenic — one supporting line of evidence from Likely Pathogenic. Worth chasing extra evidence (segregation, functional, parental testing).</td></tr>
              <tr><td><strong>Warm</strong></td><td>2–3</td><td>Intermediate — mixed or partial evidence.</td></tr>
              <tr><td><strong>Cold</strong></td><td>0–1</td><td>Little pathogenic support — closest to likely benign.</td></tr>
            </tbody>
          </table>
        </div>
        <p>
          The tier is shown as a coloured chip on the scale-bar readout (“VUS · Hot”) and, on save,
          is written back as a <code>VUS — Hot/Warm/Cold</code> review tag alongside the{' '}
          <code>VUS - class 3</code> class tag. Because it is an ordinary review tag, you can filter
          or exclude on it like any other — e.g. surface only <em>hot</em> VUS for follow-up. The tier
          only exists while the variant is a VUS; reclassifying it out of the VUS band clears it.
        </p>

        <h3>The four states a criterion can be in</h3>
        <p>Auto-evaluation positions every criterion into one of four states (all overridable):</p>
        <ul>
          <li>
            <strong>Applied (checked, green)</strong> — the data clearly supports the criterion, so it
            is pre-checked and already counts toward the score.
          </li>
          <li>
            <strong>Consider (●, amber)</strong> — there is a relevant but not decisive signal. The
            criterion is surfaced unchecked; you confirm it if appropriate.
          </li>
          <li>
            <strong>Argues against (✕, red)</strong> — the data points the other way (e.g. an
            in-silico tool calls it benign when you are looking at PP3). Left unchecked and flagged.
          </li>
          <li>
            <strong>Not applicable (greyed, struck-through)</strong> — the criterion cannot apply to
            this variant given its type, frequency band or family configuration. Greyed as a clear
            hint, but still clickable so you can override it.
          </li>
        </ul>
        <p>
          Hover any criterion for the exact evidence string behind its state. Criterion families are
          laid out benign-left to pathogenic-right to mirror the scale bar.
        </p>

        <h3>Pre-check rules (positive evidence)</h3>
        <p>
          These rules decide which criteria are pre-checked or flagged <em>consider</em> from the
          variant, gene and family data:
        </p>
        <div className="content-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Criterion</th>
                <th>State</th>
                <th>Rule / data source</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>PVS1</strong></td>
                <td>Applied (Very strong / Strong) or Consider</td>
                <td>
                  Predicted-null consequence (nonsense, frameshift, canonical ±1,2 splice, start-loss,
                  transcript ablation). Applied at <em>Very strong</em> when LOFTEE is high-confidence
                  and ClinGen lists the gene as haploinsufficient (“Sufficient evidence”); at
                  <em> Strong</em> otherwise. If the LOF disease mechanism is unconfirmed it is shown
                  as <em>Consider</em> rather than auto-applied.
                </td>
              </tr>
              <tr>
                <td><strong>PM2</strong></td>
                <td>Applied (Supporting)</td>
                <td>Absent from gnomAD, or popmax allele frequency &lt; 1×10⁻⁴.</td>
              </tr>
              <tr>
                <td><strong>BA1</strong></td>
                <td>Applied (Stand-alone)</td>
                <td>gnomAD allele frequency ≥ 5% — stand-alone benign override.</td>
              </tr>
              <tr>
                <td><strong>BS1</strong></td>
                <td>Applied (Strong)</td>
                <td>gnomAD allele frequency ≥ 1% (and &lt; 5%) — higher than expected for a Mendelian disorder.</td>
              </tr>
              <tr>
                <td><strong>BS2</strong></td>
                <td>Applied (Strong) / Consider</td>
                <td>
                  Homozygotes observed in gnomAD. Strong when the gene is recessive-associated (GenCC);
                  otherwise <em>Consider</em> pending inheritance-mode confirmation.
                </td>
              </tr>
              <tr>
                <td><strong>PM4</strong></td>
                <td>Consider (Moderate)</td>
                <td>In-frame indel or stop-loss (protein-length change) — confirm it is outside a repeat region.</td>
              </tr>
              <tr>
                <td><strong>PP2</strong></td>
                <td>Applied (Supporting)</td>
                <td>Missense in a missense-constrained gene (gnomAD missense Z ≥ 3.09).</td>
              </tr>
              <tr>
                <td><strong>PP3 / BP4</strong></td>
                <td>Applied (strength-scaled)</td>
                <td>
                  In-silico predictions. REVEL drives the call with ClinGen-calibrated thresholds —
                  PP3: ≥ 0.644 Supporting, ≥ 0.773 Moderate, ≥ 0.932 Strong; BP4: ≤ 0.290 Supporting,
                  ≤ 0.183 Moderate, ≤ 0.016 Strong. SpliceAI (max Δ ≥ 0.2 / ≥ 0.5) and AlphaMissense
                  class are also used. The opposing criterion is flagged <em>argues against</em>.
                </td>
              </tr>
              <tr>
                <td><strong>BP7</strong></td>
                <td>Applied (Supporting)</td>
                <td>Synonymous variant with no predicted splice impact (SpliceAI max Δ &lt; 0.1).</td>
              </tr>
              <tr>
                <td><strong>PP5 / BP6</strong></td>
                <td>Applied (Supporting)</td>
                <td>ClinVar reports this exact variant pathogenic (→ PP5) or benign (→ BP6).</td>
              </tr>
              <tr>
                <td><strong>PP4</strong></td>
                <td>Applied (Supporting → Moderate)</td>
                <td>
                  Phenotype specific for the gene. When the family ran with{' '}
                  <a href="#variant-prioritisation">phenotype prioritisation</a>, the strength is
                  scaled by the Monarch gene↔proband phenotype-match score — <strong>≥ 0.6</strong>{' '}
                  Moderate, <strong>≥ 0.3</strong> (or a direct HPO-term overlap) Supporting, below
                  that not suggested. Without a match score it falls back to a plain HPO-term overlap
                  at Supporting. The evaluator caps PP4 at Moderate; raise it to Strong by hand for a
                  highly specific, single-gene phenotype.
                </td>
              </tr>
              <tr>
                <td><strong>PM6</strong></td>
                <td>Applied (Moderate)</td>
                <td>
                  Trio: variant present in the proband and absent in both sequenced parents (de novo).
                  Applied as <em>assumed</em> de novo (PM6); upgrade to PS2 manually if parentage is
                  molecularly confirmed.
                </td>
              </tr>
              <tr>
                <td><strong>PP1</strong></td>
                <td>Consider (Supporting)</td>
                <td>Cosegregation: the variant is carried by ≥ 2 affected family members.</td>
              </tr>
              <tr>
                <td><strong>BS4</strong></td>
                <td>Consider (Strong)</td>
                <td>Lack of segregation: an affected relative does not carry the variant.</td>
              </tr>
            </tbody>
          </table>
        </div>

        <h3>Exclusion rules (greyed as “not applicable”)</h3>
        <p>
          Criteria that cannot apply to the variant in front of you are greyed out, so the working set
          stays honest. They remain clickable for the rare case where you need to override.
        </p>
        <div className="content-table-wrap">
          <table>
            <thead>
              <tr>
                <th>When…</th>
                <th>Greyed as not applicable</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Missense variant</td>
                <td>PVS1, PM4, BP3, BP7</td>
              </tr>
              <tr>
                <td>Loss-of-function variant</td>
                <td>PP2, PM5, BP1, BP7, BP3</td>
              </tr>
              <tr>
                <td>Synonymous variant</td>
                <td>PVS1, PM4, PP2, PM5, BP1, BP3</td>
              </tr>
              <tr>
                <td>In-frame / length-changing variant</td>
                <td>PVS1, PP2, PM5, BP1, BP7</td>
              </tr>
              <tr>
                <td>Splice-region variant</td>
                <td>PVS1, PP2, PM5, BP1, PM4</td>
              </tr>
              <tr>
                <td>Allele frequency does not match a band</td>
                <td>The frequency criteria that do not fit — only the matching one of PM2 / BS1 / BA1 stays active</td>
              </tr>
              <tr>
                <td>No homozygotes in gnomAD</td>
                <td>BS2</td>
              </tr>
              <tr>
                <td>No in-silico prediction available</td>
                <td>PP3, BP4</td>
              </tr>
              <tr>
                <td>No complete trio / parental genotypes</td>
                <td>PS2, PM6</td>
              </tr>
              <tr>
                <td>Variant inherited from a parent</td>
                <td>PS2, PM6 (it is not de novo)</td>
              </tr>
              <tr>
                <td>No additional affected carrier</td>
                <td>PP1 (cosegregation cannot be shown)</td>
              </tr>
              <tr>
                <td>No affected relative lacking the variant</td>
                <td>BS4 (lack of segregation cannot be shown)</td>
              </tr>
            </tbody>
          </table>
        </div>

        <h3>What is not auto-evaluated</h3>
        <p>
          Criteria that need information CoGA does not hold are always left for you to apply manually:
          <strong> PS1</strong> and <strong>PM5</strong> (a different/known variant at the same amino-acid
          residue — needs a residue-level ClinVar index), <strong>PS3 / BS3</strong> (functional studies),
          <strong> PS4</strong> (case–control prevalence), <strong>PM1</strong> (hotspot / functional
          domain) and <strong>PM3 / BP2</strong> (in-trans phasing).
        </p>

        <h3>Mitochondrial (mtDNA) variants</h3>
        <p>
          Opening <strong>ACMG classify</strong> on a variant from the{' '}
          <a href="#specialised-analyses">mtDNA analysis</a> switches the evaluator to an
          mtDNA-specific rule set (after the ClinGen/Wong–McCormick 2020 specifications), because the
          mitochondrial genome is haploid and maternally inherited and the nuclear assumptions do not
          hold. The points scale, classes and VUS sub-tiers are unchanged; only the pre-evaluation
          differs:
        </p>
        <ul>
          <li>
            <strong>PVS1</strong> applies only to predicted-null changes in a protein-coding mt gene;
            it is greyed out for tRNA, rRNA and control-region loci (no protein product).
          </li>
          <li>
            <strong>Frequency (PM2 / BS1 / BA1)</strong> uses stricter mtDNA thresholds against
            gnomAD-MT — BA1 ≥ 0.5%, BS1 ≥ 0.02%, PM2 below 0.002% / absent. A MITOMAP common
            polymorphism or haplogroup marker is routed to BS1.
          </li>
          <li>
            <strong>PP5 / BP6</strong> read MITOMAP / ClinVar status (confirmed pathogenic → PP5;
            benign / polymorphism → BP6).
          </li>
          <li>
            <strong>PP3 / BP4</strong> are left for manual review — the mt-specific predictors
            (MitoTIP / APOGEE / HmtVar) are not yet wired in, so no in-silico call is auto-applied.
          </li>
          <li>
            <strong>De novo (PS2 / PM6)</strong> does not apply; instead the evaluator assesses{' '}
            <strong>maternal segregation</strong> from the maternal-line calls and heteroplasmy
            (PP1 / BS4). <strong>PM1</strong> is offered as <em>consider</em> for tRNA loci, and PP4
            notes the proband’s heteroplasmy level. Nuclear-only criteria (PP2, PM5, PM3, BP1–3, …)
            are greyed out.
          </li>
        </ul>

        <h3>External evidence links</h3>
        <p>
          The modal header carries quick links for the variant — <strong>gnomAD</strong>,{' '}
          <strong>ClinVar</strong>, <strong>DECIPHER</strong> — plus a smart <strong>PubMed</strong>
          {' '}search that combines the gene/variant with the patient’s HPO terms, to check whether the
          gene–phenotype association has been published.
        </p>

        <div className="user-guide-callout">
          <strong>Decision support, not an autoclassifier.</strong> Suggestions are pre-positioned from
          the data, but you confirm, adjust strengths, and add a rationale note. On save, the resulting
          class is written back as the variant’s ACMG classification (and class tag), and the full
          per-criterion rationale is stored for audit and reuse.
        </div>

        <FurtherReading
          to="/docs/reference/acmg-classification"
          label="ACMG classification reference (pre-check rules, points & class bands, mtDNA rules)"
        />
      </>
    ),
  },
  {
    id: 'clinical-report',
    title: 'Clinical report, traceability & sign-out',
    summary:
      'Draft a report from the reported variants, and lock the result to exactly what produced it: a version footer, evidence-drift warnings, an immutable audit trail, and a frozen, content-hashed case sign-out.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Report link → Variants' }],
    content: (
      <>
        <p>
          Tag a variant with <strong>report</strong> and it joins the family&apos;s clinical
          report (the <strong>Report</strong> link in the workspace <em>Variants</em> section).
          The report drafts readable prose for each reported variant — description, ACMG
          motivation, gene context and phenotype overlap — and is also the case&apos;s{' '}
          <strong>provenance and sign-out</strong> surface: it locks the result to exactly what
          produced it.
        </p>
        <div className="user-guide-callout">
          <strong>Why this matters.</strong> Annotation sources move — a new ClinVar or gnomAD
          release — so a classification made last month may rest on evidence that has since
          changed. CoGA makes the versions, the changes, and the decisions explicit and
          permanent, so a signed-out report can be reproduced and audited.
        </div>

        <h3>1 · Provenance footer — which versions</h3>
        <p>
          A footer states the generation timestamp and the annotation/reference{' '}
          <strong>module versions</strong> behind the data — the pipeline layer (VEP, ClinVar,
          gnomAD, dbNSFP, SpliceAI, GenCC, PanelApp) and the reference layer (genome assembly +
          release, Monarch). Pipeline versions are captured automatically at import (declared in
          the import manifest) and can be recorded or overridden by an admin. The footer prints
          with the report — it is part of the signed artifact.
        </p>

        <h3>2 · Evidence-drift banner — has anything changed</h3>
        <p>
          Every ACMG save <strong>freezes the evidence it was based on</strong> (the annotation
          identity + the ClinVar significance at that moment). When you open the report, each
          classification is compared against the current annotation; if the backing evidence has
          changed, an amber banner flags it — e.g. <em>“ClinVar Uncertain significance →
          Pathogenic”</em>. Re-review a flagged variant before sign-out. Classifications made
          before this feature existed have no frozen evidence and are simply not checked.
        </p>

        <h3>3 · Classification audit trail — who did what, when</h3>
        <p>
          An immutable <strong>“Classification audit trail”</strong> lists every clinical action
          on the family&apos;s variants — who classified, tagged or annotated which variant, when,
          and what changed (before → after), including each sign-out. The events are written in
          the same transaction as the change and the underlying table is{' '}
          <strong>append-only at the database level</strong>, so the record can never be quietly
          altered. (This is the clinical <em>action</em> log; the admin{' '}
          <a href="#administration">audit log</a> records HTTP <em>access</em> separately.)
        </p>

        <h3>4 · Case sign-out — freeze the result</h3>
        <p>
          <strong>Sign out report</strong> freezes the manifest, the reported variant list (each
          classification with its frozen evidence snapshot) and the drift state into a{' '}
          <strong>versioned, SHA-256 content-hashed</strong> snapshot, stored append-only — a
          signed-out report can never change. A green record appears on the report:{' '}
          <em>“✓ Signed out — version 2 by … · Content hash …”</em>.
        </p>
        <div className="user-guide-callout">
          <strong>The drift gate.</strong> If any reported classification has drifted, sign-out is
          blocked until you re-review or explicitly <strong>acknowledge</strong> the drift — and
          the acknowledgement is itself recorded in the snapshot and the audit trail. Signing out
          again creates a new version (the button reads <em>Amend sign-out</em>); earlier versions
          are never overwritten.
        </div>

        <FurtherReading
          to="/docs/reference/clinical-traceability"
          label="Report traceability & sign-out reference (footer, drift, audit, sign-out)"
        />
      </>
    ),
  },
  {
    id: 'specialised-analyses',
    title: 'Structural variants, repeats, Paraphase, and mtDNA',
    summary:
      'Specialised review surfaces for events that small-variant tables do not capture.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Open a family' }],
    content: (
      <>
        <div className="user-guide-mini-grid">
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Structural variants</p>
            <p className="user-guide-mini-card-copy">
              Review CNVs and other SVs with genotype context, classification, and tagging; jump to
              CNV detail and the genome/Circos views for breakpoint context.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Repeat expansions (TRGT)</p>
            <p className="user-guide-mini-card-copy">
              Inspect per-sample repeat-locus calls against the catalog to assess repeat expansions in the
              family.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Paraphase</p>
            <p className="user-guide-mini-card-copy">
              Resolve paralogous / complex regions where standard variant calling is
              unreliable.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Mitochondrial DNA</p>
            <p className="user-guide-mini-card-copy">
              Review mtDNA variants with heteroplasmy and homoplasmy thresholds and per-sample
              coverage.
            </p>
          </div>
        </div>
        <p>
          A combined <strong>variant summary</strong> view rolls up small and structural variant
          counts for the family when you want a single overview before diving into a specific table.
        </p>
      </>
    ),
  },
  {
    id: 'visualization',
    title: 'Genome visualisation and follow-up',
    summary:
      'Move from a candidate row into whole-genome, per-chromosome, Circos, and IGV views.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Viewers live per family' }],
    content: (
      <>
        <p>
          Tables are for finding candidates; viewers are for confirming and inspecting them and diving deeper into their genomic context. Each viewer reads the
          tracks that were imported for the family.
        </p>
        <ul>
          <li><strong>Genome overview</strong> — whole-genome context for variants and tracks.</li>
          <li>
            <strong>Chromosome view</strong> — a single chromosome with coverage, segments, and
            variant tracks; the ROI opens here with ±1 Mb of flanking context.
          </li>
          <li><strong>Circos plot</strong> — genome-wide structural relationships at a glance.</li>
          <li>
            <strong>IGV</strong> — read-level confirmation against the reference for a specific
            locus.
          </li>
        </ul>
        <h3>Small-variant track: colours and rows</h3>
        <p>
          In the Chromosome view, each small variant is drawn as a dot. Its colour encodes the
          predicted consequence, with ClinVar taking precedence:
        </p>
        <ul>
          <li>
            <strong>Functional impact</strong> — high impact is <strong>light orange</strong>,
            medium (moderate) is <strong>light green</strong>, and low / modifier is{' '}
            <strong>light gray</strong>.
          </li>
          <li>
            <strong>ClinVar overrides the impact colour</strong> — benign / likely benign is{' '}
            <strong>light blue</strong>, and pathogenic / likely pathogenic is <strong>red</strong>.
            Other ClinVar states (uncertain, conflicting) keep the impact colour.
          </li>
          <li>
            <strong>A review tag colour</strong>, when you have tagged the variant, takes priority
            over both of the above.
          </li>
        </ul>
        <p>
          When the displayed sample is a child with a parent in the family (or phasing is
          available), the track splits into three rows by parental origin:
        </p>
        <ul>
          <li><strong>Top row</strong> — variants on the paternal haplotype (hap1).</li>
          <li><strong>Middle row</strong> — undetermined / unknown parental origin.</li>
          <li><strong>Bottom row</strong> — variants on the maternal haplotype (hap2).</li>
        </ul>
        <p>
          Origin is read from the parent genotypes (Mendelian inheritance) when both parents are
          present, falling back to the phased haplotype order of the variant otherwise. Homozygous,
          de-novo, and ambiguous variants stay in the middle row. These are the <em>raw</em> calls —
          hover any dot to see its impact, ClinVar significance, and parental origin.
        </p>

        <h3>Haplotype track</h3>
        <p>
          The haplotype track enables visual inspection of recombinations. Raw (imputed) informative
          markers are shown, and the (imputed) haplotype blocks are colour-coded according to the
          pedigree information. For individuals affected by a dominant disorder, the shared haplotype
          blocks are colour-coded in red. Individuals affected by a recessive disorder have two
          affected haplotypes (coloured orange), while carrier parents — or other carriers in the
          pedigree — carry a single affected (orange) haplotype.
        </p>

        <div className="user-guide-callout">
          <strong>Viewers only show what was imported.</strong> Coverage, segment, APCAD, and
          haplotype tracks appear when the corresponding sample data exists; an empty track usually
          means that layer was not loaded, not that the viewer failed.
        </div>
      </>
    ),
  },
  {
    id: 'haplotype-segregation',
    title: 'Haplotype segregation analysis',
    summary:
      'A PGT tool: trace the four grandparental haplotypes through the family to read off which embryos inherited the disease haplotype(s) — with the raw markers to catch recombinations and artifacts.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Opens in the chromosome view' }],
    content: (
      <>
        <p>
          The <strong>Haplotype track</strong> (in the chromosome view) is CoGA&apos;s
          preimplantation-genetic-testing (PGT) surface. It colours every family member&apos;s two
          haplotypes by which grandparental founder they descend from, identifies the haplotype that
          carries the disease allele, and from that derives — <em>per embryo</em> — whether it
          inherited it.
        </p>
        <div className="user-guide-callout">
          <strong>Derived, not entered.</strong> The founder colours, the relatives&apos; colouring,
          the disease haplotype, and each embryo&apos;s affected / carrier / unaffected call are all
          <em> computed</em> from the phased genotypes and the pedigree. The only inputs are the
          pedigree (roles, parentage, sex), the recorded affected / carrier status, the inheritance
          model, and the region of interest.
        </div>

        <h3>The two layers</h3>
        <ul>
          <li>
            <strong>Cleaned haplotype blocks</strong> — the colour-coded inheritance blocks. Each
            member shows their two homologs; a block recolours at every recombination breakpoint.
            This is the smoothed, easy-to-read interpretation layer.
          </li>
          <li>
            <strong>Raw phased-marker overlay</strong> — one dot per informative imputed marker,{' '}
            <strong>with no binning or smoothing</strong>, drawn over the blocks. This is the
            diagnostic layer: it exposes isolated phasing switches, jitter at boundaries, and the
            exact marker where a crossover occurs — so you can confirm a breakpoint is real and spot
            artifacts before trusting a call.
          </li>
        </ul>

        <h3>The colour code</h3>
        <p>Colour encodes which of the four founder haplotypes (one per grandparent line) a block descends from:</p>
        <div className="content-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Colour</th>
                <th>Meaning</th>
              </tr>
            </thead>
            <tbody>
              <tr><td><strong>Dark blue</strong></td><td>Paternal founder homolog 0</td></tr>
              <tr><td><strong>Light blue</strong></td><td>Paternal founder homolog 1</td></tr>
              <tr><td><strong>Dark green</strong></td><td>Maternal founder homolog 0</td></tr>
              <tr><td><strong>Light green</strong></td><td>Maternal founder homolog 1</td></tr>
              <tr><td><strong>Grey</strong></td><td><em>Untransmitted</em> or <em>unknown</em> — a homolog not inherited from a placed founder, or one that could not be placed. No founder identity.</td></tr>
              <tr><td><strong>Red (overlay)</strong></td><td>The <strong>dominant</strong> affected haplotype shared by the affected members at the locus.</td></tr>
              <tr><td><strong>Orange (overlay)</strong></td><td>A <strong>recessive carrier</strong> haplotype — an affected individual has two, a carrier has one.</td></tr>
            </tbody>
          </table>
        </div>
        <p>
          The dark/light split is the two grandparental haplotypes on a side. The absolute
          dark-vs-light label is arbitrary (it comes from the raw phasing); what matters is{' '}
          <strong>consistency</strong> — the same physical grandparental haplotype keeps its shade
          across the whole family, so you can trace one haplotype from an affected grandparent down to
          an embryo.
        </p>

        <h3>How relatives are coloured (pedigree IBD)</h3>
        <p>
          The stored blocks are only meaningful for the index nuclear family (the parents and their
          children/embryos), where trio phasing grounds the four founders. A relative&apos;s stored
          blocks are <em>not</em> trustworthy, and CoGA&apos;s flat role model would even paint a
          paternal grandmother (stored as <code>role = mother</code>) entirely green. So CoGA{' '}
          <strong>recomputes every relative from the raw phased genotypes</strong>: starting from the
          coloured nuclear core, it walks the pedigree and identity-by-descent (IBD) matches each
          relative&apos;s homologs against the connected member. The shared homolog inherits that
          founder colour; the untransmitted homolog is greyed. A paternal grandmother thus gets one
          homolog coloured (whichever the affected father shares) and one grey — which is exactly what
          identifies <em>which</em> paternal haplotype carries the dominant allele. Matching is{' '}
          recombination-aware (a haplotype keeps its colour but jumps lanes at a crossover), and any
          member that cannot be confidently placed is left fully grey rather than mis-coloured.
        </p>

        <h3>Single-parent (donor) families</h3>
        <p>
          For embryos with only one known parent (e.g. a single woman or a couple using a donor
          gamete) while the disorder segregates in the known parent&apos;s family, the core is
          anchored on the embryos. The known parent&apos;s two homologs are the founders (traced up to
          the affected grandparents, who phase them), and the embryos are coloured by IBD against the
          known parent — the <strong>known-parent lane is coloured and the donor lane is greyed</strong>.
        </p>

        <h3>The derived embryo call (at the ROI)</h3>
        <p>
          CoGA infers the disease haplotype(s) from the affected/carrier members and the inheritance
          model — for a dominant disorder, the single haplotype the affected members share at the
          locus; for a recessive disorder, a carrier haplotype on each parental side; X-linked is
          sex-aware. It then classifies each embryo at the ROI:
        </p>
        <ul>
          <li><strong>Affected / at-risk</strong> — carries the disease haplotype as the model requires (the dominant haplotype, both recessive carrier haplotypes, or the sex-appropriate X-linked combination).</li>
          <li><strong>Carrier</strong> — recessive: carries one of the two carrier haplotypes (X-linked female: on one side).</li>
          <li><strong>Unaffected (non-carrier)</strong> — carries none of the disease haplotype(s).</li>
          <li><strong>Uninformative</strong> — the disease model could not be resolved to a unique haplotype, so no call is made.</li>
        </ul>
        <div className="user-guide-callout">
          <strong>Two warnings make a call unsafe — and the raw markers are how you catch them.</strong>{' '}
          A <em>recombination close to the ROI</em> means the haplotype at the variant may differ from
          the flanks; use the overlay to see exactly where the breakpoint falls. And{' '}
          <em>uninformative markers at the ROI</em> mean the haplotype there is interpolated, not
          observed — check the marker overview before trusting the call.
        </div>

        <h3>The ROI marker overview and QC</h3>
        <p>
          A members × markers grid shows the raw phased genotypes across the ROI, colour-coded by
          haplotype, with each member&apos;s informative-marker count — the table to{' '}
          <strong>re-check a surprising embryo call</strong> against the underlying genotypes and to
          spot per-site artifacts (a lone marker disagreeing with its neighbours = phasing noise, not
          a real crossover). Per child, CoGA also reports two QC numbers from the jointly-informative
          sites: the <strong>informative-site count</strong> (more is stronger evidence) and the{' '}
          <strong>Mendel-error rate</strong> — the fraction of sites where the child&apos;s genotype is
          impossible given the parents&apos;. A non-trivial Mendel rate flags a likely sample swap or
          wrong pedigree and should be resolved before any haplotype call is trusted. (The raw overlay
          and overview are computed only for the index parents&apos; own children; relatives appear on
          the track but carry no marker dots, as the parent-of-origin logic is meaningless for them.)
        </p>

        <h3>Known limitations</h3>
        <ul>
          <li>
            <strong>Recessive single-parent families are uninformative at the ROI</strong> — a
            recessive call needs both parental risk haplotypes, but the donor side is unknown, so the
            embryo call is deliberately <em>uninformative</em> (the known-parent risk haplotype is
            still coloured).
          </li>
          <li>
            <strong>Relatives are greyed on the sex chromosomes and mtDNA</strong> — the IBD logic
            assumes two homologs per site, which hemizygous X, the non-recombining Y, and the
            mitochondrion break; the nuclear core keeps its role-based colouring there.
          </li>
          <li>
            <strong>Very large regions truncate the overlay</strong> — when the whole-chromosome
            marker fetch is capped, the raw overlay is suppressed (with a &ldquo;zoom in&rdquo; state)
            rather than drawn part-way; the cleaned blocks still render. Zoom into the ROI to restore
            it.
          </li>
        </ul>

        <FurtherReading
          to="/docs/reference/haplotype-segregation"
          label="Haplotype segregation reference (founder colouring, derived embryo calls, QC)"
        />
      </>
    ),
  },
  {
    id: 'monogenic-nipt',
    title: 'Monogenic NIPT (cell-free DNA)',
    summary:
      'Screen a pregnancy for single-gene disorders from maternal-plasma cfDNA against a paternal sample — the fetal genotype is inferred from the allele fraction, never sequenced directly.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Open a NIPT family' }],
    content: (
      <>
        <p>
          Monogenic NIPT (non-invasive prenatal testing) screens a pregnancy for single-gene disorders
          using <strong>cell-free DNA (cfDNA) from maternal plasma</strong>, cross-referenced with a{' '}
          <strong>paternal</strong> sample. The plasma cfDNA is a mixture: mostly maternal DNA with a
          minor <strong>fetal fraction</strong>. The fetus is never sequenced directly — its genotype is{' '}
          <em>inferred</em> from how far the cfDNA allele fraction (VAF) deviates from the clean maternal
          expectations (0%, 50%, 100%), in proportion to the fetal fraction.
        </p>
        <div className="user-guide-callout">
          <strong>Two samples, three people.</strong> CoGA models the case as a trio — father, mother,
          fetus — backed by only two physical samples. The <em>father</em> column is an ordinary germline
          VCF; the <em>mother</em> column is the plasma cfDNA (maternal plus fetal signal); the{' '}
          <em>fetus</em> is a placeholder with no sequence of its own. A family is in NIPT mode when its
          analysis type is <code>monogenic_nipt</code> and the cfDNA sample is tagged{' '}
          <code>nipt_cfdna</code> — that is what surfaces the Monogenic NIPT tab.
        </div>

        <h3>The fetal fraction, and how it is calculated</h3>
        <p>
          Everything downstream depends on the <strong>fetal fraction (FF)</strong> — the proportion of
          the plasma cfDNA that is fetal. For a biallelic site the expected cfDNA allele fraction is{' '}
          <code>VAF = m · (1 − FF) + f · FF</code>, where <code>m</code> and <code>f</code> are the
          maternal and fetal alt-allele fractions (0, ½, or 1).
        </p>
        <p>
          FF is estimated from <strong>category-7 sites</strong>: positions where the mother is
          homozygous reference, the father carries the alt allele, and the alt is present in the cfDNA.
          The fetus must have inherited a paternal alt allele, so it sits as a clean heterozygote whose
          only contribution to the plasma is fetal — the site sits at exactly <code>FF / 2</code>. Taking
          the robust median over many such sites and doubling it gives{' '}
          <strong>FF = 2 × median(VAF) over category-7 sites</strong>. CoGA restricts this to
          well-covered, high-quality autosomal sites and reports the number of sites and a 95% confidence
          interval, so the estimate can be trusted or distrusted at a glance.
        </p>
        <div className="user-guide-callout">
          <strong>Reading the FF badges.</strong> The header shows the FF estimate, its confidence
          interval, and the category-7 site count. A <em>Low-confidence FF</em> chip appears when there
          are too few sites or the interval is wide. If the run supplied an <em>external FF</em> (from an
          upstream caller) it is shown alongside, and a <em>FF disagreement</em> chip flags when the two
          differ — the computed estimate stays the default and is never silently overridden.
        </div>

        <h3>The eight maternal/fetal categories</h3>
        <p>
          Once FF is known, the expected VAF of every category is a fixed number, so each cfDNA variant
          is assigned to the category whose expected VAF best explains its observed reads (a binomial
          likelihood, not a hard cut-off). The father genotype resolves the two cases that would otherwise
          be ambiguous.
        </p>
        <div className="content-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Cat</th>
                <th>Maternal / fetal state</th>
                <th>Expected cfDNA VAF</th>
                <th>Clinical meaning</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>1</td>
                <td>De novo in fetus (absent in both parents)</td>
                <td>FF / 2 (low)</td>
                <td>Candidate de novo dominant — father hom-ref separates it from paternal transmission</td>
              </tr>
              <tr>
                <td>2</td>
                <td>Maternal het, not inherited</td>
                <td>50% − FF/2</td>
                <td>A maternal carrier allele the fetus did not receive</td>
              </tr>
              <tr>
                <td>3</td>
                <td>Maternal het, fetus het</td>
                <td>50%</td>
                <td>Maternal allele transmitted; the maternal hit of a possible compound pair</td>
              </tr>
              <tr>
                <td>4</td>
                <td>Maternal het, fetus hom-alt</td>
                <td>50% + FF/2</td>
                <td>Fetus inherited both alleles — homozygous recessive risk</td>
              </tr>
              <tr>
                <td>5</td>
                <td>Maternal hom-alt, fetus het</td>
                <td>100% − FF/2</td>
                <td>Fetus inherited one reference allele from the father</td>
              </tr>
              <tr>
                <td>6</td>
                <td>Maternal &amp; fetal hom-alt</td>
                <td>100%</td>
                <td>Both homozygous (commonly a common variant)</td>
              </tr>
              <tr>
                <td>7</td>
                <td>Paternal allele transmitted (mother hom-ref)</td>
                <td>FF / 2</td>
                <td>Drives the fetal-fraction estimate; the paternal hit of a possible compound pair</td>
              </tr>
              <tr>
                <td>8</td>
                <td>Paternal hom-alt, absent in cfDNA</td>
                <td>≈ 0 (expected FF/2)</td>
                <td>False-negative QC signal — a variant the fetus should carry but the assay missed</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p>
          Categories 1 and 7 share the same expected VAF (<code>FF / 2</code>) and are told apart only by
          the father genotype: hom-ref means de novo, a carried allele means paternal transmission. A high
          category-8 rate is a warning — those sites should have appeared at <code>FF / 2</code>, so their
          absence points to dropout, insufficient FF, or coverage gaps.
        </p>

        <h3>What is and is not resolvable</h3>
        <p>
          As FF approaches zero the category centres collapse together — category 2 (
          <code>50% − FF/2</code>) and category 3 (<code>50%</code>) nearly coincide — so a low-depth or
          low-FF site gets a low confidence rather than a forced call. Each variant carries a{' '}
          <strong>confidence</strong>; the maternal state is usually solid even when the fetal call is
          uncertain. Use the confidence and the flags (for example <em>ff_too_low</em>,{' '}
          <em>low_depth</em>, <em>ambiguous</em>) to decide how much weight a single call deserves.
        </p>

        <h3>Using the page</h3>
        <p>
          The Monogenic NIPT workspace mirrors the small-variant page. The header folds in the fetal
          fraction and the filter funnel (total → quality-filtered → artifact-filtered → analysed). The
          collapsible filter panel adds, on top of the usual small-variant filters (gene panel, genes and
          intervals, annotation, frequencies, in-silico, flags), two NIPT-specific controls:
        </p>
        <ul>
          <li>
            <strong>Maternal/fetal categories</strong> — pick any of the eight categories; each option
            shows how many variants fall in it. This replaces the genotype subsection of the
            small-variant filter.
          </li>
          <li>
            <strong>Inheritance presets</strong> — one-click clinical reads of the category assignment:{' '}
            <em>De novo</em> (category 1), <em>Paternal dominant</em> and <em>Maternal dominant</em>{' '}
            (transmitted parental alleles), and <em>Recessive at-risk</em> (the fetus inherited a maternal
            and a paternal hit in the same gene, or is homozygous).
          </li>
        </ul>
        <p>
          Results use the full small-variant display: when fewer than 100 variants match it switches to
          cards, and every variant carries the complete annotation (father and cfDNA genotypes, ClinVar,
          gnomAD, in-silico scores, HGVS) plus tagging, reporting, and ACMG classification — the same
          review state as the small-variant page. The <strong>NIPT classification block</strong> on each
          card (and the NIPT column in the table) shows the category, the maternal and fetal state, the
          observed and expected VAF, and the confidence. On-target coverage is summarised as a single
          median depth over the panel or family ROI.
        </p>
        <div className="user-guide-callout">
          <strong>Derived, not entered.</strong> The fetal fraction and every per-variant category are
          computed from the two samples — their allele fractions, depths, qualities, and the father
          genotype. The only inputs are the combined VCF, the coverage and target regions, the pedigree,
          and your filter choices.
        </div>

        <FurtherReading
          to="/docs/reference/monogenic-nipt"
          label="Monogenic NIPT reference (cfDNA workflow, fetal fraction, the 8 categories)"
        />
      </>
    ),
  },
  {
    id: 'gene-explorer',
    title: 'Gene Explorer',
    summary:
      'A locus-first gene profile: transcript overview with clinical badges, constraint metrics, and disease associations.',
    quickLinks: [{ label: 'Gene explorer', to: '/genes', note: 'Search a gene' }],
    content: (
      <>
        <p>
          When the question is about a gene rather than a single case, the Gene Explorer gives a
          consolidated profile, similar to the gene pages in dedicated interpretation platforms.
        </p>
        <h3>Transcript overview</h3>
        <p>
          The transcript table badges the clinically relevant transcripts so you can pick the right
          one at a glance:
        </p>
        <ul>
          <li><strong>MANE Select</strong> — the agreed default clinical transcript.</li>
          <li><strong>MANE Plus Clinical</strong> — additional transcripts needed for clinical reporting.</li>
          <li><strong>RefSeq Select</strong> — the representative RefSeq transcript.</li>
          <li><strong>Ensembl Canonical</strong> — the Ensembl canonical transcript.</li>
        </ul>
        <h3>Gene-level context</h3>
        <ul>
          <li>Constraint metrics (e.g. pLI, LOEUF, missense constraint) for dosage sensitivity.</li>
          <li>Disease and phenotype associations, ClinGen / GenCC evidence, and OMIM links.</li>
          <li>
            <strong>Monarch gene–disease associations</strong> and each disease’s expected HPO
            phenotypes, with patient overlap when opened in a family context — see{' '}
            <a href="#phenotype-matching">Phenotype matching</a>.
          </li>
          <li>External links out to the major knowledge resources for the gene.</li>
        </ul>
      </>
    ),
  },
  {
    id: 'variant-explorer',
    title: 'Global Small Variant Explorer',
    summary:
      'A variant-centric, cross-project view: how often a variant occurs, in which families, and with what review state.',
    quickLinks: [{ label: 'Variant explorer', to: '/variant-explorer', note: 'Cross-cohort' }],
    content: (
      <>
        <p>
          The variant explorer answers cohort-level questions that a single family cannot:
          <em> in how many samples does this pathogenic variant occur? which families carry a variant
          in this gene? which reported variants exist across the database?</em> Each row is a unique
          variant aggregated across every project you can access.
        </p>
        <h3>What each row shows</h3>
        <ul>
          <li>Gene, variant, consequence, most-severe classification, and aggregated tags.</li>
          <li>
            Total carrier samples with a heterozygous / homozygous split, and the number of distinct
            families.
          </li>
        </ul>
        <h3>How you use it</h3>
        <ul>
          <li>
            Filter with the same dimensions as the family search — gene, consequence, ClinVar,
            frequency, in-silico — plus tags and ACMG classification to surface, for example, every
            variant tagged <em>Reported</em>, or every individual with a pathogenic variant in a certain gene.
          </li>
          <li>
            Click a heterozygous, homozygous, or family count to drill into the carriers, grouped by
            family, and link straight to the family workspace.
          </li>
          <li>Optionally add a per-sample genotype filter to find variants a specific sample carries.</li>
          <li>Imputed calls are left out by default but can be included with a toggle.</li>
        </ul>
        <div className="user-guide-callout">
          <strong>Counts are relative to your access.</strong> Aggregations only span the projects
          you can see, so internal-frequency context reflects your accessible cohort.
        </div>
      </>
    ),
  },
  {
    id: 'administration',
    title: 'Administration',
    summary:
      'The admin dashboard, grouped by operational domain: reference data, users & access, data management, variant configuration, database operations, and audit logs.',
    quickLinks: [
      { label: 'Admin dashboard', to: '/admin', note: 'All tools' },
      { label: 'Family & sample data', to: '/admin/data/families', note: 'Inventory & import' },
      { label: 'Projects & access', to: '/admin/access/projects', note: 'Scoping' },
      { label: 'Users', to: '/admin/access/users', note: 'Accounts' },
      { label: 'Audit logs', to: '/admin/monitoring/audit-logs', note: 'Activity' },
    ],
    content: (
      <>
        <p>
          Administrative tooling lives behind admin access and keeps the platform governed, current
          and healthy. Everything is reachable from the{' '}
          <Link to="/admin">admin dashboard</Link>, which groups the workspaces into six operational
          domains. The sections below describe what each one does.
        </p>

        <h3>1 · Reference data</h3>
        <p>
          The shared, system-wide datasets every project reads. These rarely change day to day, but
          keeping them current is what makes annotations, gene context and phenotype matching
          accurate.
        </p>
        <ul>
          <li>
            <strong>Species &amp; assemblies</strong>{' '}
            (<Link to="/admin/reference/assemblies">/admin/reference/assemblies</Link>) — the
            reference genome builds (e.g. GRCh38) and their per-assembly layers: cytobands, gene and
            transcript models, clinical CNVs, segmental duplications and DGV. Shows the status of each
            dataset and lets you trigger a <strong>gene-metadata sync</strong> — refresh a single gene
            by symbol or queue an all-human refresh — and watch the resulting jobs (progress, errors).
            The same area rebuilds the <strong>clinical-CNV knowledge base</strong> from ClinVar and
            DGV.
          </li>
          <li>
            <strong>Gene panels</strong>{' '}
            (<Link to="/admin/reference/gene-panels">/admin/reference/gene-panels</Link>) — the panel
            catalogue, each panel’s source metadata and its gene membership. Panels chosen here are
            what analysts pick from in <a href="#phenotypes-and-panels">case setup</a> and the
            small-variant location filter.
          </li>
          <li>
            <strong>HPO terminology</strong>{' '}
            (<Link to="/admin/reference/hpo">/admin/reference/hpo</Link>) — the Human Phenotype
            Ontology release: term count, synonyms, relationships and sync status. Preview and apply a
            new ontology release so phenotype entry and matching use current terms.
          </li>
          <li>
            <strong>Monarch knowledge graph</strong> — load the monthly Monarch release (gene–disease
            and disease–phenotype associations) that powers{' '}
            <a href="#phenotype-matching">phenotype matching</a> and{' '}
            <a href="#variant-prioritisation">variant prioritisation</a>. Run it once after deploy and
            roughly monthly to stay current; until it is run, the phenotype blocks show an empty
            state.
          </li>
        </ul>

        <h3>2 · Users &amp; access</h3>
        <p>This is what scopes every query, cohort count and family list in the platform.</p>
        <ul>
          <li>
            <strong>Users</strong>{' '}
            (<Link to="/admin/access/users">/admin/access/users</Link>) — user accounts, their role,
            and their activation status. Deactivate an account to revoke access without deleting its
            history.
          </li>
          <li>
            <strong>Projects &amp; access</strong>{' '}
            (<Link to="/admin/access/projects">/admin/access/projects</Link>) — the project catalogue
            (each project pins an assembly) and the family/sample-to-project assignments. A user only
            ever sees data in the projects they are granted, and the{' '}
            <a href="#variant-explorer">Global Small Variant Explorer</a> counts only across those
            projects, so access here directly defines each analyst’s and each cohort query’s scope.
          </li>
        </ul>

        <h3>3 · Data management</h3>
        <p>The imported family, sample and assay data, plus the workflow labels around it.</p>
        <ul>
          <li>
            <strong>Family &amp; sample data</strong>{' '}
            (<Link to="/admin/data/families">/admin/data/families</Link>) — search and page the full
            inventory of families and samples, drill into a family’s members and per-assay layers,
            and reassign a family or sample to different projects. Per-family <strong>raw import
            files</strong> can be downloaded and integrity-verified. Deletion workflows are here too,
            scoped precisely: remove a single assay layer for one sample, or an entire sample or
            family. (Deletions are irreversible and audit-logged.)
          </li>
          <li>
            <strong>Family statuses</strong>{' '}
            (<Link to="/admin/data/family-statuses">/admin/data/family-statuses</Link>) — curate the
            workflow statuses analysts assign to families (e.g. <em>new</em>, <em>in progress</em>,
            <em> completed</em>): add, rename, recolour or remove them.
          </li>
          <li>
            <strong>Package import</strong>{' '}
            (<Link to="/admin/data/upload">/admin/data/upload</Link>) — validate an import manifest
            and run a package-based <strong>initial or incremental import</strong> from the browser
            (the same flow available on the CLI). See <a href="#case-setup">case setup</a> for what an
            import brings in.
          </li>
        </ul>

        <h3>4 · Variant configuration</h3>
        <p>The interpretation vocabulary and reusable filters shared across the team.</p>
        <ul>
          <li>
            <strong>Variant tags</strong>{' '}
            (<Link to="/admin/variants/tags">/admin/variants/tags</Link>) — define the review tags
            analysts apply during <a href="#interpretation-and-review">interpretation</a>: create,
            rename, recolour and remove them, and scope custom tags per project. The built-in ACMG
            class tags and the VUS hot/warm/cold tier tags are managed automatically and appear here
            for reference.
          </li>
          <li>
            <strong>Preset filters</strong>{' '}
            (<Link to="/admin/variants/presets">/admin/variants/presets</Link>) — review the built-in
            and saved <a href="#small-variant-filtering">small-variant filter presets</a> the team
            relies on for consistent review. Presets are authored from the family workspace; this view
            is the catalogue.
          </li>
        </ul>

        <h3>5 · Database &amp; operations</h3>
        <ul>
          <li>
            <strong>ClickHouse tables &amp; operations</strong>{' '}
            (<Link to="/admin/operations/clickhouse">/admin/operations/clickhouse</Link>) — the
            per-assembly variant tables that back small-variant search, with their status and size,
            plus manual maintenance: <strong>ensure</strong> (create or repair a missing/corrupt
            table), <strong>optimise</strong> (compact storage, with an optional <em>final</em> pass)
            and <strong>rebuild the small-variant gene index</strong> (the materialised view behind
            gene-based lookups). Reach for these after a large import or if a gene-scoped query looks
            incomplete.
          </li>
        </ul>

        <h3>6 · Monitoring &amp; audit</h3>
        <ul>
          <li>
            <strong>Audit logs</strong>{' '}
            (<Link to="/admin/monitoring/audit-logs">/admin/monitoring/audit-logs</Link>) — a full
            activity trail for inspection and platform optimisation, split into two views, each
            filterable by user, path, method and status. <strong>API requests</strong> records every
            backend call: who, what, when, the response and duration, inferred data updates, and which
            search filters a query used. <strong>UI interactions</strong> records client-side activity
            that never reaches the backend — every button and link click and each in-app navigation.
            Identifiers are masked (paths reduced to <em>:id</em>, query strings to their filter
            names), so the trail shows <em>how</em> the platform is used without exposing patient
            data.
          </li>
        </ul>

        <p>
          Personal display preferences live on the <Link to="/settings">Settings</Link> page (not an
          admin tool); release notes are on the <Link to="/new-features">New features</Link> page.
        </p>
      </>
    ),
  },
  {
    id: 'glossary',
    title: 'Glossary',
    summary: 'Quick definitions for the terms used throughout CoGA.',
    content: (
      <>
        <ul>
          <li><strong>Assembly</strong> — the reference genome build (e.g. GRCh38) a project uses.</li>
          <li><strong>ROI</strong> — region of interest: a gene or locus pinned to a family.</li>
          <li>
            <strong>ACMG class</strong> — five-tier variant classification: benign, likely benign,
            VUS, likely pathogenic, pathogenic.
          </li>
          <li>
            <strong>VUS sub-tier (hot / warm / cold)</strong> — a finer split of the VUS band by
            ACMG points (hot 4–5, warm 2–3, cold 0–1): how close an uncertain variant sits to Likely
            Pathogenic. Hot VUS are the ones worth chasing more evidence for.
          </li>
          <li><strong>ClinVar</strong> — public archive of variant–condition interpretations.</li>
          <li><strong>gnomAD / TOPMed</strong> — population allele-frequency references.</li>
          <li>
            <strong>CADD / REVEL / SpliceAI / SIFT / PolyPhen / AlphaMissense</strong> — in-silico
            predictors of deleteriousness or splicing impact (AlphaMissense scores missense
            variants).
          </li>
          <li><strong>HPO</strong> — Human Phenotype Ontology: a structured vocabulary of clinical phenotypes, organised as a hierarchy (specific terms inherit from more general ones).</li>
          <li>
            <strong>Monarch Initiative</strong> — a knowledge graph linking genes, diseases, and HPO
            phenotypes from curated sources; CoGA uses it for phenotype matching and ranking.
          </li>
          <li>
            <strong>Phenotype similarity (Phenomizer / semantic similarity)</strong> — a score for how
            well two sets of HPO terms match, weighting rarer, more specific shared phenotypes
            (information content) more heavily.
          </li>
          <li>
            <strong>pLI / LOEUF</strong> — gene-level constraint metrics: how intolerant a gene is to
            loss-of-function variation (high pLI / low LOEUF = constrained).
          </li>
          <li>
            <strong>De novo (trio)</strong> — a variant present in an affected child but absent in
            both parents; confirming it needs a genotyped, well-covered trio.
          </li>
          <li>
            <strong>Priority score</strong> — the Exomiser-style ranking score blending phenotype fit,
            impact, rarity, and segregation; orders candidates within a family rather than giving an
            absolute probability.
          </li>
          <li>
            <strong>MANE Select / MANE Plus Clinical</strong> — agreed reference transcripts for
            clinical reporting; <strong>RefSeq Select</strong> and <strong>Ensembl Canonical</strong>
            {' '}are the representative transcripts from each database.
          </li>
          <li><strong>Compound heterozygous</strong> — two different variants in one gene, one per allele.</li>
          <li><strong>TRGT</strong> — tandem-repeat genotyping used for repeat-expansion calls.</li>
          <li><strong>Paraphase</strong> — caller for paralogous / segmental-duplication regions.</li>
          <li><strong>Heteroplasmy</strong> — the fraction of mitochondrial genomes carrying a variant.</li>
          <li>
            <strong>Carrier vs phenotype</strong> — carrier status describes genotype; phenotype
            (clinical status) describes the individual. They are tracked independently.
          </li>
        </ul>
      </>
    ),
  },
];

const formatSectionIndex = (index: number) => String(index + 1).padStart(2, '0');

const WorkspaceLinkRow: React.FC<{ links: GuideLink[] }> = ({ links }) => (
  <div className="user-guide-link-row">
    {links.map((link) => (
      <Link key={`${link.to}:${link.label}`} to={link.to} className="user-guide-link-chip">
        <span>{link.label}</span>
        {link.note ? <span className="user-guide-link-note">{link.note}</span> : null}
      </Link>
    ))}
  </div>
);

const UserGuidePage: React.FC = () => (
  <div className="page-shell content-shell user-guide-page">
    <header className="user-guide-hero">
      <p className="page-kicker">Documentation</p>
      <h1 className="user-guide-title">CoGA user guide</h1>
      <p className="user-guide-lede">
        An in-app manual for analysts and administrators. It follows the clinical-genomics workflow
        — orient, capture phenotype, prioritise, interpret, put in cohort context, and visualise —
        and maps each step to the page that does the job.
      </p>
    </header>

    <div className="user-guide-layout">
      <aside id="user-guide-contents" className="user-guide-toc">
        <p className="user-guide-eyebrow">On this page</p>
        <nav aria-label="User guide contents">
          <ol className="user-guide-toc-list">
            {guideSections.map((section, index) => (
              <li key={section.id}>
                <a href={`#${section.id}`} className="user-guide-toc-link">
                  <span className="user-guide-toc-index">{formatSectionIndex(index)}</span>
                  <span className="user-guide-toc-title">{section.title}</span>
                </a>
              </li>
            ))}
          </ol>
        </nav>
      </aside>

      <div className="user-guide-content">
        {guideSections.map((section, index) => (
          <section key={section.id} id={section.id} className="user-guide-section">
            <div className="user-guide-section-header">
              <span className="user-guide-section-index">{formatSectionIndex(index)}</span>
              <h2 className="user-guide-section-title">{section.title}</h2>
              <p className="user-guide-section-summary">{section.summary}</p>
            </div>
            {section.quickLinks?.length ? <WorkspaceLinkRow links={section.quickLinks} /> : null}
            <div className="content-prose user-guide-section-prose">{section.content}</div>
            <a href="#user-guide-contents" className="subtle-link user-guide-backlink">
              Back to top
            </a>
          </section>
        ))}
      </div>
    </div>
  </div>
);

export default UserGuidePage;
