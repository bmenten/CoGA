import { describeFilterTranscript } from './smallVariantResultUtils';
import type { SmallVariant } from './smallVariantSearch';

const FILTER_TRANSCRIPT_TITLE =
  "The annotation filters must all hold on one transcript. The values shown are the variant's " +
  "primary transcript's (MANE Select, else canonical, else the most severe); the filters held " +
  'on this one.';

interface FilterTranscriptNoteProps {
  variant: SmallVariant;
  /** The note's class in the view that shows it, a card or a table row. */
  className: string;
  /** The class of the transcript ID within it. */
  idClassName: string;
}

/**
 * The transcript the search's annotation filters held on, when the primary transcript the
 * variant's values describe does not meet them (CLIN-51), with those of its values that
 * read otherwise. Nothing when the filters held on the variant's own transcript.
 */
export default function FilterTranscriptNote({ variant, className, idClassName }: FilterTranscriptNoteProps) {
  const summary = describeFilterTranscript(variant);
  if (!summary) return null;
  return (
    <p className={className} title={FILTER_TRANSCRIPT_TITLE}>
      Filters met on <span className={idClassName}>{summary.transcriptId ?? 'another annotation'}</span>
      {summary.gene ? ` (${summary.gene})` : ''}
      {summary.details.length ? `: ${summary.details.join(' · ')}` : ''}
    </p>
  );
}
