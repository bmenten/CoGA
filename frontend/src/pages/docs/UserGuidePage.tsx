import React from 'react';
import { Link } from 'react-router-dom';

type GuideHighlight = {
  title: string;
  description: string;
};

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

const guideHighlights: GuideHighlight[] = [
  {
    title: 'Family-first review',
    description:
      'Keep pedigree, family membership, sample tracks, and case-level review state together instead of reviewing isolated files.',
  },
  {
    title: 'Inheritance-aware variant filtering',
    description:
      'Work through dominant, recessive, compound-heterozygous, carrier-screening, and genotype-driven small-variant searches.',
  },
  {
    title: 'Cross-linked tables and viewers',
    description:
      'Move from candidate tables into genome overview, chromosome view, circos, CNV detail, gene context, and IGV follow-up.',
  },
  {
    title: 'Multiple assay layers',
    description:
      'Review small variants, structural variants, repeat expansions, coverage, APCAD, segments, and haplotype evidence in one system.',
  },
  {
    title: 'Reusable interpretation state',
    description:
      'Save classifications, notes, tags, and presets so review work survives beyond one session or one analyst.',
  },
  {
    title: 'Operational administration',
    description:
      'Manage projects, users, reference layers, uploads, gene-reference refresh, and ClickHouse storage maintenance from the app.',
  },
];

const guideSections: GuideSection[] = [
  {
    id: 'quick-start',
    title: 'Quick start and common entry points',
    summary:
      'Use this when you need to orient yourself quickly, whether you are opening an existing case or loading a new one.',
    quickLinks: [
      { label: 'Dashboard', to: '/dashboard', note: 'Start here' },
      { label: 'Family intake', to: '/family-intake', note: 'Case setup' },
      { label: 'Upload sample data', to: '/upload-data', note: 'Assays' },
      { label: 'Gene explorer', to: '/genes', note: 'Locus-first' },
      { label: 'Panel catalog', to: '/panels', note: 'Reusable filters' },
      { label: 'Settings', to: '/settings', note: 'Display' },
    ],
    content: (
      <>
        <p>
          Most usage patterns in CoGA start in one of two ways: either you already have a
          case loaded and need to review it, or you are creating a new case and need to make the
          data visible in the correct project and assembly context.
        </p>
        <div className="user-guide-callout">
          <strong>Rule of thumb:</strong> confirm the project and assembly first, then confirm the
          family, then decide whether your next question is table-driven or viewer-driven.
        </div>

        <h3>If you are reviewing an existing case</h3>
        <ol>
          <li>Open the dashboard and search by project name, family ID, or sample ID.</li>
          <li>Expand the matching project to confirm the family belongs to the expected assembly.</li>
          <li>Open the family and choose the review surface that matches the event type.</li>
          <li>Use variant tables to form a candidate shortlist before opening dense viewers.</li>
          <li>Record interpretation state with notes, tags, classifications, or saved presets.</li>
        </ol>

        <h3>If you are loading a new case</h3>
        <ol>
          <li>Create or confirm the target project and its assembly.</li>
          <li>Create the family and sample metadata through family intake or pedigree upload.</li>
          <li>Import the assay layers that belong to that family or sample.</li>
          <li>Check the family pages to verify that the expected tables and tracks appear.</li>
          <li>Only then start interpretation and shared review state.</li>
        </ol>

        <h3>What a good starting state looks like</h3>
        <div className="user-guide-mini-grid">
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Project context is correct</p>
            <p className="user-guide-mini-card-copy">
              The family is linked to the intended project and the project points to the right
              species and assembly.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Family membership is clean</p>
            <p className="user-guide-mini-card-copy">
              Sample IDs, pedigree roles, and affected status reflect the case you intend to review.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Reference layers are loaded</p>
            <p className="user-guide-mini-card-copy">
              Genes and cytobands are present for the assembly so viewers and gene lookups can
              resolve coordinates.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Track expectations are realistic</p>
            <p className="user-guide-mini-card-copy">
              Coverage, APCAD, repeats, or haplotypes only appear when they were actually imported
              for the relevant samples.
            </p>
          </div>
        </div>
      </>
    ),
  },
  {
    id: 'core-concepts',
    title: 'Core concepts: projects, families, samples, and review state',
    summary:
      'The system becomes much easier to use once you understand which data are project-scoped, family-scoped, sample-scoped, or assembly-scoped.',
    content: (
      <>
        <p>
          CoGA is not just a collection of pages. It is a scoped review model. The same
          family can only be interpreted correctly if the project, assembly, samples, and reference
          layers are aligned.
        </p>

        <h3>The main objects in the system</h3>
        <ul>
          <li>
            <strong>Projects</strong> define access and assembly context. They are the boundary for
            who can see the data and which reference layers apply.
          </li>
          <li>
            <strong>Families</strong> define the case context. They group related samples and carry
            pedigree meaning.
          </li>
          <li>
            <strong>Samples</strong> hold sample-level assay layers such as coverage, APCAD,
            segments, and repeat expansions.
          </li>
          <li>
            <strong>Small variants</strong> and <strong>structural variants</strong> are reviewed in
            family context even when imported from family or sample files.
          </li>
          <li>
            <strong>Reference data</strong> such as genes, chromosomes, blacklist intervals, and
            clinical CNVs is assembly-scoped.
          </li>
          <li>
            <strong>Review state</strong> is the interpretation layer on top of raw data: tags,
            notes, classifications, pair review, and saved presets.
          </li>
        </ul>

        <h3>Why project context matters so much</h3>
        <p>
          The same family data can look incomplete or wrong if you review it under the wrong
          project. Assembly context controls gene coordinates, chromosome annotations, viewer
          navigation, and whether imported assay tracks line up with the reference.
        </p>

        <h3>What is family-scoped versus sample-scoped</h3>
        <div className="user-guide-mini-grid">
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Family-scoped</p>
            <p className="user-guide-mini-card-copy">
              Small-variant review, structural-variant review, pedigree context, variant summary,
              and case-level interpretation logic.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Sample-scoped</p>
            <p className="user-guide-mini-card-copy">
              Coverage, APCAD, segments, repeat expansions, and other track layers that may differ
              from one family member to another.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Assembly-scoped</p>
            <p className="user-guide-mini-card-copy">
              Genes, cytobands, blacklist intervals, and other annotation layers reused across many
              cases on the same reference.
            </p>
          </div>
        </div>

        <h3>Review state does not replace assay truth</h3>
        <p>
          Notes, tags, classifications, and presets are there to help teams reason about candidate
          events. They do not alter the imported assay evidence. If a call or track looks wrong,
          verify the underlying import instead of assuming the review layer changed the raw data.
        </p>
      </>
    ),
  },
  {
    id: 'setup-and-ingestion',
    title: 'Case setup, imports, and what each data type unlocks',
    summary:
      'This section explains the clean import order, when to use the UI, and what features become available after each assay or reference upload.',
    quickLinks: [
      { label: 'Reference data', to: '/reference-data', note: 'Reference' },
      { label: 'Family intake', to: '/family-intake', note: 'Metadata' },
      { label: 'Upload sample data', to: '/upload-data', note: 'Assays' },
      { label: 'Projects', to: '/projects', note: 'Admin' },
      { label: 'Data management', to: '/admin/data', note: 'Admin' },
    ],
    content: (
      <>
        <p>
          The cleanest environments are built in layers. Reference and metadata should exist before
          you load case-level assays, because later imports resolve their scope against what already
          exists.
        </p>

        <h3>Recommended import order</h3>
        <ol>
          <li>Create or confirm the species and assembly records.</li>
          <li>Load cytobands and genes for that assembly.</li>
          <li>Optionally load blacklist regions and clinical CNVs.</li>
          <li>Create the project and confirm it points to the expected assembly.</li>
          <li>Create the family and sample metadata.</li>
          <li>Import family small variants.</li>
          <li>Import structural variants, repeat expansions, and sample BED tracks.</li>
          <li>Open the family pages and verify that tables and viewers now expose the new data.</li>
        </ol>

        <h3>What each import type enables</h3>
        <ul>
          <li>
            <strong>Genes and cytobands</strong> unlock useful gene lookups, chromosome labeling,
            and viewer context.
          </li>
          <li>
            <strong>Family small variants</strong> unlock family SNV and indel review, inheritance
            filtering, and gene-linked candidate browsing.
          </li>
          <li>
            <strong>Structural variants</strong> unlock SV tables, genome overview overlays,
            chromosome inspection, and circos context.
          </li>
          <li>
            <strong>Repeat expansions</strong> unlock the repeat-expansion review table and repeat
            tracks where available.
          </li>
          <li>
            <strong>Coverage, APCAD, and segments</strong> unlock copy-number and inheritance-aware
            visual interpretation layers.
          </li>
          <li>
            <strong>GLIMPSE2-style phased imports</strong> can also unlock haplotype track context.
          </li>
        </ul>

        <h3>When to use the UI versus scripted setup</h3>
        <p>
          Use the application for routine admin work, smaller imports, and day-to-day case
          operations. Use scripted setup or reproducible environment bootstrap when you need larger
          migrations, demo resets, automated loading, or CI-friendly workflows.
        </p>

        <div className="user-guide-callout">
          <strong>Common failure mode:</strong> if a table loads but the corresponding viewer looks
          sparse or empty, the missing layer is often reference or track data rather than a UI
          problem.
        </div>
      </>
    ),
  },
  {
    id: 'dashboard-and-catalog',
    title: 'Using the dashboard and project catalog efficiently',
    summary:
      'The dashboard is the inventory and orientation workspace. It is where you verify project context before opening any specific case page.',
    quickLinks: [
      { label: 'Dashboard', to: '/dashboard', note: 'Workspace' },
      { label: 'Families', to: '/families', note: 'Catalog' },
    ],
    content: (
      <>
        <p>
          The dashboard is more than a landing page. It is the quickest place to verify which
          projects exist, which families are linked to them, and whether your current search term is
          matching a project, a family, or just a sample ID.
        </p>

        <h3>What the project catalog is good at</h3>
        <ul>
          <li>Searching across projects, families, and sample IDs from one input.</li>
          <li>Showing which families live under which project.</li>
          <li>Surfacing unassigned families that exist but are not yet linked to a project.</li>
          <li>Confirming the reference assembly linked to each project before you review a case.</li>
        </ul>

        <h3>Practical search habits</h3>
        <ul>
          <li>Search by project name when you know the case cohort but not the exact family ID.</li>
          <li>Search by family ID when you need to re-open a specific case quickly.</li>
          <li>Search by sample ID when analysts refer to individuals more often than family names.</li>
          <li>
            Expand the project row even after search narrows the results so you confirm the family
            appears in the expected project, not merely somewhere in the system.
          </li>
        </ul>

        <h3>When to leave the dashboard</h3>
        <p>
          Once you have confirmed project and family context, move into the family pages for
          interpretation. Stay on the dashboard when the question is operational: where is the case,
          is it linked correctly, is it unassigned, or which project should I open next?
        </p>
      </>
    ),
  },
  {
    id: 'family-workspace',
    title: 'How to use the family workspace as the center of interpretation',
    summary:
      'Family pages connect pedigree, case metadata, review tables, variant summary, repeat review, and links into visual follow-up pages.',
    content: (
      <>
        <p>
          The family workspace is the main interpretation hub. It is where family membership and
          review surfaces stay close enough together that you can keep inheritance context in mind
          while filtering or visually inspecting the case.
        </p>

        <h3>The main family-level pages</h3>
        <div className="user-guide-mini-grid">
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Family detail</p>
            <p className="user-guide-mini-card-copy">
              Use this page to verify pedigree, sample roles, affected status, linked projects, and
              case-level metadata before detailed interpretation.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Small variants</p>
            <p className="user-guide-mini-card-copy">
              Use this for SNV and indel triage, inheritance filtering, review notes, and saved
              filter reuse.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Structural variants</p>
            <p className="user-guide-mini-card-copy">
              Use this when the candidate event is larger, interval-based, or best understood in
              chromosome-scale context.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Repeat expansions</p>
            <p className="user-guide-mini-card-copy">
              Use this for family-wide review of tandem-repeat calls, especially when only selected
              samples carry repeat assay data.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Variant summary</p>
            <p className="user-guide-mini-card-copy">
              Use this when you want a higher-level case snapshot before diving into one event type.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">IGV view</p>
            <p className="user-guide-mini-card-copy">
              Use this after you already have a candidate locus and need read-level follow-up rather
              than broad case browsing.
            </p>
          </div>
        </div>

        <h3>A reliable case-review rhythm</h3>
        <ol>
          <li>Confirm pedigree and affected status on the family detail page.</li>
          <li>Choose the event type that most likely explains the phenotype.</li>
          <li>Build a shortlist in the corresponding family table.</li>
          <li>Use gene links, viewer links, and IGV follow-up to validate supporting context.</li>
          <li>Write down what you learned in the review layer before leaving the case.</li>
        </ol>

        <h3>When multiple projects touch the same family</h3>
        <p>
          Review the family in the project that carries the intended assembly and access context.
          If a family is linked to more than one project, treat the project choice as part of the
          interpretation state, not as an incidental UI detail.
        </p>
      </>
    ),
  },
  {
    id: 'small-variant-review',
    title: 'Small variant review and interpretation',
    summary:
      'This is the most configurable review surface in the application, combining genotype filters, annotation filters, inheritance logic, pair review, and saved presets.',
    content: (
      <>
        <p>
          The small-variant page is where you triage SNVs and indels using a combination of family
          inheritance logic, annotation filters, sample-level genotype rules, and review state.
          This page is designed for iterative interpretation, not just one-off searches.
        </p>

        <h3>The main filter families</h3>
        <ul>
          <li>
            <strong>Region and locus filters</strong> narrow by chromosome window, gene, transcript,
            or panel-linked intervals.
          </li>
          <li>
            <strong>Annotation filters</strong> narrow by impact, effect, ClinVar status, HGVS,
            population frequency, and in silico scores.
          </li>
          <li>
            <strong>Sample filters</strong> narrow by genotype, genotype quality, depth, allele
            fraction, and other sample-specific thresholds.
          </li>
          <li>
            <strong>Review filters</strong> let you revisit already tagged, classified, or
            note-bearing variants without rebuilding the original query from memory.
          </li>
          <li>
            <strong>Presets</strong> let you reuse built-in and saved search configurations across
            cases or review sessions.
          </li>
        </ul>

        <h3>Inheritance modes and what they mean in practice</h3>
        <ul>
          <li>
            <strong>Dominant-style searches</strong> are useful for quick shortlist creation when a
            single candidate hit may explain the case.
          </li>
          <li>
            <strong>Compound heterozygous mode</strong> now returns pair-level grouped results, so
            the search result itself reflects the two-hit interpretation unit instead of a loose
            same-gene list.
          </li>
          <li>
            <strong>Recessive mode</strong> can combine pair-level compound-het hits with
            homozygous-recessive singletons in one result set.
          </li>
          <li>
            <strong>Expanded carrier screening mode</strong> is useful when you are intentionally
            looking for partner-carrier patterns rather than standard case-first prioritization.
          </li>
        </ul>

        <h3>How to read and use the results</h3>
        <ul>
          <li>Use the table when you need sorting, scanning, and quick comparison across many hits.</li>
          <li>Use card views when the candidate count is already small and per-hit context matters.</li>
          <li>
            Open the review dialog when you want to store interpretation state, not just inspect one
            transient result.
          </li>
          <li>
            Follow gene links when the locus deserves deeper biological context and panel
            cross-references.
          </li>
          <li>
            Use the viewer or IGV links when the question shifts from “does this variant match my
            filters?” to “does the locus-level evidence support this call?”
          </li>
        </ul>

        <div className="user-guide-callout">
          <strong>Best use case:</strong> treat the small-variant table as the candidate-generation
          surface and the viewers as candidate-validation surfaces. They answer different questions.
        </div>
      </>
    ),
  },
  {
    id: 'structural-and-repeat-review',
    title: 'Structural variant review and repeat expansion review',
    summary:
      'These pages focus on interval-scale or assay-specific events that are often best interpreted together with viewers rather than by table logic alone.',
    content: (
      <>
        <p>
          Structural variants and repeat expansions each have their own review surfaces because they
          pose different interpretation questions than standard small variants. They are still
          family-centered, but the evidence is often broader and more context dependent.
        </p>

        <h3>Structural variant review</h3>
        <ul>
          <li>
            Use the SV page to filter by event type, length, interval, gene overlap, and
            sample-aware evidence.
          </li>
          <li>
            Large events should be interpreted with overlap semantics in mind: the relevant event
            may cross the visible region even if its full span extends beyond the current window.
          </li>
          <li>
            SV review is especially effective when followed by chromosome view, genome overview, or
            circos rather than staying only inside the table.
          </li>
        </ul>

        <h3>Repeat expansion review</h3>
        <ul>
          <li>
            Use the repeat-expansion page to compare TRGT calls across family members in one place.
          </li>
          <li>
            This is useful when only some samples carry repeat data and you need to see inheritance
            or abnormal-status patterns across the family.
          </li>
          <li>
            Repeat results are best read as a family comparison surface first and a per-sample track
            surface second.
          </li>
        </ul>

        <h3>When to switch to visual follow-up</h3>
        <p>
          Switch out of the table when you need to answer questions about event span, neighboring
          features, track agreement, copy-number patterning, or whether multiple lines of evidence
          support the same locus.
        </p>
      </>
    ),
  },
  {
    id: 'genome-and-locus-viewers',
    title: 'Genome overview, chromosome view, circos, CNV detail, and IGV',
    summary:
      'Use the viewer stack to validate and contextualize candidate loci, not to replace the candidate-generation logic of the tables.',
    content: (
      <>
        <p>
          CoGA ships several visualization layers because one scale is never enough. Some
          questions are chromosome-wide, some are interval-specific, and some only make sense once
          you inspect individual reads.
        </p>

        <h3>Which viewer answers which question</h3>
        <div className="user-guide-mini-grid">
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Genome overview</p>
            <p className="user-guide-mini-card-copy">
              Use this for broad per-chromosome pattern review and to find where larger signals
              cluster before zooming in.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Chromosome view</p>
            <p className="user-guide-mini-card-copy">
              Use this for denser chromosome-level inspection when one chromosome clearly matters.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Circos</p>
            <p className="user-guide-mini-card-copy">
              Use this for long-range structural relationships and inter-chromosomal context, not
              detailed genotype inspection.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">CNV detail</p>
            <p className="user-guide-mini-card-copy">
              Use this when a copy-number interval needs focused evidence review rather than
              whole-chromosome navigation.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">IGV</p>
            <p className="user-guide-mini-card-copy">
              Use this at the end of the chain when you already know the locus and need read-level
              confirmation.
            </p>
          </div>
        </div>

        <h3>Track availability and realistic expectations</h3>
        <ul>
          <li>Coverage may exist for one sample and not another.</li>
          <li>APCAD is often more selective than coverage in real datasets.</li>
          <li>Repeat tracks only appear when repeat calls were imported for that sample.</li>
          <li>Haplotype blocks usually require phased family imports.</li>
          <li>
            Missing genes or ideogram structure often indicate incomplete assembly reference data,
            not a broken viewer.
          </li>
        </ul>

        <h3>How to combine tables and viewers effectively</h3>
        <p>
          Build a focused candidate set in the family tables, then move into viewers to understand
          locus behavior, interval overlap, supporting tracks, and signal consistency. Starting in a
          dense viewer without a candidate question usually slows interpretation rather than helping
          it.
        </p>
      </>
    ),
  },
  {
    id: 'gene-explorer-and-panels',
    title: 'Gene explorer, gene metadata, and panel-driven review',
    summary:
      'Use these tools when the question is locus-first rather than case-first, or when you need a reusable targeted gene set across many cases.',
    quickLinks: [
      { label: 'Gene explorer', to: '/genes', note: 'Locus' },
      { label: 'Panel catalog', to: '/panels', note: 'Reuse' },
    ],
    content: (
      <>
        <p>
          The gene explorer is the locus-centric side of the application. It combines imported gene
          models with external metadata caches, panel membership, and family-linked context when you
          enter from a case workflow.
        </p>

        <h3>What the gene explorer is good for</h3>
        <ul>
          <li>Understanding a gene before or after opening one candidate variant.</li>
          <li>Reviewing panel membership and broader gene context in one place.</li>
          <li>
            Moving from gene-level reasoning back into family-level variant pages and genome views.
          </li>
          <li>Checking locus context even when the initial question did not start from one family.</li>
        </ul>

        <h3>How panels work in practice</h3>
        <ul>
          <li>
            Panels are not just label collections. The system resolves them to genomic intervals and
            uses overlap-aware filtering.
          </li>
          <li>
            That makes panels useful across small-variant searches, structural-variant review, and
            viewer navigation where interval context matters.
          </li>
          <li>
            Build panels when your team repeatedly reviews the same disease area, phenotype program,
            or clinical shortlist.
          </li>
        </ul>

        <h3>Good times to start from a gene instead of a family</h3>
        <ol>
          <li>You already have a strong candidate gene from external evidence.</li>
          <li>You are building or refining a panel before case review begins.</li>
          <li>You want to compare how a locus behaves across multiple families later on.</li>
        </ol>
      </>
    ),
  },
  {
    id: 'settings-and-admin',
    title: 'Settings, administration, and operational maintenance',
    summary:
      'These pages control display behavior, user and project access, reference maintenance, data inventory, and backend health checks.',
    quickLinks: [
      { label: 'Settings', to: '/settings', note: 'Display' },
      { label: 'Reference data', to: '/reference-data', note: 'Reference' },
      { label: 'Projects', to: '/projects', note: 'Admin' },
      { label: 'Admin users', to: '/admin/users', note: 'Admin' },
      { label: 'Data management', to: '/admin/data', note: 'Admin' },
      { label: 'Gene reference sync', to: '/admin/gene-reference', note: 'Admin' },
    ],
    content: (
      <>
        <p>
          Some pages exist for interpretation, others for environment stewardship. This section is
          about the latter: the settings and admin surfaces that keep the system usable and the data
          organized.
        </p>

        <h3>Settings</h3>
        <ul>
          <li>Change display density and viewport behavior for genome and chromosome review.</li>
          <li>Adjust how much track detail is visible at different scales.</li>
          <li>Use settings to improve readability, not to change the underlying assay evidence.</li>
        </ul>

        <h3>The main admin workspaces</h3>
        <div className="user-guide-mini-grid">
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Projects</p>
            <p className="user-guide-mini-card-copy">
              Manage case-to-assembly relationships and access boundaries.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Users</p>
            <p className="user-guide-mini-card-copy">
              Control who can access the system and which role each person has.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Reference data</p>
            <p className="user-guide-mini-card-copy">
              Maintain species, assemblies, genes, cytobands, blacklist intervals, and clinical
              CNVs.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Gene reference sync</p>
            <p className="user-guide-mini-card-copy">
              Refresh external gene metadata and monitor gene-reference ingestion health.
            </p>
          </div>
          <div className="user-guide-mini-card">
            <p className="user-guide-mini-card-title">Data management</p>
            <p className="user-guide-mini-card-copy">
              Inspect family inventory, link projects, manage track presence, and run ClickHouse
              maintenance operations.
            </p>
          </div>
        </div>

        <h3>Who should use what</h3>
        <p>
          Analysts usually spend most of their time in family pages, viewers, genes, and panels.
          Admins additionally own project setup, reference integrity, upload operations, user
          access, and storage maintenance. If you are doing both roles, keep those mental modes
          separate: one is interpretation, the other is system stewardship.
        </p>
      </>
    ),
  },
  {
    id: 'troubleshooting-and-best-practices',
    title: 'Troubleshooting patterns and best-practice review habits',
    summary:
      'Use this section when pages look empty, tracks seem inconsistent, or you want a reliable broad-to-narrow review workflow.',
    content: (
      <>
        <p>
          Most practical issues fall into a few repeatable categories: missing reference data,
          metadata mismatches, data imported into the wrong scope, or expectations that exceed what
          was actually loaded for the family.
        </p>

        <h3>Common reasons results look empty</h3>
        <ul>
          <li>The family is being viewed under the wrong project or assembly context.</li>
          <li>The relevant assay type was never imported for that family or sample.</li>
          <li>The page is filtered by a panel, region, or review state you forgot was active.</li>
          <li>Genes or cytobands are missing for the current assembly.</li>
          <li>The sample exists, but the expected track availability differs between family members.</li>
        </ul>

        <h3>How to diagnose inconsistencies quickly</h3>
        <ol>
          <li>Check the project and family identity first.</li>
          <li>Check whether the same region is being viewed across pages.</li>
          <li>Check active filters, panels, and visible-track settings.</li>
          <li>Check whether the underlying assay was imported for the sample you expect.</li>
          <li>Only then assume there is a software or data-quality problem.</li>
        </ol>

        <h3>A disciplined broad-to-narrow workflow</h3>
        <ol>
          <li>Verify project and pedigree context.</li>
          <li>Use the relevant family table to generate a candidate set.</li>
          <li>Open gene or panel context when biology, phenotype, or target scope matters.</li>
          <li>Move into viewers for evidence validation and structural context.</li>
          <li>Finish in IGV only when a candidate locus warrants read-level follow-up.</li>
          <li>Store notes, tags, classifications, or presets before leaving the case.</li>
        </ol>

        <div className="user-guide-callout">
          <strong>Best overall strategy:</strong> use the system as one connected environment.
          Projects define the assembly, families define the case, tables define the candidate set,
          and viewers explain what those candidates mean in genomic context.
        </div>
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
    <section className="surface-card page-top-card">
      <div className="space-y-4">
        <div className="space-y-3">
          <p className="page-kicker">Documentation</p>
          <h1 className="catalog-card-title">CoGA user guide</h1>
          <p className="catalog-card-copy">
            A fuller in-app manual for analysts and administrators: how to navigate the system,
            which workspace to use for each task, how the data model affects what you see, and how
            to move from case setup to interpretation to visual follow-up.
          </p>
        </div>
        <div className="user-guide-summary-row">
          <span className="badge-chip">Family-based review</span>
          <span className="badge-chip">Tables + viewers</span>
          <span className="badge-chip">Panels + genes</span>
          <span className="badge-chip">Admin + analyst workflows</span>
        </div>
      </div>
    </section>

    <section className="surface-card">
      <div className="space-y-4">
        <div>
          <p className="page-kicker">Capabilities</p>
          <h2 className="section-title">What you can do in this system</h2>
          <p className="section-copy">
            CoGA is strongest when used as an integrated review environment rather than a
            set of isolated tables.
          </p>
        </div>
        <div className="user-guide-highlight-grid">
          {guideHighlights.map((highlight) => (
            <article key={highlight.title} className="user-guide-highlight-card">
              <p className="user-guide-highlight-title">{highlight.title}</p>
              <p className="user-guide-highlight-copy">{highlight.description}</p>
            </article>
          ))}
        </div>
      </div>
    </section>

    <div className="user-guide-layout">
      <aside id="user-guide-contents" className="surface-card user-guide-toc-card">
        <div className="space-y-3">
          <p className="page-kicker">Contents</p>
          <h2 className="section-title">Jump by workflow</h2>
          <p className="section-copy">
            Use the links below to jump directly to setup, case review, viewers, gene context, or
            admin operations.
          </p>
        </div>
        <nav aria-label="User guide contents">
          <ol className="user-guide-toc-list">
            {guideSections.map((section, index) => (
              <li key={section.id}>
                <a href={`#${section.id}`} className="user-guide-toc-link">
                  <span className="user-guide-toc-index">{formatSectionIndex(index)}</span>
                  <span className="user-guide-toc-title">{section.title}</span>
                  <span className="user-guide-toc-summary">{section.summary}</span>
                </a>
              </li>
            ))}
          </ol>
        </nav>
      </aside>

      <div className="user-guide-content">
        {guideSections.map((section, index) => (
          <section key={section.id} id={section.id} className="surface-card user-guide-section-card">
            <div className="user-guide-section-header">
              <div className="user-guide-section-lead">
                <span className="user-guide-section-index">{formatSectionIndex(index)}</span>
                <h2 className="section-title">{section.title}</h2>
                <p className="user-guide-section-summary">{section.summary}</p>
              </div>
            </div>
            {section.quickLinks?.length ? <WorkspaceLinkRow links={section.quickLinks} /> : null}
            <div className="content-prose user-guide-section-prose">{section.content}</div>
            <a href="#user-guide-contents" className="subtle-link user-guide-backlink">
              Back to contents
            </a>
          </section>
        ))}
      </div>
    </div>
  </div>
);

export default UserGuidePage;
