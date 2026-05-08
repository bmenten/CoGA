import { cssVar } from "./colors";

const STAIN_COLOR_VARS: Record<string, string> = {
  gneg: "--color-stain-gneg",
  gpos25: "--color-stain-gpos25",
  gpos50: "--color-stain-gpos50",
  gpos75: "--color-stain-gpos75",
  gpos100: "--color-stain-gpos100",
  gvar: "--color-stain-gvar",
  acen: "--color-stain-acen",
  stalk: "--color-stain-stalk",
};

export const getStainColor = (stain: string): string =>
  cssVar(STAIN_COLOR_VARS[stain] || "--color-white");
