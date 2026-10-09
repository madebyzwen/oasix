# Phase A.2 – Datenbankdesign und Architekturplanung

Status: **Design abgeschlossen – A.2.1 und A.2.2 implementiert, A.2.3 offen**

## 1. Ziel und Abgrenzung

Phase A.2 entwirft und implementiert schrittweise eine lokale, transaktionale
und migrationsfähige Persistenz für die OASIX-Control-Plane. Der Stack besteht
aus Python 3.12, SQLite im WAL-Modus, SQLAlchemy 2, Alembic und pytest.

Dieses Dokument ist der finalisierte Entwurf nach abgeschlossenem
Architektur-Review. A.2.1 setzt Runtime-Schema v2, die sichere Pfadprüfung und
das SQLAlchemy-/SQLite-Fundament um. A.2.2 ergänzt exakt sechs Fachtabellen und
eine Alembic-Initialmigration. Queue-Dispatch, Retry, Recovery, Lease-Ablauf
und Power-Steuerung werden hier nur durch Persistenzverträge vorbereitet; ihre
Geschäftslogik und vollständigen Zustandsautomaten folgen in späteren Phasen
nach separater Freigabe.

| Etappe | Status | Umfang |
| --- | --- | --- |
| A.2.1 | Implementiert; Review ausstehend | Runtime-Version 2, Datenbankpfadprüfung, Engine, Pflicht-Pragmas, begrenzter Pool, Session- und Transaktionslebenszyklus |
| A.2.2 | Implementiert; Review ausstehend | Sechs SQLAlchemy-Kerntabellen, benannte Constraints und Indizes, Initialrevision `0001_a2_2` sowie Integritätsprüfungen |
| A.2.3 | Offen | Repository-Grenzen, zentrale Nutzdatenvalidierung und verbleibende A.2-Integritätsnachweise |

## 2. Verbindliche Anforderungen

Maßgeblich ist das
[Technical Requirement v3.4](../../OASIX_Technical_Requirement_Reviewed_v3.4.docx).

| Bereich | Verbindliche Invariante für das Design |
| --- | --- |
| ARC-01, DEP-01 | Persistenz und Control-State liegen dauerhaft auf der Control Plane. |
| JOB-01 | `job_id` bleibt stabil; Parent-Bezug und optionale Idempotenz werden ohne späteren Umbau der Tabelle `jobs` ermöglicht. |
| JOB-02 | Jeder konkrete Ausführungsversuch erhält eine eigene `attempt_id`; ein Retry überschreibt keinen Attempt. |
| JOB-03 | Queue, Job-Status und Attempts überleben einen Prozessneustart. |
| JOB-04 | Schemaänderungen sind versioniert und migrationsfähig. |
| JOB-05, REC-01, REC-02 | Aktive Jobs und Attempts werden nach Neustart abgeglichen; unbestätigte Attempts bleiben nicht stillschweigend `RUNNING`. |
| REC-03, REC-04 | Job-/Wake-Retries sind begrenzt; Fehlerklassen bleiben stabil auswertbar. |
| WRK-01 bis WRK-05 | Worker startet logisch als `UNKNOWN`; Dispatch setzt reale Worker- und Service-Readiness voraus. |
| LSE-01 bis LSE-04 | Jede aktive Nutzung besitzt eine Lease mit TTL/Heartbeat; die Lease Registry ist die einzige Autorität für aktive Nutzung. |
| PWR-01 | Sleep setzt keine aktive Lease, keine ausführbare/fällige Arbeit, abgelaufene Idle-Zeit und einen zulässigen Worker-State voraus. |
| Force Sleep | Neuer Dispatch wird pausiert, Grace Period und Eingriff werden festgehalten, nicht sauber beendete Attempts werden `INTERRUPTED`. |
| WAITING/EXTERNAL | Eine Lease darf erst nach bestätigter Resumability und persistierter opaker `continuation_ref` freigegeben werden. |
| OBS-01 bis OBS-04 | IDs und verfügbare Attempt-Telemetrie sind korrelierbar; fehlende Werte bleiben `NULL`; Erweiterungen ersetzen keine Kernfelder. |
| AC-04 bis AC-07 | Lease-Ablauf, Restart-Recovery, Force-Sleep-Unterbrechung und abrufbare Telemetrie müssen später testbar sein. |

Das Requirement legt die Job- und Worker-Zustände fest, aber keinen vollständigen
Attempt-Zustandsautomaten und keine exakten Retention-Zeiten. Dieses Dokument
trifft nur die für das Schema notwendigen Entscheidungen und kennzeichnet
verbleibende Geschäftsentscheidungen ausdrücklich als offen.

## 3. Architektur und Schnittstellen

### 3.1 Komponenten und Verantwortungsgrenzen

Die synchrone SQLAlchemy-2-Persistenzschicht innerhalb der Control Plane ist in
A.2.1 bis einschließlich Engine, Session Factory und generischem
Transaktionskontext umgesetzt. Fachliche Repositories und Use Cases sind noch
geplant:

```text
API / Dispatcher / Recovery / Power-Steuerung
                    |
             Use-Case-Service
       (eine explizite Transaktion)
                    |
         Repositories / Unit of Work
                    |
       SQLAlchemy Engine + sessionmaker
                    |
           lokale SQLite-WAL-Datei
```

- Eine Engine wird pro Control-Plane-Prozess erzeugt und beim Shutdown sauber
  geschlossen.
- Sessions sind kurzlebig und an einen Request beziehungsweise Use Case
  gebunden; es gibt keine globale Session.
- Repositories kapseln SQL, rufen aber nie selbst `commit()` auf.
- Use-Case-Services besitzen die Transaktionsgrenze und committen oder rollen
  den gesamten fachlichen Zustandswechsel zurück.
- Netzwerk-, Readiness-, Wake-/Sleep- und Agent-Aufrufe finden nie innerhalb
  einer offenen DB-Transaktion statt.
- Runtime-Konfiguration bleibt die Quelle für Worker-Verbindungsdaten, Services
  und Policies. Die Datenbank speichert keine Hostnamen, Zugangsdaten,
  Secret-Werte oder Kopien der Worker-Konfiguration.

### 3.2 SQLite gegenüber PostgreSQL

| Kriterium | SQLite mit WAL | PostgreSQL | MVP-Bewertung |
| --- | --- | --- | --- |
| Betrieb | Eingebettete Datei, kein zusätzlicher Dienst | Eigener Server, Benutzer-/Netzwerk-/Upgrade-Betrieb | SQLite ist für eine einzelne Control Plane deutlich einfacher. |
| Leser/Writer | Leser können im WAL-Modus parallel zum Writer arbeiten; es gibt aber nur einen Writer gleichzeitig. | MVCC, mehrere Writer und Zeilensperren | Kurze, seltene MVP-Schreibtransaktionen passen zu SQLite. |
| Queue-Claim | Kein `SELECT ... FOR UPDATE SKIP LOCKED`; Claim muss kurz serialisiert werden. | Row Locking und `SKIP LOCKED` eignen sich für mehrere Dispatcher. | Ein Dispatcher beziehungsweise geringe Parallelität ist MVP-Annahme. |
| Ausfallsicherheit | Lokale Datei; HA und Replikation sind nicht eingebaut. | Replikation, HA- und PITR-Ökosystem | HA ist kein aktueller MVP-Umfang. |
| Dateisystem | WAL benötigt alle Zugriffe auf demselben Host und ist für Netzwerkdateisysteme ungeeignet. | Netzwerkdienst; Storage wird vom DB-Betrieb gekapselt. | SQLite-Datei muss auf einem lokalen persistenten Volume liegen. |
| Migrationen | Viele Schemaänderungen benötigen Alembic Batch/„move and copy“. | Umfangreichere native `ALTER TABLE`-Funktionen | Für das kleine MVP-Schema vertretbar, aber migrationskritisch. |
| Backup | SQLite Backup API oder `VACUUM INTO`; WAL darf nicht ignoriert werden. | Etablierte logische/physische Backup-Werkzeuge | SQLite ist beherrschbar, wenn das Verfahren operationalisiert wird. |

PostgreSQL wird neu bewertet, wenn mehrere aktive Control-Plane-Writer,
anhaltende `SQLITE_BUSY`-Konflikte, hoher Queue-Durchsatz, HA/Remote-DB oder ein
mit einer lokalen Datei nicht erreichbares RPO/RTO verlangt werden. SQLAlchemy
reduziert Dialektkopplung, macht Queue-Locking, Datentypen und Datenmigration
aber nicht automatisch portabel.

Grundlagen: [SQLite WAL](https://www.sqlite.org/wal.html),
[SQLAlchemy SQLite](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html) und
[PostgreSQL MVCC](https://www.postgresql.org/docs/current/mvcc-intro.html).

### 3.3 DB-basierte Queue ohne Message-Broker

Die Queue ist keine separate Tabelle und kein zweiter Statusspeicher. Ein Job
ist ausführbar, wenn sein `status` `QUEUED` oder `RETRY_WAIT` ist und sein
`next_eligible_at` erreicht wurde. Bei einem neuen `QUEUED`-Job entspricht der
Wert dem frühesten gewünschten Startzeitpunkt.

Der geplante Claim-Ablauf ist:

1. Eine kurze SQLite-`BEGIN IMMEDIATE`-Transaktion serialisiert konkurrierende
   Writer früh.
2. Der nächste fällige Job wird deterministisch nach Fälligkeit,
   Erstellungszeit und `job_id` ausgewählt.
3. Ein bedingtes Update mit `version` schützt vor einem veralteten Claim.
4. Jobstatus, neuer Attempt und Job-Event werden in derselben Transaktion
   geschrieben.
5. Erst nach Commit beginnen Wake-up und Dispatch.

Bei Abschluss werden Attempt-Telemetrie, Attempt-Endstatus und neuer Jobstatus
atomar gespeichert. Ein Retry setzt den Job auf `RETRY_WAIT`; erst beim nächsten
tatsächlichen Claim entsteht ein neuer Attempt. Ein optionaler In-Process-Hinweis
kann den Poller wecken, ist aber niemals die dauerhafte Queue-Quelle. SQLite
ersetzt im MVP keinen Broker für hohe Parallelität, verteilte Consumer oder
Push-Benachrichtigung.

## 4. Entscheidungen und Migrationsumfang

Die langfristigen Entscheidungen stehen knapp im
[Entscheidungsregister](../decisions.md). Dieses Dokument enthält das konkrete
Schema und die Betriebsverträge.

Die Initialrevision `0001_a2_2` erzeugt genau diese sechs fachlichen Tabellen:

1. `jobs`
2. `attempts`
3. `leases`
4. `worker_states`
5. `control_state`
6. `job_events`

Alembics technische Tabelle `alembic_version` wird vom Migrationswerkzeug
verwaltet und ist keine OASIX-Fachtabelle. Die Initialmigration legt außerdem
die Singleton-Zeile in `control_state` an. Upgrade, Downgrade auf `base` und
erneutes Upgrade sind implementiert und geprüft.

Folgende fünf fachlichen Tabellen gehören ausdrücklich **nicht** zur ersten
Migration und werden erst zusammen mit der jeweils verantwortlichen
Geschäftskomponente entworfen und migriert:

- `job_dependencies`
- `job_service_requirements`
- `service_states`
- `power_operations`
- `power_attempts`

## 5. Finalisierter Designentwurf

### 5.1 Gemeinsame Datenkonventionen

- Primär-IDs werden von der Anwendung als UUIDv4 erzeugt, kanonisch in
  Kleinschreibung mit Bindestrichen dargestellt und als `VARCHAR(36)`
  gespeichert. UUIDv4 ist der einfache, plattformunabhängige A.2-Standard;
  zeitlich sortierbare oder zentrale ID-Generatoren werden nicht eingeführt.
- `worker_id`, Rollen und technische Arten verwenden validierte, generische
  Identifikatoren; keine Infrastrukturbezeichnung wird fest codiert.
- Sämtliche Zeitpunkte werden als `BIGINT`/SQLite `INTEGER` in UTC-
  Epoch-Mikrosekunden gespeichert. `NULL` bedeutet „nicht geliefert oder nicht
  eingetreten“ und wird nicht geschätzt.
- Dauern werden als nicht negative `BIGINT`-Millisekunden gespeichert.
- Statuswerte liegen als `VARCHAR` mit benannten `CHECK`-Constraints vor.
- JSON liegt kanonisch UTF-8-codiert in `TEXT`. Die Anwendung prüft Schema,
  Verschachtelung und Byte-Limit vor dem Schreiben; die Migration ergänzt
  `CHECK(json_valid(...))`. Die Engine-Initialisierung prüft `json_valid` mit
  gültiger und ungültiger Eingabe und schlägt bei fehlender oder fehlerhafter
  Funktion geschlossen fehl.
- Alle Foreign Keys und Constraints erhalten stabile Namen. Foreign Keys
  verwenden `ON DELETE RESTRICT`; ein späterer Retention-Use-Case löscht
  abhängige Zeilen bewusst und atomar.
- Es gibt keine persistierten Aktivitätszähler. Aktive Leases und laufende
  Attempts werden aus ihren Zeilen ermittelt.

### 5.2 Schutz persistierter Nutzdaten

Die folgenden Limits gelten für die UTF-8- beziehungsweise Binärdarstellung vor
dem Datenbankschreibvorgang. Die Grenzprüfung darf den abgelehnten Wert nicht in
Exceptions oder Logs aufnehmen.

| Feld | Persistenzvertrag und maximales Volumen |
| --- | --- |
| `input_payload_json` | Höchstens 1.048.576 UTF-8-Bytes; valides JSON-Objekt, zusätzlich gegen ein typspezifisches Schema mit verbotenen unbekannten Feldern validiert. Auth-Header, private Schlüssel, Passwörter und andere Zugangsdaten sind unzulässig. Größere Eingaben müssen später extern gespeichert und sicher referenziert werden. |
| `result_ref` | Höchstens 2.048 UTF-8-Bytes; ausschließlich eine opake, nicht authentifizierende Referenz. Ergebnisinhalt und unkontrollierte Provider-Rohantworten werden nicht in `jobs` gespeichert. Credential-haltige URLs sind verboten. |
| `attempts.execution_ref` | Höchstens 2.048 UTF-8-Bytes; ausschließlich eine opake, nicht authentifizierende Runtime-Referenz. Eingebettete Zugangsdaten, authentifizierende URLs und Tokens sind verboten. Der Wert darf weder in Logs noch in Fehlermeldungen erscheinen. Es gelten dieselben Schutzregeln wie für `result_ref`. |
| `continuation_ref` | Höchstens 4.096 UTF-8-Bytes; opake, nicht authentifizierende Wiederaufnahmereferenz. Ist der Runtime-Zustand selbst geheim oder größer, muss er extern geschützt gespeichert werden; die DB enthält dann nur dessen Referenz. Das Feld wird nie geloggt. |
| `error_detail_redacted` | Höchstens 8.192 UTF-8-Bytes; ausschließlich redigierte Diagnose. Keine Rohantwort, kein Stack-Dump, kein Request-Payload und keine Header. Fehlerklasse und sicherer Fehlercode bleiben separate Felder. |
| `extra_metrics_json` | Höchstens 65.536 UTF-8-Bytes; valides JSON-Objekt mit höchstens acht Verschachtelungsebenen und validierten technischen Schlüsseln. Es ergänzt nur Metriken und darf keine Kernfelder, Nutzinhalte oder Secrets spiegeln. |
| `job_events.metadata_json` | Höchstens 16.384 UTF-8-Bytes; valides JSON-Objekt aus einer Allowlist je `event_type`. Keine freien Objekt-Dumps, Payloads, Rohantworten, Referenz-Tokens oder Zugangsdaten. |

Diese Regeln werden an der Service-/Repository-Grenze zentral durchgesetzt.
JSON-Felder dürfen nur über typisierte, feldbeschränkte Modelle geschrieben
werden; eine generische Secret-Heuristik gilt nicht als Sicherheitsgarantie.
Sichere Validierungsfehler nennen Feld, Regel und Limit, aber weder den Wert
noch Auszüge daraus. Da SQLite deklarierte `VARCHAR`-Längen nicht erzwingt,
spiegelt die Migration alle genannten Byte-Obergrenzen zusätzlich in benannten
`CHECK(length(CAST(feld AS BLOB)) <= limit)`-Constraints. Typspezifische
JSON-Schemata, Verschachtelung und Inhaltsverbote bleiben
Anwendungsverantwortung.

Für `execution_ref` akzeptiert die Anwendung nur das ausdrücklich als
nicht geheim deklarierte Referenzfeld eines Runtime-Adapters, keine URL und
kein Authentifizierungsfeld. Liefert ein Backend ausschließlich ein Token oder
eine credential-haltige URL, muss der Adapter daraus außerhalb dieser Tabelle
eine sichere Referenz bilden oder den Wert ablehnen. Eine Mustererkennung
vermeintlicher Tokens wird nicht als Sicherheitskontrolle verwendet.

Die SQLite-Datei enthält damit weiterhin potenziell sensible Nutzdaten. Datei,
WAL, SHM, Snapshots und Backups unterliegen denselben Zugriffsregeln. Ergebnisse
werden außerhalb dieser sechs Tabellen gespeichert; die Wahl und Absicherung
eines Result Stores ist eine spätere Architekturentscheidung.

### 5.3 Beziehungsübersicht

```text
jobs ──< attempts
  │          │
  ├──< job_events
  └──< leases >── worker_states
             ▲           ▲
             └─ attempts ┘

control_state: genau eine globale Zeile
```

`job_events.attempt_id` und `leases.attempt_id` sind optional. Wo sowohl Job-
als auch Attempt-Bezug gesetzt sind, muss der Attempt zum genannten Job gehören.
Diese tabellenübergreifende Invariante wird im jeweiligen atomaren Use Case
geprüft; sie rechtfertigt keinen redundanten Aktivitätszähler.

### 5.4 Tabelle `jobs`

**Zweck:** Stabile Job-Identität, Queue-Zustand, Warten, Retry-Fälligkeit,
Idempotenznachweis und externe Resultatreferenz.

| Spalte | SQL-Typ | Null | Bedeutung |
| --- | --- | --- | --- |
| `job_id` | `VARCHAR(36)` | nein | Primärschlüssel, über alle Attempts stabil |
| `job_type` | `VARCHAR(63)` | nein | Providerneutraler Jobtyp |
| `role` | `VARCHAR(63)` | ja | Logische Agent-/Jobrolle, soweit vorhanden |
| `status` | `VARCHAR(16)` | nein | Verbindlicher Jobstatus |
| `parent_job_id` | `VARCHAR(36)` | ja | Self-FK auf übergeordneten Job |
| `idempotency_scope` | `VARCHAR(128)` | ja | Interner, nicht geheimer Caller-/Tenant-Namespace |
| `idempotency_key_digest` | `BLOB` | ja | 32-Byte-SHA-256-Digest, nie der Rohschlüssel |
| `request_fingerprint` | `BLOB` | ja | 32-Byte-Digest des kanonischen semantischen Requests |
| `request_id` | `VARCHAR(128)` | ja | Korrelation mit eingehender Anfrage |
| `input_payload_json` | `TEXT` | nein | Validierte providerneutrale Jobeingabe |
| `result_ref` | `VARCHAR(2048)` | ja | Opaque Referenz auf ein extern gespeichertes Ergebnis |
| `wait_kind` | `VARCHAR(8)` | ja | `LOCAL` oder `EXTERNAL` bei `WAITING` |
| `continuation_ref` | `VARCHAR(4096)` | ja | Opaque, persistierte Wiederaufnahmereferenz |
| `resumability_confirmed_at` | `BIGINT` | ja | Explizite Bestätigung sicherer Wiederaufnahme |
| `next_eligible_at` | `BIGINT` | ja | Frühester Zeitpunkt für Claim oder Retry |
| `created_at`, `queued_at`, `updated_at` | `BIGINT` | nein | UTC-Zeitpunkte |
| `finished_at` | `BIGINT` | ja | Abschlusszeit eines terminalen Jobs |
| `version` | `INTEGER` | nein | Optimistic-Locking-Version, Startwert 1 |

Zulässige Statuswerte bleiben exakt die Vorgabe aus v3.4: `QUEUED`, `RUNNING`,
`WAITING`, `RETRY_WAIT`, `BLOCKED`, `DONE`, `FAILED`, `INTERRUPTED`.

Verbindliche Constraints und Indizes:

- PK `job_id`; Self-FK `parent_job_id` mit `RESTRICT`.
- `CHECK(version >= 1)` und `CHECK(parent_job_id IS NULL OR parent_job_id <> job_id)`.
- `queued_at >= created_at`, `updated_at >= created_at` und logisch geordnete
  optionale Abschluss-/Wartezeitpunkte.
- Idempotenz-Scope, Key-Digest und Request-Fingerprint sind entweder gemeinsam
  `NULL` oder gemeinsam gesetzt; beide Digests besitzen exakt 32 Bytes.
- Partieller Unique-Index auf `(idempotency_scope, idempotency_key_digest)`,
  wenn die Idempotenzfelder gesetzt sind. Es gibt keinen global eindeutigen
  Rohschlüssel.
- `wait_kind` ist genau für `WAITING` gesetzt. Bestätigungszeit und
  `continuation_ref` sind gemeinsam gesetzt oder `NULL`; gesetzte Werte
  verlangen `wait_kind = 'EXTERNAL'`.
- `next_eligible_at` ist für `QUEUED` und `RETRY_WAIT` gesetzt und für andere
  Statuswerte `NULL`.
- `finished_at` ist für `DONE`, `FAILED` und `INTERRUPTED` gesetzt und sonst
  `NULL`; `BLOCKED` bleibt bewusst nicht terminal.
- Partieller Queue-Index auf `(next_eligible_at, created_at, job_id)` für
  `status IN ('QUEUED', 'RETRY_WAIT')`.
- Indizes auf `parent_job_id`, `request_id` und `(status, updated_at)`.

Die Datenbank sichert Struktur und Eindeutigkeit. Welche API-Identität den
Scope liefert, wie der Request kanonisiert wird und welche Antwort ein
Idempotenztreffer erhält, bleibt Geschäftslogik nach Abschnitt 5.12.

### 5.5 Tabelle `attempts`

**Zweck:** Eigene Identität jedes konkreten Ausführungsversuchs,
Recovery-Referenz und zuordenbare Telemetrie.

| Spalte | SQL-Typ | Null | Bedeutung |
| --- | --- | --- | --- |
| `attempt_id` | `VARCHAR(36)` | nein | Primärschlüssel |
| `job_id` | `VARCHAR(36)` | nein | FK auf stabilen Job |
| `attempt_number` | `INTEGER` | nein | Bei 1 beginnende Sequenz je Job |
| `worker_id` | `VARCHAR(63)` | nein | FK auf `worker_states` |
| `status` | `VARCHAR(16)` | nein | Minimaler Attempt-Zustand |
| `execution_ref` | `VARCHAR(2048)` | ja | Opaque, nicht authentifizierende Runtime-Referenz für Recovery |
| `agent_role` | `VARCHAR(63)` | ja | Tatsächlich verwendete logische Rolle |
| `model` | `VARCHAR(255)` | ja | Tatsächlich gemeldete Modellkennung |
| `created_at`, `updated_at` | `BIGINT` | nein | UTC-Zeitpunkte |
| `started_at`, `first_token_at`, `finished_at` | `BIGINT` | ja | Attempt-Zeitpunkte, soweit geliefert |
| `queue_duration_ms`, `wake_duration_ms`, `execution_duration_ms` | `BIGINT` | ja | Nicht negative Dauerwerte |
| `input_tokens`, `output_tokens`, `total_tokens` | `BIGINT` | ja | Nicht negative Tokenwerte, nicht geschätzt |
| `tool_calls_count`, `tool_duration_ms` | `BIGINT` | ja | Tool-Telemetrie, soweit verfügbar |
| `error_class` | `VARCHAR(16)` | ja | Stabile, redigierte Fehlerklasse |
| `error_code` | `VARCHAR(128)` | ja | Maschinenlesbarer, redigierter Code |
| `error_detail_redacted` | `TEXT` | ja | Begrenzte, redigierte Diagnose |
| `extra_metrics_json` | `TEXT` | ja | Begrenzte optionale Zusatzmetriken |
| `version` | `INTEGER` | nein | Optimistic-Locking-Version |

Das vorgeschlagene Minimalmodell lautet:

- `PENDING`: Attempt ist atomar mit dem Claim angelegt, Ausführung noch nicht
  bestätigt.
- `RUNNING`: konkrete Ausführung ist bestätigt aktiv.
- `SUSPENDED`: externe Wartephase ist explizit sicher fortsetzbar; der
  Wiederaufnahmepunkt ist persistiert und die Worker-Lease darf freigegeben
  sein.
- `SUCCEEDED`, `FAILED`, `INTERRUPTED`: terminale Ergebnisse. Ein Timeout wird
  als `FAILED` mit Fehlerklasse `TIMEOUT` dargestellt und benötigt keinen
  zusätzlichen Status.

`WAITING` bleibt ausschließlich ein Jobstatus. Für `WAITING/LOCAL` ist kein
offener Attempt erforderlich. Ein noch nicht als sicher fortsetzbar bestätigtes
`WAITING/EXTERNAL` behält den Attempt `RUNNING` und seine Lease; erst die
atomare Bestätigung erlaubt `SUSPENDED` und Lease-Freigabe. `INTERRUPTED` ist
terminal und blockiert daher keinen späteren Retry-Attempt.

Verbindliche Constraints und Indizes:

- FKs `job_id` und `worker_id` mit `RESTRICT`.
- Unique `(job_id, attempt_number)`.
- Partieller Unique-Index auf `job_id` für höchstens einen offenen Attempt mit
  `status IN ('PENDING', 'RUNNING', 'SUSPENDED')`.
- Partieller Unique-Index `(worker_id, execution_ref)`, wenn `execution_ref`
  gesetzt ist.
- `execution_ref` wird vor dem Schreiben auf höchstens 2.048 UTF-8-Bytes und
  das Verbot authentifizierender Inhalte validiert. Ein benannter
  Byte-Längen-`CHECK` schützt zusätzlich vor zu großen direkten DB-Writes;
  Fehler und Logs geben den Wert nicht wieder.
- Für offene Zustände ist `finished_at` `NULL`; für terminale Zustände ist es
  gesetzt. `attempt_number` und `version` sind mindestens 1, Messwerte sind
  nicht negativ und Zeitpunkte logisch geordnet, soweit jeweils vorhanden.
- Indizes auf `(status, updated_at)`, `(worker_id, status)` und
  `(job_id, created_at)`.

Ob eine Wiederaufnahme `SUSPENDED -> RUNNING` im selben Attempt erfolgt oder
eine neue konkrete Ausführung und damit einen neuen Attempt benötigt, hängt vom
späteren Runtime-Vertrag ab. Vor Anlage eines neuen Attempts muss der bisherige
`SUSPENDED`-Attempt terminalisiert werden. Der partielle Index ist mit beiden
Varianten vereinbar; die exakten Transition Guards folgen nicht in A.2.

Fehlerklassen umfassen mindestens die in REC-04 genannten Werte `CONFIG`,
`WAKE`, `READINESS`, `LLM`, `AGENT`, `TOOL`, `TIMEOUT`. Ob zusätzlich eine
generische, sicher redigierte `INTERNAL`-Klasse benötigt wird, bleibt vor der
Implementierung zu entscheiden.

### 5.6 Tabelle `job_events`

**Zweck:** Append-only Zustands- und Auditverlauf für Diagnose, Force Sleep und
Recovery, ohne Attempt-Daten zu überschreiben.

| Spalte | SQL-Typ | Null | Bedeutung |
| --- | --- | --- | --- |
| `event_id` | `INTEGER` | nein | Autoincrement-PK, nur lokale Reihenfolge |
| `job_id` | `VARCHAR(36)` | nein | FK auf Job |
| `attempt_id` | `VARCHAR(36)` | ja | Optionaler FK auf Attempt |
| `event_type` | `VARCHAR(63)` | nein | Validierter technischer Ereignistyp |
| `from_status`, `to_status` | `VARCHAR(16)` | ja | Optionale Job-Zustandsänderung |
| `error_class` | `VARCHAR(16)` | ja | Optionale redigierte Fehlerklasse |
| `error_code` | `VARCHAR(128)` | ja | Optionaler sicherer Fehlercode |
| `metadata_json` | `TEXT` | ja | Begrenzte, allowlist-validierte Metadaten |
| `occurred_at` | `BIGINT` | nein | UTC-Zeitpunkt |

FKs verwenden `RESTRICT`. `from_status` und `to_status` akzeptieren nur die
verbindlichen Jobstatuswerte und sind entweder gemeinsam gesetzt oder gemeinsam
`NULL`. Indizes liegen auf
`(job_id, occurred_at, event_id)`, `attempt_id` und
`(event_type, occurred_at)`. Append-only ist ein Repository-Vertrag; es werden
keine Update-/Delete-Trigger eingeführt. Ein kontrollierter Retention-Use-Case
darf Events zusammen mit dem Job löschen.

### 5.7 Tabelle `leases`

**Zweck:** Einzige persistente Autorität für aktive Worker-Nutzung.

| Spalte | SQL-Typ | Null | Bedeutung |
| --- | --- | --- | --- |
| `lease_id` | `VARCHAR(36)` | nein | Primärschlüssel |
| `worker_id` | `VARCHAR(63)` | nein | FK auf `worker_states` |
| `owner` | `VARCHAR(128)` | nein | Technischer Owner, keine Zugangsdaten |
| `purpose` | `VARCHAR(63)` | nein | Validierter generischer Nutzungszweck |
| `job_id`, `attempt_id` | `VARCHAR(36)` | ja | Optionale Korrelation |
| `created_at`, `last_heartbeat_at`, `expires_at` | `BIGINT` | nein | UTC-Zeitpunkte für TTL/Heartbeat |
| `released_at` | `BIGINT` | ja | Explizite Freigabe |
| `release_reason` | `VARCHAR(63)` | ja | Redigierter technischer Grund |

Eine Lease ist genau dann aktiv, wenn `released_at IS NULL` und
`expires_at > now_utc`. Es gibt weder Statusspalte noch `active_lease_count`;
abgelaufene, noch nicht bereinigte Zeilen sind bereits inaktiv.

Verbindliche Constraints und Indizes:

- FKs auf Worker sowie optional Job/Attempt mit `RESTRICT`.
- `CHECK(expires_at > created_at)`,
  `created_at <= last_heartbeat_at <= expires_at` und Release nicht vor
  Erstellung.
- Index `(worker_id, released_at, expires_at)` für Sleep-Gate-Abfragen.
- Indizes auf `(owner, expires_at)`, `job_id` und `attempt_id`.

Acquire, Heartbeat, Release und Expiry-Cleanup sind jeweils kurze
Transaktionen. Bei `WAITING/EXTERNAL` müssen Resumability-Bestätigung,
`continuation_ref`, `SUSPENDED`-Status und Lease-Freigabe in einer fachlichen
Transaktion gespeichert werden. Die konkrete Lease-/Sleep-Logik folgt später.

### 5.8 Tabelle `worker_states`

**Zweck:** Zuletzt persistierter, beobachteter Betriebszustand generischer
Worker; keine Verbindungs-, Hardware- oder Servicekonfiguration.

| Spalte | SQL-Typ | Null | Bedeutung |
| --- | --- | --- | --- |
| `worker_id` | `VARCHAR(63)` | nein | PK, generische ID aus der Runtime-Konfiguration |
| `state` | `VARCHAR(16)` | nein | Verbindlicher Worker-Zustand |
| `observed_at` | `BIGINT` | ja | Zeitpunkt der letzten realen Beobachtung |
| `state_changed_at`, `updated_at` | `BIGINT` | nein | UTC-Zeitpunkte |
| `idle_since`, `last_ready_at` | `BIGINT` | ja | Persistierte Zeitpunkte für Idle-/Recovery-Entscheidungen |
| `last_error_class` | `VARCHAR(16)` | ja | Redigierte letzte Fehlerklasse |
| `last_error_code` | `VARCHAR(128)` | ja | Redigierter letzter Fehlercode |
| `version` | `INTEGER` | nein | Optimistic-Locking-Version |

Zulässig sind exakt `UNKNOWN`, `WAKING`, `READY`, `BUSY`, `IDLE`, `SLEEPING`
und `UNAVAILABLE`. Indizes liegen auf `(state, updated_at)` und `idle_since`.
Bei jedem Prozessstart werden konfigurierte Worker vor weiteren Entscheidungen
auf `UNKNOWN` gesetzt beziehungsweise so angelegt; ein alter `READY`-Wert gilt
niemals als aktuelle Readiness. Historische Worker-Zeilen bleiben wegen ihrer
Attempt-/Lease-Bezüge erhalten. `BUSY` ist eine Beobachtung und kein Ersatz für
die Lease-Abfrage.

### 5.9 Tabelle `control_state`

**Zweck:** Globaler persistenter Steuerungs- und Recovery-Zustand der einzelnen
Control Plane.

| Spalte | SQL-Typ | Null | Bedeutung |
| --- | --- | --- | --- |
| `singleton_id` | `SMALLINT` | nein | PK mit `CHECK(singleton_id = 1)` |
| `dispatch_mode` | `VARCHAR(24)` | nein | `ACTIVE`, `PAUSED_RECOVERY`, `PAUSED_ADMIN`, `PAUSED_POWER` |
| `recovery_status` | `VARCHAR(16)` | nein | `CLEAN`, `REQUIRED`, `RUNNING`, `FAILED` |
| `process_instance_id` | `VARCHAR(36)` | ja | Kennung des aktuellen Starts; vor dem ersten Start `NULL` |
| `started_at` | `BIGINT` | ja | Startzeit der aktuellen Instanz; vor dem ersten Start `NULL` |
| `updated_at` | `BIGINT` | nein | UTC-Zeitpunkt der letzten Änderung |
| `last_clean_shutdown_at` | `BIGINT` | ja | Letzter sauberer Shutdown, soweit vorhanden |
| `version` | `INTEGER` | nein | Optimistic-Locking-Version |

Die Initialmigration legt genau eine Zeile mit `PAUSED_RECOVERY`, `REQUIRED`
und noch keiner Prozessinstanz an. Ein Prozessstart setzt Instanz und
Startzeit; nur erfolgreicher Abgleich darf auf `ACTIVE` wechseln.
`PAUSED_POWER` bereitet die in v3.4 verlangte Dispatch-Pause bei Force Sleep
vor, ohne eine Power-Operation vorwegzunehmen. Die Tabelle enthält keine
Lease-, Job- oder Attempt-Zähler. Prozessinstanz und `started_at` sind entweder
gemeinsam gesetzt oder gemeinsam `NULL`; `version` ist mindestens 1.

### 5.10 Bewusst verschobene Erweiterungen

| Tabelle | Frühester fachlicher Bedarf | Grund für die Verschiebung |
| --- | --- | --- |
| `job_dependencies` | Lokale Abhängigkeiten/Agent-Unterjobs | Abhängigkeitsarten, Zyklusregeln und Aufwecksemantik gehören zum späteren Jobmodell. |
| `job_service_requirements` | Servicebezogener Dispatch | Die stabile Repräsentation benötigter Capabilities wird mit Job-API und Dispatcher festgelegt. |
| `service_states` | Phase B Readiness | Zustände, Probe-Gültigkeit und Persistenzbedarf müssen mit der Readiness-Implementierung entschieden werden. |
| `power_operations` | Phase C Power-Steuerung | Audit-, Grace- und Operationszustände hängen vom Manual-/Force-Sleep-Use-Case ab. |
| `power_attempts` | Phase C Wake-/Sleep-Retry | Versuchszustände und Retry-Zuordnung werden mit dem Power-Adapter festgelegt. |

Die Verschiebung verwirft keine v3.4-Anforderung. Jede Tabelle wird vor ihrer
Nutzung über eine eigene Alembic-Migration ergänzt. Die sechs Kerntabellen
enthalten bereits stabile Job-/Attempt-Identitäten, Recovery-Referenzen,
Worker-/Control-State, Leases und Events; sie erzwingen keine spekulative
Semantik der späteren Komponenten.

### 5.11 Runtime-Konfiguration `schema_version: 2`

Der Datenbankpfad ist als strikt validierte Sektion der externen Runtime-YAML
implementiert:

```yaml
schema_version: 2
persistence:
  database_path: /absolute/path/provided-by-deployment/oasix.sqlite3
  busy_timeout_ms: 5000
```

`database_path` ist ein absoluter lokaler Dateipfad, keine URL. Der Wert stammt
ausschließlich aus der Runtime-Datei, wird nicht im Anwendungscode vorbelegt
und enthält keine Zugangsdaten. `busy_timeout_ms` ist positiv validiert und
verwendet zunächst 5.000 ms als Default und Referenzwert. WAL, Foreign Keys und
`synchronous=FULL` sind geprüfte
Persistenzinvarianten und keine frei abschaltbaren Konfigurationsschalter.

Behandlung alter Konfigurationen:

- `schema_version: 1` bleibt das historische Format ohne Persistenzsektion,
  wird vom aktuellen Runtime-Loader aber nicht mehr akzeptiert.
- Die persistenzfähige Control Plane startet nur mit Version 2. Version 1 wird
  sicher abgewiesen; es gibt weder stillen Defaultpfad noch automatische
  Umdeutung oder In-place-Migration der YAML-Datei.
- Die manuelle Umstellung besteht aus dem expliziten Setzen von Version 2 und
  dem Ergänzen der validierten `persistence`-Sektion.
- Bootstrap bleibt ausschließlich für `OASIX_CONFIG_FILE` und
  `OASIX_SECRETS_DIRECTORY` zuständig. Es entsteht keine weitere
  `OASIX_`-Umgebungsvariable und keine zweite Quelle für den DB-Pfad.

Dateisystem- und Berechtigungsvertrag:

- Der kanonisch aufgelöste Elternpfad muss existieren, lokal und persistent
  sein; NFS, SMB und andere Netzwerkdateisysteme sind für WAL ausgeschlossen.
- Das Ziel darf, wenn es existiert, nur eine reguläre Datei und kein Symlink
  sein. Das dedizierte Verzeichnis darf nicht das Secret-Verzeichnis sein.
- Der Control-Plane-Prozess benötigt ausschließlich dort Rechte für Datenbank,
  `-wal` und `-shm`. Für Linux-Produktion sind ein dedizierter Owner,
  Verzeichnismodus `0700`, Dateimodus `0600` und `umask 0077` vorgesehen.
- Die A.2.1-Pfadprüfung öffnet das kanonische Elternverzeichnis mit einem
  Descriptor, prüft Owner und Modus, führt darin einen Schreibtest aus und
  reserviert beziehungsweise öffnet das Datenbankziel relativ zu diesem
  Descriptor mit `O_NOFOLLOW`, soweit die Plattform dieses Flag anbietet.
  Typ, Berechtigungen und Device-/Inode-Identität des geöffneten Objekts werden
  geprüft. Fehlerausgaben geben den vollständigen installationsspezifischen
  Pfad nicht wieder.
- Ob das Volume tatsächlich lokal, persistent und für SQLite-WAL geeignet ist,
  lässt sich unter Linux und macOS nicht vollständig portabel aus Python
  nachweisen. Diese Eigenschaft bleibt eine dokumentierte
  Deployment-Voraussetzung.
- Python/SQLite bietet der Engine Factory keinen portablen Weg, den bereits
  geprüften Dateidescriptor direkt als Datenbank zu übernehmen. Zwischen dem
  Schließen des Prüfdescriptors und dem SQLite-Öffnen bleibt daher trotz
  Device-/Inode-Prüfung vor und nach dem Verbindungsaufbau ein Restrisiko
  gegenüber einem bösartigen Prozess mit derselben Benutzer-ID. Das
  Datenbankverzeichnis muss exklusiv dem Control-Plane-Konto gehören und darf
  für andere Prozesse nicht schreibbar sein.

### 5.12 Idempotenzvertrag

Die Empfehlung ist ein Scope pro stabiler, authentifizierter Caller-/Tenant-
Identität. Der externe Rohschlüssel muss laut API-Vertrag aus mindestens 128 Bit
Zufall erzeugt und in einem streng begrenzten Format übertragen werden. Die API
kann Format und Länge, nicht aber tatsächliche Zufälligkeit eines Caller-Werts
beweisen. Der Schlüssel wird weder persistiert noch geloggt; die
Anwendung speichert seinen 32-Byte-SHA-256-Digest zusammen mit einem internen,
nicht geheimen Scope. Diese Entropieanforderung verhindert praktikable
Wörterbuchangriffe auf den nicht geheimen Digest und muss an der späteren API
validiert werden.

Der `request_fingerprint` ist ein SHA-256-Digest der kanonisierten semantischen
Eingabe, mindestens aus Jobtyp, Rolle und validiertem Payload. Die exakte
Kanonisierung wird vor Implementierung der Job-API spezifiziert und mit
Testvektoren festgeschrieben.

Trennung der Verantwortlichkeiten:

- **Datenbank:** All-or-none-Constraint der drei Idempotenzfelder, Digestlängen
  und partieller Unique-Index auf Scope plus Key-Digest.
- **API/Geschäftslogik:** Scope nach erfolgreicher Authentifizierung ableiten,
  Rohschlüssel/Fingerprint bilden und in einer Transaktion prüfen. Gleicher
  Scope/Schlüssel und gleicher Fingerprint liefert den bestehenden Job;
  abweichender Fingerprint ist ein Konflikt und erzeugt keinen neuen Job.
- **Neustart:** Die persistierte Unique-Zuordnung gilt unverändert weiter und
  benötigt keinen In-Memory-Cache.
- **Datenschutz/Retention:** Rohschlüssel und Auth-Identität werden nicht
  gespeichert. Die Idempotenzzuordnung lebt genau so lange wie ihre Jobzeile;
  nach deren kontrollierter Löschung kann derselbe Schlüssel wieder verwendet
  werden. Eine längere Replay-Sperre würde eine zusätzliche Tombstone-Tabelle
  erfordern und ist nicht Bestandteil der ersten Migration.

Die konkrete Retention-Dauer muss vor Freigabe der Job-API festgelegt werden;
bis dahin darf keine automatische Löschung implementiert werden.

### 5.13 Engine, Sessions und SQLite-Pragmas

Die in A.2.1 implementierte Factory akzeptiert nur die validierte
Runtime-Konfiguration und die Bootstrap-Quellen, prüft den Pfad und baut die
SQLite-URL intern. Beliebige Datenbank-URLs werden nicht übernommen.

Implementierte Verbindungsinitialisierung:

- Python-3.12-`sqlite3` mit `autocommit=False`, damit nicht der legacyhafte
  Transaktionsmodus die Semantik bestimmt;
- `PRAGMA foreign_keys=ON` über einen SQLAlchemy-Connect-Hook auf jeder
  Verbindung. Der Hook setzt das Pragma gemäß SQLAlchemy-Empfehlung bei
  vorübergehendem DBAPI-Autocommit und stellt den vorherigen Wert anschließend
  wieder her; eine Rückleseprüfung muss `1` ergeben;
- persistentes `PRAGMA journal_mode=WAL` bei Initialisierung und Prüfung auf
  den tatsächlich zurückgegebenen Wert `wal`;
- `PRAGMA busy_timeout=5000` auf jeder Verbindung, sofern die validierte
  Runtime-Konfiguration keinen ausdrücklich anderen positiven Wert vorgibt;
- `PRAGMA synchronous=FULL` als sicherheitsorientierter MVP-Default;
- verifiziertes SQLite-`json_valid` als Voraussetzung für die JSON-
  Datenbankconstraints;
- SQLAlchemy-`QueuePool` mit `pool_size=5`, `max_overflow=0` und
  `pool_timeout=5` Sekunden als einfache, begrenzte Ausgangskonfiguration.

Unbekannte oder nicht wirksame Pragmas werden nicht stillschweigend akzeptiert.
Die Session Factory verwendet SQLAlchemy-2-Stil und `expire_on_commit=False`.
Normale A.2-Transaktionstests verwenden `Session.begin()`. Eine spätere
`BEGIN IMMEDIATE`-Primitive für den Queue-Claim gehört zum Dispatcher und wird
nicht in A.2.1 vorweggenommen. Die Initialisierung übersetzt Verbindungs- und
Pfadfehler in sichere technische Fehlertypen. Die an den späteren Use-Case-
Grenzen erforderliche Übersetzung eines nach Ablauf des Busy-Timeouts
verbleibenden `SQLITE_BUSY`, DB-Lock-Retry und fachlicher Job-Retry sind noch
nicht implementiert.

Diese Werte sind bewusst konservative Startannahmen für eine einzelne Control
Plane und kein adaptiver Tuning-Mechanismus. Der kleine Pool begrenzt offene
Verbindungen; SQLite bleibt ungeachtet der Leserzahl Single-Writer. Linux ist
die Produktionsplattform, macOS nur Entwicklungsplattform. POSIX-Modi und
Symlink-/Typprüfungen sind dort testbar, die zuverlässige automatische
Erkennung aller Netzwerk- oder speziellen Dateisysteme ist jedoch nicht
plattformübergreifend garantiert. Das Deployment muss deshalb zusätzlich ein
lokales persistentes Volume attestieren. Abweichungen von Pool- oder Timeout-
Werten erfordern Messdaten, aber keinen neuen Architekturmechanismus.

Die Engine setzt `hide_parameters=True`; dadurch erscheinen gebundene
SQL-Parameter nicht in SQLAlchemy-Fehlertexten. Es gibt keine globale Session,
keine Schemaerzeugung beim Import und keinen automatischen Migrationslauf.

### 5.14 Transaktionsgrenzen

Folgende Änderungen sind jeweils atomar geplant:

- **Job anlegen:** Job und initiales Event; Idempotenzkonflikt wird innerhalb
  derselben Transaktion entschieden.
- **Job claimen:** Status-/Versionswechsel, neuer Attempt und Claim-Event.
- **Attempt abschließen:** Attempt-Endstatus/Telemetrie, Job-Endstatus oder
  `RETRY_WAIT`, `next_eligible_at` und Event.
- **Lease:** Acquire, einzelner Heartbeat sowie Release/Expiry-Cleanup jeweils
  als kurze Transaktion.
- **Externes Warten:** Resumability-Bestätigung, `continuation_ref`,
  `SUSPENDED`, Jobstatus und Lease-Freigabe.
- **Sleep Gate:** Dispatch pausieren und in derselben Transaktion erneut aktive
  Leases, fällige Jobs, Idle-Zeit und Worker-State prüfen. Erst danach erfolgt
  ein externer Sleep-Aufruf.
- **Recovery-Start:** `PAUSED_RECOVERY`, Prozessinstanz und Worker `UNKNOWN`
  setzen, bevor reale Probes beginnen.

Abhängigkeiten, Service-Anforderungen und Power-Operationen werden erst nach
Einführung ihrer verschobenen Tabellen Teil entsprechender Transaktionen.
Lange Wartezeiten, Polling und externe Kommunikation halten keine
DB-Transaktion offen.

### 5.15 Alembic und Upgrade-Strategie

Die Initialmigration erstellt die sechs fachlichen Tabellen, benannten
Constraints, Indizes und die `control_state`-Singleton-Zeile. Das
Produktionsschema wird nicht über `Base.metadata.create_all()` erzeugt.

Migrationen werden ausschließlich als separate Wartungsoperation ausgeführt.
Bei gesetzten Bootstrap-Quellen lautet der tatsächliche Aufruf:

```bash
OASIX_CONFIG_FILE=/path/provided-by-deployment/oasix.yaml \
OASIX_SECRETS_DIRECTORY=/path/provided-by-deployment/secrets \
python -m alembic -c alembic.ini upgrade head
```

Die Alembic-Konfiguration enthält bewusst keine zweite Datenbank-URL. `env.py`
lädt die validierte Runtime-Konfiguration und verwendet die A.2.1-Engine samt
Pfad-, Berechtigungs- und PRAGMA-Prüfungen. Erwarteter und einziger Head ist
`0001_a2_2`. Nach jeder Upgrade- oder Downgrade-Operation werden
`foreign_key_check` und `integrity_check` ausgeführt. `downgrade base` ist für
Tests und kontrollierte Wartung verfügbar, aber kein Ersatz für das unten
beschriebene Produktions-Restore-Verfahren.

- Es gibt zunächst genau einen linearen Alembic-Head.
- Autogenerate ist nur Entwurfswerkzeug; jede Migration wird manuell auf
  Constraints, Indizes, Datenmigration und Downtime geprüft.
- `render_as_batch=True` und benannte Constraints bereiten SQLite-Umbauten vor.
  Maßgeblich ist die
  [Alembic-Batch-Dokumentation](https://alembic.sqlalchemy.org/en/latest/batch.html).
- Eine vorübergehende Deaktivierung der Foreign-Key-Prüfung ist nur auf der
  exklusiven Migrationsverbindung zulässig. Vor Freigabe werden Foreign Keys
  reaktiviert und mit `foreign_key_check` geprüft.
- Migrationen laufen vor API, Dispatcher und Recovery exklusiv. Vor
  destruktiven beziehungsweise tabellenkopierenden Migrationen wird ein
  getestetes Backup erstellt; danach folgen `foreign_key_check`,
  `integrity_check` und Schema-Smoke-Test.
- Die Anwendung schlägt geschlossen fehl, wenn DB-Revision und erwarteter Head
  nicht übereinstimmen.
- Downgrades werden entwickelt und getestet, sind aber keine garantierte
  Produktionsrollback-Strategie. Nicht sicher reversible Änderungen erfordern
  Restore von Anwendungsversion und Backup.

### 5.16 Verhalten nach Prozessneustart

Der spätere Startup-/Recovery-Ablauf ist geplant als:

1. Runtime-Konfiguration Version 2 und Secrets vollständig validieren.
2. DB-Pfad prüfen, Datenbank öffnen, Pragmas und Alembic-Revision validieren.
3. Dispatch `PAUSED_RECOVERY` setzen, Prozessinstanz persistieren und Worker auf
   `UNKNOWN` setzen.
4. Abgelaufene Leases bei Aktivitätsabfragen sofort ignorieren und später
   kontrolliert bereinigen.
5. Nicht terminale Jobs und Attempts laden.
6. Worker und erforderliche Services real prüfen; `execution_ref` verwenden,
   um laufende Ausführung zweifelsfrei zu bestätigen.
7. Nicht bestätigbare Attempts in einer später implementierten Recovery-
   Transaktion `INTERRUPTED` setzen und den Job gemäß Retry-Policy kontrolliert
   auf `RETRY_WAIT`, `BLOCKED` oder `FAILED` überführen.
8. Dispatch erst nach erfolgreichem Abgleich aktivieren.

Service-State und Power-Operationen werden nach Einführung ihrer Tabellen in
den Ablauf aufgenommen. Dieses Dokument implementiert keine Probe-, Retry- oder
Recovery-Geschäftslogik.

### 5.17 Retention, Backup und Restore

**Retention-Vertrag:** Aktive beziehungsweise nicht terminale Jobs, offene
Attempts und aktive Leases werden niemals durch zeitbasierte Retention gelöscht.
Terminale Jobs werden später nur in einer expliziten Transaktion zusammen mit
Attempts und Events gelöscht. Idempotenz endet dabei mit der Jobzeile.
Abgelaufene/freigegebene Leases erhalten einen getrennten Cleanup-Zeitraum;
`worker_states` mit Referenzen und die `control_state`-Zeile bleiben erhalten.
Die numerischen Aufbewahrungsfristen sind mangels Betriebs-/Auditvorgabe in
v3.4 offen und müssen vor jeder automatischen Löschfunktion entschieden und
extern konfigurierbar gemacht werden.

Eine laufende WAL-Datenbank darf nicht durch Kopieren nur der Hauptdatei
gesichert werden. Bevorzugt ist ein konsistenter Online-Snapshot über die
[SQLite Backup API](https://www.sqlite.org/backup.html); `VACUUM INTO` ist eine
I/O-intensivere Alternative.

Backup-Ablauf:

1. Snapshot auf ein getrenntes, zugriffsgeschütztes Ziel schreiben.
2. SQLite Backup API mit Busy-Handling verwenden; keine rohe Live-Dateikopie.
3. Ziel schließen, Prüfsumme und restriktive Dateirechte setzen.
4. Backup mit `integrity_check`, `foreign_key_check` und erwarteter
   `alembic_version` validieren.
5. Backup verschlüsselt beziehungsweise durch gleichwertige Storage-
   Verschlüsselung geschützt ablegen und erst danach als erfolgreich markieren.

Bei gestoppter Control Plane ist eine kontrollierte Checkpoint-/Close-Sequenz
mit anschließender Dateikopie zulässig. Restore erfolgt nur bei gestoppter
Anwendung in ein leeres Datenverzeichnis, gefolgt von Integritäts-, Revisions-
und normaler Recovery-Prüfung. Schlüssel für Backup-Verschlüsselung dürfen
nicht neben dem Backup liegen. Konkrete RPO/RTO, Verschlüsselungstechnik,
Offsite-Ablage und numerische Backup-Retention bleiben offene Betriebsfragen.

## 6. Tests und Nachweise

Die A.2.1- und A.2.2-Tests verwenden ausschließlich temporäre lokale
Datenbanken. Der verbleibende Anwendungsumfang wird erst mit A.2.3
nachgewiesen.

### A.2.1 – implementiert und lokal nachgewiesen

- Runtime-Version 2 einschließlich Pflichtfeldern, unbekannten Feldern,
  Timeout-Grenzen und sicherer Ablehnung von Version 1,
- absoluter Datenbankpfad, existierender kanonischer Elternpfad, Trennung von
  der Secret-Quelle, Symlink-/Dateityp-, Owner-, Modus- und Schreibprüfung,
- WAL, Foreign Keys auf getrennten Verbindungen, `synchronous=FULL`,
  konfigurierter Busy-Timeout und begrenzte Poolparameter,
- explizites DBAPI-`autocommit=False` sowie Setzen von Foreign Keys außerhalb
  einer aktiven DBAPI-Transaktion,
- Session-Commit, Rollback, Ressourcenfreigabe und erneute Initialisierung mit
  persistenten Testdaten,
- keine automatische Anlage eines Anwendungsschemas sowie Redaktion privater
  Pfade und gebundener SQL-Parameter in Fehlerrepräsentationen.

### A.2.2 – implementiert und lokal nachgewiesen

- exakt sechs deklarative SQLAlchemy-Modelle und eine gemeinsame
  `Base.metadata`, einschließlich dokumentierter Beziehungen und
  Nullability-Regeln,
- explizit benannte Primär-/Fremdschlüssel, Unique- und CHECK-Constraints sowie
  normale und partielle SQLite-Indizes,
- Upgrade einer leeren Dateidatenbank auf den einzigen Head `0001_a2_2`,
  unveränderter zweiter Upgrade-Aufruf, Downgrade auf `base` und erneutes
  Upgrade,
- exakt eine initiale `control_state`-Zeile mit `PAUSED_RECOVERY`, `REQUIRED`,
  leerer Prozessinstanz und Version 1,
- erfolgreiche und abgewiesene Schreibvorgänge für Foreign Keys, Statuswerte,
  Versionen, Zeitreihenfolgen, Idempotenz, offene Attempts, Leases, JSON und
  alle dokumentierten UTF-8-Byte-Limits,
- Persistenz nach Engine-Neuerzeugung und Lesen aus einem separaten Subprozess,
  atomarer Rollback zusammengehöriger Inserts sowie Foreign Keys auf getrennten
  Verbindungen,
- `foreign_key_check`, `integrity_check`, Revisionsprüfung, sichere
  SQL-Parameterdarstellung und Fail-Closed-Test bei fehlendem `json_valid`.

### Gesamtabnahme A.2 – verpflichtend

- **Runtime-Konfiguration Version 2:** gültige Version-2-Konfiguration,
  ausdrückliche Ablehnung von Version 1 für die persistenzfähige Control Plane,
  unbekannte Felder sowie ungültige Pfade und Berechtigungen.
- **SQLite-Verbindung, WAL und Foreign Keys:** Python-3.12-Transaktionsmodus,
  WAL-Rückleseprüfung, `synchronous=FULL`, Busy-Timeout und aktivierte Foreign
  Keys auf jeder neuen Pool-Verbindung.
- **Alembic-Initialmigration:** Upgrade einer leeren Dateidatenbank bis zum
  einzigen Head, korrekte Singleton-Zeile und keine Schemaerzeugung über
  `create_all()`.
- **Sechs Kerntabellen und Constraints:** exakt `jobs`, `attempts`, `leases`,
  `worker_states`, `control_state` und `job_events` als OASIX-Fachtabellen;
  benannte Foreign Keys, Unique-, Partial-Index-, Status- und Zeit-Constraints.
- **Persistenz über Prozess-/Verbindungsneustarts:** geschriebene Kerndaten
  bleiben nach `Engine.dispose()`, neuer Engine und mindestens einem
  Subprozess-Smoke-Test unverändert lesbar.
- **Transaktionen und Rollback:** atomare Mehrzeilen-Schreibvorgänge und
  vollständiger Rollback bei Constraint- beziehungsweise simulierten Fehlern;
  keine externen Aufrufe in offenen Transaktionen.
- **Größenlimits und sicherer Umgang mit Daten:** Grenzfälle unterhalb, exakt
  auf und oberhalb jedes Limits; JSON-Validierung sowie Sentinel-Secrets, die
  abgewiesen werden und weder Datenbank noch Exceptions, Logs oder
  Repräsentationen erreichen. Dies schließt die `execution_ref`-Regeln ein.
- **Grundlegende Datenbankintegrität:** `integrity_check`,
  `foreign_key_check`, erwartete Alembic-Revision und sicherer Fehler bei
  unwirksamen Pflicht-Pragmas.

Dateibasiertes SQLite wird mit temporären lokalen Datenbanken getestet. Wo
mehrere Verbindungen erforderlich sind, werden tatsächlich getrennte
Verbindungen verwendet; eine einzelne In-Memory-Verbindung belegt weder
Poolverhalten noch Persistenz.

Die Datenbankconstraints für JSON-Syntax und Byte-Limits sind mit A.2.2
implementiert. Typspezifische JSON-Allowlist-Modelle, inhaltliche Verbote für
Secrets beziehungsweise authentifizierende Referenzen und sichere
Repository-Fehler folgen in A.2.3; sie werden nicht als bereits umgesetzt
dargestellt.

### Spätere Phasen

- Job-Claiming und Dispatcher einschließlich `BEGIN IMMEDIATE` und Konkurrenz,
- Retry-Geschäftslogik und Erzeugung neuer Attempts,
- Lease-Lifecycle, TTL/Heartbeat und Sleep-Gate,
- Recovery, Worker-/Service-Probes und Reconciliation,
- Manual-/Force-Sleep-Abläufe und Grace Period,
- Agenten-, Service- und Power-Steuerung einschließlich der fünf verschobenen
  Tabellen.

## 7. Einschränkungen, Risiken und offene Architekturfragen

### Risiken und Trade-offs

- SQLite serialisiert Writer. Lange Transaktionen oder hohe Schreiblast können
  `SQLITE_BUSY` und Dispatch-Latenz verursachen.
- Lange Reader können WAL-Checkpoints verzögern und die WAL-Datei wachsen
  lassen; Monitoring und ein Checkpoint-Betriebsplan sind nötig.
- WAL ist für Netzwerkdateisysteme ungeeignet und bietet keine eingebaute HA.
- Lokaler gegenüber Netzwerk-Storage wird nicht automatisch verlässlich
  erkannt. Zudem kann ein Prozess mit derselben Benutzer-ID theoretisch das
  Datenbankziel im verbleibenden Übergang zwischen Pfadprüfung und
  SQLite-Öffnung austauschen; exklusive Verzeichnisrechte sind deshalb Teil des
  Sicherheitsvertrags.
- SQLite-Batch-Migrationen kopieren Tabellen und benötigen Platz sowie ein
  exklusives Wartungsfenster.
- Lease-TTL basiert nach Neustart auf der UTC-Wanduhr. Zeitsynchronisation und
  Verhalten bei Uhrsprüngen sind betriebliche Voraussetzungen.
- Byte-Limits und Allowlist-Schemata reduzieren, verhindern aber nicht jede
  Fehlklassifikation sensibler Nutzdaten. Zugriffsrechte, Logging-Disziplin,
  Backupschutz und spätere Löschung bleiben notwendig.
- Ein SHA-256-Digest schützt nur ausreichend zufällige Idempotenzschlüssel; die
  spätere API kann den Erzeugungsvertrag nur durch Format- und Längenprüfung
  flankieren.
- Ein SQLAlchemy-Abstraktionslayer garantiert keine verlustfreie spätere
  Migration zu PostgreSQL.

### Vor A.2.1 zu entscheiden – im Review entschieden und umgesetzt

Das abgeschlossene Review hat die unmittelbar blockierenden Punkte entschieden;
sie gelten als verbindliche, möglichst einfache Implementierungsannahmen:

1. **UUID-Konvention:** UUIDv4, kanonisch kleingeschrieben und mit
   Bindestrichen, gespeichert als `VARCHAR(36)`.
2. **Datenbankpfad und Rechte:** absoluter externer Pfad; kanonischer
   existierender Elternpfad; existierendes Ziel nur als reguläre Datei und
   nicht als Symlink; getrennt vom Secret-Verzeichnis; Linux-Zielmodi `0700`
   für das Verzeichnis und `0600` für DB/WAL/SHM bei `umask 0077`.
3. **Transaktionsmodus und Foreign Keys:** Python 3.12 mit
   `sqlite3`-`autocommit=False`; `PRAGMA foreign_keys=ON` über den geprüften
   Connect-Hook auf jeder Verbindung.
4. **Busy-Timeout:** initial 5.000 ms und auf jeder Verbindung verifiziert.
5. **Connection Pool:** `QueuePool(pool_size=5, max_overflow=0,
   pool_timeout=5)`; keine adaptive Poolsteuerung und keine unbegrenzte
   Verbindungsanlage.

Diese Vorgaben sind für das A.2.1-Fundament umgesetzt. Pfad-, Symlink-,
POSIX-Modus- und Dateitypprüfungen werden unter Linux und macOS mit den dort
verfügbaren Descriptoroperationen ausgeführt. Nicht portabel nachweisbare
Mount-Eigenschaften bleiben als explizite Deployment-Voraussetzung
dokumentiert; eine vollständige plattformübergreifende
Netzdateisystemerkennung wird nicht behauptet.

### Für spätere Phasen zurückgestellt

1. Ableitung des stabilen Auth-/Tenant-`idempotency_scope` und exakte
   Request-Kanonisierung für die Job-API.
2. Wiederaufnahme im selben `SUSPENDED`-Attempt oder als neuer Attempt sowie
   zugehörige Transition Guards.
3. Zusätzliche Fehlerklasse `INTERNAL` und vollständige Retry-/Recovery-
   Klassifikation.
4. Queue-Prioritäten, Deadlines, Fairness und Dispatcher-Locking.
5. Snapshot oder dynamische Anwendung der Retry-Policy nach Neustart.
6. Lease-TTL, Heartbeat, Owner-/Purpose-Formate und Sleep-Gate.
7. Worker-Protokoll zur zweifelsfreien Bestätigung einer `execution_ref`.
8. Numerische Retention-Fristen und möglicher längerer Idempotenz-Tombstone.
9. Externer Result Store samt Zugriff, Löschung und Referenzintegrität.
10. RPO/RTO, konkrete Backup-Verschlüsselung, Offsite-Ablage und Rotation.
11. Messbare Schwellen für einen Wechsel von SQLite zu PostgreSQL.

Die fünf verschobenen Tabellen erhalten ihr endgültiges Schema erst mit den
zugehörigen fachlichen Verträgen.

## 8. Abnahmestatus

Der Architektur-Review ist abgeschlossen und der A.2-Entwurf ist
**designseitig freigegeben**. A.2.1 ist abgeschlossen. A.2.2 ist implementiert
und lokal geprüft; die unabhängige Code-Review-Abnahme steht noch aus. A.2.3
bleibt offen und benötigt eine separate Freigabe.

Das vorhandene Fundament belegt Konfigurations-, Verbindungs-, Schema-,
Migrations- und Datenbankintegritätseigenschaften, aber noch keine Repositories,
fachliche Transitionen, Recovery oder Produktionsreife.

## 9. GitHub-Referenzen

- Design-Pull-Request:
  [PR #3 – docs: design A2 persistence architecture](https://github.com/madebyzwen/oasix/pull/3)
- A.2.1-Implementierungs-Commit: `cba7be6`
- A.2.2-Implementierungs-Commit und zugehöriger CI-Lauf: werden mit diesem
  Arbeitsauftrag erzeugt
