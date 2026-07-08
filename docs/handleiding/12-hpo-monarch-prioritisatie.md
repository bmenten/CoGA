# 12. HPO, Monarch & variant-prioritisatie

In dit hoofdstuk wordt beschreven hoe CoGA het *fenotype* van een patiënt (de waargenomen klinische kenmerken) omzet in een concreet hulpmiddel voor de analist: een gerangschikte lijst van kandidaat-genen en een herordening van de varianten. We volgen de hele keten — van de **HPO-terminologie** (het "woordenboek" van klinische kenmerken), via de **Monarch Initiative**-kennisbank (die genen, ziekten en fenotypes met elkaar verbindt), naar de **semantische gelijkenis** (hoe dicht een gen bij het klinisch beeld ligt), tot en met de **variant-prioritisatie** (Exomiser-achtige scoring) en de **ranking-cache** die dit alles snel én reproduceerbaar houdt. Overal wijzen we aan *waar in de code* de essentie zit en welke veiligheids- en traceerbaarheidsgaranties gelden.

Enkele begrippen die telkens terugkomen, kort uitgelegd:

- **HPO** (Human Phenotype Ontology): een gestandaardiseerde, hiërarchische lijst van menselijke klinische kenmerken. Elk kenmerk heeft een stabiele code zoals `HP:0001250` (voor "epileptische aanval") en een label. "Ontologie" betekent hier: termen zijn onderling verbonden via ouder-kindrelaties (een specifieke term is een *subtype* van een algemenere).
- **CURIE**: een compacte identificator met een naamruimte-prefix, bv. `HP:0001250` (fenotype), `MONDO:0007739` (ziekte), `HGNC:1100` (gen). CoGA gebruikt dezelfde identificatoren als de bronnen, zodat koppelen "gratis" is.
- **Fenotype-score**: een getal tussen 0 en 1 dat uitdrukt hoe goed het fenotypeprofiel van een gen overeenkomt met de bij de patiënt waargenomen kenmerken.

## HPO: het fenotype-woordenboek

### Wat wordt opgeslagen

De HPO-ontologie wordt in Postgres opgeslagen in vier tabellen, gedefinieerd in `backend/db/schema/postgres/015_hpo.sql`:

| Tabel | Rol |
| --- | --- |
| `hpo_term` | Eén rij per HPO-term: `hpo_id`, `label`, `definition`, `is_obsolete`, `replaced_by`, plus versie-info (`release_version`, `release_date`). De primaire sleutel dwingt met `CHECK (hpo_id ~ '^HP:[0-9]{7}$')` af dat elke code een geldig HPO-id is. |
| `hpo_synonym` | Synoniemen per term (voor zoeken op vrije tekst). |
| `hpo_edge` | De ouder-kindrelaties (`child_id` → `parent_id`, standaard relatie `is_a`). |
| `hpo_closure` | De vooraf-berekende *transitieve sluiting*: voor elke term álle voorouders met hun afstand. Dit maakt "heeft de patiënt deze term of een specifieker subtype?" één snelle query in plaats van een recursieve boomdoorloop. |

De aparte tabel `individual_hpo` koppelt waargenomen fenotypes aan een concreet familielid (via `family_id` en een `sample_id` die naar `samples(id)` verwijst), met een `status` van `present`, `absent` of `unknown`. Alleen `present`-termen tellen mee voor prioritisatie.

**Waar in de code:** het schema in `backend/db/schema/postgres/015_hpo.sql`; de laadlogica in `backend/app/services/hpo_service.py`.

### Hoe de ontologie geladen en versiebeheerd wordt

Het inlezen gebeurt in `hpo_service.py`. Een bron-bestand (`hp.obo` of `hp.json`, de officiële HPO-releaseformaten) wordt geparseerd door `parse_hpo_obo_text` of `parse_hpo_json_text` en vervolgens weggeschreven door `import_hpo_ontology`. Die functie:

1. *upsert* alle termen in `hpo_term` (bestaande termen worden bijgewerkt, nieuwe toegevoegd);
2. herbouwt `hpo_synonym`, `hpo_edge` en `hpo_closure` volledig (delete-and-load);
3. berekent de sluiting met `compute_hpo_closure` — een breadth-first doorloop die per term de kortste afstand tot elke voorouder vastlegt.

**Versiebeheer.** De release wordt uit het bronbestand zelf gelezen (`parse_hpo_ontology_release_metadata_path`, dat de `data-version:` of `owl:versionInfo` uit het OBO-bestand haalt) en op elke `hpo_term`-rij opgeslagen in `release_version`/`release_date`. Dit is essentieel voor traceerbaarheid: elke fenotype-annotatie kan later worden teruggekoppeld aan de exacte ontologieversie die gold.

Bij een lege database wordt de ontologie automatisch geladen (`ensure_hpo_ontology_on_startup`), desnoods gedownload van het officiële PURL-adres (`DEFAULT_HPO_ONTOLOGY_URL`). Dat de gebruikte ontologie in de repo gepind is voor reproduceerbaarheid, is bewust vastgelegd (zie ook hoofdstuk [06 — Package import](06-import-pipeline.md) voor hoe fenotypes bij een familie-import binnenkomen).

### De HPO-browser en admin

Analisten zoeken termen via `search_hpo_terms` (endpoint `GET /hpo/search` in `backend/app/routers/hpo.py`), dat rangschikt op exacte match → prefix → label → synoniem. Een enkele term met ouders en kinderen komt via `get_hpo_term_details` (`GET /hpo/{hpo_id}`).

De beheerpagina is `frontend/src/pages/admin/HpoTerminologyAdminPage.tsx`. Die toont het aantal termen, de geladen release en de laatste sync, en biedt een **preview-dan-toepassen** workflow: het endpoint `POST /admin/hpo/sync` wordt eerst met `preview_only = true` aangeroepen (toont hoeveel termen nieuw/gewijzigd/verdwenen zijn), daarna pas met `preview_only = false` om echt te importeren. De HPO-adminendpoints zitten in `backend/app/routers/admin.py` (`GET /admin/hpo/summary`, `GET /admin/hpo/terms`, `POST /admin/hpo/sync`).

Er is ook een lichte losstaande pagina `frontend/src/pages/phenotypes/HpoTermsPage.tsx` die geselecteerde HPO-termen (bv. uit SV-annotaties) toont met een link naar de externe HPO-browser op `hpo.jax.org`.

### Veiligheid & traceerbaarheid (HPO)

- **Toegangscontrole:** zoeken/lezen vereist een ingelogde gebruiker (`get_current_user`); het importeren/synchroniseren van de ontologie is **admin-only** (`get_current_admin_user`) — zie de decorators in `hpo.py` (`POST /hpo/import`) en `admin.py` (`POST /admin/hpo/sync`).
- **Padvalidatie:** de sync/import accepteert een bestandspad uit de request. `ensure_authorized_hpo_ontology_path` in `hpo_service.py` normaliseert dat pad (met `os.path.normpath`, zónder eerst `read_text` aan te raken) en eist dat het in een geautoriseerde ontologie-map ligt. Zo kan een admin-request geen willekeurig hostbestand (bv. `/etc/passwd`) laten inlezen — een bewuste least-privilege-bescherming op een klinisch platform.
- **Referentiële integriteit:** `individual_hpo.hpo_id` verwijst met `ON DELETE RESTRICT` naar `hpo_term`, zodat een fenotype nooit naar een onbekende term kan wijzen. Bij import worden onbekende HPO-termen overgeslagen en als *issue* teruggemeld (`import_family_hpo_annotations`).

## Monarch: genen, ziekten en fenotypes verbinden

### Wat Monarch levert

De [Monarch Initiative](https://monarchinitiative.org/) normaliseert tientallen bronnen (OMIM, Orphanet, ClinGen, HPOA, …) tot één kennisgraaf met stabiele CURIEs. CoGA gebruikt daarvan twee soorten koppelingen:

- **Gen → ziekte** (`HGNC:` → `MONDO:`): welke genen met welke ziekten geassocieerd zijn.
- **Ziekte → fenotype** (`MONDO:` → `HP:`): welke klinische kenmerken bij een ziekte horen.

Uit deze twee wordt afgeleid: **gen → fenotype** (via de gedeelde MONDO-sleutel).

### De twee tabellen

| Tabel (schema) | Inhoud |
| --- | --- |
| `monarch_gene_disease` (`026_monarch_associations.sql`) | Eén rij per `(hgnc_id, mondo_id)`. Aggregeert `predicates` en `sources`; `predicate` bevat de *sterkste* relatie; `causal` is `TRUE` als een causale relatie (`biolink:causes`) aanwezig is; `release_version` bewaart de Monarch-release. |
| `monarch_disease_phenotype` (`027_monarch_disease_phenotype.sql`) | Eén rij per `(mondo_id, hpo_id)`. `negated = TRUE` markeert een *uitgesloten* fenotype (de ziekte presenteert dit specifiek níet); bij tegenstrijdige bronnen wint de aanwezige assertie. |

### Hoe de data geladen wordt

De ingest zit in `backend/app/services/monarch_ingest.py`. In plaats van de volledige kennisgraaf downloadt CoGA de kleine, voorgesplitste "denormalized" TSV-bestanden (labels staan inline mee, enkele MB gzipped). De kernfuncties:

- `refresh_monarch_gene_disease()` — downloadt de twee gen→ziekte-bestanden, filtert op `HGNC:` → `MONDO:` niet-negeerde randen, aggregeert per paar (`parse_gene_disease_tsv`) en vervangt de tabel.
- `refresh_monarch_disease_phenotype()` — laadt het ziekte→fenotype-bestand (`parse_disease_phenotype_tsv`) en vervangt die tabel in blokken van 5.000 rijen (de tabel telt ~265k rijen).
- `refresh_monarch()` — de orchestrator: haalt de release **één keer** op en voert beide vervangingen **in één transactie** uit (`commit=False` op beide, dan één `session.commit()`). Zo kunnen de twee tabellen nooit op verschillende releases eindigen.

Een klein codefragment illustreert de aggregatie van een gen-ziekte-paar (Python) — meerdere bronnen/predicaten vallen samen tot één record:

```python
record.predicates.add(predicate)          # bv. "causes", "contributes_to"
source = _strip_prefix(row.get("primary_knowledge_source") or "")
if source:
    record.sources.add(source)            # bv. "omim", "orphanet"
```

**Bron/versie-tracking.** `_resolve_release_version` weigert bewust data te laden met een onbekende versie: het leest `version:` uit Monarchs `metadata.yaml` en werpt een fout als dat niet lukt. De motivatie staat expliciet in de docstring — de provenance van gegevens die een *ondertekend klinisch rapport* voeden, mag nooit `NULL` zijn. Na een refresh wordt de informatie-inhoud-cache (zie verder) leeggemaakt met `reset_information_content_cache()`.

### De Monarch-adminpagina

De beheerpagina `frontend/src/pages/admin/MonarchDataAdminPage.tsx` toont de geladen release, de tabelgroottes (gen-ziekteparen, causale paren, ziekte-fenotypeparen, …) en biedt een knop "Update Monarch data" (endpoint `POST /admin/monarch/refresh`). Daarnaast is er een zoekfunctie (`GET /admin/monarch/search` → `search_monarch_associations`) waarmee een beheerder op ziekte- of fenotypenaam kan zoeken; die zoek is HPO-sluiting-bewust (een algemene term vindt ook ziekten die alleen met een specifieker subtype geannoteerd zijn).

**Waar in de code:** ingest in `monarch_ingest.py` (`refresh_monarch`, `monarch_status`, `search_monarch_associations`); endpoints in `backend/app/routers/admin.py` (`/admin/monarch/status`, `/admin/monarch/search`, `/admin/monarch/refresh`), alle **admin-only**. Ontwerpachtergrond in `docs/monarch-integration.md`.

## Semantische gelijkenis: hoe dicht ligt een gen bij het klinisch beeld?

Een patiënt heeft zelden precies de termen die in de leerboeken bij een ziekte staan. "Semantische gelijkenis" (semsim) beantwoordt daarom de vraag: *hoe goed lijkt het fenotypeprofiel van dit gen op wat we bij de patiënt zien?* — waarbij een **specifiek gedeeld kenmerk zwaarder weegt dan een algemeen kenmerk**.

### Informatie-inhoud (in gewone taal)

De sleutel is de **informatie-inhoud** (IC) van een term: `IC(t) = −ln(p)`, waarbij `p` de fractie ziekten is die aan die term (of een afstammeling ervan) is gekoppeld. Een brede term als "verstandelijke beperking" komt bij zeer veel ziekten voor → hoge `p` → bijna nul IC → discrimineert nauwelijks. Een specifiek kenmerk als "congenitale hypothyreoïdie" is zeldzaam → hoge IC → een gen dat dít verklaart, springt eruit. Deze eigenschap is inherent aan IC-gewogen gelijkenis; `docs/monarch-integration.md` legt uit waarom een enkele brede term daardoor een vlakke, bijna-willekeurige rangschikking geeft.

### Twee implementaties

CoGA berekent gelijkenis op twee manieren, voor twee verschillende doelen:

**1. Live Monarch-API — `backend/app/services/monarch_semsim.py`.** `semsim_search(termset, group, limit)` stuurt de patiënttermen naar Monarchs eigen `POST /v3/api/semsim/search` en krijgt de best passende **genen** (of ziekten) terug, gerangschikt. Dit voedt het kandidaatgen-panel op de familiepagina (zie verder). Het is bewust *live* (geen bulk-ingest), met een timeout van 25 s, een cache van 1 uur (`_CACHE_TTL_SECONDS`) op de gesorteerde termenset, en een eigen `MonarchSemsimError` zodat de UI netjes "niet beschikbaar" kan tonen. Monarch levert maximaal 50 resultaten (`MAX_LIMIT`).

**2. Lokale Phenomizer-score — `backend/app/services/monarch_phenotype_score.py`.** Dit is nodig voor variant-prioritisatie, want daar moet *elk* gen met een kandidaat-variant gescoord worden — niet enkel de top-50. De methode (Resnik / Phenomizer *best-match-average*):

- `_load_information_content` berekent de IC per HPO-term uit de lokale `monarch_disease_phenotype`-tabel, gepropageerd via `hpo_closure`. Deze IC-map is proces-breed gecachet met een TTL van een uur en een `asyncio.Lock` die de zware aggregatie (~265k rijen) *single-flight* maakt (concurrente eerste-aanvragen wachten op één berekening in plaats van allemaal tegelijk te rekenen). Een lege uitkomst wordt bewust níet gecachet.
- `_resnik(a, b)` = de IC van de meest informatieve gemeenschappelijke voorouder van twee termen.
- `phenomizer_score` neemt het symmetrische gemiddelde van de beste matches (patiënt→gen en gen→patiënt), genormaliseerd naar [0, 1] door de maximale IC.

**Determinisme.** `_cap_terms_by_ic` begrenst zowel de patiëntenset (tot 60 termen) als de gen-set (tot 200) tot de *meest informatieve* termen, met de HPO-id als tiebreaker. De docstring legt uit waarom dat cruciaal is: zonder die tiebreaker zou de selectie afhangen van set-iteratievolgorde (`PYTHONHASHSEED`), wat de fenotype-score — en dus de ranking die in de cache/rapport bevriest — van run tot run zou verstoren.

**Waar in de code:** IC-berekening en scoring in `monarch_phenotype_score.py` (`score_genes_for_hpo`, `phenomizer_score`); live API in `monarch_semsim.py` (`semsim_search`).

## Variant-prioritisatie: van fenotype-score naar rangschikking

De eigenlijke scoring-wiskunde staat in `backend/app/services/variant_prioritization.py`. Dit is het Exomiser-model: het combineert de fenotype-relevantie van het gen met de eigenschappen van de variant zelf. Het is een *pure* functie (geen database, geen netwerk), wat testbaarheid en reproduceerbaarheid ten goede komt.

`score_variant(...)` bouwt vier deelscores op:

| Deelscore | Functie | Wat het meet |
| --- | --- | --- |
| `pathogenicity` | `pathogenicity_score` | Voorspelde schadelijkheid uit impact/LoF, ClinVar, en de predictors CADD/REVEL/SpliceAI/AlphaMissense, plus gen-constraint (gnomAD pLI voor LoF, missense-Z voor missense). |
| `frequency` | `frequency_score` | Zeldzaamheid: zeldzamer = hoger, via gnomAD popmax met terugval op AF. |
| `segregation_weight` | `segregation_weight` | Gewicht op basis van het overervingspatroon (de novo, homozygoot-recessief, compound het, X-gebonden, dominant), berekend uit de stamboom. |
| `phenotype_score` | (uit `monarch_phenotype_score`) | Hoe goed het gen bij het klinisch beeld past. |

De `variant_score` is het product `pathogenicity × frequency × segregation_weight`; de eind-`combined_score` weegt fenotype erbij in via `combine()` met `_PHENOTYPE_WEIGHT = 0.5`. Twee bewuste keuzes borgen de klinische betrouwbaarheid:

- **ClinVar blijft de baas.** Alleen een ClinVar-pathogeen assertie geeft de volle 1.0; predictor-only bewijs is afgetopt op `_MAX_PREDICTOR_PATHOGENICITY = 0.9`. Zo saturen predictie-scores niet en behoudt een échte klinische assertie voorrang. De ClinVar-string wordt op héle woorden gematcht, zodat "Conflicting_interpretations_of_pathogenicity" níet als "pathogenic" telt.
- **Novel-gen-kandidaten verdwijnen niet.** Een gen zonder Monarch-fenotypedata scoort 0 op de fenotype-as, maar de rauwe `variant_score` blijft apart zichtbaar, zodat een analist op die kolom kan herordenen.

### Doorwerking in de small-variant-resultaten

De scoring wordt aangeroepen wanneer de aanroep `GET /api/families/{family_id}/small-variants?prioritize=true` gebruikt: die haalt de gefilterde kandidaatset op, berekent segregatie-modi en fenotype-scores, rangschikt, en geeft per variant een `priority`-blok terug (`_prioritized_small_variants_page` in `backend/app/services/clickhouse_family_variants.py`). In de frontend is er een ingebouwde preset **"Phenotype priority (Exomiser-style)"** (`frontend/src/pages/families/smallVariantSearch.ts`), een sorteerbare **Score**-kolom en een uitklapbare score-uitsplitsing in de reviewdialoog. Voor de precieze werking van de filterpagina en de API zie hoofdstuk [08 — Filterpagina's ↔ API](08-filterpaginas-en-api.md).

**Begrenzing & eerlijkheid.** De kandidaatset is afgetopt op 5.000 (`_PRIORITIZE_CANDIDATE_LIMIT`). Loopt de gefilterde set daaroverheen, dan meldt de respons de échte telling en zet `ranking_truncated`; de UI toont dan dat de ranking incompleet is en dat de filters verfijnd moeten worden. Scores rangschikken *binnen één familie*; het zijn geen gekalibreerde kansen zoals in Exomisers getrainde model — dit staat expliciet als caveat in `docs/monarch-integration.md`.

## De ranking-cache: snel én reproduceerbaar

Het prioritiseren kost ~10 s (vooral het per-gen fenotype-scoren). Omdat de uitkomst **deterministisch** is gegeven de invoer, en die invoer zelden verandert tussen twee keer openen, wordt de gerangschikte volgorde gecachet. Het ontwerp staat in `docs/variant-ranking-cache.md`; de code in `backend/app/services/variant_ranking_cache.py`.

### Wat wordt gecachet

De tabel `family_variant_ranking_cache` (`035_family_variant_ranking_cache.sql`, uitgebreid door `036_ranking_cache_superset.sql`) bewaart per familie en per query-signatuur de **compacte gerangschikte volgorde** — een geordende lijst van `{variant_id, priority}` plus `total`, de truncatie-vlag en provenance. Bewust wordt de variant-*annotatie* en de *review-status* níet meegecachet: die worden bij elk verzoek vers uit ClickHouse/Postgres gehaald. Zo kan een gecachete ranking nooit verouderde annotaties of een verouderde reviewstatus serveren.

### Vers houden via de `inputs_hash`

Elke cacherij is gesleuteld op `inputs_hash` = SHA-256 van een canonieke samenvatting van **alles wat de ranking verandert** (`compute_ranking_hashes`):

| Invoer | Bron in code |
| --- | --- |
| Query-filters (impact, frequentie, exclude-ClinVar, sample/QC-filters) | `canonical_filters(filters)` (paginatie wordt bewust verwijderd) |
| Genpaneel + panelversie | `_panel_version` (leest `version` en `external_version` uit `gene_panels`) |
| HPO-termen van aangedane individuen | `patient_terms` |
| Stamboom / affected-status | `_pedigree_signature` (de `structure_hash` uit `family_structure_versions`) |
| Monarch-release | `_monarch_release` (= `max(monarch_gene_disease.release_version)`) |
| Review-filterstatus | `review_signature` (opgelost buiten het filterobject, dus expliciet ingevouwen) |
| Scoring-**algoritmeversie** | de constante `_ALGORITHM_VERSION` |

Verandert één invoer, dan wijzigt de hash → *cache-miss* → herberekening. Een verouderde ranking wordt dus nooit geserveerd. Paginatie zit niet in de hash: de hele volgorde is gecachet en elke pagina wordt daaruit gesneden.

### Superset-invalidatie (paneel-onafhankelijk serveren)

De per-variant-scores zijn **paneel-onafhankelijk** — een paneel beperkt alleen *welke* varianten in beeld zijn, niet hun score. Daarom draagt elke cacherij ook een `base_hash`: dezelfde samenvatting maar *zonder* het paneel. Alle panelen over dezelfde familie/fenotype/filters delen die `base_hash`.

Bij een exacte-hash-miss zoekt `find_superset_candidates` een volledige (niet-getrunceerde) gecachete ranking met dezelfde `base_hash` waarvan het paneel de gevraagde genen **dekt** (`gevraagd ⊆ gecachet`). De integratielaag (`_serve_subpanel_from_superset` in `clickhouse_family_variants.py`) hervalideert vervolgens het lidmaatschap tegen ClickHouse en snijdt de gevraagde pagina eruit. Zo is het Mendeliome (de default) de superset voor zijn diagnostische sub-panelen: versmallen is instant. **Bewaking:** een getrunceerde superset wordt nooit voor sub-panelen gebruikt (er kon een laaggerangschikte in-panel-variant ontbreken).

### Invalidatie en achtergrond-opwarming

| Gebeurtenis | Effect |
| --- | --- |
| HPO- of stamboom/affected-wijziging | `inputs_hash` verandert (miss); een achtergrond-opwarming herberekent alvast |
| Genpaneel bijgewerkt | `panel_version` verandert → miss |
| Monarch-release ververst | `monarch_release` verandert → alle rankings missen |
| Review-tag/exclude-wijziging | review-signatuur verandert → miss |
| Variant-**herimport** | de cache van de familie wordt gewist (`clear_family_ranking_cache`) |
| Scoring-algoritme gewijzigd (code) | `_ALGORITHM_VERSION` ophogen → alle rankings missen |

De achtergrond-opwarming (`precompute_family_ranking_safe`) *replayt* de meest recente prioritaire query met de nu-actuele invoer, zodat ook het *volgende* openen na een bewerking snel is. Ze wordt via `BackgroundTasks` gepland vanuit dezelfde edit-endpoints die HPO/stamboom wijzigen (`backend/app/routers/families.py`, `backend/app/routers/ped.py`). De variant-herimport wist de cache in `backend/app/services/family_package_import.py`.

**Provenance in de UI.** De respons draagt `ranking_cached` en `ranking_computed_at`, waarmee de small-variant-pagina toont dat de prioritaire ranking uit cache komt en wanneer ze berekend is (`frontend/src/pages/families/SmallVariantResults.tsx`). De melding is puur informatief — invalidatie is automatisch, dus een gecachete ranking is altijd consistent met zijn invoer.

## Frontend: van fenotype naar kandidaat-genen

Het centrale paneel is `frontend/src/pages/families/MonarchPhenotypeMatchPanel.tsx` op de familie-detailpagina. Het is bewust *on-demand*: pas na een klik op "Find candidate genes" wordt Monarch aangeroepen (`GET /families/{family_id}/phenotype-match`), zodat de externe dienst niet bij elke paginalading wordt geraakt. Het endpoint (`families.py`, `family_phenotype_match`) verzamelt de `present`-HPO-termen van de familie (of één lid), rangschikt de genen via `semsim_search`, en verrijkt elk resultaat met:

- `gene_in_platform`: bestaat het gen in dit platform (staat het in de `genes`-tabel)? Zo ja, dan linkt de UI direct naar het gen-profiel (`/genes?gene=…&family_id=…`), waar de Monarch gen→ziekte-blokken en de fenotype-overlap renderen — zie hoofdstuk [13 — Gene Explorer](13-gene-explorer.md).
- `matching_phenotypes` versus `extra_phenotypes`: welke van de gen-fenotypes de familie exhibeert (via `gene_phenotype_breakdown` en `phenotype_closure` — sluiting-bewust, dus een algemeen gen-fenotype telt als de patiënt het óf een specifieker subtype heeft).

Zo sluit de lus: patiëntfenotypes → gerangschikte kandidaat-genen → klik → het gen-profiel met zijn Monarch-ziekten en welke patiëntfenotypes elke ziekte verklaren.

## Veiligheid & traceerbaarheid van de prioritisatie

- **Deterministisch en reproduceerbaar.** De scoring in `variant_prioritization.py` is pure wiskunde; de fenotype-scoring in `monarch_phenotype_score.py` is expliciet volgorde-onafhankelijk gemaakt (IC-tiebreak op HPO-id). Dezelfde invoer geeft dus altijd dezelfde ranking — een voorwaarde voor een auditbaar, herhaalbaar resultaat.
- **Gebonden aan databronversies.** De `inputs_hash` bevat de Monarch-release en de genpaneelversie; de HPO-annotaties dragen de ontologie-`release_version`; Monarch weigert te laden zonder bekende versie. Elke gerangschikte uitkomst is daardoor terug te voeren op exact welke referentiedata haar produceerde.
- **Automatische invalidatie boven "vertrouwen".** De cache serveert nooit een verouderde ranking: elke relevante wijziging flipt de hash, en de code kiest bij twijfel voor herberekenen (getrunceerde supersets worden geweigerd, lege IC-maps worden niet gecachet). Zie ook hoofdstuk [11 — Rapport & traceerbaarheid](11-rapport-en-traceerbaarheid.md) voor hoe deze provenance in het ondertekende rapport terechtkomt.
- **Toegangscontrole.** Fenotype-matching vereist een ingelogde gebruiker; het laden/verversen van HPO- en Monarch-referentiedata is admin-only. Geen enkele referentie-refresh kan door een gewone gebruiker worden getriggerd.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/db/schema/postgres/015_hpo.sql` | HPO-tabellen: `hpo_term`, `hpo_synonym`, `hpo_edge`, `hpo_closure`, `individual_hpo` |
| `backend/app/services/hpo_service.py` | HPO parsen/laden, versiebeheer, zoeken, sluitingsberekening, padvalidatie |
| `backend/app/routers/hpo.py` | HPO-browser-endpoints (`/hpo/search`, `/hpo/{id}`, `/hpo/import`) |
| `backend/db/schema/postgres/026_monarch_associations.sql` | `monarch_gene_disease` (gen → ziekte) |
| `backend/db/schema/postgres/027_monarch_disease_phenotype.sql` | `monarch_disease_phenotype` (ziekte → fenotype) |
| `backend/app/services/monarch_ingest.py` | Monarch-download/parse/replace, atomische refresh, status, zoeken |
| `backend/app/services/monarch_semsim.py` | Live semsim-API voor kandidaatgen-rangschikking |
| `backend/app/services/monarch_phenotype_score.py` | Lokale Phenomizer/Resnik-scoring + IC-cache |
| `backend/app/services/variant_prioritization.py` | Exomiser-achtige scoring-wiskunde (pure functie) |
| `backend/app/services/variant_ranking_cache.py` | Ranking-cache: hashing, opslag, superset-selectie, invalidatie |
| `backend/app/services/clickhouse_family_variants.py` | Prioritaire pagina, superset-serveren, achtergrond-opwarming |
| `backend/db/schema/postgres/035_family_variant_ranking_cache.sql`, `036_ranking_cache_superset.sql` | Cachetabel + `base_hash`/`panel_id` voor superset-serveren |
| `backend/app/routers/admin.py` | Admin-endpoints voor HPO- en Monarch-beheer |
| `frontend/src/pages/families/MonarchPhenotypeMatchPanel.tsx` | Kandidaatgen-paneel op de familiepagina |
| `frontend/src/pages/admin/HpoTerminologyAdminPage.tsx` | HPO-beheer (overzicht, sync-preview/apply, browser) |
| `frontend/src/pages/admin/MonarchDataAdminPage.tsx` | Monarch-beheer (status, refresh, ziekte/fenotype-zoek) |
| `frontend/src/pages/phenotypes/HpoTermsPage.tsx` | Lichte HPO-termenweergave met externe browserlink |
| `docs/monarch-integration.md`, `docs/variant-ranking-cache.md` | Ontwerpreferenties (fasen, cache-invalidatie) |
