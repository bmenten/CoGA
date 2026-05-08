import React from 'react';

interface StructuralVariantColumnControlsProps {
  visible: Record<string, boolean>;
  onToggleColumn: (key: string) => void;
}

const COLUMN_LABELS: Record<string, string> = {
  read_support: 'Read support',
  remote_chr: 'Remote chr',
  remote_start: 'Remote start',
  control_af: 'Control AF',
  region_flags: 'Regions',
  cytoband: 'Band',
};

export default function StructuralVariantColumnControls({
  visible,
  onToggleColumn,
}: StructuralVariantColumnControlsProps) {
  return (
    <div className="surface-card-flat flex flex-wrap items-center justify-end gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-[var(--color-text-muted)]">Columns</span>
        {Object.keys(visible).map((key) => (
          <label
            key={key}
            className="inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)]"
          >
            <input
              type="checkbox"
              checked={visible[key]}
              onChange={() => onToggleColumn(key)}
            />
            {COLUMN_LABELS[key] || key.charAt(0).toUpperCase() + key.slice(1)}
          </label>
        ))}
      </div>
    </div>
  );
}
