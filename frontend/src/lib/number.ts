/** `value` held within [min, max]. */
export const clamp = (value: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, value));

/** A number that is not NaN or ±Infinity; anything else (undefined, null, a string) is not. */
export const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);
