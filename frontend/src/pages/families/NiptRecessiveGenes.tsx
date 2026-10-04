import React from 'react';
import type { NiptRecessiveAlleleOut, NiptRecessiveGeneOut } from '../../lib/apiSchema.generated';
import { pct } from './niptClassification';

// A gene's fetal risk against a carrier couple's 25% prior: high when the cfDNA says the
// fetus very likely inherited both alleles, low when one allele very likely was not.
export const recessiveRiskTone = (risk?: number | null): string => {
  if (risk == null) return 'table-chip table-chip--neutral';
  if (risk >= 0.5) return 'table-chip table-chip--critical';
  if (risk <= 0.05) return 'table-chip table-chip--success';
  return 'table-chip table-chip--warning';
};

const Alleles: React.FC<{ alleles: NiptRecessiveAlleleOut[]; highlight: string | null }> = ({
  alleles,
  highlight,
}) => (
  <ul className="nipt-recessive-alleles">
    {alleles.map((allele) => (
      <li key={allele.variant_id} className={allele.variant_id === highlight ? 'nipt-recessive-allele--pair' : undefined}>
        <span className="table-mono">{allele.variant_id}</span>{' '}
        <span className="table-subtle">
          inherited {allele.inherited_probability == null ? 'not told' : pct(allele.inherited_probability)}
          {allele.category != null ? ` · cat ${allele.category}` : ''}
        </span>
        {allele.note ? <span className="table-chip table-chip--warning">{allele.note}</span> : null}
      </li>
    ))}
  </ul>
);

interface NiptRecessiveGenesProps {
  genes: NiptRecessiveGeneOut[];
}

/**
 * The recessive view's genes: where the mother and the father each carry an allele, the
 * probability that the fetus inherited each, and the fetal risk (one of each).
 */
const NiptRecessiveGenes: React.FC<NiptRecessiveGenesProps> = ({ genes }) => (
  <section className="surface-card space-y-2" aria-label="Recessive fetal risk by gene">
    <h2 className="section-title">Recessive fetal risk by gene</h2>
    <p className="table-subtle">
      Genes where both parents carry an allele among the listed variants. The fetal risk is the probability that the
      fetus inherited a maternal and a paternal allele (the highest pair; at a site both parents carry, that the fetus is
      homozygous), assuming both alleles are pathogenic and the maternal and paternal ones in trans. A carrier
      couple&apos;s prior is 25%. Screening, not diagnosis: confirm with invasive testing.
    </p>
    {genes.length === 0 ? (
      <p className="table-subtle">No gene where both parents carry an allele among the variants that match.</p>
    ) : (
      <div className="data-table-shell overflow-x-auto">
        <table>
          <thead>
            <tr>
              <th>Gene</th>
              <th>Fetal risk</th>
              <th>Maternal allele(s)</th>
              <th>Paternal allele(s)</th>
            </tr>
          </thead>
          <tbody>
            {genes.map((gene) => (
              <tr key={gene.gene}>
                <td className="font-semibold">{gene.gene}</td>
                <td>
                  <span className={recessiveRiskTone(gene.risk)}>{gene.risk == null ? '—' : pct(gene.risk)}</span>
                  {gene.risk_uses_prior ? (
                    <p className="table-subtle">An inheritance the cfDNA did not tell is read at its 50% prior.</p>
                  ) : null}
                </td>
                <td>
                  <Alleles alleles={gene.maternal} highlight={gene.maternal_variant_id} />
                </td>
                <td>
                  <Alleles alleles={gene.paternal} highlight={gene.paternal_variant_id} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )}
  </section>
);

export default NiptRecessiveGenes;
