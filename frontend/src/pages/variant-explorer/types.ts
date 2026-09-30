// API response types for the Global Small Variant Explorer
// (mirrors backend app/schemas.py VariantExplorer* / GlobalVariant* models).

export interface VariantExplorerAssembly {
  assembly_id: string;
  assembly_name: string;
  version?: string | null;
  species_name?: string | null;
  project_count: number;
}

export interface GlobalVariantRow {
  key: string;
  variant_id: string;
  chr: string;
  pos: number;
  ref?: string | null;
  alt?: string | null;
  rsid?: string | null;
  type: string;
  gene?: string | null;
  gene_symbols: string[];
  impact?: string | null;
  consequence?: string | null;
  effects: string[];
  hgvsc?: string | null;
  hgvsp?: string | null;
  clinvar?: string | null;
  classification?: string | null;
  tags: string[];
  total_samples: number;
  het_samples: number;
  hom_samples: number;
  total_families: number;
  last_seen?: string | null;
}

export interface GlobalVariantPage {
  total: number;
  total_is_estimated: boolean;
  page_size: number;
  // Keyset cursor for the next page; null on the last page.
  next_cursor?: string | null;
  assembly_id?: string | null;
  assembly_name?: string | null;
  variants: GlobalVariantRow[];
}

export interface VariantCarrierSample {
  sample_id: string;
  individual_name?: string | null;
  role?: string | null;
  genotype: string;
  zygosity: string;
  family_id: string;
  family_uuid: string;
  project_id?: string | null;
  project_name?: string | null;
  phenotype_summary?: string | null;
}

export interface VariantCarrierFamilyGroup {
  family_id: string;
  family_uuid: string;
  project_id?: string | null;
  project_name?: string | null;
  carrier_count: number;
  samples: VariantCarrierSample[];
}

export interface VariantCarriers {
  key: string;
  variant_id?: string | null;
  total_families: number;
  total_samples: number;
  het_samples: number;
  hom_samples: number;
  truncated?: boolean;
  families: VariantCarrierFamilyGroup[];
}

export type CarrierModalMode = 'het' | 'hom' | 'all' | 'families';
