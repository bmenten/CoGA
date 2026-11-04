# 15. Overige modules & adminfunctionaliteit

Dit hoofdstuk behandelt de modules zonder eigen hoofdstuk: de Clinical CNV Explorer met de kennisbank van klinische CNV's, de genpanels, de verkennende ancestry-PCA, de beheerfuncties en hun rechten, de in-app documentatie, de releasepagina en de UI-telemetrie. Uitleg voor beheerders staat in de gebruikersgids in de app (`frontend/src/content/docs/user-guide/administration.md`); het beheer van familieleden en stamboom in `docs/family-member-management.md`; alle tabellen in `docs/database.md`.

## Toegang als rode draad

Bijna elke module hangt aan een van drie poorten (hoofdstuk 2):

| Dependency | Betekenis | Toegepast op |
| --- | --- | --- |
| `get_current_user` | Elke ingelogde gebruiker met een geldig token | De CNV-catalogus, panels lezen, releases, UI-events insturen |
| `get_current_superuser` | Een superuser of een beheerder: de rol `superuser` of `admin` (`has_superuser_rights`) | Panels aanmaken, wijzigen en verwijderen, de PanelApp-import en het Mendeliome; de varianttags, het overzicht van de presetfilters en de grenzen voor sequencing-QC; de pakketimport (een bestaande familie overschrijven alleen een beheerder) |
| `get_current_admin_user` | Alleen beheerders: de rol `admin` (`is_admin_user`) | De rest van de beheerrouter `/api/admin`, projecten, de accountlijst en de rollen, PED- en andere uploads |

De actor van een handeling komt altijd uit het token, nooit uit de inhoud van het verzoek.

## Clinical CNV Explorer

De Clinical CNV Explorer (`/cnv-explorer`) laat curatoren de **kennisbank van terugkerende CNV-syndromen** doorbladeren (bv. microdeletiesyndromen), samengesteld uit o.a. ClinGen/ISCA, DECIPHER, OMIM/Orphanet en ClinVar. Per CNV toont de pagina de gegevens uit de kennisbank en de steun in ClinVar: het aantal (waarschijnlijk) pathogene ClinVar-CNV's (**ClinVar P/LP**) per kant, verlies en winst. Elke rij linkt naar een detailpagina (`/cnv-details/{id}`), die ook de ondersteunende ClinVar-records linkt.

De router `/api/cnvs` heeft drie leesendpoints, alle achter een login: één CNV op id, de doorzoekbare catalogus per assembly, en de CNV's die een genomisch bereik overlappen (voor de tracks, hoofdstuk 9).

**De cytoband.** Elk van die endpoints geeft een CNV met zijn cytoband: die uit de kennisbank, of anders de banden die de regio overspant in de cytobanden van de assembly, dezelfde die het ideogram tekent (`services/cytoband_labels.py`, ook gebruikt voor de small en structurele varianten). Zo krijgt ook een CNV een band als de build de cytobanden niet kon lezen, of als hij geladen werd voor de cytobanden van de assembly. Een assembly zonder cytobanden (T2T bewaart één band per chromosoom, met de naam van het chromosoom) geeft geen band.

**De kennisbank herbouwen.** Een beheerder start een herbouw vanaf de pagina *Organisms and assemblies* (`/reference-data`).

- Het zware bouwscript (`scripts/clinical_cnv_knowledgebase.py`, dat o.a. ClinVar ophaalt) draait als een **apart proces**. Zo raken de downloads en de extra afhankelijkheden het API-proces niet.
- Het resultaat gaat via de gewone referentielader naar `clinical_cnvs` en wordt als import geregistreerd (hoofdstuk 13).
- De voortgang staat in `clinical_cnv_kb_jobs`: wie het vroeg, de tijdstippen, het aantal rijen en een eventuele fout.
- De job bewaart de aanvrager als account (`requested_by_id`, SEC-35). De herbouw loopt, en laadt wat hij bouwde, alleen zolang dat account een actieve beheerder is. Dat wordt nagekeken bij de start en opnieuw vlak voor het laden, want de build loopt tot twee uur. Is het account intussen gedeactiveerd, geen beheerder meer of bestaat het niet meer, dan eindigt de job `failed` met de reden, en blijft de vorige kennisbank staan.
- Ontbreekt het script op de server, dan zegt de status dat en weigert de backend een herbouw (`503`).

De ruggengraat van de kennisbank zijn de dosiscuratie van ClinGen en het ClinGen-bestand met de terugkerende CNV-regio's. Kan het script een van beide niet laden, dan stopt het, in plaats van een kennisbank zonder die bron te schrijven. De herbouw mislukt dan: de fout noemt de bron en de reden, de pagina toont dat bij de laatste herbouw van die assembly, en de vorige kennisbank blijft staan.

Het bestand met de terugkerende CNV-regio's is per assembly vastgezet op zijn SHA-256 (`CLINGEN_RECURRENT_CNV_SHA256`, TRACE-14). Het script leest het alleen als de SHA-256 klopt, en stopt anders op dezelfde manier. Voor GRCh38 is dat ClinGens bestand van release V2.1, hetzelfde als het meegeleverde bestand van de segmentale duplicaties. Voor GRCh37 is het ClinGens V2.1-bestand voor GRCh37, op 2026-10-27 met toestemming van de eigenaar gedownload van ftp.clinicalgenome.org (TRACE-18).

De andere bronnen leest het script zoals ze die dag gepubliceerd zijn: de dosiscuratie van ClinGen, de UCSC-cytobanden, ClinVar, Orphanet en een DECIPHER-koppeling. Het registreert elk bestand dat het in de kennisbank inlas, met zijn SHA-256 en grootte; een lokaal bestand (een Orphanet-XML of de DECIPHER-koppeling) onder zijn naam, niet onder zijn pad (TRACE-18). De import registreert al die bestanden als release van de kennisbank: het vastgezette met release en SHA-256, de andere met naam en SHA-256. Een ondertekend analyserecord bevriest dat. Een build die ze niet volledig registreerde, wordt niet geladen.

**ClinVar-steun.** Per regio telt de build de (waarschijnlijk) pathogene ClinVar-CNV's (niet de "conflicting") die de regio voldoende wederzijds overlappen, apart voor verlies en winst. De kant volgt eerst uit het type in ClinVar, dan uit het kopieaantal in de naam van een array-record, en pas dan uit woorden in de naam. De tellingen worden alleen geschreven als ClinVar echt geraadpleegd werd. Een kennisbank zonder ClinVar staat als leeg in de tabel en leest in de app als "not recorded", nooit als 0.

**Eén herbouw tegelijk.** De databank laat hooguit één actieve job toe: een unieke index op een constante (`idx_clinical_cnv_kb_jobs_one_active`) voor jobs met status `queued` of `running`. Een aanvraag tijdens een actieve herbouw krijgt daardoor `409`, ook als twee aanvragen tegelijk binnenkomen. Een lopende job meldt elke minuut dat hij nog leeft (`worker_id`, `heartbeat_at`). Blijft dat teken tien minuten uit (bv. door een herstart of nieuwe uitrol van de server), dan sluit de statusopvraging of een nieuwe aanvraag de job af als mislukt ("Interrupted: …"). Daarna kan een nieuwe herbouw starten. Een build die langer duurt dan `CLINICAL_CNV_KB_BUILD_TIMEOUT_SECONDS` (standaard twee uur) wordt gestopt en als mislukt gemarkeerd. Zo eindigt elke job als `completed` of `failed`, en blokkeert geen job de volgende herbouw voorgoed.

**Waar in de code:** `backend/app/routers/cnvs.py`; de herbouw in `backend/app/services/clinical_cnv_kb_jobs.py` en de endpoints `/api/admin/clinical-cnv-kb/...`; de pagina's `frontend/src/pages/cnv-explorer/ClinicalCnvExplorerPage.tsx` en `frontend/src/pages/genome/CnvDetailsPage.tsx`.

## Genpanels

Een **genpanel** is een benoemde lijst genen (eventueel met regio's en repeatloci) die variantfilters afbakent. De catalogus voedt het panelfilter op de filterpagina's (hoofdstuk 8) en in de Variant Explorer, die een panel toepast zoals de familiepagina (hoofdstuk 14), en het panellidmaatschap in de Gene Explorer (hoofdstuk 13).

- **Lezen** (elke ingelogde gebruiker): de lijst, één panel, zijn versies, en zoeken in PanelApp.
- **Schrijven** (alleen een superuser of beheerder, `get_current_superuser`): aanmaken, wijzigen, verwijderen, een PanelApp-panel importeren en het Mendeliome opnieuw opbouwen. Ook het nakijken van gennamen voor een bewerking (`POST /api/panels/genes/check`) is voor hen: het leest alleen, maar is een POST, zoals de bewerking die het dient.

**Versies.** Een panel krijgt versie 1 bij het aanmaken, het opbouwen (het Mendeliome) of de eerste import uit PanelApp. Het krijgt een volgende versie telkens zijn genen of regio's veranderen: door een bewerking (bij een lokaal panel ook een nieuwe beschrijving), een nieuw opgebouwd Mendeliome met andere genen, of een nieuwe PanelApp-import met andere genen of regio's. Elke versie wordt bewaard met de volledige genen- en regiolijst, de bron, de externe versie en wie de wijziging deed (ook als tekst, zodat die herkenbaar blijft als het account verdwijnt). Zo is terug te vinden welke genset een panel op een bepaald moment had. De app herschrijft een bewaarde versie nooit, en de databank weigert een tweede versie met hetzelfde nummer. Geen trigger maakt de tabel `gene_panel_versions` echter append-only. **Let op:** een panel verwijderen wist ook zijn versiegeschiedenis. Daarom vraagt *Delete* in de lijst eerst een bevestiging, die het panel en zijn versie noemt; pas die stuurt `DELETE /api/panels/{panel_id}`. *Cancel* en Escape sturen niets.

**Onbekende namen.** Een gennaam die geen geladen assembly kent (een tikfout, een oud symbool), blijft in de genlijst van het panel maar krijgt geen regio: een filter op het panel vindt er geen variant in. De pagina van het panel noemt zulke namen *not imported* (de genen zonder regio in welke assembly ook).

**Een lokaal panel bewerken.** Op de pagina van het panel verwijdert een superuser of beheerder een gen met het vuilbakje op zijn rij, voegt hij namen toe, of bewerkt hij de hele lijst. Alle drie leveren dezelfde lijst wijzigingen op (*Changes for version N*). Er wordt pas iets bewaard na de bevestiging. De toe te voegen namen worden vooraf nagekeken (`POST /api/panels/genes/check`); een naam die geen assembly kent, wordt gemarkeerd. De bevestiging stuurt de volledige nieuwe genlijst met de versie waarop de wijziging gemaakt werd (`expected_version`). De backend leest het panel `FOR UPDATE` en weigert met `409` als het intussen een nieuwere versie heeft: de lijst zou anders de wijzigingen van die versie ongezien terugdraaien. De pagina laadt het panel dan opnieuw en houdt de wijzigingen klaar om opnieuw te bevestigen.

**PanelApp.** Een superuser of beheerder zoekt en importeert panels uit Genomics England PanelApp. Hij kiest het betrouwbaarheidsniveau, de assembly, en of regio's en repeats meekomen. Het PanelApp-id, de versie en de bron-URL worden bij het panel bewaard. De versie van PanelApp (`external_version`) staat los van de versie van het panel in CoGA. Een panel dat al in de catalogus staat opnieuw importeren (uit een andere release of met andere keuzes), geeft het een nieuwe versie als zijn genen of regio's veranderen. Blijven die gelijk, dan houdt het panel zijn versie en wordt er niets bewaard; de import legt wel de PanelApp-versie, de keuzes en het tijdstip vast. De sleutel van de ranking-cache (hoofdstuk 12) bevat de versie van het panel en zijn genen en regio's. Een rangschikking op de vorige genen is dus geen exacte treffer meer, en bedient ook geen ander panel meer.

**Mendeliome.** Een gegenereerd panel met de genen die in de Monarch-kennisgraaf een oorzakelijke of geassocieerde relatie met een aandoening hebben. Het wordt opnieuw opgebouwd wanneer een nieuwe Monarch-release wordt geladen, en krijgt dan een nieuwe versie als zijn genen veranderen (hoofdstuk 12).

De UI verbergt de schrijfknoppen voor wie geen superuser of beheerder is, maar de afscherming zit op de server.

**Waar in de code:** `backend/app/routers/panels.py`, `backend/app/services/panel_metadata_service.py` en `panelapp_service.py`; de pagina's in `frontend/src/pages/panels/`.

## Ancestry-PCA (verkennend)

**Buiten het beoogde doel.** De sectie *Ancestry (exploratory)*, de laatste kaart van de Sample QC van een familie (`FamilyAncestryPanel.tsx` in `FamilySampleQcPage.tsx`), toont hoe genetisch gelijk elk lid is aan publieke referentiepopulaties. Ze is niet gevalideerd en niet voor klinische interpretatie, en komt nooit in een ondertekening of een ondertekend record (TF-01 §4, TF-15 §3). De sectie en haar plot zeggen dat: *Exploratory — not for clinical use*. Ze projecteert elk lid op vier vaste hoofdcomponenten van een publieke referentie en tekent het naast de referentiepersonen. De uitleg voor ontwikkelaars staat in `docs/ancestry-pca.md`.

**Wat een projectie leest.** De opgeslagen small variants van de familie in ClickHouse, op de referentiemarkers alleen (`ancestry_callsets.read_marker_rows`): de callsets `clair3` (rechtstreekse calls), `nipt` (de calls per sample van een NIPT-familie) en `glimpse2` (geïmputeerde genotypes). Dat duurt minder dan een seconde; er wordt geen VCF opnieuw gelezen en niets geregistreerd.

**Wat een callset dekt: gemeten, niet verklaard.** Een gesequencet genoom draagt het ALT-allel op ongeveer twee op vijf markers (Hardy-Weinberg uit de referentiefrequentie). De dekking van een callset is het aantal markers met een niet-referentiecall van het lid, gedeeld door die verwachting, binnen en buiten het exoommasker:

- **een call op elke marker** (`complete`): een expliciete genotypecall op minstens 95 % van de markers; een ontbrekend record telt niet mee;
- **genoom**: minstens 75 % binnen en buiten het exoom; een ontbrekend record is homozygoot referentie;
- **exoom**: minstens 75 % binnen het exoom, minder erbuiten; een ontbrekend record is referentie binnen het exoommasker, erbuiten valt de marker weg;
- **een deel van de markers** (`partial`: een genpanel, een deel van de sites, een gefilterde imputatie): minder dan 75 % ook binnen het exoom; niet geprojecteerd.

Van de callsets waaruit een lid geprojecteerd kan worden, neemt CoGA eerst de rechtstreekse calls, dan de NIPT-calls per sample, dan de geïmputeerde genotypes. Een cfDNA-sample uit maternaal plasma en een embryo vallen weg; de andere leden van zo'n familie worden wel geprojecteerd.

**De berekening.** Per lid en marker is de dosis het aantal ALT-allelen van een diploïde call met precies de REF en ALT van de marker die zijn filter doorstaat; een call zonder filter (`FILTER=.`) telt voor geïmputeerde genotypes en voor een callset die geen enkele call filterde. Elke andere call, of twee rijen van één callset op één positie, laat de marker weg. De PC's zijn de kleinstekwadratenfit van de referentieassen aan de bruikbare markers, zodat een weggelaten marker het punt niet naar het midden trekt. Een lid wordt geprojecteerd met minstens 1.000 bruikbare markers (`MIN_MARKERS`) en 95 % van de gedekte markers bruikbaar (`MIN_CALL_RATE`); anders zegt de tabel waarom niet.

**Wanneer ze loopt.** De ancestry-worker loopt waar de pakketimport loopt: in de importworkerprocessen, of in de API als die zelf importworkers draait (hoofdstuk 4). Elke 30 seconden zet hij een automatische job (`ancestry_runs`, `trigger = 'automatic'`) in de wachtrij voor elke familie waarvan de dataversie van de small variants nog door geen job gelezen werd, zodra ze op GRCh38 staat, haar laatste import voltooid is en er geen loopt. *Calculate PCA projection* zet er een met de hand in de wachtrij (`trigger = 'user'`). Geen import of upload roept hem aan. Een voltooide job schrijft één append-only snapshot (`ancestry_results`): de vier PC's van elk lid of waarom niet, de callset waaruit het gelezen werd en hoe, de scan van elke callset, de gelezen dataversie, de rekenversie en de regels. Een mislukte of onderbroken job publiceert niets.

**De referentie.** `backend/app/data/ancestry/1000g-grch38-v1.npz`: 2.504 personen van het 1000 Genomes Project (fase 3, vijf superpopulaties) en 9.569 autosomale GRCh38-SNP's, afgeleid van de vastgezette publieke bestanden van peddy, die zijn SNP's in exoomcaptureregio's koos. Het exoommasker (`1000g-grch38-v1.exome.json`) markeert de 8.398 markers in de CDS van een eiwitcoderend transcript van GENCODE v50, of binnen 20 bp ervan. De vier PC's komen uit de referentie alleen, zodat familiedata de assen nooit bepalen. De SHA-256 van het model en van het masker zijn vastgezet (`DEFAULT_REFERENCE_SHA256`, `DEFAULT_EXOME_MASK_SHA256`); `ANCESTRY_REFERENCE_PATH` kiest een eigen model, dat tegen zijn eigen JSON-bestand gecontroleerd wordt. Elke projectie houdt de referentie waarmee ze berekend werd (`ancestry_reference_panels`), en blijft leesbaar op haar eigen assen.

**De sloten.** Een berekening controleert de familie onder de schrijfsloten op haar varianten, sluit die transactie en leest de markers. Ze neemt de sloten opnieuw om haar resultaat te schrijven, en weigert als er intussen naar de varianten van de familie geschreven werd of haar leden veranderden. Zo wacht een import of een ondertekening nooit op de PCA.

**Vroegere projecties.** Tot revisie 0016 las een projectie een geregistreerd WGS-bestand (`wgs_sources`). Die jobs en resultaten blijven leesbaar; de sectie toont ze als vroegere projecties. Er wordt geen bestand meer geregistreerd, en `metadata.ancestry` in een manifest wordt niet gelezen.

**Toegang.** Een lezer van het project ziet de sectie; met de hand berekenen vraagt het recht om het project te wijzigen. Een familie of project buiten de projecten van de gebruiker antwoordt `404`, zoals een onbekende. De tabellen staan in hoofdstuk 3.

**Waar in de code:** `backend/app/routers/ancestry.py`; `backend/app/services/ancestry_service.py`, `ancestry_callsets.py` en `ancestry_projection.py`; de sectie `frontend/src/pages/families/FamilyAncestryPanel.tsx`.

## Beheerfuncties

De hele beheer-API staat in `backend/app/routers/admin.py` (`/api/admin`); elk endpoint vraagt `get_current_admin_user`, behalve de varianttags, het overzicht van de presetfilters en de grenzen voor sequencing-QC, die `get_current_superuser` vragen. De schermen staan in `frontend/src/pages/admin/`, met `AdminDashboardPage.tsx` als ingang. Een superuser ziet daar alleen zijn vijf werkruimtes: *Gene panels*, *Sequencing QC thresholds*, *Package import*, *Variant tags* en *Preset filters* (`visibleAdminGroups`); de andere zijn voor een beheerder (hoofdstuk 2).

### Gebruikers en projecten

- **Gebruikers:** per gebruiker e-mail, naam, affiliatie, aanmeldwijze, datum toegevoegd, status, rol, actief en projecttoegang, de accounts die op goedkeuring wachten bovenaan. Een beheerder activeert of deactiveert een account, zet zijn rol, een van de vijf (`PATCH /api/auth/users/{user_id}`, hoofdstuk 2), archiveert of herstelt het, en verwijdert een account dat nooit actief was (`DELETE /api/auth/users/{user_id}`, hoofdstuk 5), telkens na een bevestiging; projecttoegang wordt daar alleen getoond. De pagina zegt naar wie de mail over een wachtend account gaat en stuurt een testmail.
- **Projecten van een familie:** welke projecten een familie zien. De data-inventaris (`GET /api/admin/data`) toont ze per familie; `PUT /api/admin/families/{family_id}/projects` wijzigt ze.
- **Een project verwijderen** (`ProjectsPage.tsx`, `DELETE /api/projects/{project_id}`): de databank wist met het project ook zijn koppelingen (`ON DELETE CASCADE`). Zijn families en samples blijven, maar zonder koppeling aan het project; zijn leden verliezen de toegang die het hun gaf; de reviewtags van het project worden gewist. Een familie die in geen ander project zit, kan daarna alleen een superuser of beheerder nog openen. Daarom vraagt *Delete project* eerst een bevestiging die dat zegt, met de aantallen: families, samples, families die in geen ander project zitten, en leden. De pagina telt ze uit de projectenlijst, die een beheerder volledig ziet. Pas de bevestiging stuurt het verzoek. *Cancel* en Escape sturen niets.

### Datamanagement en herkomst

- **Datamanagement** (`DataManagementPage.tsx`): per familie de leden, samples, projecttoegang, assaydata en ruwe bronbestanden, met per soort data het aantal rijen in Postgres en ClickHouse. Elke verwijdering vraagt `confirm=true`: per datasoort van een sample of familie, of een volledig sample of volledige familie. Een intervaltrack die meer dan één caller van een sample vulde (de coverage van een long-read sample uit HiFiCNV, WisecondorX en QDNAseq, of de segmenten), wordt ook per caller verwijderd. De inventaris telt elke caller apart (`track_source_counts`). `DELETE /admin/data/samples/{sample_id}/{tracktype}?source=…` verwijdert de track van die ene caller (rijen en record) en laat de andere staan. Een sample of familie verwijderen komt ook in het klinische auditspoor van de familie: een sample als stamboomwijziging, een familie als de laatste regel van haar spoor (`family_deleted`, hoofdstuk 11).
- **Bronbestanden** (`RawFileProvenanceTable.tsx`): alle ruwe bronbestanden van een familie, met opslagpad, grootte en SHA-256. Een lokaal bestand is te downloaden (`410` als het niet meer op zijn pad staat) en te **verifiëren**: de backend berekent de SHA-256 opnieuw en vergelijkt met de opgeslagen waarde (hoofdstuk 6). Een bestand in een bucket wordt niet via CoGA gedownload (`409`). De verificatie vergelijkt het object dan met het record van de opslag, zonder opnieuw te hashen: een verdwenen object is `missing`, een vervangen object `mismatch`.
- **Stamboomexport:** een PED-bestand per familie.
- **Familiestructuur:** leden, ouder-kindrelaties en partners bewerken. Een controle weigert inconsistente stambomen, en de backend toont vooraf wat een wijziging raakt. Een lid hernoemen wordt geweigerd zolang er genomische data aan de oude sample-ID hangt. Een lid verwijderen vraagt een bevestiging en maakt het lid inactief. Een sample-ID die een bewerking aan een lid geeft (een nieuwe naam, een vader of moeder, een lid dat de structuur toevoegt), volgt dezelfde regel als bij de import: afdrukbare tekst zonder spaties, en geen pad. De witruimte errond valt weg. Een ID dat de ID-regel weigert, wordt geweigerd (400) voor er iets onder gelezen of geschreven wordt: met een stuurteken (zoals een regeleinde, een tab, een escape of een NUL) of witruimte erin; met een teken dat de schrijfrichting omkeert of geen breedte heeft; met een `/` of een `\`; of een ID dat `.` of `..` is (hoofdstuk 6). Een batch met zo'n ID hernoemt niemand. De geïmporteerde data blijft bewaard; wat van de gewijzigde gegevens afhangt, wordt als verouderd gemarkeerd in `families.metadata.derived_data_status`. Alleen `clear_existing_genomic_data` wist de data (de interface stuurt het nooit). De markering zegt dan dat de data niet bewaard bleef, en de versierij in `family_structure_versions` telt wat gewist werd. De varianten en tracks in ClickHouse gaan als laatste, vlak vóór de vastlegging, na elke andere schrijfactie van de wijziging. Zo zet een wijziging die eerder mislukt, de familie terug met haar varianten en tracks (vroeger gingen die eerst, en bleven ze weg). Elke wijziging aan de stamboom wordt een nieuwe structuurversie (append-only en hash-geketend per familie) en een `pedigree`-regel in het klinische auditspoor, met wie ze maakte en elk gewijzigd lid ervoor en erna (hoofdstuk 11). Zie `docs/family-member-management.md`.

### ClickHouse-onderhoud

Per assembly: de status van de tabellen, en onderhoudsacties achter een bevestiging: tabellen aanmaken of bijwerken, onderdelen samenvoegen (*optimize*), de gen-index van de small variants herbouwen, en een integriteitscontrole (hoofdstuk 11). Zonder een controle te starten toont de pagina ook het laatste resultaat van de geplande integriteitscontrole per assembly en wanneer die liep, of dat ze niet kon lopen (`GET /api/admin/clickhouse/variants/integrity-monitor`, alleen voor beheerders).

### Familiestatussen

De catalogus van werkstatussen (bv. "Solved", "Analysis in progress"). Een status verwijderen verwijdert geen familie: haar statusveld wordt leeg. De status en de toewijzing van een familie zelf zet wie de familie mag wijzigen: een analist van een van haar projecten met de rol `analyst` of hoger, een superuser of een beheerder (hoofdstuk 2).

### Grenzen voor sequencing-QC

Op de pagina *Sequencing QC thresholds* stelt een superuser of beheerder per QC-metriek een **waarschuwings-** en een **foutgrens** in, per profiel:

- **Een profiel per assay.** Eén grens kan geen twee assays dienen: wat normaal is voor een long-read-genoom, is een fout voor een panel. Een familie gebruikt het profiel uit haar metadata, en anders het standaardprofiel (er is er precies één).
- **De metrieken staan in de code, de grenzen in de databank.** Welke metrieken bestaan en of een lage of een hoge waarde de foutkant is, volgt uit wat de QC-parsers schrijven; wie de grenzen instelt, kan de richting niet omdraaien. Een grens op een metriek die uit de catalogus verdween, beoordeelt niets meer. De pagina toont ze onder de catalogus, met *Remove*, langs dezelfde bevestiging en reden als elke wijziging (vroeger stond ze er niet, en weigerde de server ze te wissen).
- **Geen standaardgrenzen.** Een grens is een klinische beslissing; er worden er geen meegeleverd. Zonder grens wordt een metriek gemeld als *niet beoordeeld*, nooit als geslaagd.
- **Eén oordeel voor iedereen.** De backend evalueert de grenzen, zodat de werkruimte, de sample-QC en het rapport hetzelfde oordeel tonen. Ook de mtDNA-grenzen komen van hier.
- **Het oordeel in de ledentabel.** De chip *Seq. QC* op de familiepagina toont per sample het slechtste oordeel naast de gemiddelde diepte. Een geslaagd sample toont alleen het getal. Elk ander oordeel staat er als woord: *Warning* (oranje), *Fail* (rood), of *Not run* (grijs) voor een sample dat niet beoordeeld werd, omdat voor geen van zijn metrieken een grens is ingesteld of de server geen oordeel teruggaf. Zo leest een niet beoordeeld sample nooit als geslaagd, ook niet zonder kleur. Heeft een sample geen gemiddelde diepte of N50, dan toont de chip het oordeel met de waarde van de metriek die het bepaalde (*Fail ADO 14.2%*): de eerste metriek, in de volgorde van de catalogus, met het oordeel van het sample. Dat is zo bij een embryo dat alleen in de familietabellen van de PGT-pipeline staat; zijn QC is dan alleen zijn `pgt`-deel (allel-drop-out en -drop-in en de Mendel-concordantie voor en na imputatie, in %). Bepaalde geen metriek het oordeel (*Not run*), dan staat alleen het oordeel er. Een `-` betekent dat er voor het sample geen sequencing-QC te tonen is. De tooltip verschijnt bij de muis en bij de toetsenbordfocus, ook op een chip zonder QC-rapport. Hij noemt elke metriek over haar grens, met haar waarde afgerond zoals op de chip en de grens zoals ingesteld, in de eenheid van de metriek (*Mean depth 18.6x below 20x*), of zegt waarom het sample niet beoordeeld werd.
- **Streng vastgelegd.** Een wijziging vraagt een bevestiging die beide waarden herhaalt, en een **reden**. Ze komt in de append-only tabel `qc_threshold_changes`, met de waarde die ze vervangt en de reden. Een trigger weigert wijzigen en wissen, en de runtime-rol `coga_app` heeft er geen rechten voor (hoofdstuk 2). De HTTP-auditlog kent de vorige waarde niet: alleen hier is te zien wie een grens verlaagde en van welke waarde.
- **Alle waarden op de Sample QC-pagina en in het rapport.** De tabel per sample op de Sample QC-pagina toont naast de integriteitscontroles, onder de kolomgroep *Sequencing & embryo QC*, elke sequencing-QC-waarde: één kolom per metriek, en in de kolom *Seq. QC* het slechtste oordeel. Dat zijn de gemiddelde diepte, de alignmentmetrieken van Qualimap, en voor een embryo van de PGT-pipeline de allel-drop-out en -drop-in (Picard) en de Mendel-concordantie voor en na imputatie (RTG Tools). Die laatste zijn de getallen van het dashboard van de pipeline; de import leest ze uit haar tabellen (`picard/<familie>_ADO_ADI.csv`, `rtgtools/<familie>_{cohort,imputed}_concordance.csv`). Een waarde over haar grens staat er met haar oordeel bij; een waarde zonder grens wordt getoond, niet beoordeeld. Het zijn geen sample-integriteitscontroles: ze veranderen de ring en de status van het sample en het totaaloordeel van de pagina niet, en houden de sign-out niet tegen. De ondertekende versie toont per sample elke bevroren waarde, niet alleen die over hun grens.

**Waar in de code:** `backend/app/services/qc_threshold_service.py`; de pagina `AdminQcThresholdsPage.tsx`; de chip `frontend/src/pages/families/SampleQcCell.tsx`; de kolommen op de Sample QC-pagina `sequencingQcColumns.tsx`.

### De repeatcatalogus: telwijze van TRGT

- **Per locus een register** (`AdminRepeatLociPage.tsx`, *Repeat catalogue* onder Reference Data). Per rij van de repeatcatalogus staan de telcorrectie van QA (*Correction: TRGT’s count −1*; geen als er geen is) en of QA gevalideerd heeft dat de telling van TRGT, met die correctie, er de klinische repeattelling is, met de reden, wie en wanneer, en hoeveel opgeslagen calls de rij noemen. De rijen waar de TRGT-catalogus van het labo naar verwijst, komen eerst. Wat er over de telling van TRGT bij een gen bekend is (*FMR1*, *HTT*, *POLG*), staat erbij.
- **De telcorrectie.** *Set count correction* opent het venster met het veld *Count correction*: een geheel getal van −20 tot 20, het aantal repeats dat CoGA bij de telling van TRGT optelt voor het die classificeert (0 voor geen). Het venster van *Record as validated* en *Withdraw validation* toont hetzelfde veld met de huidige correctie, want elke opslag vermeldt het hele register. Een validatie geldt voor de correctie waarmee ze gegeven werd: wie bij een gevalideerd locus alleen de correctie wijzigt, legt het locus als niet gevalideerd vast, tot QA het met de nieuwe correctie valideert. Bij *FMR1* staat naast het veld dat een vaste correctie er fout is (TRGT telt 1 tot 3 te laag, naargelang de AGG-onderbrekingen): laat ze op 0 en het locus niet gevalideerd. Niets is vooraf ingevuld.
- **Altijd met een reden.** Een wijziging vraagt een bevestiging en een reden (bv. het validatierapport, de nagekeken TRGT-catalogus en -versie). Ze is een gebeurtenis in de klinische audittrail, op de keten `system:repeat-loci`, met de correctie en de validatie ervoor en erna; de geschiedenis staat onderaan de pagina. Een correctie geldt meteen, gevalideerd of niet. Bij een niet gevalideerd locus markeren de repeatpagina en de genoomtracks (gecorrigeerde) tellingen dicht bij een grens (hoofdstuk 8). Leg een locus alleen als gevalideerd vast als de telling van TRGT, met haar correctie, er de klinische repeattelling is.

### Presets en tags

Een overzicht van de bewaarde filterpresets voor small variants van alle gebruikers, elk met de gebruikersnaam waaronder hij laatst bewaard werd (de eigenaar is het account, hoofdstuk 10), en het beheer van de eigen varianttags (hoofdstuk 10). Beide zijn ook voor een superuser.

### Auditlogs en integriteit

- **Auditlogs** (`AdminAuditLogsPage.tsx`): de HTTP-auditlog (*API requests*), te filteren op methode, status, gebruiker en pad, met per verzoek de afgeleide databankwijziging; en de UI-telemetrie (*UI interactions*). De kolom *Flags* toont alleen wat de backend in een rij markeerde, niets anders uit `request_meta` of `detail`. Er zijn twee markeringen. Een verzoek naar een familie, sample of project buiten de projecten van de gebruiker, beantwoord als voor een onbekend record (`request_meta.record_hidden`, bv. *Family outside the user's projects*). En de kolommen waarin een waarde die Postgres niet kan opslaan als escape bewaard werd (`_escaped`, bv. *Escaped: path, query string*; hoofdstuk 7). Bij een UI-interactie telt alleen `_escaped`: de rest van `detail` komt van de browser.
- **Integriteit:** de hash-keten van een familie nalopen, en integriteitsankers maken en controleren (hoofdstuk 11). Daarvoor bestaan alleen endpoints, geen scherm.

### Overige

- **NIPT-artefacten:** de lijst terugkerende artefacten per assay (hoofdstuk 8 en `docs/data-import.md`). Op aanvraag vult de API de lijst aan met de varianten die in meerdere cfDNA-samples van dezelfde assay terugkomen (standaard minstens vijf). Of hij leest de lijst van terugkerende artefacten van de NIPT-M-pipeline in (`POST /api/admin/nipt/artifacts/import`). Beide nemen geen frequente varianten op, en geen varianten met een ClinVar-melding pathogeen, waarschijnlijk pathogeen of conflicterend. Een allel dat nog geen familie draagt, is bij het opnemen niet te controleren. Daarom controleert elke familie-analyse de gelijste allelen opnieuw met haar eigen annotatie, en houdt ze een frequent of ClinVar-pathogeen allel over, gemarkeerd `artifact_list_protected`. Een import is één klinische auditgebeurtenis, `nipt_artifacts_imported`.
- De synchronisatie van de genreferentie (hoofdstuk 13).
- Het beheer van HPO en Monarch (hoofdstuk 12).

## In-app documentatie

De documentatie voor gebruikers zit in de app zelf, onder `/docs`.

- **Referentiedocumenten:** Markdown-bestanden in `frontend/src/content/docs/`, geregistreerd in `frontend/src/pages/docs/referenceDocs.ts` en getoond op `/docs/reference/<slug>`.
- **De gebruikersgids:** één Markdown-bestand per sectie in `frontend/src/content/docs/user-guide/`, met de lijst van secties in `frontend/src/pages/docs/userGuideSections.ts`.

De gebruikersgids hoort bij de informatie voor veilig gebruik (`docs/regulatory/TF-15-instructions-for-use.md`). Een test legt de tekst van elke sectie vast (`UserGuideContent.test.tsx`), zodat elke tekstwijziging bewust gebeurt.

## Releases

De pagina *New features* (`NewFeaturesPage.tsx`) toont de versiegeschiedenis uit de GitHub-releases (`GET /api/product/releases`, achter een login). De backend vraagt de GitHub-API op, met een token als dat is ingesteld. Lukt dat niet, dan geeft hij een nette foutmelding en verwijst de pagina rechtstreeks naar GitHub. Het resultaat wordt een tijd bewaard, zodat GitHub niet bij elke opening wordt bevraagd. Deze catalogus is informatie voor de gebruiker; de softwareversie die een rapport bindt, komt uit de build (hoofdstuk 11).

**Waar in de code:** `backend/app/routers/product.py` en `backend/app/services/github_releases_service.py`.

## UI-telemetrie

Naast de HTTP-auditlog legt CoGA **betekenisvolle interacties** in de interface vast die de backend anders niet ziet: klikken op knoppen, links en tabs, en navigatie binnen de app.

- **In de browser** vangt `frontend/src/lib/telemetry.ts` die gebeurtenissen op. Het stuurt ze om de vijf seconden in groepjes naar `POST /api/ui-events`, en bij het verbergen of verlaten van de pagina met een `keepalive`-verzoek. Telemetrie verstoort de app nooit. Een groep blijft in de wachtrij tot de API haar aanneemt. Komt ze door een netwerk- of serverfout niet aan, dan gaat ze de volgende keer opnieuw mee; sluit de pagina terwijl ze onderweg is, dan met het `keepalive`-verzoek (vroeger ging ze dan verloren).
- **Het token.** Het verzoek bij het verlaten kan niet wachten op een nieuw token en stuurt het token van de sessie. Daarom vernieuwt de timer het Microsoft-token van de sessie al vijf minuten voor het verloopt, ook als er niets te versturen is, en opnieuw wanneer de pagina verborgen of weer getoond wordt. Vroeger stuurde een pagina die na een uur zonder verzoeken sloot, haar laatste gebeurtenissen met een verlopen token, en weigerde de API ze.
- **Wachten in de browser.** De browser stuurt gebeurtenissen niet om ze te laten weigeren, en gooit ze ook niet weg, als het token toch verlopen is (het toestel sliep, of MSAL heeft de gebruiker nodig), als de API een groep weigert omdat de sessie niet meer geldt (`401`), of als de `401` op een ander verzoek de sessie beëindigde (vroeger gingen die gebeurtenissen verloren: er was geen token meer om ze mee te sturen). Ze wachten dan in de `localStorage` van de browser, onder het account waaronder ze gemaakt werden (het object-ID van het Microsoft-token), hoogstens 200 per account. Ze gaan mee met het eerste verzoek van dat account dat weer aankomt (na een vernieuwd token of een nieuwe aanmelding), nooit onder een ander account. Ook de gebeurtenissen van het `keepalive`-verzoek wachten zo, al vóór het vertrekt. Ze verdwijnen pas uit de browser als de API ze aanneemt (een 2xx-antwoord). Mislukt het verzoek, weigert de API het, of sluit de pagina voor het antwoord er is, dan gaan ze opnieuw mee met het volgende verzoek van dat account dat aankomt (vroeger las niemand dat antwoord, en gingen de gebeurtenissen van een mislukt verzoek verloren).
- **Meerdere tabbladen.** Elke bewaarde gebeurtenis staat onder een eigen sleutel. Zo overschrijven twee tabbladen die tegelijk schrijven elkaars gebeurtenissen niet (vroeger kon dat, met één gedeelde waarde). Hoogstens sturen beide tabbladen dezelfde gebeurtenis; de server bewaart ze één keer.
- **Zonder sessie** (op de inlogpagina, of nadat de sessie eindigde) wordt een gebeurtenis niet vastgelegd. De server legt elke gebeurtenis van een groep vast als handeling van de gebruiker van het token dat haar stuurt, en geen account maakte ze. Vroeger ging ze mee met de sessie die de volgende verzending vond, als die er was, als handeling van die gebruiker.
- **Op de server** filtert `ingest_ui_events`. Alleen een vaste lijst soorten wordt aanvaard (`click`, `navigation`, `submit`, `view`, `query`). In paden worden id's gemaskeerd en querystrings tot hun sleutels teruggebracht, zodat klinische identificaties niet worden opgeslagen. Gevoelige velden (wachtwoord, token, …) worden gemaskeerd, geneste structuren tot hun type herleid. De actor komt uit het token. Elke gebeurtenis draagt het id dat de browser haar gaf (`event_id`, een UUID). De server bewaart ze onder dat id, en de INSERT doet niets voor een id dat de tabel al heeft. Zo komt een groep die de browser opnieuw stuurt, er één keer in. Een gebeurtenis zonder UUID van de browser krijgt er een van de server.
- **Het tijdstip** `occurred_at` is de klok van de browser. De server neemt het alleen over als het tussen een dag vóór en vijf minuten na de ontvangst ligt (zonder tijdzone geldt UTC). Anders slaat hij het ontvangsttijdstip op, en zet hij de waarde van de client in `detail._client_occurred_at`, een sleutel die de client zelf niet kan zetten. Vroeger liet een tijdstip vóór het jaar 1 in UTC (`0001-01-01T00:00:00+05:00`) het wegschrijven mislukken, in de async-modus samen met de rest van de batch, ook de gebeurtenissen van andere gebruikers.
- **Opslag:** de gebeurtenissen gaan naar de tabel `ui_events`, via dezelfde verliesvrije wachtrij als de HTTP-auditlog (hoofdstuk 7). Een gebeurtenis die de databank niet aanneemt, wordt met inhoud gelogd en geteld. De batch krijgt toch 202, zodat de browser niets opnieuw stuurt dat al bewaard is.

**Waar in de code:** `frontend/src/lib/telemetry.ts`, `backend/app/routers/ui_events.py` en `backend/app/services/ui_event_pg.py`.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/admin.py` | De volledige beheer-API: voor een beheerder, de varianttags, presetfilters en QC-grenzen ook voor een superuser |
| `backend/app/routers/cnvs.py` · `backend/app/services/clinical_cnv_kb_jobs.py` | De CNV-catalogus en de herbouw van de kennisbank |
| `backend/app/routers/panels.py` · `backend/app/services/panel_metadata_service.py` | Genpanels, versies, Mendeliome |
| `backend/app/services/panelapp_service.py` | PanelApp |
| `backend/app/routers/ancestry.py` · `backend/app/services/ancestry_service.py` | De verkennende ancestry-PCA |
| `backend/app/services/admin_service.py` | Data-inventaris, verwijderen, bronbestanden verifiëren |
| `backend/app/services/qc_threshold_service.py` | Grenzen voor sequencing-QC en hun geschiedenis |
| `backend/app/services/family_structure_service.py` · `family_member_management_service.py` | Familiestructuur en ledenbeheer |
| `backend/app/routers/ui_events.py` · `backend/app/services/ui_event_pg.py` | UI-telemetrie |
| `frontend/src/pages/admin/` | De beheerschermen |
| `frontend/src/pages/docs/` · `frontend/src/content/docs/` | De in-app documentatie |
