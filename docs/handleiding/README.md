# CoGA — Volledige codebase-handleiding (voor het review board)

Deze handleiding legt de **volledige codebase van CoGA** (*Comprehensive Genomic Analysis*) uit,
hoofdstuk per hoofdstuk, in het Nederlands. Ze is geschreven voor een **review board / auditoren**
en is bewust toegankelijk gehouden voor lezers met **beperkte Python- of TypeScript-ervaring**:
elk vakbegrip wordt kort uitgelegd, en bij elk onderwerp staat expliciet **waar in de code** het
is geïmplementeerd.

CoGA wordt geëxploiteerd als **in-house IVD onder IVDR Artikel 5(5)** bij CMGG (ISO 15189). De
device-grens loopt van *geannoteerde VCF* tot *ondertekend klinisch rapport*. Daarom loopt door de
hele handleiding één rode draad: **explainability, traceability en veiligheid** — telkens met
verwijzing naar het bestand waar de betreffende controle, logging of toegangsbeperking is afgedwongen.

> De data in het systeem is **synthetisch**; er is geen echte patiëntdata.

---

## Welke codeversie beschrijft deze handleiding?

| Item | Waarde |
| --- | --- |
| Applicatieversie (`VERSION`) | `0.1.0` |
| Git-commit | `6641228` (`66412286a2124e83d9155e4624206074dacfd8f8`) |
| Datum commit | 2026-07-08 |
| Branch | `docs/codebase-handleiding-nl` (lokaal) |

Alle bestands-, functie- en tabelverwijzingen in deze handleiding zijn geverifieerd tegen **exact
deze commit**. Bij een latere codewijziging kunnen paden verschuiven; controleer dan of deze
handleiding op de nieuwe commit moet worden bijgewerkt.

---

## Leeswijzer

- **Bestandsverwijzingen** staan in `monospace` en zijn relatief t.o.v. de repository-root,
  bv. `backend/app/routers/auth.py`. Waar mogelijk verwijzen we naar een **functie of endpoint bij
  naam** in plaats van naar een regelnummer (regelnummers verschuiven bij codewijzigingen).
- Elk belangrijk concept heeft een **“Waar in de code:”-aanwijzing** zodat u meteen naar de bron kunt.
- Elk hoofdstuk sluit af met een tabel **“Belangrijkste bestanden”** als snelle index.
- De hoofdstukken zijn zelfstandig leesbaar, maar bouwen logisch op elkaar voort. Voor een eerste
  lezing raden we de volgorde 1 → 15 aan; voor gericht auditwerk kunt u rechtstreeks naar een
  hoofdstuk springen via de inhoudstabel hieronder.

---

## Webversie (voor het review board)

Naast deze Markdown-bestanden is er een **self-contained webpagina** met alle hoofdstukken op één
navigeerbare pagina — zijbalk-inhoudstabel met scrollspy, licht/donker-thema en print-/PDF-export:
[`coga-handleiding.html`](coga-handleiding.html). Open dit bestand rechtstreeks in een browser; er
zijn geen externe afhankelijkheden nodig.

De webpagina wordt gegenereerd uit **exact deze Markdown-bestanden** met [`build_site.py`](build_site.py):

```bash
pip install markdown
python docs/handleiding/build_site.py   # (her)schrijft coga-handleiding.html
```

Werk je een hoofdstuk bij, draai dan dit script opnieuw zodat de Markdown en de webpagina overeenkomen.

---

## Inhoudstabel

| # | Hoofdstuk | Waarover het gaat |
| --- | --- | --- |
| 1 | [Algemene architectuur & structuur](01-architectuur.md) | De drie lagen (frontend, backend, databanken), de levensloop van een verzoek van klik tot data, de mappenstructuur en de tech stack. De “kaart” voor de rest. |
| 2 | [Gebruikersrollen, machtigingen & afscherming](02-beveiliging-rollen-rechten.md) | Rollen (gewone gebruiker / admin / superuser), project-scoped RBAC, backend-afdwinging vs. frontend-guards, de least-privilege database-rol, rate limiting, security headers, CORS, secret-weigering en upload-veiligheid. |
| 3 | [Databankstructuren (Postgres & ClickHouse)](03-databankstructuren.md) | Waarom twee databanken, welke tabellen er zijn (per groep), hoe ClickHouse-variantrijen aan Postgres-metadata worden gekoppeld, en hoe de schema’s worden toegepast. |
| 4 | [Initiële deployment & seeding](04-deployment-en-seeding.md) | Van nul naar een draaiende stack: aanmaken van de databankstructuren, seeden van de eerste admin en de referentiedata (GRCh38, cytobands, genen, tracks), de opstartsequentie, en de GCP/Terraform-deployment. |
| 5 | [Login & authenticatie](05-login-authenticatie.md) | Hoe gebruikers inloggen, wachtwoord-hashing, JWT-uitgifte en -verificatie, sessiebeheer in de frontend, rate limiting/lockout, optionele Azure AD, en waartoe men na login gerechtigd is. |
| 6 | [Package import — manifest, controles, traceerbaarheid](06-import-pipeline.md) | Hoe een pakket veilig wordt geïmporteerd: manifest-opbouw vanuit S3 / Google Cloud / lokale map, de data-controles en dry-run, wat in welke tabel terechtkomt, en de provenance/logging. |
| 7 | [Backend: routers & services in detail](07-backend-routers-en-services.md) | De API-laag: het patroon router → service → opslag, dependency-injection, de veiligheids-invarianten (geparametriseerde SQL, allowlisted `ORDER BY`), en een overzichtstabel van alle routers en services. |
| 8 | [Filterpagina's ↔ API](08-filterpaginas-en-api.md) | Hoe na een correcte import de **juiste data wordt gefilterd en getoond** — end-to-end — voor small variants, structural variants, mitochondriaal (mtDNA) + Sample QC, Paraphase, TRGT repeats, monogene NIPT en PGT haplotype-segregatie. |
| 9 | [Visualisaties (chromosome, genome, circos, IGV)](09-visualisaties.md) | Hoe elke visualisatie de juiste data ophaalt en tekent: genoomoverzicht, per-chromosoomweergave, Circos, de ingebedde IGV-browser en de onderliggende tracks. |
| 10 | [Variant-tagging & semi-automatische ACMG-classificatie](10-tagging-en-acmg-classificatie.md) | Tagging van varianten en de semi-automatische ACMG-classificator: hoe criteria vooraf worden geëvalueerd op een puntenschaal, hoe elk criterium overrijdbaar is, en hoe evidence-snapshots en classification-drift worden vastgelegd. |
| 11 | [Rapport & volledige traceerbaarheid](11-rapport-en-traceerbaarheid.md) | Hoe alles samenkomt in een ondertekend rapport: de sign-out flow, het bevroren versie-gebonden snapshot, de gating op drift en Sample-QC, en de append-only, hash-chained audit met integrity anchors. |
| 12 | [HPO, Monarch & variant-prioritisatie](12-hpo-monarch-prioritisatie.md) | De fenotype-gedreven laag: HPO-terminologie, Monarch-associaties en semantische gelijkenis, en hoe dit varianten prioriteert (met de ranking-cache). |
| 13 | [Gene Explorer & versiecontrole](13-gene-explorer.md) | Het gen-profiel: transcript-overzicht met MANE/RefSeq-badges, constraint-metrieken en associaties; uit welke externe bronnen de data komt, hoe die wordt gecachet, en hoe referentiedatasets **versiebeheerd** worden. |
| 14 | [Variant Explorer](14-variant-explorer.md) | De cross-cohort zoekfunctie: aggregatie van varianten over alle toegankelijke projecten, dragertellingen, filters en carrier drill-down — met bijzondere nadruk op de cross-project afscherming. |
| 15 | [Overige modules & adminfunctionaliteit](15-overige-modules-en-admin.md) | De resterende modules (Clinical CNV Explorer, gene-panel-catalogus, adminfuncties, in-app docs, releases, UI-telemetrie) plus een **dekkingschecklist** die bevestigt dat de volledige codebase is behandeld. |

---

## Hoe deze handleiding is opgesteld (methodologie)

Elk hoofdstuk is opgesteld door de betrokken broncode en de bestaande projectdocumentatie
(`docs/`) systematisch uit te lezen, en vervolgens is **elke bestands-, functie- en tabelverwijzing
opnieuw geverifieerd tegen de code** op de hierboven vermelde commit. Verwijzingen die niet
konden worden bevestigd, zijn verwijderd of gecorrigeerd. De handleiding is zo opgebouwd dat een
reviewer elke bewering kan natrekken tot in het exacte bestand.

Voor de dieperliggende onderbouwing verwijst deze handleiding waar relevant naar de bestaande
projectdocumentatie in [`docs/`](../README.md) en naar de IVDR technical file in
[`docs/regulatory/`](../regulatory/README.md).
