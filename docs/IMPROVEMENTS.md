# MailBrain: veilige uitvoering voor een wekelijkse job

## Opdracht voor Sol of Terra

Implementeer de drie stappen hieronder in volgorde, met tests per stap. Behoud Python, SQLite, de bestaande Gmail-client en de pure rule engine. Dit is een betrouwbaarheidsverbetering, geen rewrite.

Basis van dit document: `fd3641f` op `master`, gecontroleerd op 11 september 2026. De laatste review vond 144 geslaagde tests en groene Ruff/mypy-checks. Controleer bij aanvang opnieuw HEAD en de worktree; andere agents kunnen intussen wijzigingen hebben gemaakt.

Dit document is een implementatieopdracht, geen toestemming om de echte mailbox te wijzigen, secrets te lezen, de productiedatabase te migreren of commits te maken. Gebruik tijdelijke databases en fake Gmail-clients. Vraag afzonderlijke toestemming voor live uitvoering en publicatie.

## Doel

MailBrain mag alleen een begrensd, gecontroleerd plan uitvoeren op verse data. Als uitvoering onderbroken wordt, blijven intentie en voortgang bewaard. Rollback mag niet blind later gewijzigde Gmail-state overschrijven.

De drie opleveringen:

1. Centrale safety-controle en een process lock.
2. Expliciete, verse en volledige scanscope.
3. Duurzame apply/rollback met conservatieve recovery.

Pas daarna komt een `weekly`-commando of scheduler. Een groene CI-run is geen toestemming om Gmail automatisch te muteren.

## Bestaande basis

| Bestand | Huidig gedrag | Benodigde wijziging |
| --- | --- | --- |
| `src/mailbrain/cli.py` | Compositie, preview en bevestiging | Gedeelde gecontroleerde uitvoeringsroute |
| `src/mailbrain/scan.py` | Upsert; laadt vervolgens alle historische cache | Scanstatus en expliciete selectie |
| `src/mailbrain/apply.py` | Alle Gmail-writes voor een enkele commit | Intentie vooraf, checkpoints per batch |
| `src/mailbrain/rollback.py` | Blind omgekeerde delta, geen cache-update | Live controle, audit en checkpoints |
| `src/mailbrain/storage/models.py` | Run/Mutation zonder lifecycle | Scan- en uitvoeringsstatus |
| `src/mailbrain/storage/db.py` | Alleen `create_all()` | Versiebeheer en geteste migratie |
| `src/mailbrain/gmail/client.py` | Reads en writes achter adapter | Revalidatie, read-retries en labelreconciliatie |
| `src/mailbrain/config.py` | Runtime gebruikt settings nog niet | Strikte safety-configuratie daadwerkelijk inlezen |
| `src/mailbrain/report.py` | Plan en aantallen | Blokkades, werkelijke voortgang en recovery-status |
| `tests/test_rules_scenarios.py` | Scenario's tegen echte rules | Behouden en safety-regressies toevoegen |

De recente rules-fixes niet opnieuw ontwerpen. Dubbele labels zijn niet per definitie een conflict: iO-lease heeft bewust zowel een werk- als leaselabel. Rabobank-subdomeinen worden al door parent-domainmatching ondersteund.

## Werkafspraken

- Lees de actuele bestanden voordat je edits maakt; behandel oude plannen als achtergrond, niet als actuele implementatie.
- Behoud bestaande wijzigingen van gebruiker en andere agents. Geen reset, checkout of amend.
- Schrijf eerst een regressietest voor het betreffende failure scenario.
- Gebruik bestaande functies waar passend; geen generiek workflow-framework, queue of repositorylaag toevoegen.
- Geen nieuwe inline codecomments; leg niet-obvious ontwerpkeuzes vast in een ADR onder `docs/adr/`.
- Voeg geen fallback naar onveilige historische functionaliteit toe om oude tests groen te houden.
- Houd classificatie en safety-beslissingen deterministisch en zonder netwerk-I/O.
- Gebruik een injecteerbare klok, tijdelijke SQLite-bestanden en een stateful fake Gmail-client voor tests.
- Geen echte mailinhoud of credentials als testfixture opnemen.

## Stap 1: safety en locking

### Gedrag

Voeg `src/mailbrain/safety.py` toe voor pure planvalidatie en `src/mailbrain/locking.py` voor een OS-bestandslock. Voeg alleen helpers toe die daadwerkelijk door meerdere paden worden gebruikt.

Safety wordt na het combineren van alle rules beoordeeld, voor labelcreatie of andere Gmail-writes. Beoordeel zowel de volledige classificatie als de werkelijke delta: een reeds aanwezig label mag een onveilige combinatie niet verbergen.

Bij een blokkade stopt het hele plan. Niet stil een subset uitvoeren of verboden acties wegfilteren. Toon redenen, betrokken bericht-ID's, matched rule-ID's en aantallen. Onderwerpen zijn niet nodig in de standaard foutmelding.

`--yes` slaat uitsluitend de bevestigingsvraag over. Het omzeilt geen lock, safety-beleid, scanscope of recovery-controle. Voeg in deze fase geen algemene `--force` toe.

### Limieten

Voeg een strikt gevalideerde safety-sectie toe aan de settings en sluit deze via een expliciet settingspad aan op de CLI. `load_settings()` bestaat al, maar wordt nu niet gebruikt.

| Instelling | Betekenis |
| --- | --- |
| `max_mutations` | Maximum unieke berichten met een daadwerkelijke wijziging |
| `max_archives` | Maximum unieke berichten waarvan INBOX wordt verwijderd |
| `max_archive_fraction` | Archives gedeeld door het aantal INBOX-berichten in de geselecteerde scan |
| `max_scan_age_minutes` | Maximum leeftijd vanaf de start van de scan; gehandhaafd vanaf stap 2 |

Absolute limieten zijn niet-negatieve integers; een fraction ligt tussen 0 en 1; scanleeftijd is positief. Gelijkheid aan een limiet is toegestaan. Een no-op met nul berichten is geldig en deelt niet door nul.

Geen mailbox-specifieke productielimieten gokken. Zonder expliciet geconfigureerde limieten zijn previews toegestaan maar writes geblokkeerd. Laat de gebruiker waarden kiezen op basis van dry-runs. Tests gebruiken eigen expliciete waarden.

### Beschermde berichten

Ondersteun beschermde labelprefixen en onderwerpcriteria voor loonstroken, transacties, security, hypotheek- en operationele reismail. Neem huidige labels en alle voorgestelde labels mee. Prefixmatching respecteert de slash-grens: `A` dekt `A` en `A/B`, niet `AB`.

Voor een beschermd bericht blokkeer je een voorgenomen archive, mark-read of toevoeging van `to-be-removed`. Een label-only classificatie blijft toegestaan. Dit is een centrale controle en mag niet door een andere matchende promo-rule worden opgeheven.

Begin conservatief en test ook ambigue onderwerpen: een echte factuur met het woord `korting` mag niet automatisch onbeschermd worden. Regelmatige false positives worden gerapporteerd; geen impliciete whitelist op basis van alleen Gmail's Promotions-categorie.

Deze heuristieken herkennen niet iedere belangrijke mail. Documenteer dat expliciet; volumelimieten zijn een aanvullende bescherming, geen bewijs van correcte classificatie.

### Lock

Gebruik voor de huidige macOS/Linux-doelomgeving een niet-blokkerende OS-lock, bijvoorbeeld `fcntl.flock`, op een vaste locatie onder `MAILBRAIN_HOME`. Geen stale PID-file-protocol bouwen. Verwijder het lockbestand niet bij vrijgave; sluit de file descriptor.

Houd de lock vast vanaf het lezen van mutatie-relevante state tot de laatste statuscommit. De OS-lock wordt ook bij een crash vrijgegeven. Een tweede proces stopt direct met een duidelijke nonzero exitcode.

Dek `scan`, `apply`, `rollback` en migraties. Read-only previews gebruiken dezelfde lock als ze mutable cache lezen. Voorkom dubbel locken via geneste servicecalls.

`scripts/reconcile.py` en `scripts/unlabel.py` mogen geen achterdeur vormen. Voeg dezelfde lock toe en blokkeer hun write-modus totdat zij in stap 3 de veilige executor gebruiken. Hun read-only analyse blijft beschikbaar.

### Acceptatietests

- [ ] Een limietoverschrijding resulteert in nul labelcreaties en nul `batch_modify`-calls.
- [ ] Exact op de grens is toegestaan; nul/no-op veroorzaakt geen divide-by-zero.
- [ ] Beschermde mail plus een brede archive-rule blokkeert het plan, ook bij `--yes`.
- [ ] Safety-configuratie ontbreekt of is ongeldig: preview toont dit, writes falen gesloten.
- [ ] Twee echte processen met hetzelfde home kunnen niet tegelijk state muteren.
- [ ] Na een geforceerd gestopt testproces kan een nieuw proces de lock verkrijgen.
- [ ] Bestaande rule-scenario's blijven groen; safety wordt aanvullend getest.

## Stap 2: actuele scanscope en migratie

### Kleinste bruikbare model

Voeg een `ScanRun`-tabel toe met ID, query, start/eindtijd, status en aantallen listed/fetched/failed. Gebruik `running`, `succeeded`, `failed` en `invalidated` als expliciete statussen.

Voeg `last_scan_id` toe aan `Message`. Zet deze uitsluitend bij succesvol ophalen en opslaan van metadata. Oude berichten blijven behouden voor audit, maar behoren niet vanzelf tot de nieuwe scan.

Dit eenvoudige model ondersteunt alleen toepassing van de nieuwste scan, niet willekeurige historische snapshots. Een scan die later faalt mag dus nooit aanleiding zijn om automatisch terug te vallen op een oudere succesvolle scan.

### Migratie hoort al in deze stap

Schemawijzigingen beginnen hier, niet pas bij apply. Gebruik Alembic met een baseline van het bestaande schema en een aparte upgrade-revisie. Ondersteun een vers databasebestand en de bestaande onversioned database.

Voor een bestaande database: verifieer eerst dat het schema de bekende baseline is, maak een consistente SQLite-backup, registreer dan de baseline en voer de upgrade uit. Een onbekend schema wordt geweigerd, niet blind gestampt. Backup/migratie onder de lock, backupbestanden met restrictieve rechten.

`init` initialiseert nieuwe databases via dezelfde migraties. Voeg een expliciet `db upgrade`-commando toe; normale writes weigeren een verouderd schema met een herstelinstructie. Geen automatische productiemigratie tijdens deze opdracht uitvoeren.

Bestaande berichten krijgen geen fictieve scanherkomst: `last_scan_id` blijft leeg totdat opnieuw gescand is.

### Scancontract

1. Bewaar de nieuwe `running` scan voordat Gmail wordt gelezen.
2. Haal de volledige ID-lijst op voor de query, dedupliceer IDs en refresh labels.
3. Haal ieder bericht opnieuw op; commit voortgang zoals nu in batches.
4. Iedere onopgeloste fetch-, parse- of listingfout maakt de scan `failed` en geeft een nonzero exitcode.
5. Alleen volledige scans worden `succeeded`. Een succesvolle lege zoekopdracht is geldig.

Voeg bounded retries toe voor tijdelijke read-fouten. Onderscheid rate-limit-403 van permanente autorisatiefouten; test 429, tijdelijke 5xx en transportfouten. Een na listing verdwenen bericht telt in deze conservatieve versie als onvolledigheid en vereist een nieuwe scan.

`--resume` mag niet langer alle historisch gecachte IDs overslaan. Eenvoudigste veilige overgang: behoud de CLI-optie, maar laat haar een volledige verse scan uitvoeren met een duidelijke melding. Efficiient hervatten van specifieke snapshots is buiten scope.

### Applycontract

- `classify` en `apply --dry-run` tonen scan-ID, query, leeftijd en geschiktheid voor writes.
- `apply --scan-id <id>` is verplicht voor writes. Het ID moet de nieuwste, volledige, niet-invalidated scan zijn en binnen de ingestelde leeftijd vallen.
- Selecteer alleen berichten met dat `last_scan_id`; geen fallback naar de hele cache.
- Lees voor uitvoering de geplande kandidaten live opnieuw. Bij veranderde metadata/labels of verdwenen berichten: stop en vraag een verse scan, niet stil herplannen onder oude bevestiging.
- Er is geen atomische Gmail-snapshot of compare-and-swap. Een race na revalidatie blijft mogelijk; presenteer de controle niet als volledige concurrency-garantie.
- Voordat mailboxwrites kunnen starten, invalidateer de scan duurzaam. Nieuwe apply vereist daarna een nieuwe scan, ook na een crash. Recovery van de bestaande run wordt apart geregeld in stap 3.

### Acceptatietests

- [ ] Scan A bevat m1/m2; scan B alleen m2: apply B kan nooit m1 wijzigen.
- [ ] Scan B faalt: apply gebruikt niet alsnog A.
- [ ] Een bestaand maar niet opnieuw opgehaald bericht krijgt geen membership van B.
- [ ] Verouderde, running, mislukte en invalidated scans worden geweigerd.
- [ ] Een lange scan kan de maximale leeftijd overschrijden; starttijd bepaalt versheid.
- [ ] Tijdelijke reads worden geretryd; permanente fouten niet eindeloos.
- [ ] `--resume` levert verse labels op in plaats van historische cache over te slaan.
- [ ] Live state wijkt af: nul Gmail-writes en een duidelijke blokkade.
- [ ] Migratie van een legacy fixture behoudt alle berichten en auditrecords; herhalen is veilig.
- [ ] Een onbekend schema of mislukte backup verhindert de upgrade.

## Stap 3: herstelbare apply en rollback

### Duurzame intentie

Breid `Run` en `Mutation` uit via een volgende migratie. Bewaar regels- en safety-configuratiehash, scan-ID, timestamps, fouten en echte voortgang. Bewaar matched rule-ID's voor uitleg.

Runs krijgen type `apply` of `rollback`, eventueel `source_run_id`, en status `prepared`, `running`, `succeeded`, `partial`, `failed`, `needs_review`, `legacy` of `abandoned`. Mutations krijgen `pending`, `in_flight`, `applied`, `failed`, `uncertain`, `conflict`, `legacy` of `abandoned`, plus een batch-ID. Rollbackmutations verwijzen naar hun oorspronkelijke mutation.

Persist naast leesbare labelnamen de exacte Gmail-ID's van voor-state en add/remove-delta. Gebruik geen huidige naamlookup om later blind een oude mutation terug te draaien. Een verwijderde en opnieuw aangemaakte labelnaam is niet dezelfde Gmail-labelidentiteit.

Legacy records blijven herkenbaar als `legacy`, zonder achteraf zekerheid toe te kennen. Maak nieuwe statussen authoritative; voorkom twee onafhankelijke waarheden naast het oude `applied`-veld. Verwijder oude velden pas wanneer scripts en migraties zijn aangepast. De omgekeerde betekenis van bestaande `archived_*`/`read_*`-velden mag historische audit niet ongemerkt veranderen.

### Uitvoeringsvolgorde

1. Lock, scanscope en safety controleren; kandidaten live revalideren.
2. Run, alle bedoelde mutations en scaninvalidatie opslaan en committen, voor de eerste Gmail-write.
3. Benodigde labelcreaties als intentie registreren. Refresh live labels; hergebruik bestaande IDs. Bij een create-timeout of 409 eerst opnieuw listen, niet blind opnieuw maken. Bewaar gevonden mappings duurzaam. Een onzekere labelcreatie blokkeert berichtwrites.
4. Sla resolved delta's en stabiele batchindeling op. Maximaal 1000 IDs per Gmail-batch.
5. Markeer de betreffende mutations `in_flight` en commit voor de API-call.
6. Na bevestigd succes: markeer `applied`, refresh/update de cache en commit. Behoud onbekende externe labels; vervang de cache niet vanuit een oude snapshot.
7. Bij fout: stop volgende batches. Bewaar fout/status indien mogelijk; als de database onbereikbaar is, blijft de eerder gecommitte `in_flight`-intentie bestaan.
8. Rond de run alleen af als `succeeded` wanneer alle bedoelde writes bevestigd en lokaal verwerkt zijn.

Gmail en SQLite hebben geen gezamenlijke transactie. Een crash na Gmail-succes maar voor de statuscommit is onvermijdelijk ambigu. Claim daarom geen exactly-once uitvoering.

De huidige write-backoff mag onzekere transportfouten niet ongemerkt opnieuw versturen. Maak het retrycontract expliciet: alleen veilig retrybare reacties automatisch herhalen; onzekere writes naar recovery.

### Recovery zonder gevaarlijke aannames

Voeg `runs` en `runs inspect <id>` toe voor read-only inspectie, en `runs reconcile <id>` voor live vergelijking plus lokale status/cache-reconciliatie. Die laatste mag geen Gmail-writes doen.

Een achtergelaten `in_flight` mutation wordt behandeld als `uncertain`. Live state gelijk aan het gewenste resultaat bewijst niet dat MailBrain de wijziging heeft veroorzaakt; live state gelijk aan voor-state bewijst niet dat er niets is gebeurd en later teruggedraaid.

Voor deze eerste versie: uncertain/conflict-resultaten blokkeren automatische herhaling en rollback van die mutations. Toon een concrete handmatige herstelroute. Geen generieke auto-resume bouwen. Blokkeer nieuwe writes zolang onopgeloste intenties bestaan; bied een expliciete, bevestigde lokale afsluiting als `abandoned` met reden voor een door de operator afgehandeld geval. Dat wist geen audit en maakt de oorspronkelijke scan niet opnieuw geldig.

Onopgeloste intenties zijn `pending`, `in_flight`, `uncertain` en `conflict` van nieuwe runs. `failed` betekent aantoonbaar niet uitgevoerd en is terminal; gebruik die status niet voor een timeout. `applied`, `failed`, `legacy` en `abandoned` blokkeren op zichzelf geen nieuwe run. Tijdens de actieve uitvoering mogen de eigen pending/in-flight records uiteraard bestaan; de blokkade betreft een volgende onafhankelijke writer.

Een lokale afsluiting vereist dat alle in-flight records eerst uncertain zijn gemaakt en met live state vergeleken zijn. De operator sluit uitsluitend de resterende onopgeloste mutations af met reden en tijdstip; bevestigde applied mutations blijven applied. Een run met een combinatie blijft partial met de afsluitreden, een geheel niet meer uit te voeren run wordt abandoned. Pas na deze afhandeling mogen bevestigde batches uit een partial run worden teruggedraaid. Legacy records blijven uitsluitend inspecteerbaar en worden niet door afsluiting alsnog rollbackbaar.

### Rollback

Hergebruik de checkpointed executor met een inverse delta en een eigen run. Voeg `--dry-run` en bevestiging toe; `--yes` mag alleen de prompt overslaan.

- Alleen bevestigde nieuwe `applied` mutations zijn automatisch eligible; legacy en uncertain records niet.
- Een onbekende run-ID geeft een fout, geen succesvol nulresultaat.
- Check actuele Gmail-state en latere MailBrain-mutaties op dezelfde delta. Bij afwijking of overlap: conflict, geen write voor het hele rollbackplan.
- Pas alleen de inverse van de originele delta toe. Behoud externe labels buiten die delta.
- Voor cacheversheid, budgets en locking geldt hetzelfde principe als bij apply. Rollback heeft geen verse classificatiescan nodig, wel verse live-statecontrole; invalidateer bestaande scanstate voor writes.
- Bij rollback geldt `max_mutations` voor alle inverse berichtwijzigingen. Alleen een inverse delta die INBOX verwijdert telt als archive; daarvoor gelden `max_archives` en `max_archive_fraction` tegen een verse, volledig opgehaalde live INBOX-ID-set. Ontbreekt die set of is de noemer nul bij een archive, blokkeer. Zonder archives is de fraction nul. Gebruik nooit de oude scan als noemer.
- Een bewuste terugdraaiing van een bevestigde actie is geen nieuwe classificatiebeslissing: hergebruik volumecontroles, maar blokkeer bijvoorbeeld herstel van UNREAD niet als nieuwe mark-read actie.
- Na iedere succesvolle batch: cache en bronverwijzing atomair lokaal bijwerken, zodat een herhaalde rollback een no-op is.
- Een partial rollback gebruikt dezelfde uncertain-afhandeling. Geen automatische rollback-van-rollback in deze versie.

Een gebruiker kan een label verwijderen en opnieuw toevoegen zonder dat de uiteindelijke set verandert. Dit is niet uit een snapshot te reconstrueren. Beschrijf deze beperking in CLI-help en ADR; beloof niet dat handmatige wijzigingen altijd herkenbaar zijn.

Reparatiescripts mogen alleen terug naar write-modus wanneer zij deze executor, audit, live checks, locking en relevante safety-controles gebruiken. Anders blijven ze read-only.

### Acceptatietests

- [ ] Voor de eerste Gmail-write ziet een aparte DB-sessie de volledige intentie.
- [ ] Batch 1 slaagt, batch 2 faalt, batch 3 wordt niet aangeroepen; audit van batch 1 blijft bestaan.
- [ ] Gmail muteert en geeft daarna een timeout: uncertain, geen automatische tweede write.
- [ ] Gmail slaagt maar statuscommit faalt: nieuwe sessie ziet de bestaande in-flight intentie.
- [ ] Proceskill na precommit: lock komt vrij en intentie blijft aantoonbaar aanwezig.
- [ ] Labelcreatie slaagt maar response ontbreekt: relist hergebruikt label; geen duplicaat.
- [ ] 2001 gelijksoortige mutations worden als 1000/1000/1 verwerkt.
- [ ] Apply -> rollback -> cache komt overeen met live fake Gmail; tweede rollback doet nul writes.
- [ ] Handmatige wijziging op geraakte labels of een latere overlappende run blokkeert rollback.
- [ ] Extern toegevoegd ongerelateerd label blijft behouden.
- [ ] Legacy en uncertain audit worden niet als automatisch rollbackbaar voorgesteld.
- [ ] Pending/uncertain/conflict blokkeren een volgende writer tot expliciete afhandeling; abandoned status geeft geen automatisch recht tot replay.
- [ ] Bevestigde batches van een partial run blijven rollbackbaar na afhandeling van de overige intenties.
- [ ] Rollback-archivebudget gebruikt verse live INBOX-aantallen; geen scan of nulnoemer kan de limiet omzeilen.
- [ ] Preview, geweigerde bevestiging en alle preflight-blokkades doen nul Gmail-writes.
- [ ] Report onderscheidt intended, confirmed, failed, uncertain en conflict; geen succes op basis van alleen `len(plans)`.

## Verificatie per oplevering

Voer eerst de nieuwe gerichte tests uit en daarna de bestaande CI-checks:

```bash
uv sync --locked --all-extras --dev
uv run ruff check .
uv run mypy
uv run pytest
uv run pytest --cov=mailbrain --cov-branch --cov-report=term-missing
uv build
```

Gebruik aparte tijdelijke homes voor alle CLI-integratietests. Test failures met een stateful fake, niet alleen losse MagicMock-call-assertions. Test migraties ook vanuit een fixture van het oude schema, niet alleen nieuwe databases.

Nieuwe tests staan bij voorkeur in `tests/test_safety.py`, `tests/test_locking.py`, `tests/storage/test_migrations.py`, de bestaande scan/apply/rollback-tests en `tests/test_pipeline_integration.py`.

Pas README, CLI-help en CHANGELOG aan bij veranderd gedrag. Houd de bestaande CI offline en zonder Gmail-secrets. Los nieuwe warnings op; rapporteer eventuele bestaande warnings expliciet.

Elke oplevering rapporteert: gewijzigde bestanden, geteste failure-scenario's, commands en resultaten, gedrag dat bewust verandert en resterende beperkingen. Geen commits/pushes zonder verzoek.

## Buiten scope en vervolg

Geen LLM, Kotlin-port, microservices, externe queue, cloud database, Notion-digest of Gmail History API in deze opdracht. Ook niet alle persoonlijke labeltaxonomie opnieuw ontwerpen.

Na deze drie stappen blijven voor een echte wekelijkse job nog nodig: noninteractive OAuth die bij reauth direct faalt, veilige atomische tokenopslag, schedulerconfiguratie, foutnotificatie en een dun `weekly`-commando. Deze fase alleen is dus nog geen volledige unattended go-live.

CI-hardening en packaging zijn aparte kleine verbeteringen: required checks op master, action-pinning, timeout en een installed-wheel-smoketest. Lever generieke voorbeeldrules mee of initialiseer expliciete gebruikersconfiguratie; publiceer niet ongemerkt persoonlijke rules als package-default.

## Eindcriterium

- [ ] Onveilige plannen worden voor Gmail-writes geblokkeerd.
- [ ] Nieuwe apply gebruikt uitsluitend de nieuwste verse en volledige scan.
- [ ] Elke mogelijk verstuurde write heeft vooraf duurzame intentie.
- [ ] Bevestigd succes en onzekere uitkomst blijven onderscheiden na restart.
- [ ] Rollback is geaudit, cache-consistent en niet blind herhaalbaar.
- [ ] Geen writer of reparatiescript omzeilt het nieuwe uitvoeringspad.
- [ ] Bestaande database en audit hebben een geteste, niet-destructieve migratie.
- [ ] Alle checks zijn groen en de beperkingen zijn eerlijk gedocumenteerd.
