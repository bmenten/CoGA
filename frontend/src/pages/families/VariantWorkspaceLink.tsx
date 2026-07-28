import React from 'react';
import { Link } from 'react-router';

/**
 * A variant-workspace navigation control. Renders a link only when its data type
 * is available; when there is no underlying data the control is omitted entirely
 * so the family page surfaces only the workspaces this family actually has.
 */
const VariantWorkspaceLink: React.FC<{
  active: boolean;
  to: string;
  className: string;
  children: React.ReactNode;
}> = ({ active, to, className, children }) =>
  active ? (
    <Link to={to} className={`${className} hover:no-underline`}>
      {children}
    </Link>
  ) : null;

export default VariantWorkspaceLink;
