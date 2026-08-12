import React, { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';

import PageState from '../../components/PageState';
import api from '../../lib/api';
import type { ApiFamilyRecord } from '../../lib/apiTypes';
import { useFamilyReference } from '../../lib/reference';

interface GeneSuggestion {
  symbol: string;
  gene_id: string;
  chr: string;
  start: number;
  end: number;
  transcript_count: number;
  assembly_count: number;
}

interface GeneTranscript {
  transcript_id: string;
  start: number;
  end: number;
  exon_count: number;
  strand: number;
  biotype?: string | null;
  source?: string | null;
  // Stated per transcript by the annotation itself, so these are known for every gene.
  mane_select?: boolean;
  mane_plus_clinical?: boolean;
  ensembl_canonical?: boolean;
  // Matching RefSeq transcript accessions. Empty for a transcript RefSeq has no
  // equivalent of, which is most of them — GENCODE maps about a sixth.
  refseq_accessions?: string[];
  // CCDS membership: the coding sequence agreed across Ensembl, NCBI and UCSC.
  ccds_id?: string | null;
}

interface GenePanelMembership {
  panel_id: string;
  name: string;
  gene_count: number;
}

interface GeneVariantCounts {
  small_variants: number;
  structural_variants: number;
}

interface GeneSourceStatus {
  // 'not_consulted' means the source was never queried for this gene — not a statement
  // about whether it has anything.
  status: 'success' | 'missing' | 'not_consulted' | 'error';
  fetched_at: string;
  source_url?: string | null;
  message?: string | null;
  // Which issue of the source answered: dbNSFP's version, HGNC's newest modification
  // date, ClinGen's file date. Absent for sources that state no release of their own.
  release?: string | null;
  release_detail?: { label?: string | null; checksum?: string | null; size_bytes?: number | null };
  payload: Record<string, unknown>;
}

// Readable names for the raw source keys, so the provenance strip reads as data sources
// rather than as column names.
const SOURCE_LABELS: Record<string, string> = {
  dbnsfp_gene: 'dbNSFP',
  hgnc_complete_set: 'HGNC',
  clingen_gene_validity: 'ClinGen validity',
  clingen_dosage: 'ClinGen dosage',
  gencc: 'GenCC',
  clinvar_gene_condition: 'ClinVar',
  ncbi: 'NCBI Gene',
};

interface GeneExternalLink {
  label: string;
  href: string;
}

interface GeneAssemblyLocation {
  assembly_id: string;
  assembly_name: string;
  assembly_version?: string | null;
  chr: string;
  start: number;
  end: number;
  transcript_count: number;
  is_primary: boolean;
  is_family_context: boolean;
}

interface GeneClingenCurationCounts {
  gene_disease_validity?: number | null;
  dosage_sensitivity?: number | null;
  clinical_actionability?: number | null;
  variant_pathogenicity?: number | null;
  cpic_pharmgkb?: string | null;
}

interface GeneClingenFacts {
  hgnc_name?: string | null;
  gene_type?: string | null;
  locus_type?: string | null;
  previous_symbols?: string[];
  alias_symbols?: string[];
  gencc_classifications?: Record<string, number>;
  haploinsufficiency_index?: number | null;
  pli?: number | null;
  loeuf?: number | null;
  acmg_secondary_finding?: boolean | null;
  cytoband?: string | null;
  mane_select_transcript?: string | null;
  mane_plus_clinical_transcript?: string | null;
  function?: string | null;
  genomic_coordinates?: Record<string, string>;
}

interface GeneConstraintMetrics {
  missense_z?: number | null;
  shet?: number | null;
  phaplo?: number | null;
  ptriplo?: number | null;
  p_hi?: number | null;
  hipred_score?: number | null;
  ghis?: number | null;
  p_rec?: number | null;
  rvis_evs?: number | null;
  rvis_percentile_evs?: number | null;
  lof_fdr_exac?: number | null;
  rvis_exac?: number | null;
  rvis_percentile_exac?: number | null;
  exac_pli?: number | null;
  exac_prec?: number | null;
  exac_pnull?: number | null;
  gnomad_pli?: number | null;
  gnomad_prec?: number | null;
  gnomad_pnull?: number | null;
  gnomad_lof_oe?: number | null;
  gnomad_mis_oe?: number | null;
  gnomad_loeuf?: number | null;
  gnomad_moeuf?: number | null;
  exac_del_score?: number | null;
  exac_dup_score?: number | null;
  exac_cnv_score?: number | null;
  gdi?: number | null;
  gdi_phred?: number | null;
  loftool_score?: number | null;
  gene_indispensability_score?: number | null;
}

interface GeneOmimDiseaseEntry {
  label?: string | null;
  disease?: string | null;
  name?: string | null;
  omim_id?: string | number | null;
  href?: string | null;
}

interface GeneDbnsfpAssociationEntry {
  label?: string | null;
  disease?: string | null;
  title?: string | null;
  source?: string | null;
  significance?: string | null;
  details?: string | null;
}

interface GeneHpoTerm {
  hpo_id?: string | null;
  label?: string | null;
}

interface MonarchPhenotypeMatch {
  hpo_id: string;
  label?: string | null;
}

interface GeneMonarchAssociation {
  mondo_id: string;
  disease_label?: string | null;
  predicate: string;
  predicates?: string[];
  sources?: string[];
  causal?: boolean;
  monarch_url?: string | null;
  phenotype_count?: number;
  matched_phenotypes?: MonarchPhenotypeMatch[];
}

interface GeneOntology {
  biological_process?: string[];
  cellular_component?: string[];
  molecular_function?: string[];
}

interface GeneDbnsfpPathways {
  uniprot?: string[];
  biocarta_short?: string[];
  biocarta_full?: string[];
  consensus_path_db?: string[];
  kegg_ids?: string[];
  kegg?: string[];
}

interface GeneOrphanetAssertion {
  orphanet_id?: string | null;
  disease_title?: string | null;
  association_type?: string | null;
}

interface GeneGenccAssertion {
  gencc_id?: string | null;
  disease_title?: string | null;
  classification_title?: string | null;
  moi_title?: string | null;
  pmids?: string[];
}

interface GeneClingenDosageAssertion {
  haploinsufficiency?: string | null;
  description?: string | null;
  disease?: string | null;
  pmids?: string[];
}

interface GeneModelOrganism {
  symbol?: string | null;
  phenotypes?: string[];
  structures?: string[];
  phenotype_quality?: string[];
  phenotype_tags?: string[];
}

interface GeneDbnsfpModelOrganisms {
  mouse?: GeneModelOrganism;
  zebrafish?: GeneModelOrganism;
}

interface GeneDbnsfpExpression {
  tissue_specificity?: string | null;
  hpa_consensus_tpm?: Record<string, number>;
  hpa_highly_expressed?: string[];
}

interface GeneProfileExtra {
  hgnc_name?: string | null;
  hgnc_gene_group?: string[];
  ensembl_canonical_transcript?: string | null;
  ensembl_description?: string | null;
  ncbi_other_designations?: string[];
  clingen_curation_counts?: GeneClingenCurationCounts;
  clingen_gene_facts?: GeneClingenFacts;
  // HGNC's identifiers live in their own block, mirroring the complete set. The flat
  // hgnc_vega_id above it is a leftover promotion from the per-gene HGNC REST payload,
  // kept only so caches written before that lookup was removed still render.
  hgnc_identifiers?: {
    vega_id?: string | null;
    ucsc_id?: string | null;
    mgd_id?: string | null;
    ccds_id?: string[];
    uniprot_ids?: string[];
    mane_select?: string[];
  };
  hgnc_vega_id?: string | null;
  refseq_accessions?: string[];
  omim_diseases?: Array<string | GeneOmimDiseaseEntry>;
  dbnsfp_disease_associations?: Array<string | GeneDbnsfpAssociationEntry>;
  hpo_terms?: GeneHpoTerm[];
  dbnsfp_orphanet_assertions?: GeneOrphanetAssertion[];
  dbnsfp_trait_associations?: string[];
  gencc_assertions?: GeneGenccAssertion[];
  clingen_dosage_assertions?: GeneClingenDosageAssertion[];
  gene_ontology?: GeneOntology;
  dbnsfp_pathways?: GeneDbnsfpPathways;
  dbnsfp_tissue_expression?: GeneDbnsfpExpression;
  dbnsfp_model_organisms?: GeneDbnsfpModelOrganisms;
  constraint_metrics?: GeneConstraintMetrics;
}

interface GeneProfile {
  assembly_id: string;
  assembly_name: string;
  assembly_version?: string | null;
  species_name: string;
  symbol: string;
  gene_id: string;
  display_name?: string | null;
  summary?: string | null;
  chr: string;
  start: number;
  end: number;
  strand: number;
  biotype?: string | null;
  transcript_count: number;
  transcripts: GeneTranscript[];
  aliases: string[];
  previous_symbols: string[];
  ensembl_gene_id?: string | null;
  ncbi_gene_id?: string | null;
  hgnc_id?: string | null;
  omim_gene_id?: string | null;
  gene_type?: string | null;
  location?: string | null;
  assembly_locations: GeneAssemblyLocation[];
  panels: GenePanelMembership[];
  family_counts?: GeneVariantCounts | null;
  source_status: Record<string, GeneSourceStatus>;
  // The annotation the loci and transcripts came from, recorded per assembly at import.
  gene_annotation_source?: string | null;
  gene_annotation_imported_at?: string | null;
  external_links: GeneExternalLink[];
  monarch_associations?: GeneMonarchAssociation[];
  extra: GeneProfileExtra;
  updated_at?: string | null;
}

interface DetailRow {
  label: string;
  value: React.ReactNode;
}

interface NamedLink {
  label: string;
  href: string;
}

interface ParsedOmimDisease {
  label: string;
  href?: string;
}

interface ParsedDbnsfpAssociation {
  label: string;
  meta?: string | null;
}

interface NamedStringGroup {
  label: string;
  items: string[];
}

type TranscriptBadgeTone = 'success' | 'warning' | 'accent' | 'strong' | 'neutral';

interface TranscriptBadge {
  label: string;
  tone: TranscriptBadgeTone;
}

interface TranscriptAnnotationContext {
  maneSelect?: string | null;
  manePlusClinical?: string | null;
  ensemblCanonical?: string | null;
  // HGNC-curated RefSeq accessions (the representative / RefSeq Select set).
  refseqSelect?: string[];
}

const formatLocus = (profile: Pick<GeneProfile, 'chr' | 'start' | 'end'>) => {
  const chrom = profile.chr.startsWith('chr') ? profile.chr : `chr${profile.chr}`;
  return `${chrom}:${profile.start.toLocaleString()}-${profile.end.toLocaleString()}`;
};

const formatAssemblyLabel = (
  location: Pick<GeneAssemblyLocation, 'assembly_name' | 'assembly_version'>,
) => `${location.assembly_name}${location.assembly_version ? ` ${location.assembly_version}` : ''}`;

const formatTimestamp = (value?: string | null) => {
  if (!value) return 'Not yet refreshed';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};

const formatPercent = (value?: number | null, digits = 1) =>
  typeof value === 'number' ? `${value.toFixed(digits)}%` : '—';

const formatNumber = (value?: number | null, digits = 2) =>
  typeof value === 'number' ? value.toFixed(digits) : '—';

const formatInteger = (value?: number | null) =>
  typeof value === 'number' ? value.toLocaleString() : '—';

const formatMetricNumber = (value: number, digits?: number) => {
  if (typeof digits === 'number') return value.toFixed(digits);
  if (Math.abs(value) < 0.01 && value !== 0) return value.toFixed(4);
  if (Math.abs(value) < 1) return value.toFixed(3);
  return value.toFixed(2);
};

const formatReadableKey = (value: string) =>
  value
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());

const uniqueStrings = (...groups: Array<string[] | undefined>) =>
  Array.from(
    new Set(
      groups
        .flatMap((group) => group || [])
        .map((entry) => entry.trim())
        .filter(Boolean),
    ),
  );

const compactStrings = (items?: Array<string | null | undefined>, limit?: number) => {
  const values = Array.from(
    new Set((items || []).map((item) => item?.trim()).filter((item): item is string => Boolean(item))),
  );
  return typeof limit === 'number' ? values.slice(0, limit) : values;
};

const normalizeTranscriptId = (value?: string | null) => value?.split('.')[0] || '';

const isSameTranscript = (left?: string | null, right?: string | null) => {
  const normalizedLeft = normalizeTranscriptId(left);
  const normalizedRight = normalizeTranscriptId(right);
  return Boolean(normalizedLeft) && normalizedLeft === normalizedRight;
};

const classifyTranscript = (transcriptId: string) => {
  if (/^ENS/i.test(transcriptId)) return 'Ensembl';
  if (/^(NM_|NR_|XM_|XR_)/i.test(transcriptId)) return 'RefSeq';
  return 'Imported';
};

const matchesAnyTranscript = (transcriptId: string, references?: string[] | null) =>
  Boolean(references?.some((reference) => isSameTranscript(transcriptId, reference)));

/**
 * Badge a transcript from the annotation's own flags first, falling back to matching
 * its id against the gene-level references.
 *
 * The id-matching path alone left these badges almost never shown: the MANE and
 * canonical transcript ids come from a per-gene ClinGen scrape and an Ensembl lookup
 * that only run for genes the local dbNSFP file does not cover — so the well-known
 * genes people look up were exactly the ones with nothing to match against. The
 * annotation states the same facts per transcript, for every gene, with no network call.
 */
const transcriptBadgesFor = (
  transcript: Pick<
    GeneTranscript,
    'transcript_id' | 'mane_select' | 'mane_plus_clinical' | 'ensembl_canonical' | 'ccds_id'
  >,
  context: TranscriptAnnotationContext,
): TranscriptBadge[] => {
  const transcriptId = transcript.transcript_id;
  return [
    transcript.mane_select || isSameTranscript(transcriptId, context.maneSelect)
      ? { label: 'MANE Select', tone: 'success' as const }
      : null,
    transcript.mane_plus_clinical || isSameTranscript(transcriptId, context.manePlusClinical)
      ? { label: 'MANE Plus Clinical', tone: 'accent' as const }
      : null,
    matchesAnyTranscript(transcriptId, context.refseqSelect)
      ? { label: 'RefSeq Select', tone: 'strong' as const }
      : null,
    transcript.ensembl_canonical || isSameTranscript(transcriptId, context.ensemblCanonical)
      ? { label: 'Ensembl Canonical', tone: 'warning' as const }
      : null,
    transcript.ccds_id ? { label: 'CCDS', tone: 'neutral' as const } : null,
  ].filter((badge): badge is TranscriptBadge => Boolean(badge));
};

const ADVANCED_CONSTRAINT_METRICS: Array<{
  key: keyof GeneConstraintMetrics;
  label: string;
  digits?: number;
}> = [
  { key: 'gnomad_lof_oe', label: 'gnomAD LoF OE', digits: 3 },
  { key: 'gnomad_loeuf', label: 'gnomAD LOEUF', digits: 3 },
  { key: 'gnomad_mis_oe', label: 'gnomAD missense OE', digits: 3 },
  { key: 'gnomad_moeuf', label: 'gnomAD MOEUF', digits: 3 },
  { key: 'gnomad_pli', label: 'gnomAD pLI', digits: 3 },
  { key: 'gnomad_prec', label: 'gnomAD pRec', digits: 3 },
  { key: 'gnomad_pnull', label: 'gnomAD pNull', digits: 3 },
  { key: 'p_hi', label: 'DECIPHER P(HI)', digits: 3 },
  { key: 'p_rec', label: 'DECIPHER P(rec)', digits: 3 },
  { key: 'phaplo', label: 'pHaplo', digits: 3 },
  { key: 'ptriplo', label: 'pTriplo', digits: 3 },
  { key: 'shet', label: 'sHet', digits: 3 },
  { key: 'missense_z', label: 'Missense z-score', digits: 2 },
  { key: 'gdi', label: 'GDI', digits: 2 },
  { key: 'gdi_phred', label: 'GDI Phred', digits: 2 },
  { key: 'loftool_score', label: 'LoFtool score', digits: 3 },
  { key: 'hipred_score', label: 'HIPred score', digits: 3 },
  { key: 'ghis', label: 'GHIS', digits: 3 },
  { key: 'rvis_exac', label: 'RVIS ExAC', digits: 3 },
  { key: 'rvis_percentile_exac', label: 'RVIS percentile ExAC', digits: 2 },
  { key: 'rvis_evs', label: 'RVIS EVS', digits: 3 },
  { key: 'rvis_percentile_evs', label: 'RVIS percentile EVS', digits: 2 },
  { key: 'lof_fdr_exac', label: 'LoF FDR ExAC', digits: 3 },
  { key: 'exac_pli', label: 'ExAC pLI', digits: 3 },
  { key: 'exac_prec', label: 'ExAC pRec', digits: 3 },
  { key: 'exac_pnull', label: 'ExAC pNull', digits: 3 },
  { key: 'exac_del_score', label: 'ExAC deletion score', digits: 3 },
  { key: 'exac_dup_score', label: 'ExAC duplication score', digits: 3 },
  { key: 'exac_cnv_score', label: 'ExAC CNV score', digits: 3 },
  { key: 'gene_indispensability_score', label: 'Gene indispensability score', digits: 3 },
];

const buildAdvancedConstraintRows = (metrics?: GeneConstraintMetrics | null): DetailRow[] =>
  ADVANCED_CONSTRAINT_METRICS.flatMap(({ key, label, digits }) => {
    const value = metrics?.[key];
    return typeof value === 'number'
      ? [{ label, value: formatMetricNumber(value, digits) }]
      : [];
  });

const pickAssemblyLocation = (
  locations: GeneAssemblyLocation[],
  matcher: (location: GeneAssemblyLocation) => boolean,
) => locations.find((location) => matcher(location)) || null;

const formatAssemblyCoordinate = (
  location: GeneAssemblyLocation | null,
  fallback?: string | null,
) => {
  if (location) return `${formatLocus(location)} (${formatAssemblyLabel(location)})`;
  if (fallback) return fallback;
  return 'Not imported';
};

const MONARCH_PREDICATE_LABELS: Record<string, string> = {
  causes: 'Causes',
  gene_associated_with_condition: 'Associated',
  contributes_to: 'Contributes to',
  associated_with_increased_likelihood_of: 'Increased likelihood',
};

const formatMonarchPredicate = (predicate: string): string =>
  MONARCH_PREDICATE_LABELS[predicate] ||
  predicate.replace(/_/g, ' ').replace(/^\w/, (char) => char.toUpperCase());

const extractOmimDiseases = (profile: GeneProfile): ParsedOmimDisease[] => {
  const rawEntries = Array.isArray(profile.extra.omim_diseases) ? profile.extra.omim_diseases : [];

  return rawEntries
    .map((entry) => {
      if (typeof entry === 'string') {
        return { label: entry.trim() };
      }

      const label =
        entry.label?.trim() ||
        entry.disease?.trim() ||
        entry.name?.trim() ||
        (entry.omim_id ? `OMIM ${entry.omim_id}` : '');
      if (!label) return null;

      return {
        label,
        href:
          entry.href?.trim() ||
          (entry.omim_id ? `https://www.omim.org/entry/${entry.omim_id}` : undefined),
      };
    })
    .filter((entry): entry is ParsedOmimDisease => Boolean(entry?.label));
};

const extractDbnsfpAssociations = (profile: GeneProfile): ParsedDbnsfpAssociation[] => {
  const rawEntries = Array.isArray(profile.extra.dbnsfp_disease_associations)
    ? profile.extra.dbnsfp_disease_associations
    : [];

  return rawEntries
    .map((entry) => {
      if (typeof entry === 'string') {
        return entry.trim() ? { label: entry.trim() } : null;
      }

      const label = entry.label?.trim() || entry.disease?.trim() || entry.title?.trim() || '';
      if (!label) return null;

      const meta = [entry.source, entry.significance, entry.details]
        .map((value) => value?.trim())
        .filter(Boolean)
        .join(' · ');

      return {
        label,
        meta: meta || null,
      };
    })
    .filter((entry): entry is ParsedDbnsfpAssociation => Boolean(entry?.label));
};

const buildGeneLinks = (profile: GeneProfile): NamedLink[] => {
  const byLabel = new Map(
    profile.external_links.map((link) => [link.label.toLowerCase(), link.href]),
  );
  const links: NamedLink[] = [];
  const seen = new Set<string>();
  const symbolQuery = encodeURIComponent(profile.symbol);
  const omimGeneHref =
    byLabel.get('omim') ||
    (profile.omim_gene_id ? `https://www.omim.org/entry/${profile.omim_gene_id}` : null);
  const ncbiGeneHref =
    byLabel.get('ncbi gene') ||
    (profile.ncbi_gene_id
      ? `https://www.ncbi.nlm.nih.gov/gene/${encodeURIComponent(profile.ncbi_gene_id)}`
      : null);
  const proteinAtlasHref = profile.ensembl_gene_id
    ? `https://www.proteinatlas.org/${encodeURIComponent(profile.ensembl_gene_id)}-${encodeURIComponent(
        profile.symbol,
      )}`
    : `https://www.proteinatlas.org/search/${symbolQuery}`;
  const gnomadHref =
    byLabel.get('gnomad') ||
    (profile.ensembl_gene_id
      ? `https://gnomad.broadinstitute.org/gene/${encodeURIComponent(profile.ensembl_gene_id)}`
      : null);
  const keggHref = profile.ncbi_gene_id
    ? `https://www.genome.jp/dbget-bin/www_bget?hsa:${encodeURIComponent(profile.ncbi_gene_id)}`
    : `https://www.genome.jp/kegg-bin/search?keyword=${symbolQuery}`;

  const pushLink = (label: string, href?: string | null) => {
    if (!href || seen.has(label)) return;
    links.push({ label, href });
    seen.add(label);
  };

  pushLink('OMIM', omimGeneHref);
  pushLink('PubMed', byLabel.get('pubmed'));
  pushLink('GeneCards', byLabel.get('genecards'));
  pushLink('Protein Atlas', proteinAtlasHref);
  pushLink('NCBI Gene', ncbiGeneHref);
  pushLink('GTEx Portal', byLabel.get('gtex') || byLabel.get('gtex portal'));
  // Monarch addresses nodes by CURIE, and a gene node is its HGNC id — the same shape
  // the disease associations already link with (monarchinitiative.org/MONDO:…). Without
  // it this was a free-text search that lands on a result list rather than the gene.
  pushLink(
    'Monarch',
    profile.hgnc_id
      ? `https://monarchinitiative.org/${profile.hgnc_id}`
      : `https://monarchinitiative.org/search?q=${symbolQuery}`,
  );
  pushLink('DECIPHER', byLabel.get('decipher'));
  pushLink('UniProt', byLabel.get('uniprot'));
  pushLink('Geno2MP', 'https://geno2mp.gs.washington.edu/Geno2MP/');
  pushLink('gnomAD', gnomadHref);
  // MGI catalogues the mouse, so the useful destination is the ortholog's marker page,
  // which HGNC's mgd_id addresses directly. The old searchtool endpoint is dead — it
  // answers every query with the MGI homepage — so the fallback is quicksearch, which
  // does still run the search for the genes that have no mouse ortholog.
  const mgdId = profile.extra.hgnc_identifiers?.mgd_id?.trim();
  pushLink(
    'MGI',
    mgdId
      ? `https://www.informatics.jax.org/marker/${mgdId}`
      : `https://www.informatics.jax.org/quicksearch/summary?query=${symbolQuery}`,
  );
  pushLink('IMPC', `https://www.mousephenotype.org/data/search?term=${symbolQuery}`);
  pushLink('KEGG', keggHref);
  pushLink('ClinGen', byLabel.get('clingen'));
  pushLink('ClinVar', byLabel.get('clinvar'));
  return links;
};

const DetailList: React.FC<{ rows: DetailRow[] }> = ({ rows }) => (
  <dl className="gene-compact-detail-list">
    {rows.map((row) => (
      <div key={row.label} className="gene-compact-detail-row">
        <dt>{row.label}</dt>
        <dd>{row.value}</dd>
      </div>
    ))}
  </dl>
);

const PubmedLinks: React.FC<{ pmids?: string[] }> = ({ pmids }) => {
  const visiblePmids = compactStrings(pmids, 4);
  if (!visiblePmids.length) return null;

  return (
    <span className="gene-compact-inline-links">
      {visiblePmids.map((pmid) => (
        <a
          key={pmid}
          href={`https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(pmid)}/`}
          target="_blank"
          rel="noreferrer"
          className="gene-compact-link"
        >
          PMID {pmid}
        </a>
      ))}
    </span>
  );
};

const CompactStringGroup: React.FC<{
  group: NamedStringGroup;
  expanded: boolean;
  limit?: number;
}> = ({ group, expanded, limit = 4 }) => {
  const visibleItems = expanded ? group.items : group.items.slice(0, limit);
  if (!visibleItems.length) return null;

  return (
    <div className="gene-compact-subsection">
      <p className="gene-compact-subtitle-label">{group.label}</p>
      <div className="gene-compact-chip-grid">
        {visibleItems.map((item) => (
          <span key={`${group.label}-${item}`} className="gene-compact-term-chip">
            {item}
          </span>
        ))}
      </div>
    </div>
  );
};

const GeneInfoPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();

  const familyId = searchParams.get('family_id') || undefined;
  const projectIdParam = searchParams.get('project_id') || undefined;
  const geneParam = searchParams.get('gene') || '';
  const assemblyParam = searchParams.get('assembly_id') || '';

  const [draftGene, setDraftGene] = useState(geneParam);
  const [transcriptsExpanded, setTranscriptsExpanded] = useState(false);
  const [diseaseEvidenceExpanded, setDiseaseEvidenceExpanded] = useState(false);
  const [advancedConstraintExpanded, setAdvancedConstraintExpanded] = useState(false);
  const [functionContextExpanded, setFunctionContextExpanded] = useState(false);

  useEffect(() => {
    setDraftGene(geneParam);
  }, [geneParam]);

  useEffect(() => {
    setTranscriptsExpanded(false);
    setDiseaseEvidenceExpanded(false);
    setAdvancedConstraintExpanded(false);
    setFunctionContextExpanded(false);
  }, [geneParam]);

  const { data: family } = useQuery<Pick<ApiFamilyRecord, 'projects'>>({
    queryKey: ['family', familyId],
    enabled: !!familyId,
    queryFn: async () => {
      const response = await api.get(`/families/${familyId}`);
      return response.data as Pick<ApiFamilyRecord, 'projects'>;
    },
  });

  const familyReference = useFamilyReference(
    family?.projects as string[] | undefined,
    projectIdParam,
  );
  const resolvedProjectId = familyId
    ? familyReference.projectId || undefined
    : projectIdParam || undefined;

  const { data: suggestions = [] } = useQuery<GeneSuggestion[]>({
    queryKey: ['gene-search', draftGene],
    enabled: draftGene.trim().length >= 2,
    queryFn: async () => {
      const response = await api.get('/genes/search', {
        params: { q: draftGene.trim() },
      });
      return response.data as GeneSuggestion[];
    },
  });

  const { data: profile, isLoading } = useQuery<GeneProfile>({
    queryKey: ['gene-profile', geneParam, assemblyParam, familyId, resolvedProjectId],
    enabled: geneParam.trim().length > 0,
    queryFn: async () => {
      const response = await api.get('/genes/profile', {
        params: {
          symbol: geneParam.trim(),
          assembly_id: assemblyParam || undefined,
          family_id: familyId,
          project_id: resolvedProjectId,
        },
      });
      return response.data as GeneProfile;
    },
  });

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const next = new URLSearchParams(searchParams);
    const normalizedGene = draftGene.trim();
    if (normalizedGene) {
      next.set('gene', normalizedGene);
    } else {
      next.delete('gene');
    }
    next.delete('assembly_id');
    setSearchParams(next);
  };

  const activeSuggestion = useMemo(
    () =>
      suggestions.find(
        (entry) => entry.symbol.toLowerCase() === draftGene.trim().toLowerCase(),
      ),
    [draftGene, suggestions],
  );

  const familyLinks = useMemo(() => {
    if (!profile || !familyId) return null;
    const familyAssemblyLocation =
      profile.assembly_locations.find((location) => location.is_family_context) || profile;
    const projectQuery = resolvedProjectId
      ? `&project_id=${encodeURIComponent(resolvedProjectId)}`
      : '';
    return {
      smallVariants: `/families/${familyId}/small-variants?gene=${encodeURIComponent(profile.symbol)}${projectQuery}`,
      structuralVariants: `/families/${familyId}/structural-variants?gene=${encodeURIComponent(profile.symbol)}${projectQuery}`,
      chromosomeView: `/families/${familyId}/chromosome/${familyAssemblyLocation.chr}?start=${familyAssemblyLocation.start}&end=${familyAssemblyLocation.end}${projectQuery}`,
    };
  }, [familyId, profile, resolvedProjectId]);

  const clingenFacts = profile?.extra.clingen_gene_facts;
  const curationCounts = profile?.extra.clingen_curation_counts;
  const aliasSymbols = uniqueStrings(profile?.aliases, clingenFacts?.alias_symbols);
  const previousSymbols = uniqueStrings(
    profile?.previous_symbols,
    clingenFacts?.previous_symbols,
  );
  const hg38Location = profile
    ? formatAssemblyCoordinate(
        pickAssemblyLocation(
          profile.assembly_locations,
          (location) => /grch38|hg38/i.test(formatAssemblyLabel(location)),
        ),
        clingenFacts?.genomic_coordinates?.['GRCh38/hg38'],
      )
    : '—';
  const t2tLocation = profile
    ? formatAssemblyCoordinate(
        pickAssemblyLocation(
          profile.assembly_locations,
          (location) => /t2t|chm13/i.test(formatAssemblyLabel(location)),
        ),
      )
    : '—';
  const omimDiseases = useMemo(
    () => (profile ? extractOmimDiseases(profile) : []),
    [profile],
  );
  const dbnsfpAssociations = useMemo(
    () => (profile ? extractDbnsfpAssociations(profile) : []),
    [profile],
  );
  const monarchAssociations = useMemo<GeneMonarchAssociation[]>(
    () => profile?.monarch_associations || [],
    [profile],
  );
  const compactLinks = useMemo(() => (profile ? buildGeneLinks(profile) : []), [profile]);
  const sourceEntries = useMemo(
    () => (profile ? Object.entries(profile.source_status) : []),
    [profile],
  );
  const constraintMetrics = profile?.extra.constraint_metrics;
  const hpoTerms = useMemo<GeneHpoTerm[]>(() => {
    const seen = new Set<string>();
    return (profile?.extra.hpo_terms || []).filter((term) => {
      const label = term.label?.trim() || '';
      const hpoId = term.hpo_id?.trim() || '';
      if (!label && !hpoId) return false;
      const key = `${hpoId}-${label}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [profile]);
  const orphanetAssertions = useMemo<GeneOrphanetAssertion[]>(
    () =>
      (profile?.extra.dbnsfp_orphanet_assertions || []).filter(
        (entry) =>
          Boolean(entry.disease_title?.trim()) ||
          Boolean(entry.orphanet_id?.trim()) ||
          Boolean(entry.association_type?.trim()),
      ),
    [profile],
  );
  const genccAssertions = useMemo<GeneGenccAssertion[]>(
    () =>
      (profile?.extra.gencc_assertions || []).filter(
        (entry) =>
          Boolean(entry.disease_title?.trim()) ||
          Boolean(entry.classification_title?.trim()) ||
          Boolean(entry.moi_title?.trim()),
      ),
    [profile],
  );
  const clingenDosageAssertions = useMemo<GeneClingenDosageAssertion[]>(
    () =>
      (profile?.extra.clingen_dosage_assertions || []).filter(
        (entry) =>
          Boolean(entry.haploinsufficiency?.trim()) ||
          Boolean(entry.description?.trim()) ||
          Boolean(entry.disease?.trim()) ||
          Boolean(entry.pmids?.length),
      ),
    [profile],
  );
  const traitAssociations = useMemo(
    () => compactStrings(profile?.extra.dbnsfp_trait_associations),
    [profile],
  );
  const advancedConstraintRows = useMemo(
    () => buildAdvancedConstraintRows(constraintMetrics),
    [constraintMetrics],
  );
  const ontologyGroups = useMemo<NamedStringGroup[]>(() => {
    const ontology = profile?.extra.gene_ontology;
    return [
      { label: 'Biological process', items: compactStrings(ontology?.biological_process) },
      { label: 'Cellular component', items: compactStrings(ontology?.cellular_component) },
      { label: 'Molecular function', items: compactStrings(ontology?.molecular_function) },
    ].filter((group) => group.items.length);
  }, [profile]);
  const pathwayGroups = useMemo<NamedStringGroup[]>(() => {
    const pathways = profile?.extra.dbnsfp_pathways;
    return [
      { label: 'UniProt pathways', items: compactStrings(pathways?.uniprot) },
      { label: 'BioCarta pathways', items: compactStrings(pathways?.biocarta_full) },
      { label: 'ConsensusPathDB', items: compactStrings(pathways?.consensus_path_db) },
      { label: 'KEGG pathways', items: compactStrings(pathways?.kegg) },
      { label: 'KEGG IDs', items: compactStrings(pathways?.kegg_ids) },
    ].filter((group) => group.items.length);
  }, [profile]);
  const tissueExpression = profile?.extra.dbnsfp_tissue_expression;
  const highlightedTissues = useMemo(
    () => compactStrings(tissueExpression?.hpa_highly_expressed),
    [tissueExpression],
  );
  const topTissueExpression = useMemo(
    () =>
      Object.entries(tissueExpression?.hpa_consensus_tpm || {})
        .filter((entry): entry is [string, number] => Number.isFinite(entry[1]))
        .sort(([leftName, leftValue], [rightName, rightValue]) =>
          rightValue === leftValue ? leftName.localeCompare(rightName) : rightValue - leftValue,
        )
        .slice(0, 5),
    [tissueExpression],
  );
  const modelOrganismRows = useMemo<
    Array<{ label: string; symbol?: string | null; details: string[] }>
  >(() => {
    const modelOrganisms = profile?.extra.dbnsfp_model_organisms;
    const mouse = modelOrganisms?.mouse;
    const zebrafish = modelOrganisms?.zebrafish;
    return [
      {
        label: 'Mouse',
        symbol: mouse?.symbol,
        details: compactStrings(mouse?.phenotypes),
      },
      {
        label: 'Zebrafish',
        symbol: zebrafish?.symbol,
        details: compactStrings([
          ...(zebrafish?.structures || []),
          ...(zebrafish?.phenotype_quality || []),
          ...(zebrafish?.phenotype_tags || []),
        ]),
      },
    ].filter((row) => Boolean(row.symbol?.trim()) || row.details.length);
  }, [profile]);
  const clingenHaploIndex =
    clingenFacts?.haploinsufficiency_index ??
    (typeof constraintMetrics?.p_hi === 'number' ? constraintMetrics.p_hi * 100 : undefined);
  const gnomadPli = clingenFacts?.pli ?? constraintMetrics?.gnomad_pli ?? constraintMetrics?.exac_pli;
  const gnomadLoeuf = clingenFacts?.loeuf ?? constraintMetrics?.gnomad_loeuf;
  const diseaseEvidenceCanExpand =
    hpoTerms.length > 8 ||
    orphanetAssertions.length > 3 ||
    genccAssertions.length > 0 ||
    traitAssociations.length > 0;
  const functionContextCanExpand =
    pathwayGroups.some((group) => group.items.length > 4) ||
    ontologyGroups.some((group) => group.items.length > 4) ||
    highlightedTissues.length > 6 ||
    modelOrganismRows.some((row) => row.details.length > 4);
  const maxTissueTpm = Math.max(...topTissueExpression.map(([, value]) => value), 0);

  const overviewRows = useMemo<DetailRow[]>(
    () =>
      profile
        ? [
            { label: 'hg38 locus', value: hg38Location },
            { label: 'T2T locus', value: t2tLocation },
            { label: 'Cytoband', value: clingenFacts?.cytoband || profile.location || '—' },
            {
              label: 'OMIM gene',
              value: profile.omim_gene_id ? (
                <a
                  href={`https://www.omim.org/entry/${encodeURIComponent(profile.omim_gene_id)}`}
                  target="_blank"
                  rel="noreferrer"
                  className="gene-compact-link"
                >
                  {profile.omim_gene_id}
                </a>
              ) : (
                '—'
              ),
            },
            {
              label: 'Local panels',
              value: profile.panels.length ? (
                <span className="gene-compact-inline-links">
                  {profile.panels.map((panel) => (
                    <Link
                      key={panel.panel_id}
                      to={`/panels/${panel.panel_id}`}
                      className="gene-compact-link"
                    >
                      {panel.name}
                    </Link>
                  ))}
                </span>
              ) : (
                '—'
              ),
            },
            {
              label: 'Vega',
              value:
                profile.extra.hgnc_identifiers?.vega_id || profile.extra.hgnc_vega_id || '—',
            },
            {
              label: 'Canonical transcript',
              value:
                profile.transcripts.find((transcript) => transcript.ensembl_canonical)
                  ?.transcript_id ||
                profile.extra.ensembl_canonical_transcript ||
                '—',
            },
            {
              label: 'MANE transcript',
              value:
                profile.transcripts.find((transcript) => transcript.mane_select)?.transcript_id ||
                clingenFacts?.mane_select_transcript ||
                '—',
            },
            // Rare — 74 transcripts genome-wide — but when a gene has one it is the
            // clinically important alternative, so it belongs beside MANE Select rather
            // than only as a badge further down the page.
            {
              label: 'MANE Plus Clinical',
              value:
                profile.transcripts.find((transcript) => transcript.mane_plus_clinical)
                  ?.transcript_id || '—',
            },
          ]
        : [],
    [clingenFacts?.cytoband, clingenFacts?.mane_select_transcript, hg38Location, profile, t2tLocation],
  );

  const genccRows = useMemo<DetailRow[]>(
    () =>
      profile
        ? [
            {
              label: 'Gene-disease validity',
              value: formatInteger(curationCounts?.gene_disease_validity),
            },
            {
              label: 'Dosage sensitivity',
              value: formatInteger(curationCounts?.dosage_sensitivity),
            },
            {
              label: 'Clinical actionability',
              value: formatInteger(curationCounts?.clinical_actionability),
            },
            {
              label: 'Variant pathogenicity',
              value: formatInteger(curationCounts?.variant_pathogenicity),
            },
            {
              label: 'ACMG secondary finding',
              value:
                clingenFacts?.acmg_secondary_finding === true
                  ? 'Yes'
                  : clingenFacts?.acmg_secondary_finding === false
                    ? 'No'
                    : '—',
            },
          ]
        : [],
    [clingenFacts?.acmg_secondary_finding, curationCounts, profile],
  );

  const constraintRows = useMemo<DetailRow[]>(
    () =>
      profile
        ? [
            {
              label: 'DECIPHER %HI',
              value: formatPercent(clingenHaploIndex),
            },
            {
              label: 'gnomAD pLI',
              value: formatNumber(gnomadPli),
            },
            {
              label: 'gnomAD LOEUF',
              value: formatNumber(gnomadLoeuf),
            },
            {
              label: 'Missense z-score',
              value: formatNumber(constraintMetrics?.missense_z),
            },
            { label: 'sHet', value: formatNumber(constraintMetrics?.shet, 3) },
            { label: 'pHaplo', value: formatNumber(constraintMetrics?.phaplo, 3) },
            { label: 'pTriplo', value: formatNumber(constraintMetrics?.ptriplo, 3) },
          ]
        : [],
    [clingenHaploIndex, constraintMetrics, gnomadLoeuf, gnomadPli, profile],
  );

  const transcriptBadgeContext = useMemo<TranscriptAnnotationContext>(
    () => ({
      maneSelect: clingenFacts?.mane_select_transcript,
      manePlusClinical: clingenFacts?.mane_plus_clinical_transcript,
      ensemblCanonical: profile?.extra.ensembl_canonical_transcript,
      refseqSelect: profile?.extra.refseq_accessions,
    }),
    [
      clingenFacts?.mane_select_transcript,
      clingenFacts?.mane_plus_clinical_transcript,
      profile?.extra.ensembl_canonical_transcript,
      profile?.extra.refseq_accessions,
    ],
  );

  const orderedTranscripts = useMemo(() => {
    if (!profile) return [];

    return [...profile.transcripts].sort((left, right) => {
      const score = (transcript: GeneTranscript) => {
        // Surface clinically annotated transcripts first (MANE Select, MANE
        // Plus Clinical, RefSeq Select, Ensembl Canonical), then by source.
        const annotated =
          transcriptBadgesFor(transcript, transcriptBadgeContext).length > 0 ? 0 : 1;
        const mane =
          transcript.mane_select ||
          isSameTranscript(transcript.transcript_id, transcriptBadgeContext.maneSelect)
            ? 0
            : 1;
        const sourceRank = /^ENS/i.test(transcript.transcript_id)
          ? 0
          : /^(NM_|NR_|XM_|XR_)/i.test(transcript.transcript_id)
            ? 1
            : 2;
        return { annotated, mane, sourceRank, transcriptId: transcript.transcript_id };
      };

      const leftScore = score(left);
      const rightScore = score(right);
      return (
        leftScore.annotated - rightScore.annotated ||
        leftScore.mane - rightScore.mane ||
        leftScore.sourceRank - rightScore.sourceRank ||
        leftScore.transcriptId.localeCompare(rightScore.transcriptId)
      );
    });
  }, [profile, transcriptBadgeContext]);

  if (isLoading) {
    return (
      <PageState
        kicker="Reference"
        title="Loading gene explorer"
        message="Resolving cached gene annotations, curated evidence, and assembly-aware context."
      />
    );
  }

  return (
    <div className="page-shell gene-compact-page">
      <section className="surface-card page-top-card gene-compact-header">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Genes</p>
            <h1 className="catalog-card-title">Gene Explorer</h1>
            <p className="page-copy">
              Search a human gene and open a compact reference summary with local panels, disease
              evidence, transcripts, and direct resource links.
            </p>
          </div>
        </div>

        <form className="gene-compact-search" onSubmit={handleSubmit}>
          <label className="field-label">
            <input
              aria-label="Gene symbol"
              value={draftGene}
              onChange={(event) => setDraftGene(event.target.value)}
              placeholder="BRCA1"
              list="gene-suggestion-list"
            />
            <datalist id="gene-suggestion-list">
              {suggestions.map((suggestion) => (
                <option
                  key={`${suggestion.symbol}-${suggestion.gene_id}`}
                  value={suggestion.symbol}
                >
                  {`${suggestion.symbol} • ${suggestion.assembly_count} assemblies`}
                </option>
              ))}
            </datalist>
          </label>
          <button type="submit" className="form-button">
            Open gene
          </button>
        </form>

        {(activeSuggestion || familyId) && (
          <div className="gene-compact-header-meta">
            {activeSuggestion ? (
              <p className="dashboard-link-note">
                {activeSuggestion.symbol} is available in {activeSuggestion.assembly_count} imported
                human assemblies.
              </p>
            ) : null}
            {familyId ? (
              <p className="dashboard-link-note">
                Family context is active for <strong>{familyId}</strong>.
              </p>
            ) : null}
          </div>
        )}
      </section>

      {!profile ? (
        <PageState
          kicker="Gene"
          title="Select a human gene"
          message="Open a symbol to inspect cached overview data, OMIM and GenCC context, transcript models, and curated link-outs."
        />
      ) : (
        <>
          <section className="gene-compact-section gene-compact-section--overview">
            <div className="gene-compact-title-row">
              <div className="gene-compact-title-block">
                <h2 className="gene-compact-gene-title">{profile.symbol}</h2>
                <p className="gene-compact-gene-subtitle">
                  {profile.display_name || clingenFacts?.hgnc_name || 'No descriptive name cached.'}
                </p>
                <p className="gene-compact-locus-line">
                  hg38 {hg38Location} · T2T {t2tLocation} · {clingenFacts?.cytoband || profile.location || 'No cytoband cached'}
                </p>
              </div>

              <div className="gene-compact-title-meta">
                <span className="gene-compact-meta-chip">
                  {profile.gene_type || clingenFacts?.gene_type || profile.biotype || 'Gene'}
                </span>
                <span className="gene-compact-meta-chip">
                  {profile.transcript_count} transcript{profile.transcript_count === 1 ? '' : 's'}
                </span>
                <span className="gene-compact-meta-chip">
                  {profile.panels.length} panel{profile.panels.length === 1 ? '' : 's'}
                </span>
              </div>
            </div>

            {aliasSymbols.length ? (
              <p className="gene-compact-alias-line">
                <span>Aliases</span>
                {aliasSymbols.join(', ')}
                {previousSymbols.length ? ` · Previous: ${previousSymbols.join(', ')}` : ''}
              </p>
            ) : null}

            <p className="gene-compact-summary">
              {profile.summary ||
                clingenFacts?.function ||
                profile.extra.ensembl_description ||
                'No cached summary is available for this gene yet.'}
            </p>

            <div className="gene-compact-columns">
              <div className="gene-compact-block">
                <h3>Overview</h3>
                <DetailList rows={overviewRows} />
              </div>

              <div className="gene-compact-block">
                <h3>Context</h3>
                <dl className="gene-compact-detail-list">
                  <div className="gene-compact-detail-row">
                    <dt>GenCC classifications</dt>
                    <dd>
                      {Object.entries(clingenFacts?.gencc_classifications || {}).length ? (
                        <span className="gene-compact-inline-links">
                          {Object.entries(clingenFacts?.gencc_classifications || {}).map(
                            ([label, count]) => (
                              <span key={label} className="gene-compact-token">
                                {label} {count}
                              </span>
                            ),
                          )}
                        </span>
                      ) : (
                        '—'
                      )}
                    </dd>
                  </div>
                  <div className="gene-compact-detail-row">
                    <dt>RefSeq accessions</dt>
                    <dd>
                      {profile.extra.refseq_accessions?.length
                        ? profile.extra.refseq_accessions.join(', ')
                        : '—'}
                    </dd>
                  </div>
                  <div className="gene-compact-detail-row">
                    <dt>Family overlap</dt>
                    <dd>
                      {profile.family_counts ? (
                        <>
                          {profile.family_counts.small_variants} small variants ·{' '}
                          {profile.family_counts.structural_variants} structural variants
                        </>
                      ) : (
                        'No family context open'
                      )}
                    </dd>
                  </div>
                  <div className="gene-compact-detail-row">
                    <dt>Family links</dt>
                    <dd>
                      {familyLinks ? (
                        <span className="gene-compact-inline-links">
                          <Link to={familyLinks.smallVariants} className="gene-compact-link">
                            Small variants
                          </Link>
                          <Link to={familyLinks.structuralVariants} className="gene-compact-link">
                            Structural variants
                          </Link>
                          <Link to={familyLinks.chromosomeView} className="gene-compact-link">
                            Chromosome view
                          </Link>
                        </span>
                      ) : (
                        '—'
                      )}
                    </dd>
                  </div>
                </dl>
              </div>
            </div>
          </section>

          <section className="gene-compact-section">
            <div className="gene-compact-columns">
              <div className="gene-compact-block">
                <h3>Disease associations</h3>

                <div className="gene-compact-subsection">
                  <p className="gene-compact-subtitle-label">OMIM disorders</p>
                  {omimDiseases.length ? (
                    <ul className="gene-compact-list">
                      {omimDiseases.map((disease) => (
                        <li key={`${disease.label}-${disease.href || 'omim'}`}>
                          {disease.href ? (
                            <a
                              href={disease.href}
                              target="_blank"
                              rel="noreferrer"
                              className="gene-compact-link"
                            >
                              {disease.label}
                            </a>
                          ) : (
                            disease.label
                          )}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="gene-compact-empty">No OMIM disorders cached for this gene.</p>
                  )}
                </div>

                <div className="gene-compact-subsection">
                  <p className="gene-compact-subtitle-label">dbNSFP disease associations</p>
                  {dbnsfpAssociations.length ? (
                    <ul className="gene-compact-list">
                      {dbnsfpAssociations.map((entry) => (
                        <li key={`${entry.label}-${entry.meta || 'dbnsfp'}`}>
                          <span>{entry.label}</span>
                          {entry.meta ? (
                            <span className="gene-compact-list-meta">{entry.meta}</span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="gene-compact-empty">
                      No dbNSFP disease association summary is cached for this gene.
                    </p>
                  )}
                </div>

                <div className="gene-compact-subsection">
                  <p className="gene-compact-subtitle-label">Monarch gene–disease associations</p>
                  {monarchAssociations.length ? (
                    <ul className="gene-compact-list">
                      {monarchAssociations
                        .slice(0, diseaseEvidenceExpanded ? monarchAssociations.length : 6)
                        .map((entry) => {
                          const matched = entry.matched_phenotypes || [];
                          const meta = [
                            formatMonarchPredicate(entry.predicate),
                            entry.sources?.length ? entry.sources.join(', ') : null,
                            entry.phenotype_count
                              ? `${entry.phenotype_count} phenotypes`
                              : null,
                            matched.length ? `${matched.length} observed in family` : null,
                          ]
                            .filter(Boolean)
                            .join(' · ');
                          return (
                            <li key={`${entry.mondo_id}`}>
                              {entry.monarch_url ? (
                                <a
                                  href={entry.monarch_url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="gene-compact-link"
                                >
                                  {entry.disease_label || entry.mondo_id}
                                </a>
                              ) : (
                                <span>{entry.disease_label || entry.mondo_id}</span>
                              )}
                              {meta ? <span className="gene-compact-list-meta">{meta}</span> : null}
                              {matched.length ? (
                                <div className="gene-compact-chip-grid">
                                  {matched.map((match) => (
                                    <span
                                      key={match.hpo_id}
                                      className="gene-compact-term-chip"
                                      title={`${match.label || match.hpo_id} observed in this family`}
                                    >
                                      {match.label || match.hpo_id}
                                    </span>
                                  ))}
                                </div>
                              ) : null}
                            </li>
                          );
                        })}
                    </ul>
                  ) : (
                    <p className="gene-compact-empty">
                      No Monarch gene–disease associations cached for this gene.
                    </p>
                  )}
                </div>

                {hpoTerms.length ? (
                  <div className="gene-compact-subsection">
                    <p className="gene-compact-subtitle-label">HPO phenotypes</p>
                    <div className="gene-compact-chip-grid">
                      {hpoTerms
                        .slice(0, diseaseEvidenceExpanded ? hpoTerms.length : 8)
                        .map((term) => (
                          <span
                            key={`${term.hpo_id || 'hpo'}-${term.label || 'term'}`}
                            className="gene-compact-term-chip"
                          >
                            {term.label || term.hpo_id}
                            {term.label && term.hpo_id ? <span>{term.hpo_id}</span> : null}
                          </span>
                        ))}
                    </div>
                  </div>
                ) : null}

                {orphanetAssertions.length ? (
                  <div className="gene-compact-subsection">
                    <p className="gene-compact-subtitle-label">Orphanet assertions</p>
                    <ul className="gene-compact-list">
                      {orphanetAssertions
                        .slice(0, diseaseEvidenceExpanded ? orphanetAssertions.length : 3)
                        .map((entry, index) => {
                          const meta = [
                            entry.association_type,
                            entry.orphanet_id ? `Orphanet ${entry.orphanet_id}` : null,
                          ]
                            .filter(Boolean)
                            .join(' · ');
                          return (
                            <li
                              key={`${entry.orphanet_id || index}-${entry.disease_title || 'orphanet'}`}
                            >
                              <span>{entry.disease_title || entry.orphanet_id}</span>
                              {meta ? <span className="gene-compact-list-meta">{meta}</span> : null}
                            </li>
                          );
                        })}
                    </ul>
                  </div>
                ) : null}

                {diseaseEvidenceExpanded ? (
                  <div id="gene-disease-expanded" className="gene-compact-advanced-block">
                    {genccAssertions.length ? (
                      <div className="gene-compact-subsection">
                        <p className="gene-compact-subtitle-label">GenCC assertions</p>
                        <ul className="gene-compact-list">
                          {genccAssertions.map((entry, index) => {
                            const meta = [
                              entry.classification_title,
                              entry.moi_title,
                              entry.gencc_id ? `GenCC ${entry.gencc_id}` : null,
                            ]
                              .filter(Boolean)
                              .join(' · ');
                            return (
                              <li
                                key={`${entry.gencc_id || index}-${entry.disease_title || 'gencc'}`}
                              >
                                <span>{entry.disease_title || entry.classification_title}</span>
                                {meta ? (
                                  <span className="gene-compact-list-meta">{meta}</span>
                                ) : null}
                                <PubmedLinks pmids={entry.pmids} />
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    ) : null}

                    {traitAssociations.length ? (
                      <div className="gene-compact-subsection">
                        <p className="gene-compact-subtitle-label">GWAS traits</p>
                        <div className="gene-compact-chip-grid">
                          {traitAssociations.map((trait) => (
                            <span key={trait} className="gene-compact-term-chip">
                              {trait}
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
                ) : null}

                {diseaseEvidenceCanExpand ? (
                  <button
                    type="button"
                    className="button-secondary gene-compact-collapse-button"
                    aria-expanded={diseaseEvidenceExpanded}
                    aria-controls="gene-disease-expanded"
                    onClick={() => setDiseaseEvidenceExpanded((expanded) => !expanded)}
                  >
                    {diseaseEvidenceExpanded ? 'Show less' : 'Show more'} disease evidence
                  </button>
                ) : null}

                <div className="gene-compact-subsection">
                  <p className="gene-compact-subtitle-label">GenCC details</p>
                  <DetailList rows={genccRows} />
                </div>
              </div>

              <div className="gene-compact-block">
                <h3>Constraint and dosage</h3>
                <DetailList rows={constraintRows} />

                {clingenDosageAssertions.length ? (
                  <div className="gene-compact-subsection">
                    <p className="gene-compact-subtitle-label">ClinGen dosage assertions</p>
                    <ul className="gene-compact-list">
                      {clingenDosageAssertions.map((entry, index) => {
                        const meta = [
                          entry.haploinsufficiency
                            ? `HI score ${entry.haploinsufficiency}`
                            : null,
                          entry.description,
                        ]
                          .filter(Boolean)
                          .join(' · ');
                        return (
                          <li key={`${entry.disease || index}-${entry.haploinsufficiency || 'hi'}`}>
                            <span>{entry.disease || 'ClinGen haploinsufficiency'}</span>
                            {meta ? <span className="gene-compact-list-meta">{meta}</span> : null}
                            <PubmedLinks pmids={entry.pmids} />
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ) : null}

                {advancedConstraintRows.length ? (
                  <div className="gene-compact-subsection">
                    <button
                      type="button"
                      className="button-secondary gene-compact-collapse-button"
                      aria-expanded={advancedConstraintExpanded}
                      aria-controls="gene-advanced-constraint-metrics"
                      onClick={() => setAdvancedConstraintExpanded((expanded) => !expanded)}
                    >
                      {advancedConstraintExpanded ? 'Hide' : 'Show'} advanced constraint metrics
                    </button>
                    {advancedConstraintExpanded ? (
                      <div
                        id="gene-advanced-constraint-metrics"
                        className="gene-compact-advanced-block"
                      >
                        <DetailList rows={advancedConstraintRows} />
                      </div>
                    ) : null}
                  </div>
                ) : null}

              </div>
            </div>
          </section>

          {pathwayGroups.length ||
          ontologyGroups.length ||
          tissueExpression?.tissue_specificity ||
          highlightedTissues.length ||
          topTissueExpression.length ||
          modelOrganismRows.length ? (
            <section className="gene-compact-section">
              <div className="gene-compact-section-header">
                <div>
                  <p className="page-kicker">dbNSFP context</p>
                  <h3 className="gene-compact-section-title">Function and expression</h3>
                </div>
                {functionContextCanExpand ? (
                  <button
                    type="button"
                    className="button-secondary gene-compact-collapse-button"
                    aria-expanded={functionContextExpanded}
                    aria-controls="gene-function-context"
                    onClick={() => setFunctionContextExpanded((expanded) => !expanded)}
                  >
                    {functionContextExpanded ? 'Show less' : 'Show more'} function context
                  </button>
                ) : null}
              </div>

              <div id="gene-function-context" className="gene-compact-columns">
                <div className="gene-compact-block">
                  <h3>Pathways and ontology</h3>
                  {pathwayGroups.length || ontologyGroups.length ? (
                    <div className="gene-compact-group-stack">
                      {pathwayGroups.map((group) => (
                        <CompactStringGroup
                          key={group.label}
                          group={group}
                          expanded={functionContextExpanded}
                        />
                      ))}
                      {ontologyGroups.map((group) => (
                        <CompactStringGroup
                          key={group.label}
                          group={group}
                          expanded={functionContextExpanded}
                        />
                      ))}
                    </div>
                  ) : (
                    <p className="gene-compact-empty">
                      No pathway or ontology annotations are cached for this gene.
                    </p>
                  )}
                </div>

                <div className="gene-compact-block">
                  <h3>Tissue and model systems</h3>

                  {tissueExpression?.tissue_specificity ? (
                    <div className="gene-compact-subsection">
                      <p className="gene-compact-subtitle-label">Tissue specificity</p>
                      <p className="gene-compact-paragraph">
                        {tissueExpression.tissue_specificity}
                      </p>
                    </div>
                  ) : null}

                  {highlightedTissues.length ? (
                    <div className="gene-compact-subsection">
                      <p className="gene-compact-subtitle-label">Highly expressed tissues</p>
                      <div className="gene-compact-chip-grid">
                        {highlightedTissues
                          .slice(0, functionContextExpanded ? highlightedTissues.length : 6)
                          .map((tissue) => (
                            <span key={tissue} className="gene-compact-term-chip">
                              {formatReadableKey(tissue)}
                            </span>
                          ))}
                      </div>
                    </div>
                  ) : null}

                  {topTissueExpression.length ? (
                    <div className="gene-compact-subsection">
                      <p className="gene-compact-subtitle-label">Top HPA consensus TPM</p>
                      <div className="gene-compact-expression-list">
                        {topTissueExpression.map(([tissue, value]) => (
                          <div key={tissue} className="gene-compact-expression-row">
                            <div className="gene-compact-expression-labels">
                              <span>{formatReadableKey(tissue)}</span>
                              <span>{formatMetricNumber(value, 1)} TPM</span>
                            </div>
                            <div className="gene-compact-mini-bar" aria-hidden="true">
                              <span
                                className="gene-compact-mini-bar-fill"
                                style={{
                                  width: `${
                                    maxTissueTpm > 0
                                      ? Math.max(4, (value / maxTissueTpm) * 100)
                                      : 0
                                  }%`,
                                }}
                              />
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {modelOrganismRows.length ? (
                    <div className="gene-compact-subsection">
                      <p className="gene-compact-subtitle-label">Model organisms</p>
                      <ul className="gene-compact-list">
                        {modelOrganismRows.map((row) => (
                          <li key={row.label}>
                            <span>
                              {row.label}
                              {row.symbol ? `: ${row.symbol}` : ''}
                            </span>
                            {row.details.length ? (
                              <span className="gene-compact-list-meta">
                                {row.details
                                  .slice(0, functionContextExpanded ? row.details.length : 4)
                                  .join(' · ')}
                              </span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </div>
              </div>
            </section>
          ) : null}

          <section className="gene-compact-section">
            <div className="gene-compact-section-header">
              <div>
                <p className="page-kicker">Transcripts</p>
                <h3 className="gene-compact-section-title">Canonical and MANE first</h3>
              </div>
              <div className="gene-compact-section-actions">
                <span className="gene-compact-section-note">
                  Imported transcripts are ordered with canonical and MANE models first.
                </span>
                <button
                  type="button"
                  className="button-secondary gene-compact-collapse-button"
                  aria-expanded={transcriptsExpanded}
                  aria-controls="gene-transcript-table"
                  onClick={() => setTranscriptsExpanded((expanded) => !expanded)}
                >
                  {transcriptsExpanded ? 'Hide' : 'Show'} {orderedTranscripts.length} transcript
                  {orderedTranscripts.length === 1 ? '' : 's'}
                </button>
              </div>
            </div>

            {transcriptsExpanded ? (
              <div id="gene-transcript-table" className="data-table-shell overflow-x-auto">
                <table className="analysis-table table-sticky gene-transcript-table">
                  <thead>
                    <tr>
                      <th>Transcript</th>
                      <th>RefSeq</th>
                      <th>Type</th>
                      <th>Source</th>
                      <th>Start</th>
                      <th>End</th>
                      <th>Exons</th>
                      <th>Flags</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orderedTranscripts.length ? (
                      orderedTranscripts.map((transcript) => {
                        const badges = transcriptBadgesFor(transcript, transcriptBadgeContext);

                        return (
                          <tr key={transcript.transcript_id}>
                            <td>{transcript.transcript_id}</td>
                            <td>
                              {transcript.refseq_accessions?.length ? (
                                <span className="gene-compact-inline-links">
                                  {transcript.refseq_accessions.map((accession) => (
                                    <a
                                      key={accession}
                                      className="gene-compact-link"
                                      href={`https://www.ncbi.nlm.nih.gov/nuccore/${encodeURIComponent(accession)}`}
                                      target="_blank"
                                      rel="noreferrer"
                                    >
                                      {accession}
                                    </a>
                                  ))}
                                </span>
                              ) : (
                                <span className="table-empty">—</span>
                              )}
                            </td>
                            <td>{classifyTranscript(transcript.transcript_id)}</td>
                            <td>{transcript.source || '—'}</td>
                            <td>{transcript.start.toLocaleString()}</td>
                            <td>{transcript.end.toLocaleString()}</td>
                            <td>{transcript.exon_count}</td>
                            <td>
                              {badges.length ? (
                                <div className="variant-transcript-badges">
                                  {badges.map((badge) => (
                                    <span
                                      key={`${transcript.transcript_id}-${badge.label}`}
                                      className={`variant-card-chip variant-card-chip--${badge.tone}`}
                                    >
                                      {badge.label}
                                    </span>
                                  ))}
                                </div>
                              ) : (
                                <span className="table-empty">—</span>
                              )}
                            </td>
                          </tr>
                        );
                      })
                    ) : (
                      <tr>
                        <td colSpan={7} className="table-empty">
                          No transcripts are cached for this gene.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            ) : null}
          </section>

          <section className="gene-compact-section">
            <div className="gene-compact-section-header">
              <div>
                <p className="page-kicker">Links</p>
                <h3 className="gene-compact-section-title">External resources</h3>
              </div>
            </div>

            <div className="gene-compact-link-grid">
              {compactLinks.map((link) => (
                <a
                  key={link.label}
                  href={link.href}
                  target="_blank"
                  rel="noreferrer"
                  className="gene-compact-resource-link"
                >
                  {link.label}
                </a>
              ))}
            </div>
          </section>

          <footer className="gene-compact-footer">
            <span>Latest refresh: {formatTimestamp(profile.updated_at)}</span>
            {/* Which release of each source produced what is on this page. A source that
                answered without stating a release shows its status instead, rather than
                an invented version. */}
            <span className="gene-compact-inline-links">
              <span
                className="gene-compact-source gene-compact-source--success"
                title={
                  profile.gene_annotation_imported_at
                    ? `Gene loci and transcripts imported ${formatTimestamp(profile.gene_annotation_imported_at)}`
                    : undefined
                }
              >
                Annotation {profile.gene_annotation_source || 'unknown'}
              </span>
              {sourceEntries.map(([name, source]) => (
                <span
                  key={name}
                  className={`gene-compact-source gene-compact-source--${source.status}`}
                  title={[
                    source.message,
                    source.release_detail?.checksum
                      ? `sha256 ${source.release_detail.checksum.slice(0, 12)}…`
                      : null,
                    `fetched ${formatTimestamp(source.fetched_at)}`,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                >
                  {SOURCE_LABELS[name] || name.toUpperCase()} {source.release || source.status}
                </span>
              ))}
            </span>
          </footer>
        </>
      )}
    </div>
  );
};

export default GeneInfoPage;
