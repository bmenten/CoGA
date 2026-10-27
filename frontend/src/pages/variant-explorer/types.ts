// The Global Small Variant Explorer's responses, read through their generated types.
import type {
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
