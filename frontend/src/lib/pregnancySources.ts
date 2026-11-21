/**
 * The storage sources a pregnancy's NIPT keeps as its own (backend
 * `family_package_records.pregnancy_source`): the source's name, then `_p` and eight hex digits of
 * the pregnancy (`nipt_p1a2b3c4d`, `ccnv_p1a2b3c4d`, `web_p1a2b3c4d`). A family holds several
 * pregnancies, and each reads the data its own package delivered.
 */
const PREGNANCY_SOURCE = /^([a-z][a-z0-9_]*)_p[0-9a-f]{8}$/;

/**
 * The source a pregnancy's source is kept for, as a reader tells what a source holds (its label,
 * its axis, its order): `ccnv` for `ccnv_p1a2b3c4d`; any other source itself (backend
 * `family_package_records.source_base`).
 */
export const sourceBase = (source: string | null | undefined): string => {
  const text = source ?? '';
  const match = PREGNANCY_SOURCE.exec(text);
  return match ? match[1] : text;
};
