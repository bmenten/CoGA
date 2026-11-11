// A reported variant's class on the report, the live one and the signed version alike (the
// owner's decision of 2026-11-10): the class its review shows by the one rule of the family
// page, *Not classified* when it shows none, and beside it, labelled as the score, the scoring's
// class when that is another: "VUS - class 3 (ACMG score: Likely pathogenic - class 4 · 8 pts)".
// The points are the scoring's, so they stand with the score. The chip and the sentence print as
// they show.

import React from 'react';

import { joinWithAnd } from '../../lib/format';
import type { ReportedClass } from './reportNarrative';

/** What scored the variant: a small variant's ACMG/AMP criteria, an SV's ClinGen CNV criteria. */
export type ReportScoring = 'acmg' | 'cnv';

const SCORE_LABEL: Record<ReportScoring, string> = {
  acmg: 'ACMG score',
  cnv: 'CNV score',
};

const pointsText = (points: string | null): string =>
  points ? ` · ${points} pts` : '';

/** The class chip in a reported variant's heading. */
export const ReportClassificationChip: React.FC<{
  value: ReportedClass;
  scoring: ReportScoring;
}> = ({ value, scoring }) => {
  const beside = value.score
    ? ` (${SCORE_LABEL[scoring]}: ${value.score}${pointsText(value.points)})`
    : '';
  return value.shown ? (
    <span className="table-chip report-classification-chip">
      {value.shown}
      {beside || pointsText(value.points)}
    </span>
  ) : (
    <span className="table-chip report-classification-chip report-classification-chip--none">
      Not classified{beside}
    </span>
  );
};

/**
 * The sentence that opens the classification motivation: the class and the criteria it rests
 * on; when the criteria score another class than the review shows, both, each named for what it
 * is, so the criteria never read as the grounds of a class they do not give.
 */
export const ClassificationMotivationSentence: React.FC<{
  value: ReportedClass;
  scoring: ReportScoring;
  codes: string[];
}> = ({ value, scoring, codes }) => {
  const criteria = joinWithAnd(codes);
  if (!value.score) {
    return (
      <p className="report-paragraph">
        This variant was classified as{' '}
        <strong>{value.shown || 'uncertain significance'}</strong>
        {scoring === 'acmg'
          ? ` based on ${criteria}.`
          : ` by the ClinGen CNV criteria ${criteria}.`}
      </p>
    );
  }
  return (
    <p className="report-paragraph">
      {scoring === 'acmg'
        ? `The ACMG/AMP criteria ${criteria}`
        : `The ClinGen CNV criteria ${criteria}`}{' '}
      score this variant <strong>{value.score}</strong>;{' '}
      {value.shown ? (
        <>
          it is classified as <strong>{value.shown}</strong>.
        </>
      ) : (
        'it is not classified.'
      )}
    </p>
  );
};
