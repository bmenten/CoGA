import React, { useState } from 'react';
import { memberAccessLevel, type ProjectAccessLevel } from '../lib/familyAccess';

/** A user or a project that can be given access, with the label the picker shows of it. */
export interface AccessEntry {
  id: string;
  label: string;
  /** What the level list's accessible name calls it ("Access level of …"); the label if unset. */
  name?: string;
}

/** The words of one side of the picker: a project's members, or an account's projects. */
export interface AccessLevelPickerText {
  /** The heading of the column that names each entry with access: "Member", "Project". */
  column: string;
  /** Shown while nothing has access. */
  none: React.ReactNode;
  /** The search's label: "Add a member". */
  add: string;
  placeholder: string;
  /** Shown when the search finds nothing without access. */
  noMatch: (search: string) => React.ReactNode;
  /** Shown once every entry has access. */
  all: string;
}

/** How many entries without access the picker lists at once; a search finds the others. */
const CANDIDATE_LIMIT = 8;

const byLabel = (left: AccessEntry, right: AccessEntry) => left.label.localeCompare(right.label);

/**
 * Who has access, and at what level: an analyst reads and changes the project's
 * records, a reader only reads them. It is the same memberships seen from either side: a
 * project's members on *Projects & access*, an account's projects on *Users*. Those with access
 * are a table, each with their level and Remove; one is added from a search that lists a few
 * entries without access at a time, so the picker stays short however many there are. An entry
 * added starts as an analyst. Nothing is saved here: the page around it saves the lists.
 */
const AccessLevelPicker: React.FC<{
  entries: AccessEntry[];
  /** The ids with access, and those among them that only read. */
  memberIds: readonly string[];
  readerIds: readonly string[];
  onToggle: (id: string) => void;
  onLevel: (id: string, level: ProjectAccessLevel) => void;
  text: AccessLevelPickerText;
}> = ({ entries, memberIds, readerIds, onToggle, onLevel, text }) => {
  const [search, setSearch] = useState('');
  const query = search.trim().toLowerCase();
  const members = entries.filter((entry) => memberIds.includes(entry.id)).sort(byLabel);
  const others = entries.filter((entry) => !memberIds.includes(entry.id));
  const matches = (
    query ? others.filter((entry) => entry.label.toLowerCase().includes(query)) : others
  ).sort(byLabel);
  const shown = matches.slice(0, CANDIDATE_LIMIT);

  return (
    <div className="project-member-picker">
      {members.length > 0 ? (
        <div className="data-table-shell data-table-shell--fit">
          <table className="analysis-table project-member-table">
            <thead>
              <tr>
                <th scope="col">{text.column}</th>
                <th scope="col">Access</th>
                <th scope="col">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {members.map((entry) => (
                <tr key={entry.id}>
                  <td>{entry.label}</td>
                  <td>
                    <select
                      aria-label={`Access level of ${entry.name ?? entry.label}`}
                      value={memberAccessLevel(readerIds, entry.id)}
                      onChange={(event) => onLevel(entry.id, event.target.value as ProjectAccessLevel)}
                    >
                      <option value="analyst">Analyst (read and change)</option>
                      <option value="reader">Reader (read only)</option>
                    </select>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="button-ghost project-member-action"
                      aria-label={`Remove ${entry.label}`}
                      onClick={() => onToggle(entry.id)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : memberIds.length === 0 ? (
        <p className="table-empty">{text.none}</p>
      ) : null}

      {others.length > 0 ? (
        <div className="project-member-add">
          <label className="field-label project-member-search">
            {text.add}
            <input
              type="search"
              value={search}
              placeholder={text.placeholder}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                // Enter adds the one entry the search found; it never submits the form around it.
                if (event.key !== 'Enter') return;
                event.preventDefault();
                if (query && matches.length === 1) {
                  onToggle(matches[0].id);
                  setSearch('');
                }
              }}
            />
          </label>
          {shown.length > 0 ? (
            <ul className="project-member-candidates">
              {shown.map((entry) => (
                <li key={entry.id}>
                  <span>{entry.label}</span>
                  <button
                    type="button"
                    className="button-secondary project-member-action"
                    aria-label={`Add ${entry.label}`}
                    onClick={() => onToggle(entry.id)}
                  >
                    Add
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="table-empty">{text.noMatch(search.trim())}</p>
          )}
          {matches.length > shown.length ? (
            <p className="table-subtle">
              {shown.length} of {matches.length} shown: {text.placeholder.toLowerCase()} for the others.
            </p>
          ) : null}
        </div>
      ) : (
        <p className="table-subtle">{text.all}</p>
      )}
    </div>
  );
};

export default AccessLevelPicker;
