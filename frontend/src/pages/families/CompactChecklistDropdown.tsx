import { useMemo, useState } from 'react';
import type { MouseEvent } from 'react';

export type CompactChecklistOption = {
  value: string;
  label: string;
  description?: string | null;
  meta?: string | null;
  group?: string | null;
  color?: string | null;
};

type CompactChecklistDropdownProps = {
  label: string;
  options: CompactChecklistOption[];
  selectedValues: string[];
  onToggleValue: (value: string) => void;
  onClear: () => void;
  placeholder: string;
  disabled?: boolean;
  maxPreviewItems?: number;
  selectionMode?: 'single' | 'multiple';
  panelTestId?: string;
};

export default function CompactChecklistDropdown({
  label,
  options,
  selectedValues,
  onToggleValue,
  onClear,
  placeholder,
  disabled = false,
  maxPreviewItems = 2,
  selectionMode = 'multiple',
  panelTestId,
}: CompactChecklistDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const summaryLabel = useMemo(() => {
    if (!selectedValues.length) return placeholder;
    const selectedOptionLabels = options
      .filter((option) => selectedValues.includes(option.value))
      .map((option) => option.label);
    if (selectedOptionLabels.length <= maxPreviewItems) {
      return selectedOptionLabels.join(', ');
    }
    return `${selectedOptionLabels.length} selected`;
  }, [maxPreviewItems, options, placeholder, selectedValues]);

  const groupedOptions = useMemo(() => {
    const groups: Array<{ label: string | null; options: CompactChecklistOption[] }> = [];
    options.forEach((option) => {
      const groupLabel = option.group || null;
      const existingGroup = groups.find((entry) => entry.label === groupLabel);
      if (existingGroup) {
        existingGroup.options.push(option);
        return;
      }
      groups.push({ label: groupLabel, options: [option] });
    });
    return groups;
  }, [options]);

  const handleTriggerClick = (event: MouseEvent<HTMLElement>) => {
    event.preventDefault();
    if (disabled) return;
    setIsOpen((current) => !current);
  };

  const handleOptionToggle = (value: string) => {
    onToggleValue(value);
    if (selectionMode === 'single') {
      setIsOpen(false);
    }
  };

  return (
    <details
      className={`compact-checklist${disabled ? ' compact-checklist--disabled' : ''}`}
      open={isOpen}
    >
      <summary className="compact-checklist-trigger" onClick={handleTriggerClick}>
        <span className="compact-checklist-trigger-copy">
          <span className="compact-checklist-label">{label}</span>
          <span className="compact-checklist-value">{summaryLabel}</span>
        </span>
        <span className="compact-checklist-caret" aria-hidden="true">
          ▾
        </span>
      </summary>

      {isOpen ? (
        <div className="compact-checklist-panel" data-testid={panelTestId}>
          <div className="compact-checklist-list">
            {groupedOptions.map((group) => (
              <div key={group.label || 'ungrouped'} className="compact-checklist-group">
                {group.label ? (
                  <p className="compact-checklist-group-label">{group.label}</p>
                ) : null}
                <div className="compact-checklist-group-options">
                  {group.options.map((option) => {
                    const checked = selectedValues.includes(option.value);
                    return (
                      <label
                        key={option.value}
                        className={`compact-checklist-option${
                          checked ? ' compact-checklist-option--selected' : ''
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => handleOptionToggle(option.value)}
                          disabled={disabled}
                        />
                        <span className="compact-checklist-option-copy">
                          <span className="compact-checklist-option-title-row">
                            {option.color ? (
                              <span
                                className="compact-checklist-option-swatch"
                                style={{ backgroundColor: option.color }}
                                aria-hidden="true"
                              />
                            ) : null}
                            <span className="compact-checklist-option-title">{option.label}</span>
                          </span>
                          {option.meta || option.description ? (
                            <span className="compact-checklist-option-meta">
                              {option.meta || option.description}
                            </span>
                          ) : null}
                        </span>
                        <span className="compact-checklist-tick" aria-hidden="true">
                          {checked ? '✓' : ''}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>

          <div className="compact-checklist-actions">
            <button
              type="button"
              className="button-secondary compact-checklist-clear"
              disabled={disabled || !selectedValues.length}
              onClick={onClear}
            >
              Clear
            </button>
          </div>
        </div>
      ) : null}
    </details>
  );
}
