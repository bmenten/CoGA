import React, { useMemo } from 'react';
import type { StructuralSummary } from './structuralVariantSearch';

interface StructuralVariantSummaryTableProps {
  summary: StructuralSummary;
}

export default function StructuralVariantSummaryTable({
  summary,
}: StructuralVariantSummaryTableProps) {
  const types = Object.keys(summary);
  const sources = useMemo(() => {
    const values = new Set<string>();
    types.forEach((typeKey) => {
      Object.keys(summary[typeKey]).forEach((sourceKey) => values.add(sourceKey));
    });
    return Array.from(values);
  }, [summary, types]);

  if (!types.length) return null;

  return (
    <div className="analysis-results-card overflow-x-auto">
      <table className="analysis-table">
        <thead>
          <tr>
            <th>Type / Source</th>
            {sources.map((source) => (
              <th key={source}>{source || '—'}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {types.map((typeKey) => (
            <tr key={typeKey}>
              <td>{typeKey || '—'}</td>
              {sources.map((source) => (
                <td key={source}>{summary[typeKey]?.[source] ?? 0}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
