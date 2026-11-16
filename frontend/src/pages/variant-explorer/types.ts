// The Global Small Variant Explorer's responses, read through their generated types.
import type {
  ExplorerFrequencyCellOut,
  ExplorerFrequencyOut,
  ExplorerGenePageOut,
  ExplorerGeneRowOut,
  ExplorerTranscriptPageOut,
  ExplorerTranscriptRowOut,
  GlobalVariantPageOut,
  GlobalVariantRowOut,
  VariantCarriersOut,
  VariantExplorerAssemblyOut,
} from '../../lib/apiSchema.generated';

export type VariantExplorerAssembly = VariantExplorerAssemblyOut;

export type GlobalVariantRow = GlobalVariantRowOut;

// `next_cursor` is the keyset cursor of the next page; null on the last page.
export type GlobalVariantPage = GlobalVariantPageOut;

export type VariantCarriers = VariantCarriersOut;

export type CarrierModalMode = 'het' | 'hom' | 'all' | 'families';

// The gene, transcript and cohort-frequency views, over the same filters.
export type ExplorerGenePage = ExplorerGenePageOut;
export type ExplorerGeneRow = ExplorerGeneRowOut;
export type ExplorerTranscriptPage = ExplorerTranscriptPageOut;
export type ExplorerTranscriptRow = ExplorerTranscriptRowOut;
export type ExplorerFrequency = ExplorerFrequencyOut;
export type ExplorerFrequencyCell = ExplorerFrequencyCellOut;
