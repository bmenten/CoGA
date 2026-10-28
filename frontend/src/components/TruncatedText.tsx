import React from 'react';
import InfoTip from './InfoTip';

/**
 * Text cut to its first `max` characters and "…" on screen, with the whole of it in a
 * tooltip (hover or keyboard focus) and in the DOM for a screen reader, which reads the
 * full value and never the cut one. Display only: copying, filtering and the record keep
 * the full value. Text no longer than `max` renders as it is, without a tooltip.
 *
 * For values that only fill space when long — a repeat motif or sequence, a disease name
 * in a dense row — never for a result or a warning, whose words all count.
 */
const TruncatedText: React.FC<{
  text: string;
  max: number;
  className?: string;
  /** Monospaced, for a nucleotide sequence. */
  mono?: boolean;
}> = ({ text, max, className, mono = false }) => {
  const classes = ['truncated-text', mono ? 'truncated-text--mono' : '', className ?? '']
    .filter(Boolean)
    .join(' ');
  if (text.length <= max) {
    return <span className={classes}>{text}</span>;
  }
  const shown = `${text.slice(0, max).trimEnd()}…`;
  return (
    <InfoTip staticChild label={text} className={`${classes} truncated-text--cut`}>
      <span aria-hidden="true">{shown}</span>
      <span className="sr-only">{text}</span>
    </InfoTip>
  );
};

export default TruncatedText;
