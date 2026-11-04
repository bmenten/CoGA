# TF-14 — Data Protection Impact Assessment (DPIA)

| Field | Value |
| --- | --- |
| Document ID | TF-14 |
| Version | v0.1 DRAFT |
| Status | Draft for internal review. A DPIA signed with the UZ Gent DPO exists outside this repository (owner, 2026-09-29); this draft is to be reconciled with it and filed (#518). |
| Owner | ‹CMGG software lead + UZ Gent DPO› |
| Approver | ‹UZ Gent Data Protection Officer› |
| Date | 2026-06-25 |
| Basis | GDPR (EU) 2016/679 Art. 9 (special-category data), Art. 35 (DPIA); Belgian data-protection law |

> A DPIA is **required** (GDPR Art. 35): CoGA processes **genetic and health data** (special
> category) on a large scale in a clinical setting. The signed DPIA and this draft's
> reconciliation with it: see Status (#518).

---

## 1. Processing overview
| Item | Description |
| --- | --- |
| Purpose | Clinical genomic interpretation and reporting (diagnosis, screening, PGT, carrier screening) within CMGG. |
| Controller | Ghent University Hospital (UZ Gent) / CMGG. |
| Data subjects | Patients, pregnant individuals + partner, prospective parents/couples, embryos (and, via family/pedigree, relatives). |
| Categories of data | **Special category:** genetic data (variants, genotypes, haplotypes), health/clinical status, phenotype (HPO), reproductive/pregnancy data; identifiers/pedigree metadata; user account data (staff). |
| Processing operations | Ingestion of validated genomic files, storage (Postgres + ClickHouse + file/object store), filtering/analysis/visualization, classification, reporting, audit logging. |
| Hosting / processors | Production target: **Google Cloud**, deployed with Terraform (owner decision, #627). The configuration is in `terraform/` and not yet applied, so no patient data is processed yet. Google Cloud will store and process the data as a **processor** for the institution: Cloud SQL, a ClickHouse VM and CMEK-encrypted storage buckets in the configured region (default `europe-west1`), behind an HTTPS load balancer that go-live restricts to institutional networks ([TF-13 §3](TF-13-cybersecurity.md)). Local development uses Docker Compose on synthetic data only. The data-processing agreement with Google is being signed (owner, 2026-09-29). **🔲 OWNER/DPO (#518):** file the signed DPIA and the executed agreement with this file; record the region, any sub-processors and transfers; settle whether Google is a *processor* (this file, TF-02) or a *sub-processor*. |
| Scale & duration | All CMGG cases across the five applications; retention per clinical/legal record requirements. |

## 2. Lawful basis & special-category condition
- **Lawful basis (Art. 6):** ‹typically Art. 6(1)(e) public-interest task / (c) legal obligation for healthcare — confirm with DPO›.
- **Art. 9 condition:** ‹Art. 9(2)(h) — medical diagnosis, provision of health care/treatment, management of health systems, under EU/Member-State law and subject to professional secrecy — confirm; reproductive screening/PGT specifics to review with DPO.›
- Processing is by/under the responsibility of professionals bound by **professional secrecy**.

## 3. Necessity & proportionality
- **Data minimization:** CoGA processes only the data interpretation needs; project-scoped access limits exposure; query strings and log bodies are minimized or masked ([security-posture.md](../security-posture.md)).
- **Accuracy & integrity:** version-pinned provenance, evidence snapshots, immutable audit and content-hashed sign-out keep records accurate and intact ([clinical-traceability.md](../clinical-traceability.md)).
- **Storage limitation:** retention aligned to clinical-record obligations ‹define period with DPO›.
- **Pseudonymization:** **🔲 INPUT NEEDED** — state how far records in CoGA are pseudonymized or identifiable.

## 4. Data subjects' rights
‹How access, rectification, erasure and restriction are handled, given that signed reports and
audit trails are **immutable** by design, for medico-legal reasons. Reconcile erasure rights with
record-keeping obligations with the DPO. The append-only design has a documented user-unlink
cascade: it nulls `user_id` and keeps the actor record.›

## 5. Security measures
Per [TF-13 Cybersecurity](TF-13-cybersecurity.md): RBAC, append-only audit, authentication,
PHI download scoping, refuse-to-start on default secrets. The open deployment items and
go-live switches of [TF-13 §3](TF-13-cybersecurity.md) are **prerequisites** for processing real
patient data.

## 6. Risk assessment to rights & freedoms
| Risk | Mitigation | Residual |
| --- | --- | --- |
| Unauthorized access to genetic data | RBAC, audit, encryption (S-1..S-8 in TF-13) | ‹…› |
| Re-identification from genomic data | Access control, minimization, professional-secrecy context | ‹…› |
| Incidental findings / familial implications | Clinical governance, reporting policy ‹CMGG SOP› | ‹…› |
| Data integrity / wrong record | Immutable audit, content-hash, provenance | ‹…› |
| Breach (confidentiality) | TF-13 controls + incident process (TF-17) + breach notification per GDPR Art. 33/34 | ‹…› |
| Processing by a cloud provider | Data-processing agreement, EU region, CMEK encryption at rest, TLS in transit, least-privilege service accounts, audit logging (TF-13; `terraform/`) | ‹… — owner/DPO, #518› |

## 7. Consultation & sign-off
- **UZ Gent DPO opinion:** copied from the signed DPIA when it is filed (#518). The governing SOP **H11.1-OP5 §4.2** requires the DPO's advice in the **functional-analysis** phase whenever personal data is processed, so DPO consultation is a standing step of CoGA's lifecycle, not a one-off.
- Prior consultation with the supervisory authority (Belgian DPA) **only if** a high residual risk cannot be mitigated (Art. 36) — ‹assess with DPO›.
- Review on material change to processing or device (TF-18) and at the PMS cadence (TF-16).
