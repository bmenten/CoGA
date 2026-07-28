import { Link } from 'react-router';

import type { SmallVariantTagDefinition } from '../families/smallVariantSearch';
import type { CarrierModalMode, GlobalVariantRow } from './types';
import type { GlobalVariantSort } from './globalSmallVariantSearch';

interface GlobalSmallVariantTableProps {
  variants: GlobalVariantRow[];
  tagDefinitions: SmallVariantTagDefinition[];
  sort: GlobalVariantSort;
  order: 'asc' | 'desc';
  onSort: (sort: GlobalVariantSort) => void;
  onOpenCarriers: (variant: GlobalVariantRow, mode: CarrierModalMode) => void;
}

const sortIndicator = (active: boolean, order: 'asc' | 'desc') =>
  active ? (order === 'desc' ? ' ↓' : ' ↑') : '';

const GlobalSmallVariantTable = ({
  variants,
  tagDefinitions,
  sort,
  order,
  onSort,
  onOpenCarriers,
}: GlobalSmallVariantTableProps) => {
  const tagLabel = (key: string) => tagDefinitions.find((tag) => tag.key === key)?.label || key;
  const tagColor = (key: string) =>
    tagDefinitions.find((tag) => tag.key === key)?.color || '#5b6b79';

  return (
    <div className="analysis-results-card overflow-x-auto">
      <table className="analysis-table table-sticky small-variant-table">
        <thead>
          <tr>
            <th className="table-sortable" onClick={() => onSort('position')}>
              Gene{sortIndicator(sort === 'position', order)}
            </th>
            <th>Variant</th>
            <th>Classification</th>
            <th>Consequence</th>
            <th>Tags</th>
            <th className="table-sortable table-mono" onClick={() => onSort('total_samples')}>
              Total samples{sortIndicator(sort === 'total_samples', order)}
            </th>
            <th className="table-sortable table-mono" onClick={() => onSort('het_samples')}>
              Het{sortIndicator(sort === 'het_samples', order)}
            </th>
            <th className="table-sortable table-mono" onClick={() => onSort('hom_samples')}>
              Hom{sortIndicator(sort === 'hom_samples', order)}
            </th>
            <th className="table-sortable table-mono" onClick={() => onSort('total_families')}>
              Families{sortIndicator(sort === 'total_families', order)}
            </th>
          </tr>
        </thead>
        <tbody>
          {variants.map((variant) => {
            const geneHref = variant.gene
              ? `/genes?symbol=${encodeURIComponent(variant.gene)}`
              : null;
            return (
              <tr key={variant.key}>
                <td>
                  {geneHref ? (
                    <Link to={geneHref} className="table-link">
                      {variant.gene}
                    </Link>
                  ) : (
                    <span className="table-empty">—</span>
                  )}
                </td>
                <td className="table-mono">
                  <div>
                    {variant.chr}:{variant.pos} {variant.ref}&gt;{variant.alt}
                  </div>
                  {variant.hgvsc ? <div className="table-subtle">{variant.hgvsc}</div> : null}
                </td>
                <td>
                  {variant.classification ? (
                    variant.classification
                  ) : variant.clinvar ? (
                    <span className="table-subtle">{variant.clinvar}</span>
                  ) : (
                    <span className="table-empty">—</span>
                  )}
                </td>
                <td>
                  {variant.consequence || <span className="table-empty">—</span>}
                  {variant.impact ? (
                    <div className="table-subtle">{variant.impact}</div>
                  ) : null}
                </td>
                <td>
                  {variant.tags.length ? (
                    <div className="variant-explorer-tag-list">
                      {variant.tags.map((tag) => (
                        <span
                          key={tag}
                          className="variant-explorer-tag"
                          style={{ backgroundColor: tagColor(tag) }}
                        >
                          {tagLabel(tag)}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span className="table-empty">—</span>
                  )}
                </td>
                <td className="table-mono">
                  {variant.total_samples > 0 ? (
                    <button
                      type="button"
                      className="table-link variant-explorer-count-button"
                      onClick={() => onOpenCarriers(variant, 'all')}
                    >
                      {variant.total_samples}
                    </button>
                  ) : (
                    0
                  )}
                </td>
                <td className="table-mono">
                  {variant.het_samples > 0 ? (
                    <button
                      type="button"
                      className="table-link variant-explorer-count-button"
                      onClick={() => onOpenCarriers(variant, 'het')}
                    >
                      {variant.het_samples}
                    </button>
                  ) : (
                    0
                  )}
                </td>
                <td className="table-mono">
                  {variant.hom_samples > 0 ? (
                    <button
                      type="button"
                      className="table-link variant-explorer-count-button"
                      onClick={() => onOpenCarriers(variant, 'hom')}
                    >
                      {variant.hom_samples}
                    </button>
                  ) : (
                    0
                  )}
                </td>
                <td className="table-mono">
                  {variant.total_families > 0 ? (
                    <button
                      type="button"
                      className="table-link variant-explorer-count-button"
                      onClick={() => onOpenCarriers(variant, 'families')}
                    >
                      {variant.total_families}
                    </button>
                  ) : (
                    0
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

export default GlobalSmallVariantTable;
