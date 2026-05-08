import React, { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
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
  status: 'success' | 'missing' | 'error';
  fetched_at: string;
  source_url?: string | null;
  message?: string | null;
  payload: Record<string, unknown>;
}

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
  function?: string | null;
  genomic_coordinates?: Record<string, string>;
}

interface GeneConstraintMetrics {
  missense_z?: number | null;
  shet?: number | null;
  phaplo?: number | null;
  ptriplo?: number | null;
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

interface GeneProfileExtra {
  hgnc_name?: string | null;
  hgnc_gene_group?: string[];
  ensembl_canonical_transcript?: string | null;
  ensembl_description?: string | null;
  ncbi_other_designations?: string[];
  clingen_curation_counts?: GeneClingenCurationCounts;
  clingen_gene_facts?: GeneClingenFacts;
  hgnc_vega_id?: string | null;
  refseq_accessions?: string[];
  omim_diseases?: Array<string | GeneOmimDiseaseEntry>;
  dbnsfp_disease_associations?: Array<string | GeneDbnsfpAssociationEntry>;
  constraint_metrics?: GeneConstraintMetrics;
  primad_url?: string | null;
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
  external_links: GeneExternalLink[];
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

const uniqueStrings = (...groups: Array<string[] | undefined>) =>
  Array.from(
    new Set(
      groups
        .flatMap((group) => group || [])
        .map((entry) => entry.trim())
        .filter(Boolean),
    ),
  );

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
  const primadHref = profile.extra.primad_url?.trim() || null;

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
  pushLink('Monarch', `https://monarchinitiative.org/search?q=${symbolQuery}`);
  pushLink('DECIPHER', byLabel.get('decipher'));
  pushLink('UniProt', byLabel.get('uniprot'));
  pushLink('Geno2MP', 'https://geno2mp.gs.washington.edu/Geno2MP/');
  pushLink('gnomAD', gnomadHref);
  pushLink('primAD', primadHref);
  pushLink('MGI', `https://www.informatics.jax.org/searchtool/Search.do?query=${symbolQuery}`);
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

const GeneInfoPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();

  const familyId = searchParams.get('family_id') || undefined;
  const projectIdParam = searchParams.get('project_id') || undefined;
  const geneParam = searchParams.get('gene') || '';
  const assemblyParam = searchParams.get('assembly_id') || '';

  const [draftGene, setDraftGene] = useState(geneParam);

  useEffect(() => {
    setDraftGene(geneParam);
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
  const compactLinks = useMemo(() => (profile ? buildGeneLinks(profile) : []), [profile]);
  const sourceEntries = useMemo(
    () => (profile ? Object.entries(profile.source_status) : []),
    [profile],
  );
  const constraintMetrics = profile?.extra.constraint_metrics;

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
            { label: 'Vega', value: profile.extra.hgnc_vega_id || '—' },
            {
              label: 'Canonical transcript',
              value: profile.extra.ensembl_canonical_transcript || '—',
            },
            { label: 'MANE transcript', value: clingenFacts?.mane_select_transcript || '—' },
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
              value: formatPercent(clingenFacts?.haploinsufficiency_index),
            },
            {
              label: 'gnomAD pLI',
              value: formatNumber(clingenFacts?.pli),
            },
            {
              label: 'gnomAD LOEUF',
              value: formatNumber(clingenFacts?.loeuf),
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
    [clingenFacts?.haploinsufficiency_index, clingenFacts?.loeuf, clingenFacts?.pli, constraintMetrics, profile],
  );

  const orderedTranscripts = useMemo(() => {
    if (!profile) return [];
    const canonicalTranscript = profile.extra.ensembl_canonical_transcript;
    const maneTranscript = clingenFacts?.mane_select_transcript;

    return [...profile.transcripts].sort((left, right) => {
      const score = (transcript: GeneTranscript) => {
        const canonical = isSameTranscript(transcript.transcript_id, canonicalTranscript) ? 0 : 1;
        const mane = isSameTranscript(transcript.transcript_id, maneTranscript) ? 0 : 1;
        const sourceRank = /^ENS/i.test(transcript.transcript_id)
          ? 0
          : /^(NM_|NR_|XM_|XR_)/i.test(transcript.transcript_id)
            ? 1
            : 2;
        return [canonical, mane, sourceRank, transcript.transcript_id];
      };

      const leftScore = score(left);
      const rightScore = score(right);
      return leftScore < rightScore ? -1 : leftScore > rightScore ? 1 : 0;
    });
  }, [clingenFacts?.mane_select_transcript, profile]);

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
      <section className="gene-compact-header">
        <div className="gene-compact-header-copy">
          <h1 className="catalog-card-title">Gene Explorer</h1>
          <p className="dashboard-link-note">
            Search a human gene and open a compact reference summary with local panels, disease
            evidence, transcripts, and direct resource links.
          </p>
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
                  <p className="gene-compact-subtitle-label">GenCC details</p>
                  <DetailList rows={genccRows} />
                </div>
              </div>

              <div className="gene-compact-block">
                <h3>Constraint and dosage</h3>
                <DetailList rows={constraintRows} />

                <div className="gene-compact-subsection">
                  <p className="gene-compact-subtitle-label">Description</p>
                  <p className="gene-compact-paragraph">
                    {clingenFacts?.function ||
                      profile.extra.ensembl_description ||
                      'No additional functional description is cached.'}
                  </p>
                </div>
              </div>
            </div>
          </section>

          <section className="gene-compact-section">
            <div className="gene-compact-section-header">
              <div>
                <p className="page-kicker">Transcripts</p>
                <h3 className="gene-compact-section-title">Canonical and MANE first</h3>
              </div>
              <span className="gene-compact-section-note">
                Imported transcripts are ordered with canonical and MANE models first.
              </span>
            </div>

            <div className="data-table-shell overflow-x-auto">
              <table className="analysis-table table-sticky">
                <thead>
                  <tr>
                    <th>Priority</th>
                    <th>Transcript</th>
                    <th>Type</th>
                    <th>Source</th>
                    <th>Start</th>
                    <th>End</th>
                    <th>Exons</th>
                  </tr>
                </thead>
                <tbody>
                  {orderedTranscripts.map((transcript) => {
                    const flags = [
                      isSameTranscript(
                        transcript.transcript_id,
                        profile.extra.ensembl_canonical_transcript,
                      )
                        ? 'Canonical'
                        : null,
                      isSameTranscript(
                        transcript.transcript_id,
                        clingenFacts?.mane_select_transcript,
                      )
                        ? 'MANE'
                        : null,
                    ].filter((flag): flag is string => Boolean(flag));

                    return (
                      <tr key={transcript.transcript_id}>
                        <td>
                          {flags.length ? (
                            <span className="gene-compact-inline-links">
                              {flags.map((flag) => (
                                <span key={`${transcript.transcript_id}-${flag}`} className="gene-compact-token">
                                  {flag}
                                </span>
                              ))}
                            </span>
                          ) : (
                            <span className="table-subtle">Standard</span>
                          )}
                        </td>
                        <td>{transcript.transcript_id}</td>
                        <td>{classifyTranscript(transcript.transcript_id)}</td>
                        <td>{transcript.source || '—'}</td>
                        <td>{transcript.start.toLocaleString()}</td>
                        <td>{transcript.end.toLocaleString()}</td>
                        <td>{transcript.exon_count}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
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
            {sourceEntries.length ? (
              <span className="gene-compact-inline-links">
                {sourceEntries.map(([name, source]) => (
                  <span key={name} className={`gene-compact-source gene-compact-source--${source.status}`}>
                    {name.toUpperCase()} {source.status}
                  </span>
                ))}
              </span>
            ) : null}
          </footer>
        </>
      )}
    </div>
  );
};

export default GeneInfoPage;
