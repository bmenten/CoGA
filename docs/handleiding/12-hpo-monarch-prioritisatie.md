# 12. HPO, Monarch & variant-prioritisatie

Dit hoofdstuk beschrijft hoe CoGA het *fenotype* van een patiënt (de waargenomen klinische kenmerken) omzet in hulp voor de analist: een lijst kandidaatgenen en een rangschikking van de varianten. De keten loopt van de **HPO-terminologie**, via de kennisbank van het **Monarch Initiative** en de **semantische gelijkenis**, naar de **variantprioritering**. De **ranking-cache** houdt dat snel en reproduceerbaar. Uitleg voor gebruikers staat in de app (*Docs → Phenotype matching with Monarch*, `frontend/src/content/docs/monarch-integration.md`); het ontwerp in `docs/monarch-integration.md` en `docs/variant-ranking-cache.md`.

Enkele begrippen:

- **HPO** (*Human Phenotype Ontology*): een gestandaardiseerde, hiërarchische lijst van klinische kenmerken. Elk kenmerk heeft een vaste code, zoals `HP:0001250` (epileptische aanval). De termen hangen samen als ouder en kind: een specifieke term is een subtype van een algemenere.
- **CURIE:** een korte identificatie met een voorvoegsel, bv. `HP:0001250` (fenotype), `MONDO:0007739` (ziekte), `HGNC:1100` (gen). CoGA gebruikt dezelfde identificaties als de bronnen.
- **Fenotypescore:** een getal tussen 0 en 1: hoe goed past het fenotypeprofiel van een gen bij de kenmerken van de patiënt?

## HPO: het woordenboek van fenotypes

De ontologie staat in vier Postgres-tabellen:

- `hpo_term`: één rij per term, met de release;
- `hpo_synonym`: synoniemen, om op vrije tekst te zoeken;
- `hpo_edge`: de ouder-kindrelaties;
- `hpo_closure`: per term al zijn voorouders met hun afstand. Zo is "heeft de patiënt deze term of een specifieker subtype?" één snelle vraag.

De tabel `individual_hpo` koppelt fenotypes aan een familielid, met de status `present`, `absent` of `unknown`. Alleen `present` telt mee voor de prioritering.

**Laden en versies.** Bij een lege databank downloadt de backend de nieuwste HPO-release van haar eigen, vaste PURL en laadt die, als het bestand die release vermeldt. Lukt dat niet, dan laadt hij de reserve: het bestand dat in de repository vastligt (`data/ref-data/hpo/hp.obo`), alleen met de SHA-256 van dat bestand; elk ander weigert hij. Bronnen en de SHA-256 van de reserve liggen vast in de code, niet in een instelling (hoofdstuk 4). De release wordt uit het bestand zelf gelezen en bij elke term bewaard. Zo is een analyse later terug te voeren op de ontologieversie die gold.

**Zoeken en beheer.** Analisten zoeken termen via `GET /api/hpo/search`: eerst exacte match, dan begin van het label, dan label en synoniem. De beheerpagina (`HpoTerminologyAdminPage.tsx`) toont het aantal termen en de geladen release, en zegt of HPO intussen een nieuwere release uitbracht (`GET /api/admin/hpo/release-check`, uit de laatste GitHub-release van HPO). Een nieuwe release komt uit een bestand op de server, of wordt gedownload van haar eigen, vaste PURL (`https://purl.obolibrary.org/obo/hp/releases/<datum>/hp.obo`); een gedownload bestand moet die release vermelden. Niets wordt ingelezen zonder beheerder: een nieuwe release verandert de fenotypematching. Een nieuwe versie laat ze eerst *voorbekijken* (hoeveel termen nieuw, gewijzigd of verdwenen zijn) en pas daarna toepassen. Alleen de pagina dwingt die volgorde af: ze laat pas toepassen na een geslaagde voorvertoning van precies hetzelfde bestand, en stuurt dan de SHA-256 uit de voorvertoning mee: de backend leest alleen die bytes in. Het endpoint `POST /api/admin/hpo/sync` zelf aanvaardt ook meteen een toepassing (`preview_only: false`).

De release is die welke het bestand zelf vermeldt (zijn `data-version`). De termen bewaren de SHA-256 van de ingelezen bytes (`metadata.release_sha256`); de beheerpagina, de geladen release en elk ondertekend record noemen die (TRACE-18). *Expected release* en *Expected release date* zijn wat de beheerder verwacht. Vermeldt het bestand een andere release, of geen, dan weigert de sync (`400`) en wordt niets ingelezen.

**Veiligheid.**

- Zoeken en lezen vraagt een login; importeren en synchroniseren alleen een beheerder.
- De fenotypes van een familie toevoegen, wijzigen en verwijderen mag een analist van het project van de familie met de rol `analyst` of hoger, een superuser of een beheerder; geen lezer en geen viewer (hoofdstuk 2).
- Elke wijziging komt in het klinische auditspoor (hoofdstuk 11). Ook die van een pakketimport: per lid, op naam van wie de import startte.
- Het bestandspad in een syncverzoek moet binnen een toegelaten map liggen. Zo kan een verzoek geen willekeurig bestand op de server laten inlezen.
- Een fenotype kan niet naar een onbekende term wijzen (de verwijzing naar `hpo_term` is afgedwongen). Een import slaat onbekende termen over en meldt ze.

**Waar in de code:** `backend/app/services/hpo_service.py`; `backend/app/routers/hpo.py` en de HPO-endpoints in `backend/app/routers/admin.py`; de tabellen in `02_reference.sql` en `03_assay.sql`.

## Monarch: genen, ziekten en fenotypes verbinden

Het **Monarch Initiative** brengt tientallen bronnen (OMIM, Orphanet, ClinGen, HPO-annotaties, …) samen in één kennisgraaf met vaste identificaties. CoGA gebruikt twee soorten koppelingen, elk in een eigen tabel:

- **gen → ziekte** (`monarch_gene_disease`: HGNC → MONDO, met de relaties, de bronnen en of er een oorzakelijke relatie is);
- **ziekte → fenotype** (`monarch_disease_phenotype`: MONDO → HPO, met een markering voor een fenotype dat bij de ziekte uitdrukkelijk *niet* voorkomt).

Via de gedeelde ziekte volgt daaruit gen → fenotype.

**Laden.** CoGA downloadt kleine, voorgesplitste bestanden van Monarch, niet de hele kennisgraaf. Het vervangt beide tabellen **in één transactie**, zodat ze nooit op verschillende releases staan. Kan de release niet bepaald worden, dan weigert CoGA te laden: de herkomst van gegevens achter een ondertekend rapport mag nooit leeg zijn. Een start op een databank zonder Monarch-data laadt de nieuwste release (`ensure_monarch_on_startup`, `REFERENCE_BOOTSTRAP_MONARCH`; beslissing van de eigenaar, 2026-11-05). Lukt dat niet, dan logt hij een waarschuwing en gaat de start door; de volgende start of de beheerpagina probeert opnieuw. Het Mendeliome-panel bouwt de start niet, want een panel maakt een account aan: dat gebeurt op de pagina Genpanels. De beheerpagina (`MonarchDataAdminPage.tsx`) toont de geladen release en de tabelgroottes, laat de data verversen en zoeken op ziekte of fenotype. Alle Monarch-beheer is alleen voor beheerders. Na een nieuwe release wordt ook het Mendeliome-panel opnieuw opgebouwd (hoofdstuk 15).

**Waar in de code:** `backend/app/services/monarch_ingest.py`; de endpoints `/api/admin/monarch/...` in `backend/app/routers/admin.py`.

## Semantische gelijkenis

Een patiënt heeft zelden precies de termen uit het leerboek. *Semantische gelijkenis* meet daarom hoe goed het fenotypeprofiel van een gen lijkt op wat we bij de patiënt zien. Een **specifiek gedeeld kenmerk weegt zwaarder** dan een algemeen. Dat gewicht is de *informatie-inhoud* van een term: hoe zeldzamer de term (of een subtype ervan) bij ziekten voorkomt, hoe hoger. "Verstandelijke beperking" komt bij zeer veel ziekten voor en zegt weinig. "Congenitale hypothyreoïdie" is zeldzaam en laat een gen dat het verklaart opvallen. Eén brede term geeft daardoor een vlakke, bijna willekeurige rangschikking.

CoGA berekent gelijkenis op twee manieren:

1. **Live via Monarch** (`monarch_semsim.py`), voor het paneel met kandidaatgenen op de familiepagina. CoGA stuurt alleen de HPO-termen (geen identiteit) naar de publieke API van Monarch en krijgt de best passende genen terug. Het resultaat wordt kort gecachet; is Monarch niet bereikbaar, dan zegt de UI dat.
2. **Lokaal** (`monarch_phenotype_score.py`), voor de variantprioritering. Daar moet *elk* gen met een kandidaatvariant een score krijgen, niet alleen de beste. De methode (Resnik, *best-match average*) berekent de informatie-inhoud uit de lokale Monarch-tabellen. De selectie van termen hangt niet af van de volgorde: dezelfde invoer geeft altijd dezelfde score.

## Variantprioritering

De scoring zit in `backend/app/services/variant_prioritization.py`: een zuivere functie, zonder databank of netwerk. Dat maakt testen en herhalen eenvoudig. Ze volgt het model van Exomiser en combineert vier deelscores:

| Deelscore | Meet |
| --- | --- |
| Pathogeniciteit | Voorspelde schadelijkheid: impact en loss-of-function, ClinVar, de voorspellers (CADD, REVEL, SpliceAI, AlphaMissense) en genconstraint. Een structurele variant heeft die voorspellers niet: ze scoort op het soort gebeurtenis, het aantal geraakte genen en de genconstraint. Een kopieverlies scoort als deletie, in een gen met een pLI van 0,9 of meer als haploinsufficiënt: een `DEL`, ook `DEL:ME`, of een `CNV` waarvan het opgeslagen kopieaantal een verlies toont (dezelfde regel als de tweede hit, hoofdstuk 8, in `sv_copy_change.py`). Een kopiewinst scoort als duplicatie: een `DUP`, ook `DUP:TANDEM`, of een `CNV` die een winst toont. Vroeger scoorden `DEL:ME`, `DUP:TANDEM` en elke `CNV` als onbekende gebeurtenis (CLIN-47) |
| Frequentie | Zeldzaamheid in gnomAD: hoe zeldzamer, hoe hoger |
| Segregatie | Het overervingspatroon in de stamboom (de novo, homozygoot recessief, compound heterozygoot, X-gebonden, dominant). Bij een zoon op X of Y buiten de pseudo-autosomale regio's volgt het de-novogewicht de ouder die dat chromosoom doorgeeft (hoofdstuk 8). Een structurele variant draagt de *Inheritance*-annotatie van NeedlR voor het query-sample. *De novo* telt als de novo. Een geërfde SV telt alleen als dominant als het kind en de ouder van wie ze komt (maternaal: de moeder, paternaal: de vader) aangedaan zijn; anders telt ze niet mee |
| Fenotype | Hoe goed het gen bij het klinische beeld past |

De eerste drie vormen samen de variantscore. De eindscore is een gewogen combinatie van de variantscore en de fenotypescore. Twee keuzes bewaken de klinische betrouwbaarheid:

- **ClinVar gaat voor.** Alleen een pathogene ClinVar-uitspraak geeft de volle pathogeniciteitsscore; bewijs uit alleen voorspellers blijft daaronder. "Conflicting interpretations of pathogenicity" telt niet als pathogeen.
- **Nieuwe kandidaatgenen verdwijnen niet.** Een gen zonder Monarch-data scoort 0 op fenotype, maar de variantscore blijft apart zichtbaar in de uitsplitsing van de score (*Variant*). Sorteren kan op de gecombineerde score (*Score*), met de variantscore als tweede sleutel.

De prioritering draait bij `GET /api/families/{family_id}/small-variants?prioritize=true`. De pagina zet dit standaard aan; de API zelf staat standaard uit. De service haalt de gefilterde kandidaten op, bepaalt de segregatie en de fenotypescores, rangschikt, en geeft per variant een scoreblok terug. De kandidatenset is begrensd. Loopt ze over, dan meldt het antwoord de echte telling, en zegt de UI dat de rangschikking onvolledig is en de filters verfijnd moeten worden. De scores rangschikken binnen één familie; het zijn geen gekalibreerde kansen.

**De prioritering verandert de volgorde, niet wat een zoekopdracht vindt.** Dat geldt voor een overervingsmodel dat varianten paart (compound-het, recessief) en voor dragerscreening. Een gerangschikte zoekopdracht houdt dan precies over wat dezelfde zoekopdracht zonder prioritering overhoudt: dezelfde regels in Python op dezelfde kandidaten (de paren, met hun fase; de homozygote en X-gebonden varianten; de genregel van het koppel, hoofdstuk 8). Alleen die varianten worden gerangschikt, met de segregatie die ze onderling hebben. Vroeger rangschikte ze alles wat de SQL-voorfilter doorliet, bv. bij compound-het elke variant die bij alle aangedane leden heterozygoot is, ook zonder partner (CLIN-44).

- Een paar blijft een paar en staat op de plaats van zijn best gerangschikte variant. Deelt het die variant met een ander item, dan beslist de volgende.
- Elke variant toont zijn eigen rang.
- Zo'n zoekopdracht leest één venster van 5.000 kandidaten, het grootste dat de zoekopdracht zonder prioritering leest. Loopt het over, dan zijn de rangschikking en de kandidaten allebei begrensd (`ranking_truncated` en `candidates_capped`). De telling is dan wat binnen het venster gevonden werd: een ondergrens.

**Waar in de code:** `variant_prioritization.py`; de prioritering in `clickhouse_family_variants.py`; de score-uitsplitsing in de reviewdialoog.

## De ranking-cache

Prioriteren duurt enkele seconden, vooral door het scoren per gen. De uitkomst is **deterministisch**: dezelfde invoer geeft dezelfde uitkomst. Daarom bewaart CoGA de gerangschikte volgorde in de Postgres-tabel `family_variant_ranking_cache`: alleen de volgorde en de score per variant, nooit de annotatie of de reviewtoestand. Die worden bij elke weergave vers opgehaald. Zo toont een gecachte rangschikking nooit een verouderde annotatie of reviewstatus.

Elke cacherij hangt aan een SHA-256 over **alles wat de rangschikking kan veranderen**:

| Invoer | Verandert wanneer … |
| --- | --- |
| De filters (zonder paginering), ook het overervingsmodel en de dragerscreening | de analist een filter wijzigt |
| Het genpanel en zijn versie | het panel wordt bijgewerkt |
| De genen en regio's die de panelfilter toepaste (een digest; de regio's bevatten de loci van elk gen uit de genreferentie) | het panel andere genen of regio's krijgt, of een herladen genreferentie een van zijn genen verplaatst |
| De regio's waartoe de zoekopdracht zich beperkte of die ze uitsloot (de intervallen en de loci van de uitgesloten genen), en de uitgesloten genen | de analist die filters wijzigt, of een herladen genreferentie een van die genen verplaatst |
| De HPO-termen van de aangedane leden | een fenotype wordt toegevoegd of verwijderd |
| De stamboom, de aangedane status en het geslacht van elk lid (ook het koppel van een dragerscreening) | de familiestructuur verandert |
| De versie van de variantdata van de familie | er iets aan de varianten verandert: import, upload, verwijdering door een beheerder of herstel |
| De review-selecties in de filters | een tag of review verandert die de filter raakt |
| De Monarch-release, de HPO-release en het moment van de laatste genconstraint-update | de referentiedata wordt ververst |
| De assembly en de versie van het algoritme | de code van de scoring verandert, of welke varianten een filter overhoudt |

Verandert één invoer, dan verandert de hash: de cache mist en de rangschikking wordt opnieuw berekend. Een exacte treffer is dus nooit een rangschikking op andere invoer. De loci van een gen komen bij elke zoekopdracht uit de genreferentie, niet uit een versie. Daarom telt wat de zoekopdracht toepaste, en niet alleen de versie van het panel.

Een smaller panel kan uit een bredere rangschikking van dezelfde familie worden bediend (het Mendeliome dekt zijn deelpanels), maar alleen als:

- die rangschikking volledig is;
- haar panel nog precies de genen en regio's bevat waarop ze berekend werd. Elke cacherij legt die vast (`provenance.panel_members`). Het smallere panel kan immers alleen varianten uit de bredere rangschikking weglaten, nooit toevoegen. Een rangschikking van vóór een gen aan haar panel werd toegevoegd, mist de varianten van dat gen en dient dus geen enkel panel meer. Een rij die niets vastlegde, of waarvan het panel verwijderd is, wordt evenmin gebruikt;
- ze elke variant bevat die het smallere panel bereikt, met de segregatie die ze binnen dat panel heeft. CoGA haalt die varianten daarom eerst op.

Elk panel wordt op gen en op positie vergeleken, ook het Mendeliome (beslissing van de eigenaar van 14 oktober 2026; vroeger werd een panel van meer dan 1000 regio's alleen op gensymbool vergeleken). De rangschikking van het Mendeliome bevat dus ook een variant die het kleinere panel via de locus van een gen bereikt, bv. omdat de annotatie het gen anders noemt dan de genreferentie. Twee gevallen kunnen de laatste voorwaarde doen falen; het smallere panel wordt dan rechtstreeks berekend:

- Het bereikt een variant alleen via een eigen regio die het bredere panel niet heeft: coördinaten die PanelApp voor een gen gaf, of een locus die bij het aanmaken van het panel werd opgeslagen. De bredere rangschikking kan die variant missen.
- Het laat de partner van een compound-heterozygoot paar weg. Binnen dat panel is het dan geen paar: de variant verliest die overervingswijze, en daarmee een deel van zijn segregatiegewicht en score, tenzij een andere even zwaar weegt.

Een panel dat uit een bredere rangschikking komt, krijgt zijn eigen rangnummers vanaf 1. Zo toont het altijd wat een rechtstreekse berekening van dat panel geeft.

Een zoekopdracht met paren of dragerscreening haalt ook bij een exacte treffer eerst haar kandidaten op en bepaalt haar paren binnen haar eigen bereik. De cache bewaart de volgorde van de varianten, niet de paren. Die volgorde wordt alleen gebruikt als ze elke gevonden variant bevat, met dezelfde segregatie.

Na een wijziging van fenotypes of stamboom rekent de backend de laatste rangschikking alvast opnieuw uit, zodat de volgende opening snel is. De intervallen en de uitgesloten genen en intervallen van die zoekopdracht past hij daarbij niet toe: een zoekopdracht met zulke filters wordt bij de volgende opening opnieuw berekend. De UI meldt of de rangschikking uit de cache komt of berekend werd.

**Een panel opzoeken.** Wat een panelfilter toepast, leest CoGA niet bij elke zoekopdracht opnieuw: de genen van het panel, zijn opgeslagen regio's voor de assembly van de familie, en de loci van zijn genen in de genreferentie van die assembly. Elk proces bewaart dat, met de digest ervan, per panel en assembly. Het gebruikt het alleen zolang het kenmerk ervan onveranderd is. Dat kenmerk leest CoGA bij elk gebruik: de versie van het panel, een SHA-256 van zijn genrijen en van zijn regiorijen voor de assembly, en de imports van de genreferentie voor de assembly. Het panel wordt dus opnieuw gelezen na elke wijziging van het panel, langs welke weg ook, en na elke nieuwe lading van de genreferentie (elke import registreert zich in de transactie die haar rijen schrijft). Een opzoeking wordt alleen bewaard als het kenmerk na het lezen nog hetzelfde is als ervoor.

Waarom (ENG-14): voor het Mendeliome (5300 genen en, met één rij per transcript, zo'n 40.000 loci) kostte het opzoeken bij elke zoekopdracht meer dan het berekenen van een klein deelpanel. Dat gold ook bij een exacte treffer van de standaardweergave, en bij de controle die een deelpanel uit de rangschikking van het Mendeliome bedient. Nu kost zo'n deelpanel ongeveer wat zijn eigen berekening kost. Een rij van `genes` die buiten de import om in de databank wordt geschreven, ziet een bewaarde opzoeking pas na de volgende import van de genreferentie of een herstart.

**Waar in de code:** `backend/app/services/variant_ranking_cache.py`; het opzoeken van een panel in `backend/app/services/clickhouse_family_variants.py` (`_fetch_panel_constraints`); de ontwerpnota `docs/variant-ranking-cache.md`.

## Kandidaatgenen op de familiepagina

Het paneel `MonarchPhenotypeMatchPanel.tsx` roept Monarch pas aan na een klik op *Find candidate genes* (`GET /api/families/{family_id}/phenotype-match`). Zo wordt de externe dienst niet bij elke paginalading aangesproken. Elke klik, ook op *Re-run match*, stelt de vraag opnieuw, met de termen van dat moment. De backend hergebruikt het antwoord van Monarch voor dezelfde termen een uur lang (`monarch_semsim.py`). Tijdens een vraag toont het paneel geen vorig resultaat; een mislukte vraag verschijnt als fout met de reden.

Het endpoint verzamelt de `present`-termen van de familie (of van één lid) en verrijkt elk gevonden gen:

- Bestaat het gen in CoGA? Dan linkt de UI naar het genprofiel (hoofdstuk 13).
- Welke fenotypes van het gen heeft de familie wel of niet? De ontologie telt mee: een algemeen genfenotype telt ook als de patiënt een specifieker subtype heeft.

## Veiligheid en traceerbaarheid

- **Deterministisch.** Dezelfde invoer geeft altijd dezelfde rangschikking: een voorwaarde voor een auditeerbaar, herhaalbaar resultaat.
- **Gebonden aan versies.** De cachesleutel bevat de Monarch-, HPO- en panelversies en de genen en regio's die de zoekopdracht toepaste. HPO-termen dragen hun release. Monarch weigert te laden zonder bekende versie.
- **Liever herberekenen dan vertrouwen.** Elke relevante wijziging verandert de hash, en bij twijfel rekent de code opnieuw.
- **Toegang.** Fenotype-matching vraagt een login; het laden en verversen van HPO- en Monarch-data alleen een beheerder.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/services/hpo_service.py` | HPO laden, versies, zoeken, padcontrole |
| `backend/app/services/monarch_ingest.py` | Monarch downloaden en vervangen, status, zoeken |
| `backend/app/services/monarch_semsim.py` · `monarch_phenotype_score.py` | Gelijkenis: live via Monarch; lokaal voor de prioritering |
| `backend/app/services/variant_prioritization.py` | De scoring |
| `backend/app/services/sv_copy_change.py` | Of een SV een kopie verwijdert of toevoegt: voor de SV-scoring, de tweede hit en het tekenen van SV's (hoofdstuk 9) |
| `backend/app/services/variant_ranking_cache.py` | De ranking-cache |
| `backend/app/routers/hpo.py` · `backend/app/routers/admin.py` | HPO-endpoints; beheer van HPO en Monarch |
| `backend/db/schema/postgres/02_reference.sql` · `03_assay.sql` | HPO- en Monarch-tabellen; `individual_hpo` en de cache |
| `frontend/src/pages/families/MonarchPhenotypeMatchPanel.tsx` | Kandidaatgenen op de familiepagina |
| `frontend/src/pages/admin/HpoTerminologyAdminPage.tsx` · `MonarchDataAdminPage.tsx` | Beheer van HPO en Monarch |
