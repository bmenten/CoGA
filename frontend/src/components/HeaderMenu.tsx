import React, { useEffect, useRef, useState } from 'react';
import { NavLink } from 'react-router';
import { isAdmin } from '../lib/auth';

/**
 * The main sections — the projects, the explorers, the gene panels, the guide and, for an
 * administrator, Admin — behind a small arrow at the right of the header. A disclosure, not
 * an ARIA menu: the arrow says whether the list is open, the entries are ordinary links,
 * and Escape or a click elsewhere closes it. The dashboard keeps its own buttons to the same
 * places.
 */
const HeaderMenu: React.FC = () => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      toggleRef.current?.focus();
    };
    document.addEventListener('pointerdown', closeOnOutsidePointer);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePointer);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [open]);

  const close = () => setOpen(false);

  return (
    <div className="app-menu" ref={rootRef}>
      <button
        ref={toggleRef}
        type="button"
        className="button-ghost app-header-control app-menu-toggle"
        aria-label="Main menu"
        title="Main menu"
        aria-expanded={open}
        aria-controls="app-menu-panel"
        onClick={() => setOpen((current) => !current)}
      >
        <svg className="app-menu-caret" viewBox="0 0 10 6" aria-hidden="true">
          <path d="M1 1l4 4 4-4" />
        </svg>
      </button>
      <nav id="app-menu-panel" className="app-menu-panel" aria-label="Main" hidden={!open}>
        <NavLink to="/dashboard" onClick={close}>
          Projects
        </NavLink>
        <NavLink to="/variant-explorer" onClick={close}>
          Variant explorer
        </NavLink>
        <NavLink to="/genes" onClick={close}>
          Gene explorer
        </NavLink>
        <NavLink to="/cnv-explorer" onClick={close}>
          CNV explorer
        </NavLink>
        <NavLink to="/panels" onClick={close}>
          Panels
        </NavLink>
        {isAdmin() && (
          <NavLink to="/admin" onClick={close}>
            Admin
          </NavLink>
        )}
        <NavLink to="/docs" onClick={close}>
          User guide
        </NavLink>
      </nav>
    </div>
  );
};

export default HeaderMenu;
