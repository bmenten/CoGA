import { createContext, useContext } from 'react';

/**
 * Project access levels (SEC-1). Each project member is an analyst, who reads and changes the
 * project's records, or a reader, who only reads them; an administrator has every right in
 * every project. The server refuses a reader's change; the screens hide or disable the
 * controls a reader may not use, reading the caller's own level from the record they show
 * (`read_only` on a family or project).
 */

/** What a disabled control says to a reader. */
export const READ_ONLY_HINT =
  "Read-only access: you are a reader in this family's project, so you can view and export it but not change it.";

/**
 * A case is assigned to, and recorded as reviewed by, an active analyst of one of the family's
 * projects or an active administrator. One stored before its user became a reader (or left the
 * family's projects) stays shown with this mark until it is changed.
 */
export const NOT_ASSIGNABLE_MARK = ' (not eligible)';

/** What the picker of such a value says. */
export const NOT_ASSIGNABLE_HINT =
  "Not eligible: not an active analyst of this family's projects or an administrator. It stays until you change it.";

/**
 * Whether the family on screen is read-only for the signed-in user. A family page provides it
 * from the family record; a control deep in the page reads it with `useFamilyReadOnly`.
 * Outside a provider nothing is read-only, so a control shown without one keeps working.
 */
export const FamilyReadOnlyContext = createContext<boolean>(false);

export function useFamilyReadOnly(): boolean {
  return useContext(FamilyReadOnlyContext);
}

/** A member's level in a project, as the admin pages name it. */
export type ProjectAccessLevel = 'analyst' | 'reader';

export function memberAccessLevel(
  readerIds: readonly string[] | null | undefined,
  userId: string,
): ProjectAccessLevel {
  return readerIds?.includes(userId) ? 'reader' : 'analyst';
}
