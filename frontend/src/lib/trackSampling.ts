const clamp = (value: number, min: number, max: number): number =>
  Math.min(Math.max(value, min), max);

export const getTrackVariantLimit = (width: number): number => {
  if (!Number.isFinite(width) || width <= 0) {
    return 800;
  }
  return clamp(Math.round(width * 2), 400, 4000);
};

export const getTrackBinLimit = (width: number): number => {
  if (!Number.isFinite(width) || width <= 0) {
    return 1200;
  }
  return clamp(Math.round(width * 2), 300, 6000);
};

export const getTrackSegmentLimit = (width: number): number => {
  if (!Number.isFinite(width) || width <= 0) {
    return 1600;
  }
  return clamp(Math.round(width * 1.5), 200, 4000);
};

export const getAdaptiveTrackWindow = (
  span: number,
  width: number,
  minimumWindow: number,
): number => {
  if (!Number.isFinite(span) || span <= 0) {
    return Math.max(Math.round(minimumWindow), 1);
  }
  const safeWidth = Number.isFinite(width) && width > 0 ? width : 800;
  const targetBins = Math.max(Math.round(safeWidth / 2), 1);
  const dynamicWindow = Math.ceil(span / targetBins);
  return Math.max(Math.round(minimumWindow), dynamicWindow, 1);
};
