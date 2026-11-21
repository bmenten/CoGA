import React from 'react';
import { useLocation, useNavigate } from 'react-router';

import { ANALYSIS_PARAM, analysisLabel } from '../../lib/familyAnalyses';

/**
 * The analysis a page of a family reads, of a family with two or more of the analyses it shows:
 * short-read and then long-read WGS of the same DNA, or the NIPT of each of a woman's pregnancies.
 * Each is read on its own, its list, counts, reviews and sign-out its own (the owner's decisions of
 * 2026-11-20 and 2026-11-21). Nothing for any other family.
 */
const AnalysisSelect: React.FC<{
  analyses: string[];
  analysis: string | null;
  onChange: (analysis: string) => void;
}> = ({ analyses, analysis, onChange }) => {
  if (analyses.length < 2 || !analysis) return null;
  return (
    <select
      aria-label="Analysis whose data are shown"
      value={analysis}
      onChange={(event) => onChange(event.target.value)}
    >
      {analyses.map((key) => (
        <option key={key} value={key}>
          {analysisLabel(key) ?? key}
        </option>
      ))}
    </select>
  );
};

/**
 * Read another of the family's analyses on a page that keeps its state in its URL: the URL's
 * `analysis` set to it, its other parameters kept.
 */
export const useAnalysisNavigation = (): ((analysis: string) => void) => {
  const location = useLocation();
  const navigate = useNavigate();
  return (analysis: string) => {
    const params = new URLSearchParams(location.search);
    params.set(ANALYSIS_PARAM, analysis);
    navigate({ pathname: location.pathname, search: `?${params.toString()}` });
  };
};

export default AnalysisSelect;
