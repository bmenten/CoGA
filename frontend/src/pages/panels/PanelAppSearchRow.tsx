import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type { GenePanel } from '../../lib/apiTypes';
import type {
  PanelAppConfidenceCounts,
  PanelAppPanelCountsOut,
  PanelAppPanelSummaryOut,
} from '../../lib/apiSchema.generated';
import { formatDateTime } from '../../lib/format';
import { routePath } from '../../lib/routePath';
import {
  PANELAPP_IMPORT_CHOICES,
  countAtLevels,
  importsNothing,
  panelAppSelection,
  panelAppSelectionLabel,
} from './panelAppConfidence';

/** One kind of entity of a PanelApp panel: its total, then how many are green, amber and red,
 * and how many PanelApp rates none of these (level 0), once the counts are read. */
const ConfidenceLine: React.FC<{ noun: string; total: number; counts?: PanelAppConfidenceCounts }> = ({
  noun,
  total,
  counts,
}) => (
  <div className="panelapp-confidence-line">
    <span className="panelapp-confidence-total">
      {counts?.total ?? total} {noun}
    </span>
    {counts ? (
      <>
        {(['green', 'amber', 'red'] as const).map((name) => (
          <span key={name} className="panelapp-confidence-count">
            <span className={`panelapp-confidence-dot panelapp-confidence-dot--${name}`} aria-hidden="true" />
            {counts[name]} {name}
          </span>
        ))}
        {counts.unrated > 0 ? (
          <span
            className="panelapp-confidence-count table-subtle"
            title="PanelApp confidence level 0: no rating, or removed by an expert review. No import takes them."
          >
            {counts.unrated} unrated
          </span>
        ) : null}
      </>
    ) : null}
  </div>
);

type PanelAppSearchRowProps = {
  panel: PanelAppPanelSummaryOut;
  /** The catalogue's panels imported from this PanelApp panel, one per choice of levels. */
  imported: GenePanel[];
  /** The selection of this panel being imported now, if any. */
  importing: string | null;
  /** An import of any panel is running: no other starts until it ends. */
  busy: boolean;
  includeRegions: boolean;
  includeStrs: boolean;
  onImport: (panel: PanelAppPanelSummaryOut, levels: string[]) => void;
};

/** A PanelApp search result: its genes, regions and STRs by confidence level, read from
 * PanelApp for the version found, and an import for each choice of levels. Each choice is a
 * panel of its own in the catalogue; one already there is listed, and importing it again
 * updates it. */
const PanelAppSearchRow: React.FC<PanelAppSearchRowProps> = ({
  panel,
  imported,
  importing,
  busy,
  includeRegions,
  includeStrs,
  onImport,
}) => {
  const counts = useQuery<PanelAppPanelCountsOut>({
    queryKey: ['panelapp-counts', panel.panelapp_id, panel.version ?? null],
    queryFn: async () => {
      const res = await api.get<PanelAppPanelCountsOut>(apiPath`/panels/panelapp/${panel.panelapp_id}/counts`, {
        params: panel.version ? { version: panel.version } : {},
      });
      return res.data;
    },
    staleTime: 10 * 60 * 1000,
    retry: false,
  });
  const inCatalogue = PANELAPP_IMPORT_CHOICES.flatMap(({ levels }) => {
    const found = imported.find((entry) => entry.external_selection === panelAppSelection(levels));
    return found ? [found] : [];
  });

  return (
    <tr>
      <td>
        <div className="font-semibold">{panel.name}</div>
        <div className="table-subtle">
          PanelApp {panel.panelapp_id}
          {panel.disease_group ? ` · ${panel.disease_group}` : ''}
        </div>
      </td>
      <td>
        {panel.version || 'latest'}
        {panel.version_created && <div className="table-subtle">{formatDateTime(panel.version_created)}</div>}
      </td>
      <td>
        <ConfidenceLine noun="genes" total={panel.gene_count} counts={counts.data?.genes} />
        {panel.region_count > 0 || (counts.data?.regions.total ?? 0) > 0 ? (
          <ConfidenceLine noun="regions" total={panel.region_count} counts={counts.data?.regions} />
        ) : null}
        {panel.str_count > 0 || (counts.data?.strs.total ?? 0) > 0 ? (
          <ConfidenceLine noun="STRs" total={panel.str_count} counts={counts.data?.strs} />
        ) : null}
        {counts.isPending ? <div className="table-subtle">Counting green, amber and red…</div> : null}
        {counts.isError ? (
          <div className="table-subtle">Could not read the confidence levels from PanelApp</div>
        ) : null}
      </td>
      <td>{panel.types.join(', ') || 'PanelApp'}</td>
      <td>
        <div className="panelapp-import-choices">
          {PANELAPP_IMPORT_CHOICES.map(({ levels, label }) => {
            const selection = panelAppSelection(levels);
            const genes = counts.data ? countAtLevels(counts.data.genes, levels) : null;
            const empty = counts.data ? importsNothing(counts.data, levels, { includeRegions, includeStrs }) : false;
            return (
              <button
                key={selection}
                type="button"
                className="form-button"
                onClick={() => onImport(panel, levels)}
                disabled={busy || empty}
                title={
                  empty
                    ? `PanelApp rates no gene, region or STR of this panel ${panelAppSelectionLabel(selection)}`
                    : `Import the ${panelAppSelectionLabel(selection)} entities as a panel of their own`
                }
              >
                {importing === selection ? 'Importing…' : label}
                {genes !== null && importing !== selection ? ` (${genes})` : ''}
              </button>
            );
          })}
        </div>
        {inCatalogue.length > 0 ? (
          <div className="table-subtle">
            In catalogue:{' '}
            {inCatalogue.map((entry, index) => (
              <React.Fragment key={entry._id}>
                {index > 0 ? ' · ' : ''}
                <Link to={routePath`/panels/${entry._id}`} className="table-link">
                  {panelAppSelectionLabel(entry.external_selection)} v{entry.version ?? 1}
                </Link>
              </React.Fragment>
            ))}
          </div>
        ) : null}
      </td>
    </tr>
  );
};

export default PanelAppSearchRow;
