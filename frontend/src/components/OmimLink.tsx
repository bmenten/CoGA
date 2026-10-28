import React from 'react';

/**
 * The small "(OMIM)" link after a disease name, the same on every finding page. It opens a
 * tab (it leaves CoGA), and its accessible name says which disease and that it does, since
 * the visible "OMIM" alone would read the same for every disease in a list.
 */
const OmimLink: React.FC<{ href: string; subject: string }> = ({ href, subject }) => {
  const label = `Open ${subject} on OMIM (opens in a new tab)`;
  return (
    <span className="omim-link-wrap">
      (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="omim-link"
        aria-label={label}
        title={label}
      >
        OMIM
      </a>
      )
    </span>
  );
};

export default OmimLink;
