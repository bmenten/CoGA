import { countPresetRules, type SmallVariantFilterPreset } from '../pages/families/smallVariantSearch';

type SmallVariantFilterPresetTableProps = {
  presets: SmallVariantFilterPreset[];
  emptyMessage: string;
  showOwner?: boolean;
  deletingPresetId?: string | null;
  onDeletePreset?: (preset: SmallVariantFilterPreset) => Promise<void> | void;
};

const formatPresetDate = (value?: string) => {
  if (!value) return '—';
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return value;
  return timestamp.toLocaleDateString();
};

const getScopeLabel = (preset: SmallVariantFilterPreset) => {
  if (preset.scope === 'global') return 'Reusable';
  return preset.family_id ? `Legacy family (${preset.family_id})` : 'Legacy family';
};

export default function SmallVariantFilterPresetTable({
  presets,
  emptyMessage,
  showOwner = false,
  deletingPresetId = null,
  onDeletePreset,
}: SmallVariantFilterPresetTableProps) {
  if (!presets.length) {
    return (
      <div className="preset-catalog-empty">
        <p className="table-empty">{emptyMessage}</p>
      </div>
    );
  }

  return (
    <div className="analysis-results-card overflow-x-auto">
      <table className="analysis-table preset-catalog-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Scope</th>
            <th>Rules</th>
            {showOwner ? <th>Created by</th> : null}
            <th>Created</th>
            {onDeletePreset ? <th>Actions</th> : null}
          </tr>
        </thead>
        <tbody>
          {presets.map((preset) => (
            <tr key={preset._id}>
              <td className="preset-catalog-name-cell">
                <div className="preset-catalog-name">
                  <span>{preset.name}</span>
                  {preset.description?.trim() ? (
                    <p className="preset-catalog-description">{preset.description}</p>
                  ) : null}
                </div>
              </td>
              <td>{getScopeLabel(preset)}</td>
              <td>{countPresetRules(preset)} rules</td>
              {showOwner ? <td>{preset.owner}</td> : null}
              <td>{formatPresetDate(preset.created_at)}</td>
              {onDeletePreset ? (
                <td>
                  <button
                    type="button"
                    className="button-secondary"
                    disabled={deletingPresetId === preset._id}
                    onClick={() => {
                      void onDeletePreset(preset);
                    }}
                  >
                    {deletingPresetId === preset._id ? 'Removing…' : 'Delete'}
                  </button>
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
