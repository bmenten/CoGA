export interface IdeogramBandLike {
  name: string;
  start: number;
  end: number;
  stain: string;
}

export type GradientStop = {
  offset: string;
  stopColor: string;
  stopOpacity?: number;
};

export const getAcenDirection = (
  band: Pick<IdeogramBandLike, "name" | "start" | "end">,
  chromLength: number,
): "p" | "q" => {
  const bandName = `${band.name || ""}`.toLowerCase();
  if (/(^|[^a-z])p\d/.test(bandName) || bandName.startsWith("p")) return "p";
  if (/(^|[^a-z])q\d/.test(bandName) || bandName.startsWith("q")) return "q";
  return band.start + band.end <= chromLength ? "p" : "q";
};

export const blendHex = (hex: string, target: string, amount: number): string => {
  const normalize = (value: string) => {
    if (!value.startsWith("#")) return null;
    const trimmed = value.slice(1);
    if (trimmed.length === 3) {
      return trimmed
        .split("")
        .map((char) => char + char)
        .join("");
    }
    if (trimmed.length === 6) return trimmed;
    return null;
  };

  const sourceHex = normalize(hex);
  const targetHex = normalize(target);
  if (!sourceHex || !targetHex) return hex;

  const source = [
    parseInt(sourceHex.slice(0, 2), 16),
    parseInt(sourceHex.slice(2, 4), 16),
    parseInt(sourceHex.slice(4, 6), 16),
  ];
  const destination = [
    parseInt(targetHex.slice(0, 2), 16),
    parseInt(targetHex.slice(2, 4), 16),
    parseInt(targetHex.slice(4, 6), 16),
  ];

  const blended = source.map((component, index) =>
    Math.round(component + (destination[index] - component) * amount),
  );

  return `#${blended.map((component) => component.toString(16).padStart(2, "0")).join("")}`;
};

export const getBandGradientStops = (
  color: string,
  finish: "standard" | "glossy",
): GradientStop[] => {
  if (finish === "glossy") {
    return [
      { offset: "0%", stopColor: blendHex(color, "#ffffff", 0.28) },
      { offset: "18%", stopColor: blendHex(color, "#ffffff", 0.14) },
      { offset: "52%", stopColor: color },
      { offset: "100%", stopColor: blendHex(color, "#000000", 0.12) },
    ];
  }

  return [
    { offset: "0%", stopColor: color, stopOpacity: 0.85 },
    { offset: "50%", stopColor: color, stopOpacity: 1 },
    { offset: "100%", stopColor: color, stopOpacity: 0.85 },
  ];
};

export const collapseBandsForResolution = (
  bands: IdeogramBandLike[],
  chromLength: number,
  width: number,
  resolution: "full" | "compact",
): IdeogramBandLike[] => {
  if (resolution !== "compact" || width <= 0 || bands.length <= 1) {
    return bands;
  }

  const minBandWidthPx = 4;
  const bucketCount = Math.max(1, Math.floor(width / minBandWidthPx));
  const bucketBp = Math.max(chromLength / bucketCount, 1);
  const collapsed: IdeogramBandLike[] = [];

  for (let bucketStart = 0; bucketStart < chromLength; bucketStart += bucketBp) {
    const bucketEnd = Math.min(chromLength, bucketStart + bucketBp);
    let dominantStain = bands[0]?.stain || "gneg";
    let dominantScore = -1;

    bands.forEach((band) => {
      const overlapStart = Math.max(bucketStart, band.start);
      const overlapEnd = Math.min(bucketEnd, band.end);
      const overlap = overlapEnd - overlapStart;
      if (overlap <= 0) return;
      if (overlap > dominantScore) {
        dominantScore = overlap;
        dominantStain = band.stain;
      }
    });

    const previous = collapsed[collapsed.length - 1];
    if (previous && previous.stain === dominantStain) {
      previous.end = bucketEnd;
    } else {
      collapsed.push({
        name: dominantStain,
        start: bucketStart,
        end: bucketEnd,
        stain: dominantStain,
      });
    }
  }

  const acenBands = bands.filter((band) => band.stain === "acen");
  if (acenBands.length === 0) {
    return collapsed;
  }

  let withAcen = [...collapsed];
  acenBands.forEach((acenBand, index) => {
    const next: IdeogramBandLike[] = [];
    withAcen.forEach((band) => {
      const overlaps = band.start < acenBand.end && band.end > acenBand.start;
      if (!overlaps) {
        next.push(band);
        return;
      }

      if (band.start < acenBand.start) {
        next.push({
          ...band,
          end: acenBand.start,
        });
      }

      if (band.end > acenBand.end) {
        next.push({
          ...band,
          start: acenBand.end,
        });
      }
    });

    next.push({
      ...acenBand,
      name: acenBand.name || `acen-${index + 1}`,
    });

    next.sort((left, right) => left.start - right.start);
    withAcen = next.reduce<IdeogramBandLike[]>((acc, band) => {
      const previous = acc[acc.length - 1];
      if (
        previous &&
        previous.stain === band.stain &&
        previous.stain !== "acen" &&
        Math.abs(previous.end - band.start) < 1
      ) {
        previous.end = band.end;
        return acc;
      }

      acc.push({ ...band });
      return acc;
    }, []);
  });

  return withAcen;
};
