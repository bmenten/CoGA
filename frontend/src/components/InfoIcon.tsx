import React from 'react';
import InfoTip from './InfoTip';

/**
 * A small "i" that holds supplementary, explanatory text in a tooltip (hover or keyboard
 * focus): how to read a region, what a legend colour means. `name` is what a screen reader
 * reads for the icon ("How to read SMN1/SMN2"); the text is its description while shown.
 *
 * Only for explanation. A result, an interpretation of this family's data or a warning stays
 * on the page, never behind this icon.
 */
const InfoIcon: React.FC<{ name: string; text: string; className?: string }> = ({
  name,
  text,
  className,
}) => (
  <InfoTip staticChild label={text} className={`info-icon${className ? ` ${className}` : ''}`}>
    <span aria-hidden="true">i</span>
    <span className="sr-only">{name}</span>
  </InfoTip>
);

export default InfoIcon;
