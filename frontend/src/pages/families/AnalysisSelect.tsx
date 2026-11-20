import React from 'react';

import { analysisLabel } from '../../lib/familyAnalyses';

/**
 * The analysis a page of a family's variants reads, of a family with two or more analyses with
 * small variants of their own (short-read and then long-read WGS of the same DNA): each is read
 * on its own, its list, counts and reviews its own (the owner's decision of 2026-11-20). Nothing
 * for any other family.
 */
const AnalysisSelect: React.FC<{
  analyses: string[];
  analysis: string | null;
  onChange: (analysis: string) => void;
}> = ({ analyses, analysis, onChange }) => {
  if (analyses.length < 2 || !analysis) return null;
  return (
    <select
      aria-label="Analysis whose variants are shown"
      value={analysis}
      onChange={(event) => onChange(event.target.value)}
    >
      {analyses.map((analysisType) => (
        <option key={analysisType} value={analysisType}>
          {analysisLabel(analysisType) ?? analysisType}
        </option>
      ))}
    </select>
  );
};

export default AnalysisSelect;
