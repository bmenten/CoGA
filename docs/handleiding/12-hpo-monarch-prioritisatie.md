# 12. HPO, Monarch & variant-prioritisatie

Dit hoofdstuk beschrijft hoe CoGA het *fenotype* van een patiënt (de waargenomen klinische kenmerken) omzet in hulp voor de analist: een lijst kandidaatgenen en een rangschikking van de varianten. De keten loopt van de **HPO-terminologie**, via de kennisbank van het **Monarch Initiative** en de **semantische gelijkenis**, naar de **variantprioritering** en de **ranking-cache** die dat snel en reproduceerbaar houdt. De uitleg voor gebruikers staat in de app (*Docs → Phenotype matching with Monarch*, `frontend/src/content/docs/monarch-integration.md`); het ontwerp in `docs/monarch-integration.md` en `docs/variant-ranking-cache.md`.

Enkele begrippen:

- **HPO** (*Human Phenotype Ontology*): een gestandaardiseerde, hiërarchische lijst van klinische kenmerken. Elk kenmerk heeft een vaste code, zoals `HP:0001250` (epileptische aanval). De termen zijn verbonden door ouder-kindrelaties: een specifieke term is een subtype van een algemenere.
- **CURIE:** een korte identificatie met een voorvoegsel, bv. `HP:0001250` (fenotype), `MONDO:0007739` (ziekte), `HGNC:1100` (gen). CoGA gebruikt dezelfde identificaties als de bronnen.
- **Fenotypescore:** een getal tussen 0 en 1 dat zegt hoe goed het fenotypeprofiel van een gen past bij de kenmerken van de patiënt.

## HPO: het woordenboek van fenotypes

De ontologie staat in vier Postgres-tabellen: `hpo_term` (één rij per term, met de release), `hpo_synonym` (synoniemen, om op vrije tekst te zoeken), `hpo_edge` (de ouder-kindrelaties) en `hpo_closure` (voor elke term al zijn voorouders met hun afstand, zodat "heeft de patiënt deze term of een specifieker subtype?" één snelle vraag is). De tabel `individual_hpo` koppelt fenotypes aan een familielid, met de status `present`, `absent` of `unknown`; alleen `present` telt mee voor de prioritering.

**Laden en versies.** Bij een lege databank laadt de backend de ontologie uit het bestand dat in de repository vastligt (`data/ref-data/hpo/hp.obo`). Alleen als dat ontbreekt, downloadt hij het, optioneel gecontroleerd tegen een vaste SHA-256 (`HPO_ONTOLOGY_SHA256`). De release wordt uit het bestand zelf gelezen en bij elke term bewaard, zodat een analyse later terug te voeren is op de ontologieversie die gold.

**Zoeken en beheer.** Analisten zoeken termen via `GET /api/hpo/search` (exacte match eerst, dan begin van het label, dan label en synoniem). De beheerpagina (`HpoTerminologyAdminPage.tsx`) toont het aantal termen en de geladen release, en laat een nieuwe versie eerst *voorbekijken* (hoeveel termen nieuw, gewijzigd of verdwenen zijn) en pas daarna toepassen. Die volgorde dwingt alleen de pagina af: de pagina laat pas toepassen na een geslaagde voorvertoning van precies hetzelfde bestand. Het endpoint `POST /api/admin/hpo/sync` zelf aanvaardt ook meteen een toepassing (`preview_only: false`).

**Veiligheid.** Zoeken en lezen vraagt een login; importeren en synchroniseren alleen een beheerder. Het bestandspad in zo'n verzoek moet binnen een toegelaten map liggen, zodat een verzoek geen willekeurig bestand op de server kan laten inlezen. Een fenotype kan niet naar een onbekende term wijzen (de verwijzing naar `hpo_term` is afgedwongen); bij een import worden onbekende termen overgeslagen en gemeld.

**Waar in de code:** `backend/app/services/hpo_service.py`; `backend/app/routers/hpo.py` en de HPO-endpoints in `backend/app/routers/admin.py`; de tabellen in `02_reference.sql` en `03_assay.sql`.

## Monarch: genen, ziekten en fenotypes verbinden

Het **Monarch Initiative** brengt tientallen bronnen (OMIM, Orphanet, ClinGen, HPO-annotaties, …) samen in één kennisgraaf met vaste identificaties. CoGA gebruikt twee soorten koppelingen, elk in een eigen tabel:

- **gen → ziekte** (`monarch_gene_disease`: HGNC → MONDO, met de relaties, de bronnen en of er een oorzakelijke relatie is);
- **ziekte → fenotype** (`monarch_disease_phenotype`: MONDO → HPO, met een markering voor een fenotype dat bij de ziekte uitdrukkelijk *niet* voorkomt).

Daaruit volgt gen → fenotype, via de gedeelde ziekte.

**Laden.** CoGA downloadt kleine, voorgesplitste bestanden van Monarch in plaats van de hele kennisgraaf, en vervangt beide tabellen **in één transactie**, zodat ze nooit op verschillende releases staan. Kan de release niet bepaald worden, dan weigert CoGA te laden: de herkomst van gegevens die een ondertekend rapport voeden, mag nooit leeg zijn. De beheerpagina (`MonarchDataAdminPage.tsx`) toont de geladen release en de tabelgroottes, laat de data verversen en zoeken op ziekte of fenotype. Alle Monarch-beheer is alleen voor beheerders. Na een nieuwe release wordt ook het Mendeliome-panel opnieuw opgebouwd (hoofdstuk 15).

**Waar in de code:** `backend/app/services/monarch_ingest.py`; de endpoints `/api/admin/monarch/...` in `backend/app/routers/admin.py`.

## Semantische gelijkenis

Een patiënt heeft zelden precies de termen uit het leerboek. *Semantische gelijkenis* beantwoordt daarom: hoe goed lijkt het fenotypeprofiel van dit gen op wat we bij de patiënt zien? Een **specifiek gedeeld kenmerk weegt zwaarder** dan een algemeen. Dat gewicht is de *informatie-inhoud* van een term: hoe zeldzamer de term (of een subtype ervan) bij ziekten voorkomt, hoe hoger. "Verstandelijke beperking" komt bij zeer veel ziekten voor en zegt weinig; "congenitale hypothyreoïdie" is zeldzaam en laat een gen dat het verklaart opvallen. Eén brede term geeft daardoor een vlakke, bijna willekeurige rangschikking.

CoGA berekent gelijkenis op twee manieren:

1. **Live via Monarch** (`monarch_semsim.py`): voor het paneel met kandidaatgenen op de familiepagina. CoGA stuurt alleen de HPO-termen (geen identiteit) naar de publieke API van Monarch en krijgt de best passende genen terug. Het resultaat wordt kort gecachet; is Monarch niet bereikbaar, dan zegt de UI dat.
2. **Lokaal** (`monarch_phenotype_score.py`): voor de variantprioritering, want daar moet *elk* gen met een kandidaatvariant een score krijgen, niet alleen de beste. De methode (Resnik, *best-match average*) berekent de informatie-inhoud uit de lokale Monarch-tabellen. De selectie van termen is volgordeonafhankelijk gemaakt, zodat dezelfde invoer altijd dezelfde score geeft.

## Variantprioritering

De scoring zit in `backend/app/services/variant_prioritization.py`, een zuivere functie zonder databank of netwerk, wat testen en herhalen eenvoudig maakt. Ze volgt het model van Exomiser en combineert vier deelscores:

| Deelscore | Meet |
| --- | --- |
| Pathogeniciteit | Voorspelde schadelijkheid: impact en loss-of-function, ClinVar, de voorspellers (CADD, REVEL, SpliceAI, AlphaMissense) en genconstraint |
| Frequentie | Zeldzaamheid in gnomAD: hoe zeldzamer, hoe hoger |
| Segregatie | Het overervingspatroon in de stamboom (de novo, homozygoot recessief, compound heterozygoot, X-gebonden, dominant). Bij een zoon op X of Y buiten de pseudo-autosomale regio's volgt het de-novogewicht de ouder die dat chromosoom doorgeeft (hoofdstuk 8). Een structurele variant draagt de *Inheritance*-annotatie van NeedlR voor het query-sample: *de novo* telt als de novo; een geërfde SV telt alleen als dominant wanneer de ouder van wie ze komt (maternaal: de moeder, paternaal: de vader) en het kind aangedaan zijn, anders telt ze niet mee |
| Fenotype | Hoe goed het gen bij het klinische beeld past |

De eerste drie vormen samen de variantscore; de eindscore is een gewogen combinatie met de fenotypescore. Twee keuzes bewaken de klinische betrouwbaarheid:

- **ClinVar gaat voor.** Alleen een pathogene ClinVar-uitspraak geeft de volle pathogeniciteitsscore; bewijs dat alleen uit voorspellers komt, blijft daaronder. "Conflicting interpretations of pathogenicity" telt niet als pathogeen.
- **Nieuwe kandidaatgenen verdwijnen niet.** Een gen zonder Monarch-data scoort 0 op fenotype, maar de variantscore blijft apart zichtbaar, zodat de analist erop kan sorteren.

De prioritering draait bij `GET /api/families/{family_id}/small-variants?prioritize=true` (standaard aan): de service haalt de gefilterde kandidaten op, bepaalt de segregatie en de fenotypescores, rangschikt en geeft per variant een scoreblok terug. De kandidatenset is begrensd; loopt ze erover, dan meldt het antwoord de echte telling en zegt de UI dat de rangschikking onvolledig is en de filters verfijnd moeten worden. De scores rangschikken binnen één familie; het zijn geen gekalibreerde kansen.

**Waar in de code:** `variant_prioritization.py`; de prioritering in `clickhouse_family_variants.py`; de score-uitsplitsing in de reviewdialoog.

## De ranking-cache

Prioriteren duurt enkele seconden, vooral door het scoren per gen. Omdat de uitkomst **deterministisch** is voor dezelfde invoer, bewaart CoGA de gerangschikte volgorde in de Postgres-tabel `family_variant_ranking_cache`: alleen de volgorde en de score per variant, nooit de annotatie of de reviewtoestand. Die worden bij elke weergave vers opgehaald, zodat een gecachte rangschikking nooit een verouderde annotatie of reviewstatus toont.

Elke cacherij hangt aan een SHA-256 over **alles wat de rangschikking kan veranderen**:

| Invoer | Verandert wanneer … |
| --- | --- |
| De filters (zonder paginering) | de analist een filter wijzigt |
| Het genpanel en zijn versie | het panel wordt bijgewerkt |
| De HPO-termen van de aangedane leden | een fenotype wordt toegevoegd of verwijderd |
| De stamboom en de aangedane status | de familiestructuur verandert |
| De versie van de variantdata van de familie | er iets aan de varianten verandert: import, upload, verwijdering door een beheerder of herstel |
| De review-selecties in de filters | een tag of review verandert die de filter raakt |
| De Monarch-release, de HPO-release en het moment van de laatste genconstraint-update | de referentiedata wordt ververst |
| De assembly en de versie van het algoritme | de code van de scoring verandert |

Verandert één invoer, dan verandert de hash: de cache mist en de rangschikking wordt opnieuw berekend. Een verouderde rangschikking wordt dus nooit getoond. Een smaller panel kan uit een bredere, volledige rangschikking van dezelfde familie worden bediend (het Mendeliome dekt zijn deelpanels), maar nooit uit een onvolledige. Na een wijziging van fenotypes of stamboom rekent de backend de laatste rangschikking alvast opnieuw uit, zodat de volgende opening snel is. De UI meldt wanneer de rangschikking uit de cache komt en wanneer ze berekend werd.

**Waar in de code:** `backend/app/services/variant_ranking_cache.py`; de ontwerpnota `docs/variant-ranking-cache.md`.

## Kandidaatgenen op de familiepagina

Het paneel `MonarchPhenotypeMatchPanel.tsx` roept Monarch pas aan na een klik op *Find candidate genes* (`GET /api/families/{family_id}/phenotype-match`), zodat de externe dienst niet bij elke paginalading wordt aangesproken. Elke klik, ook op *Re-run match*, stelt de vraag opnieuw, met de termen van dat moment; de backend hergebruikt het antwoord van Monarch voor dezelfde termen een uur lang (`monarch_semsim.py`). Tijdens een vraag toont het paneel geen vorig resultaat, en een mislukte vraag verschijnt als fout met de reden. Het endpoint verzamelt de `present`-termen van de familie (of van één lid) en verrijkt elk gevonden gen: bestaat het gen in CoGA (dan linkt de UI naar het genprofiel, hoofdstuk 13), en welke fenotypes van het gen heeft de familie wel of niet (met de ontologie meegerekend, dus een algemeen genfenotype telt ook als de patiënt een specifieker subtype heeft).

## Veiligheid en traceerbaarheid

- **Deterministisch.** Dezelfde invoer geeft altijd dezelfde rangschikking: een voorwaarde voor een auditeerbaar, herhaalbaar resultaat.
- **Gebonden aan versies.** De cachesleutel bevat de Monarch-, HPO- en panelversies; HPO-termen dragen hun release; Monarch weigert te laden zonder bekende versie.
- **Liever herberekenen dan vertrouwen.** Elke relevante wijziging verandert de hash, en bij twijfel rekent de code opnieuw.
- **Toegang.** Fenotype-matching vraagt een login; het laden en verversen van HPO- en Monarch-data alleen een beheerder.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/services/hpo_service.py` | HPO laden, versies, zoeken, padcontrole |
| `backend/app/services/monarch_ingest.py` | Monarch downloaden en vervangen, status, zoeken |
| `backend/app/services/monarch_semsim.py` · `monarch_phenotype_score.py` | Gelijkenis: live via Monarch; lokaal voor de prioritering |
| `backend/app/services/variant_prioritization.py` | De scoring |
| `backend/app/services/variant_ranking_cache.py` | De ranking-cache |
| `backend/app/routers/hpo.py` · `backend/app/routers/admin.py` | HPO-endpoints; beheer van HPO en Monarch |
| `backend/db/schema/postgres/02_reference.sql` · `03_assay.sql` | HPO- en Monarch-tabellen; `individual_hpo` en de cache |
| `frontend/src/pages/families/MonarchPhenotypeMatchPanel.tsx` | Kandidaatgenen op de familiepagina |
| `frontend/src/pages/admin/HpoTerminologyAdminPage.tsx` · `MonarchDataAdminPage.tsx` | Beheer van HPO en Monarch |
