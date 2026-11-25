import React from 'react';
import type { NiptRecessiveAlleleOut, NiptRecessiveGeneOut } from '../../lib/apiSchema.generated';
import { RECESSIVE_STATUS_LABELS, pct, recessiveProbabilitiesText, recessiveStatusTone } from './niptClassification';

// "affected 92.0%, carrier 7.0%, unaffected 1.0%" for a gene's probabilities.
const describeProbabilities = (gene: NiptRecessiveGeneOut): string =>
  recessiveProbabilitiesText(gene.risk, gene.carrier_probability, gene.unaffected_probability, gene.mode === 'x_linked_son');

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
 * The recessive view's genes: where the mother and the father each carry an allele (and the
 * X-linked genes in which a son's mother carries one), the probability that the fetus
 * inherited each, and the fetal status with its probabilities.
 */
const NiptRecessiveGenes: React.FC<NiptRecessiveGenesProps> = ({ genes }) => (
  <section className="surface-card space-y-2" aria-label="Recessive fetal status by gene">
    <h2 className="section-title">Recessive fetal status by gene</h2>
    <p className="table-subtle">
      Genes where both parents carry an allele among the listed variants, and X-linked genes in which a son&apos;s
      mother carries one (her allele alone makes him affected). For the pair most likely to make the fetus affected:
      the fetal status and the probabilities that the fetus is affected (it inherited a maternal and a paternal
      allele; at a site both parents carry, it is homozygous), a carrier, or neither, assuming the alleles are
      pathogenic and in trans. A homozygous parent always passes the allele. An inheritance between 20% and 80%, or one
      the cfDNA did not tell, leaves the status undetermined. A carrier couple&apos;s prior is 25%. Screening, not
      diagnosis: confirm with invasive testing.
    </p>
    {genes.length === 0 ? (
      <p className="table-subtle">
        No gene where both parents carry an allele, or a son&apos;s mother an X-linked one, among the variants that
        match.
      </p>
    ) : (
      <div className="data-table-shell overflow-x-auto">
        <table>
          <thead>
            <tr>
              <th>Gene</th>
              <th>Fetal status</th>
              <th>Maternal allele(s)</th>
              <th>Paternal allele(s)</th>
            </tr>
          </thead>
          <tbody>
            {genes.map((gene) => (
              <tr key={gene.gene}>
                <td className="font-semibold">
                  {gene.gene}
                  {gene.mode !== 'autosomal' ? <p className="table-subtle">X-linked</p> : null}
                </td>
                <td>
                  <span className={recessiveStatusTone(gene.status)}>
                    {RECESSIVE_STATUS_LABELS[gene.status] ?? gene.status}
                  </span>
                  <p className="table-subtle">{describeProbabilities(gene)}</p>
                  {gene.risk_uses_prior ? (
                    <p className="table-subtle">An inheritance the cfDNA did not tell is read at its 50% prior.</p>
                  ) : null}
                </td>
                <td>
                  <Alleles alleles={gene.maternal} highlight={gene.maternal_variant_id} />
                </td>
                <td>
                  {gene.mode === 'x_linked_son' ? (
                    <p className="table-subtle">A son does not inherit his father&apos;s X.</p>
                  ) : (
                    <Alleles alleles={gene.paternal} highlight={gene.paternal_variant_id} />
                  )}
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
