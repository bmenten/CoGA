# CoGA — Codebase-handleiding

Deze handleiding legt de codebase van CoGA (*Comprehensive Genomic Analysis*) uit, hoofdstuk per hoofdstuk, in het Nederlands. Ze is geschreven voor het **review board en auditoren** en bewust leesbaar gehouden voor wie weinig ervaring heeft met Python of TypeScript: elk vakbegrip wordt kort uitgelegd, en bij elk onderwerp staat **waar in de code** het zit.

CoGA draait als **in-house IVD onder IVDR Artikel 5(5)** bij CMGG (ISO 15189). Het gereguleerde deel loopt van het *geannoteerde VCF-bestand* tot het *ondertekende klinische rapport*. Daarom loopt door de hele handleiding één rode draad: **uitlegbaarheid, traceerbaarheid en veiligheid**, telkens met de plaats in de code waar een controle, een logregel of een toegangsbeperking wordt afgedwongen.

> De data in het systeem is **synthetisch**; er zijn geen echte patiëntgegevens.

## Welke code beschrijft deze handleiding?

Deze handleiding beschrijft de huidige `main`. Wijzigt een PR gedrag dat hier beschreven staat, dan werkt dezelfde PR het hoofdstuk bij en vermeldt dat in zijn beschrijving (vanaf de eerste release candidate ook in zijn CHANGELOG-regel). De CI-controle `scripts/check-handleiding-sync.sh` bewaakt dat de webpagina overeenkomt met de Markdown. Reviewt u een vaste versie, noteer dan de release-tag of de commit die u leest. Bij een verschil gaat de code voor, dan `docs/regulatory/`, dan `docs/`.

## Leeswijzer

- **Paden** staan in `monospace`, relatief ten opzichte van de hoofdmap van de repository (bv. `backend/app/routers/auth.py`). Functies en endpoints worden bij naam genoemd, nooit met een regelnummer.
- Een **"Waar in de code"**-aanwijzing leidt telkens naar de bron.
- Elk hoofdstuk eindigt met een tabel **"Belangrijkste bestanden"**.
- Waar de handleiding de Engelse documentatie of de technical file samenvat, noemt ze de bron: een pad (bv. `docs/application-scheme.md`) of een pagina in de app (bv. *Docs → Sample-integrity QC*). In de webversie zijn zulke verwijzingen gewone tekst; alleen de verwijzingen tussen hoofdstukken zijn links.
- De hoofdstukken zijn apart leesbaar. Voor een eerste lezing raden we de volgorde 1 → 15 aan.

## Webversie

Naast deze Markdown-bestanden is er een webpagina met alle hoofdstukken op één pagina, met een inhoudstabel in de zijbalk, een licht en donker thema en een printweergave voor PDF: [`coga-handleiding.html`](coga-handleiding.html). Open het bestand rechtstreeks in een browser; het heeft niets extern nodig. De zijbalk toont de productversie uit `VERSION`.

De pagina wordt gebouwd uit precies deze Markdown-bestanden, met [`build_site.py`](build_site.py):

```bash
pip install markdown
python docs/handleiding/build_site.py
```

Werk je een hoofdstuk bij of verandert `VERSION`, bouw de pagina dan opnieuw en commit ze mee. Gebruik dezelfde versie van `markdown` als CI (de stap *catalogue* in `.github/workflows/ci.yml`), zodat de uitvoer overeenkomt.

## Inhoudstabel

| # | Hoofdstuk | Waarover het gaat |
| --- | --- | --- |
| 1 | [Algemene architectuur & structuur](01-architectuur.md) | De drie lagen, de weg van één verzoek, de mappen, de configuratie en de technologie. De kaart voor de rest. |
| 2 | [Gebruikersrollen, machtigingen & afscherming](02-beveiliging-rollen-rechten.md) | Rollen, projectgebonden toegang, afdwinging in de backend, de beperkte databankrol, security-headers, de weigering van zwakke geheimen en de bescherming van uploads. |
| 3 | [Databankstructuren (Postgres & ClickHouse)](03-databankstructuren.md) | Waarom twee databanken, hoe de tabellen gegroepeerd zijn, hoe ClickHouse-rijen aan Postgres gekoppeld worden en welke garanties in de databank zitten. |
| 4 | [Initiële deployment & seeding](04-deployment-en-seeding.md) | Van nul naar een draaiend platform: het schema, de eerste beheerder, de referentiedata, de opstartvolgorde en de uitrol op Google Cloud. |
| 5 | [Login & authenticatie](05-login-authenticatie.md) | Inloggen, wachtwoorden, tokens, de sessie in de browser, rate limiting, registratie en optioneel Azure AD. |
| 6 | [Package import — manifest, controles, traceerbaarheid](06-import-pipeline.md) | Hoe een familiepakket veilig wordt ingelezen: bronnen, manifest, dry-run, waar de data terechtkomt en de herkomstregisters. |
| 7 | [Backend: routers & services in detail](07-backend-routers-en-services.md) | Het patroon router → service → opslag, de veiligheidsregels voor databankvragen, alle routers en de request-logging. |
| 8 | [Filterpagina's ↔ API](08-filterpaginas-en-api.md) | De keten van filter tot weergave voor small en structurele varianten, en een samenvatting van mtDNA en sample-QC, Paraphase, repeat-expansies, monogene NIPT en PGT. |
| 9 | [Visualisaties (chromosome, genome, circos, IGV)](09-visualisaties.md) | Het genoomoverzicht, de weergave per chromosoom, Circos, IGV en de tracks. |
| 10 | [Variant-tagging & semi-automatische ACMG-classificatie](10-tagging-en-acmg-classificatie.md) | Tags en de semi-automatische ACMG-classificatie (small variants, mtDNA en CNV's), het bevroren bewijs en drift. |
| 11 | [Rapport & volledige traceerbaarheid](11-rapport-en-traceerbaarheid.md) | Het ondertekenen, de poorten, het bevroren snapshot, het hash-geketende auditspoor en de integriteitsankers. |
| 12 | [HPO, Monarch & variant-prioritisatie](12-hpo-monarch-prioritisatie.md) | Fenotypes, Monarch, semantische gelijkenis, de variantprioritering en de ranking-cache. |
| 13 | [Gene Explorer & versiecontrole](13-gene-explorer.md) | Het genprofiel, de bronnen van de geninformatie en de versiecontrole van referentiedata. |
| 14 | [Variant Explorer](14-variant-explorer.md) | Varianten over alle toegankelijke projecten heen, met nadruk op de afscherming tussen projecten. |
| 15 | [Overige modules & adminfunctionaliteit](15-overige-modules-en-admin.md) | De CNV-kennisbank, genpanels, de beheerfuncties, de in-app documentatie, releases en UI-telemetrie. |

## Hoe deze handleiding is opgesteld

Elk hoofdstuk is geschreven uit de broncode en de projectdocumentatie, en elke verwijzing naar een bestand, functie of tabel is in de code nagegaan. Waar de handleiding de Engelse documentatie in `docs/` of de technical file in `docs/regulatory/` samenvat, noemt ze die bron.
