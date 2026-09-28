# TF-14 — Data Protection Impact Assessment (DPIA)

| Field | Value |
| --- | --- |
| Document ID | TF-14 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review — **requires UZ Gent DPO consultation** |
| Owner | ‹CMGG software lead + UZ Gent DPO› |
| Approver | ‹UZ Gent Data Protection Officer› |
| Date | 2026-06-25 |
| Basis | GDPR (EU) 2016/679 Art. 9 (special-category data), Art. 35 (DPIA); Belgian data-protection law |

> A DPIA is **required** (GDPR Art. 35): CoGA processes **genetic and health data** (special
> category) on a large scale in a clinical setting. This draft frames the assessment; it must
> be completed and signed off with the **UZ Gent DPO**.

---

## 1. Processing overview
| Item | Description |
| --- | --- |
| Purpose | Clinical genomic interpretation and reporting (diagnosis, screening, PGT, carrier screening) within CMGG. |
| Controller | Ghent University Hospital (UZ Gent) / CMGG. |
| Data subjects | Patients, pregnant individuals + partner, prospective parents/couples, embryos (and, via family/pedigree, relatives). |
| Categories of data | **Special category:** genetic data (variants, genotypes, haplotypes), health/clinical status, phenotype (HPO), reproductive/pregnancy data; identifiers/pedigree metadata; user account data (staff). |
| Processing operations | Ingestion of validated genomic files, storage (Postgres + ClickHouse + file/object store), filtering/analysis/visualization, classification, reporting, audit logging. |
| Hosting / processors | **On premises** (Docker Compose within CMGG/UZ Gent): no processor. **Google Cloud** (codified in `terraform/`, not yet applied): Google Cloud stores and processes the data as a **processor** — Cloud SQL, a ClickHouse VM and CMEK-encrypted storage buckets in the configured region, served through an internet-facing HTTPS load balancer. **🔲 OWNER/DPO (#518):** confirm the production target; for Google Cloud, the data-processing agreement, the region (EU), any sub-processors and transfers, and network restriction of the load balancer (#364). |
| Scale & duration | All CMGG cases across the five applications; retention per clinical/legal record requirements. |

## 2. Lawful basis & special-category condition
- **Lawful basis (Art. 6):** ‹typically Art. 6(1)(e) public-interest task / (c) legal obligation for healthcare — confirm with DPO›.
- **Art. 9 condition:** ‹Art. 9(2)(h) — medical diagnosis, provision of health care/treatment, management of health systems, under EU/Member-State law and subject to professional secrecy — confirm; reproductive screening/PGT specifics to review with DPO.›
- Processing is by/under the responsibility of professionals bound by **professional secrecy**.

## 3. Necessity & proportionality
- **Data minimization:** CoGA processes only data needed for interpretation; project-scoped access limits exposure; query strings/log bodies are minimized/masked ([security-posture.md](../security-posture.md)).
- **Accuracy & integrity:** version-pinned provenance, evidence snapshots, immutable audit, content-hashed sign-out support accuracy and integrity of records ([clinical-traceability.md](../clinical-traceability.md)).
- **Storage limitation:** retention aligned to clinical-record obligations ‹define period with DPO›.
- **Pseudonymization:** **🔲 INPUT NEEDED** — state to what extent records are pseudonymized within CoGA vs identifiable.

## 4. Data subjects' rights
‹How access/rectification/erasure/restriction are handled given that signed reports and audit
trails are intentionally **immutable** for medico-legal reasons — reconcile erasure rights vs
record-keeping obligations with the DPO; the append-only design has a documented user-unlink
cascade that nulls `user_id` while preserving the actor record.›

## 5. Security measures
Per [TF-13 Cybersecurity](TF-13-cybersecurity.md): RBAC, append-only audit, authentication,
PHI download scoping, refuse-to-start on default secrets; **plus the open deployment items**
(encryption at rest, TLS, secrets management, byte-level download audit) which are
**prerequisites** for processing real PHI at clinical scale.

## 6. Risk assessment to rights & freedoms
| Risk | Mitigation | Residual |
| --- | --- | --- |
| Unauthorized access to genetic data | RBAC, audit, encryption (S-1..S-8 in TF-13) | ‹…› |
| Re-identification from genomic data | Access control, minimization, professional-secrecy context | ‹…› |
| Incidental findings / familial implications | Clinical governance, reporting policy ‹CMGG SOP› | ‹…› |
| Data integrity / wrong record | Immutable audit, content-hash, provenance | ‹…› |
| Breach (confidentiality) | TF-13 controls + incident process (TF-17) + breach notification per GDPR Art. 33/34 | ‹…› |
| Processing by a cloud provider (Google Cloud deployment only) | Data-processing agreement, EU region, CMEK encryption at rest, TLS in transit, least-privilege service accounts, audit logging (TF-13; `terraform/`) | ‹… — owner/DPO, #518› |

## 7. Consultation & sign-off
- **UZ Gent DPO opinion:** ‹to be recorded›. Per the governing SOP **H11.1-OP5 §4.2**, the DPO's advice is obtained during the **functional-analysis** phase whenever personal data is processed — so DPO consultation is a standing step of CoGA's lifecycle, not a one-off.
- Prior consultation with the supervisory authority (Belgian DPA) **only if** high residual risk cannot be mitigated (Art. 36) — ‹assess with DPO›.
- Review on material change to processing or device (TF-18) and at the PMS cadence (TF-16).
