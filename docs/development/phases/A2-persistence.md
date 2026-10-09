# Phase A.2 – Datenbankdesign und Architekturplanung

Status: **Design in Arbeit – nicht implementiert**

## 1. Ziel und Abgrenzung

Phase A.2 plant eine lokale, transaktionale und migrationsfähige Persistenz für
die OASIX-Control-Plane. Der vorgesehene Stack besteht aus Python 3.12, SQLite
im WAL-Modus, SQLAlchemy 2, Alembic und pytest.

Dieses Dokument ist ein Review-Entwurf. Es wurden weder Anwendungscode noch
Dependencies, Datenbankdateien oder Alembic-Migrationen angelegt. Queue-
Dispatch, Retry, Recovery, Lease-Ablauf und Power-Steuerung werden hier nur als
Persistenzverträge beschrieben; ihre Geschäftslogik folgt nach separater
Freigabe.

## 2. Verbindliche Anforderungen

Maßgeblich ist das
[Technical Requirement v3.4](../../OASIX_Technical_Requirement_Reviewed_v3.4.docx).

| Bereich | Verbindliche Invariante für das Design |
| --- | --- |
| ARC-01, DEP-01 | Persistenz und Control-State liegen dauerhaft auf der Control Plane. |
| JOB-01 | `job_id` bleibt stabil; `parent_job_id` und `idempotency_key` werden ohne späteren Schemaumbau ermöglicht. |
| JOB-02 | Jeder konkrete Ausführungsversuch erhält eine eigene `attempt_id`; ein Retry überschreibt keinen Attempt. |
| JOB-03 | Queue, Job-Status und Attempts überleben einen Prozessneustart. |
| JOB-04 | Schemaänderungen sind versioniert und migrationsfähig. |
| JOB-05, REC-01, REC-02 | Aktive Jobs und Attempts werden nach Neustart abgeglichen; unbestätigte Attempts bleiben nicht stillschweigend `RUNNING`. |
| REC-03, REC-04 | Job-/Wake-Retries sind begrenzt; Fehlerklassen bleiben stabil auswertbar. |
| WRK-01 bis WRK-05 | Worker startet nach Control-Plane-Start logisch als `UNKNOWN`; Readiness ist servicebezogen und Dispatch setzt Worker-/Service-Readiness voraus. |
| LSE-01 bis LSE-04 | Jede Nutzung besitzt eine Lease mit TTL/Heartbeat; die Lease Registry ist die einzige Autorität für aktive Nutzung. |
| PWR-01 | Sleep setzt keine aktive Lease, keine ausführbare/fällige Arbeit, abgelaufene Idle-Zeit und einen zulässigen Worker-State voraus. |
| Force Sleep | Neuer Dispatch wird pausiert, Grace Period und Eingriff werden festgehalten, nicht sauber beendete Attempts werden `INTERRUPTED`. |
| WAITING/EXTERNAL | Eine Lease darf erst nach bestätigter Resumability und persistierter opaker `continuation_ref` freigegeben werden. |
| OBS-01 bis OBS-04 | IDs und verfügbare Attempt-Telemetrie sind korrelierbar; fehlende Werte bleiben `NULL`; Erweiterungen dürfen stabile Kernfelder nicht ersetzen. |
| AC-04 bis AC-07 | Lease-Ablauf, Restart-Recovery, Force-Sleep-Unterbrechung und abrufbare Telemetrie müssen später testbar sein. |

Das Requirement definiert Job- und Worker-Zustände, jedoch keine vollständigen
Attempt-, Service-, Power- oder Recovery-Zustandsautomaten. Die in diesem
Dokument vorgeschlagenen zusätzlichen Statuswerte bleiben bis zur
Implementierungsfreigabe Designentscheidungen.

## 3. Architektur und Schnittstellen

### 3.1 Komponenten und Verantwortungsgrenzen

Geplant ist eine synchrone SQLAlchemy-2-Persistenzschicht innerhalb der Control
Plane:

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
  und Policies. Die Datenbank speichert keine Hostnamen, Zugangsdaten oder
  Secret-Werte.

### 3.2 SQLite gegenüber PostgreSQL

| Kriterium | SQLite mit WAL | PostgreSQL | MVP-Bewertung |
| --- | --- | --- | --- |
| Betrieb | Eingebettete Datei, kein zusätzlicher Dienst | Eigener Server, Benutzer-/Netzwerk-/Upgrade-Betrieb | SQLite ist für eine einzelne Control Plane deutlich einfacher. |
| Leser/Writer | Leser können im WAL-Modus parallel zum Writer arbeiten; es gibt aber nur einen Writer gleichzeitig. | MVCC, mehrere Writer und Zeilensperren | Kurze, seltene MVP-Schreibtransaktionen passen zu SQLite. |
| Queue-Claim | Kein `SELECT ... FOR UPDATE SKIP LOCKED`; Claim muss kurz serialisiert werden. | Row Locking und `SKIP LOCKED` eignen sich für mehrere Dispatcher. | Ein Dispatcher beziehungsweise geringe Parallelität ist MVP-Annahme. |
| Ausfallsicherheit | Lokale Datei; HA und Replikation sind nicht eingebaut. | Replikation, HA- und PITR-Ökosystem | HA ist kein aktueller MVP-Umfang. |
| Dateisystem | WAL benötigt alle Zugriffe auf demselben Host und ist für Netzwerkdateisysteme ungeeignet. | Netzwerkdienst; Storage wird vom DB-Betrieb gekapselt. | SQLite-Datei muss auf einem lokalen persistenten Volume liegen. |
| Migrationen | Viele Schemaänderungen benötigen Alembic Batch/„move and copy“. | Umfangreichere native `ALTER TABLE`-Funktionen | Für das kleine MVP-Schema vertretbar, aber migrationskritisch. |
| Backup | SQLite Backup API oder `VACUUM INTO`; WAL darf nicht ignoriert werden. | Etablierte logische/physische Backup-Werkzeuge | SQLite ist beherrschbar, wenn das Verfahren fest operationalisiert wird. |

PostgreSQL wird neu bewertet, wenn mindestens eines der folgenden Kriterien
eintritt: mehrere aktive Control-Plane-Writer, anhaltende `SQLITE_BUSY`-
Konflikte trotz kurzer Transaktionen, hoher Queue-Durchsatz, geforderte HA/
Remote-DB oder ein Backup-/RPO-Ziel, das mit einer lokalen Datei nicht
vertretbar erreichbar ist. SQLAlchemy reduziert Dialektkopplung, macht
Queue-Locking, Datentypen und Datenmigration aber nicht automatisch portabel.

Grundlagen: [SQLite WAL](https://www.sqlite.org/wal.html),
[SQLAlchemy SQLite](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html) und
[PostgreSQL MVCC](https://www.postgresql.org/docs/current/mvcc-intro.html).

### 3.3 DB-basierte Queue ohne Message-Broker

Die Queue ist keine separate Tabelle und kein zweiter Statusspeicher. Ein Job
ist ausführbar, wenn sein `status` `QUEUED` oder `RETRY_WAIT` ist und sein
`next_eligible_at` erreicht wurde. Bei einem neuen `QUEUED`-Job entspricht
dieser Wert dem frühesten gewünschten Startzeitpunkt. Ein partieller
Fälligkeitsindex unterstützt die gemeinsame Abfrage beider Statuswerte.

Der geplante Claim-Ablauf ist:

1. Eine kurze SQLite-`BEGIN IMMEDIATE`-Transaktion serialisiert konkurrierende
   Writer früh und vermeidet einen späteren Lock-Upgrade-Konflikt.
2. Der nächste fällige Job wird deterministisch nach Fälligkeit,
   Erstellungszeit und `job_id` ausgewählt.
3. Ein bedingtes Update mit `version` schützt vor einem veralteten Claim.
4. In derselben Transaktion werden Jobstatus, neuer Attempt und Job-Event
   geschrieben.
5. Nach Commit beginnt Wake-up beziehungsweise Dispatch außerhalb der
   Transaktion.

Bei Abschluss werden Attempt-Telemetrie, Attempt-Endstatus und der neue
Jobstatus atomar gespeichert. Ein Retry setzt den Job auf `RETRY_WAIT`; erst
beim nächsten tatsächlichen Claim entsteht der nächste Attempt. Ein optionaler
In-Process-Hinweis kann den Poller wecken, ist aber niemals die dauerhafte
Queue-Quelle. Nach Neustart genügt die DB-Abfrage, um fällige Arbeit wieder zu
finden.

SQLite ist dafür im MVP geeignet, solange Schreibtransaktionen kurz bleiben und
die Last niedrig ist. Es ersetzt nicht die Fähigkeiten eines Brokers für hohe
Parallelität, verteilte Consumer oder Push-Benachrichtigung.

## 4. Entscheidungen

Die maßgeblichen Einträge stehen im
[Entscheidungsregister](../decisions.md):

- OASIX-DEC-005: SQLite/WAL für den Single-Control-Plane-MVP,
- OASIX-DEC-006: DB-basierte Queue ohne zusätzlichen Broker,
- OASIX-DEC-009: explizite Transaktionen und Alembic-Migrationen,
- OASIX-DEC-010: Persistenzgrenzen und Lease-Autorität.

Dieses Phasendokument enthält das detaillierte Schema und Betriebsdesign; das
Register hält nur die langfristig relevanten Entscheidungen und Trade-offs.

## 5. Umsetzung – geplanter Designentwurf

### 5.1 Gemeinsame Datenkonventionen

- Primär-IDs werden von der Anwendung als kanonische UUID-Strings erzeugt und
  als `VARCHAR(36)` gespeichert. Die genaue UUID-Version ist noch offen.
- `worker_id`, `service_id`, Rollen und technische Arten verwenden validierte,
  generische Identifikatoren; keine Infrastrukturbezeichnung wird im Schema
  fest codiert.
- Sämtliche Zeitpunkte werden als `BIGINT`/SQLite `INTEGER` in UTC-
  Epoch-Mikrosekunden gespeichert. `NULL` bedeutet „von der Quelle nicht
  geliefert“ und wird nicht geschätzt.
- Dauern werden als nicht negative `BIGINT`-Millisekunden gespeichert.
- Statuswerte liegen als `VARCHAR` mit benannten `CHECK`-Constraints vor.
- Flexible Payloads und `extra_metrics` sind kanonisches JSON in `TEXT`; ihre
  Struktur und Größe werden vor dem Schreiben validiert.
- Alle Foreign Keys und Constraints erhalten stabile Namen. Foreign Keys sind
  standardmäßig `ON DELETE RESTRICT`; reine Zuordnungstabellen dürfen bei einer
  später explizit freigegebenen Job-Löschung kaskadieren.
- Es gibt keine persistierten Aktivitätszähler. Ableitbare Werte wie aktive
  Leases oder laufende Attempts werden abgefragt.

### 5.2 Beziehungsübersicht

```text
jobs ──< attempts ──< leases
  │          │
  ├──< job_events
  ├──< job_dependencies >── jobs
  └──< job_service_requirements

worker_states ──< service_states
       ├──< attempts
       ├──< leases
       └──< power_operations ──< power_attempts

control_state: genau eine globale Zeile
```

### 5.3 Tabelle `jobs`

**Zweck:** Stabile Job-Identität, Queue-Zustand, Warten, Retry-Fälligkeit und
Resultatreferenz.

| Spalte | SQL-Typ | Null | Bedeutung |
| --- | --- | --- | --- |
| `job_id` | `VARCHAR(36)` | nein | Primärschlüssel, über alle Attempts stabil |
| `job_type` | `VARCHAR(63)` | nein | Providerneutraler Jobtyp |
| `role` | `VARCHAR(63)` | ja | Logische Agent-/Jobrolle, soweit vorhanden |
| `status` | `VARCHAR(16)` | nein | Verbindlicher Jobstatus |
| `parent_job_id` | `VARCHAR(36)` | ja | Self-FK auf übergeordneten Job |
| `idempotency_key` | `VARCHAR(255)` | ja | Optionaler Wiederholungsschutz; Scope offen |
| `request_id` | `VARCHAR(128)` | ja | Korrelation mit eingehender Anfrage |
| `input_payload_json` | `TEXT` | nein | Validierte, providerneutrale Jobeingabe |
| `result_ref` | `TEXT` | ja | Opaque Referenz auf ein Ergebnis |
| `wait_kind` | `VARCHAR(8)` | ja | `LOCAL` oder `EXTERNAL` bei `WAITING` |
| `continuation_ref` | `TEXT` | ja | Opaque, persistierte Wiederaufnahmereferenz |
| `resumability_confirmed_at` | `BIGINT` | ja | Zeitpunkt der expliziten Resumability-Bestätigung |
| `next_eligible_at` | `BIGINT` | ja | Frühester Zeitpunkt für einen Retry/erneuten Claim |
| `created_at`, `queued_at`, `updated_at` | `BIGINT` | nein | UTC-Zeitpunkte |
| `finished_at` | `BIGINT` | ja | Abschlusszeit eines terminalen Jobs |
| `version` | `INTEGER` | nein | Optimistic-Locking-Version, Startwert 1 |

Zulässige Statuswerte sind verbindlich: `QUEUED`, `RUNNING`, `WAITING`,
`RETRY_WAIT`, `BLOCKED`, `DONE`, `FAILED`, `INTERRUPTED`.

Constraints und Indizes:

- PK `job_id`; Self-FK `parent_job_id` mit `RESTRICT`.
- `CHECK(version >= 1)` und `CHECK(parent_job_id IS NULL OR parent_job_id <> job_id)`.
- `resumability_confirmed_at` setzt `wait_kind = 'EXTERNAL'` und eine nicht leere
  `continuation_ref` voraus.
- Provisorischer partieller Unique-Index auf `idempotency_key`, wenn nicht
  `NULL`; der endgültige Scope ist eine offene Frage.
- Partieller Queue-Index auf `(next_eligible_at, created_at, job_id)` für
  `status IN ('QUEUED', 'RETRY_WAIT')`; für beide Statuswerte ist
  `next_eligible_at` verpflichtend, sonst `NULL`.
- Index auf `parent_job_id` und optional `request_id`.

### 5.4 Tabellen `job_dependencies` und `job_service_requirements`

`job_dependencies` bildet lokale Warteabhängigkeiten ohne JSON-Auswertung ab:

| Spalte | SQL-Typ | Bedeutung |
| --- | --- | --- |
| `job_id` | `VARCHAR(36)` | FK auf wartenden Job |
| `depends_on_job_id` | `VARCHAR(36)` | FK auf vorausgesetzten Job |
| `created_at` | `BIGINT` | UTC-Zeitpunkt |

Der zusammengesetzte PK lautet `(job_id, depends_on_job_id)`; Selbstbezüge sind
verboten. Ein zusätzlicher Index auf `depends_on_job_id` unterstützt das
Aufwecken abhängiger Jobs.

`job_service_requirements` persistiert die für den Dispatch erforderlichen
generischen Service-IDs:

| Spalte | SQL-Typ | Bedeutung |
| --- | --- | --- |
| `job_id` | `VARCHAR(36)` | FK auf Job |
| `service_id` | `VARCHAR(63)` | Generische Service-ID aus der Runtime-Konfiguration |
| `created_at` | `BIGINT` | UTC-Zeitpunkt |

Der PK ist `(job_id, service_id)`. Die Runtime-Konfiguration bleibt Quelle für
Endpoint und Probe; diese Tabelle speichert nur die Anforderung des Jobs.

### 5.5 Tabelle `attempts`

**Zweck:** Unveränderliche Identität jedes konkreten Ausführungsversuchs,
Recovery-Referenz, Ergebnis und zuordenbare Telemetrie.

| Spalte | SQL-Typ | Null | Bedeutung |
| --- | --- | --- | --- |
| `attempt_id` | `VARCHAR(36)` | nein | Primärschlüssel |
| `job_id` | `VARCHAR(36)` | nein | FK auf stabilen Job |
| `attempt_number` | `INTEGER` | nein | Bei 1 beginnende Sequenz je Job |
| `worker_id` | `VARCHAR(63)` | nein | FK auf `worker_states` |
| `status` | `VARCHAR(16)` | nein | Vorgeschlagener Attempt-Zustand |
| `execution_ref` | `TEXT` | ja | Opaque Worker-/Runtime-Referenz für Recovery |
| `agent_role`, `model` | `VARCHAR(255)` | ja | Tatsächlich verwendete Rolle beziehungsweise Modellkennung |
| `created_at`, `updated_at` | `BIGINT` | nein | UTC-Zeitpunkte |
| `dispatched_at`, `started_at`, `first_token_at`, `finished_at` | `BIGINT` | ja | Attempt-Zeitpunkte, soweit geliefert |
| `queue_duration_ms`, `wake_duration_ms`, `execution_duration_ms` | `BIGINT` | ja | Nicht negative Dauerwerte |
| `input_tokens`, `output_tokens`, `total_tokens` | `BIGINT` | ja | Nicht negative Tokenwerte, nicht geschätzt |
| `tool_calls_count`, `tool_duration_ms` | `BIGINT` | ja | Tool-Telemetrie, soweit verfügbar |
| `outcome_code` | `VARCHAR(63)` | ja | Providerneutraler Endcode |
| `error_class` | `VARCHAR(16)` | ja | Stabile Fehlerklasse |
| `error_code` | `VARCHAR(128)` | ja | Maschinenlesbarer, redigierter Code |
| `error_detail_redacted` | `TEXT` | ja | Optionale redigierte Diagnose, nie Secret/Rohantwort |
| `extra_metrics_json` | `TEXT` | ja | Erweiterbare optionale Metriken |
| `version` | `INTEGER` | nein | Optimistic-Locking-Version |

Vorgeschlagene Attempt-Statuswerte sind `CREATED`, `DISPATCHING`, `RUNNING`,
`WAITING`, `SUCCEEDED`, `FAILED`, `TIMED_OUT`, `INTERRUPTED`. Diese Werte sind
nicht vollständig durch v3.4 vorgegeben und benötigen Review.

Fehlerklassen entsprechen REC-04: `CONFIG`, `WAKE`, `READINESS`, `LLM`,
`AGENT`, `TOOL`, `TIMEOUT`; eine generische `INTERNAL`-Klasse wird als offene
Ergänzung vorgeschlagen.

Constraints und Indizes:

- FK `job_id` und `worker_id` mit `RESTRICT`.
- Unique `(job_id, attempt_number)`.
- Partieller Unique-Index für höchstens einen nicht terminalen Attempt pro Job.
- Partieller Unique-Index `(worker_id, execution_ref)`, wenn eine Referenz
  vorhanden ist.
- Indizes `(status, updated_at)`, `(worker_id, status)` und `(job_id, created_at)`.
- `CHECK(attempt_number >= 1)`, nicht negative Metriken und logisch geordnete
  Zeitpunkte, soweit beide Werte vorhanden sind.

### 5.6 Tabelle `job_events`

**Zweck:** Append-only Audit- und Zustandsverlauf für Diagnose, Force Sleep und
Recovery, ohne frühere Attempts oder Fehlermessungen zu überschreiben.

| Spalte | SQL-Typ | Bedeutung |
| --- | --- | --- |
| `event_id` | `INTEGER` | Autoincrement-PK, nur lokale Reihenfolge |
| `job_id` | `VARCHAR(36)` | FK auf Job |
| `attempt_id` | `VARCHAR(36)` | Optionaler FK auf Attempt |
| `event_type` | `VARCHAR(63)` | Stabiler technischer Ereignistyp |
| `from_status`, `to_status` | `VARCHAR(16)` | Optionale Zustandsänderung |
| `error_class`, `error_code` | `VARCHAR` | Optionale redigierte Klassifikation |
| `metadata_json` | `TEXT` | Redigierte Zusatzdaten ohne Secrets |
| `occurred_at` | `BIGINT` | UTC-Zeitpunkt |

Indizes liegen auf `(job_id, occurred_at, event_id)` und `attempt_id`. Die
Retention ist noch festzulegen; Ereignisse ersetzen keine strukturierten Logs.

### 5.7 Tabelle `leases`

**Zweck:** Einzige persistente Autorität für aktive Worker-Nutzung.

| Spalte | SQL-Typ | Null | Bedeutung |
| --- | --- | --- | --- |
| `lease_id` | `VARCHAR(36)` | nein | Primärschlüssel |
| `worker_id` | `VARCHAR(63)` | nein | FK auf `worker_states` |
| `owner` | `VARCHAR(128)` | nein | Technischer Owner, keine Zugangsdaten |
| `purpose` | `VARCHAR(63)` | nein | Nutzung wie Inference, Agent, Job, Build, Test oder Development |
| `job_id`, `attempt_id` | `VARCHAR(36)` | ja | Optionale Korrelation |
| `created_at`, `last_heartbeat_at`, `expires_at` | `BIGINT` | nein | UTC-Zeitpunkte für TTL/Heartbeat |
| `released_at` | `BIGINT` | ja | Explizite Freigabe |
| `release_reason` | `VARCHAR(63)` | ja | Redigierter technischer Grund |

Eine Lease ist genau dann aktiv, wenn `released_at IS NULL` und
`expires_at > now_utc`. Es gibt bewusst weder Statusspalte noch
`active_lease_count`. Abgelaufene, noch nicht bereinigte Zeilen sind bereits
inaktiv.

Constraints und Indizes:

- FKs auf Worker sowie optional Job/Attempt mit `RESTRICT`.
- `CHECK(expires_at > created_at)`, Heartbeat nicht vor Erstellung und Release
  nicht vor Erstellung.
- Index `(worker_id, released_at, expires_at)` für den Sleep-Gate-Check.
- Indizes auf `(owner, expires_at)`, `job_id` und `attempt_id`.

Acquire, Heartbeat, Release und Expiry-Cleanup sind jeweils eigene kurze
Transaktionen. Bei `WAITING/EXTERNAL` dürfen Bestätigung,
`continuation_ref`-Persistierung und Lease-Freigabe nur in derselben fachlichen
Transaktion erfolgen.

### 5.8 Tabellen `worker_states` und `service_states`

`worker_states` speichert ausschließlich beobachteten Betriebszustand, keine
Verbindungs- oder Hardwaredaten:

| Spalte | SQL-Typ | Bedeutung |
| --- | --- | --- |
| `worker_id` | `VARCHAR(63)` | PK, generische ID aus der Konfiguration |
| `state` | `VARCHAR(16)` | `UNKNOWN`, `WAKING`, `READY`, `BUSY`, `IDLE`, `SLEEPING`, `UNAVAILABLE` |
| `observed_at`, `state_changed_at`, `updated_at` | `BIGINT` | UTC-Zeitpunkte |
| `idle_since`, `last_ready_at` | `BIGINT` | Optionale abgeleitete Zeitpunkte |
| `last_error_class`, `last_error_code` | `VARCHAR` | Redigierte letzte Beobachtung |
| `version` | `INTEGER` | Optimistic-Locking-Version |

Indizes: `(state, updated_at)` und `idle_since`. Nach jedem Prozessstart wird
der aktuelle Zustand vor weiteren Entscheidungen auf `UNKNOWN` gesetzt; ein
alter `READY`-Wert gilt niemals als aktuelle Readiness. `BUSY` ist nur ein
beobachteter Zustand und niemals ein Ersatz für die Lease-Abfrage.

`service_states` bildet die servicebezogene Readiness ab:

| Spalte | SQL-Typ | Bedeutung |
| --- | --- | --- |
| `worker_id`, `service_id` | `VARCHAR(63)` | Zusammengesetzter PK; Worker-FK |
| `state` | `VARCHAR(16)` | Vorgeschlagen: `UNKNOWN`, `CHECKING`, `READY`, `NOT_READY`, `UNAVAILABLE` |
| `observed_at`, `last_ready_at`, `updated_at` | `BIGINT` | UTC-Zeitpunkte |
| `latency_ms` | `BIGINT` | Optionale Probe-Laufzeit |
| `last_error_class`, `last_error_code` | `VARCHAR` | Redigierter Fehler |
| `version` | `INTEGER` | Optimistic-Locking-Version |

Der Service-Zustandsautomat ist eine offene Detailentscheidung. Persistierte
Readiness muss nach Restart durch neue Probes bestätigt werden.

### 5.9 Tabelle `control_state`

**Zweck:** Globaler, persistenter Steuerungs- und Recovery-Zustand der einen
Control Plane.

| Spalte | SQL-Typ | Bedeutung |
| --- | --- | --- |
| `singleton_id` | `SMALLINT` | PK mit `CHECK(singleton_id = 1)` |
| `dispatch_mode` | `VARCHAR(24)` | Vorgeschlagen: `ACTIVE`, `PAUSED_RECOVERY`, `PAUSED_SLEEP`, `PAUSED_ADMIN` |
| `recovery_status` | `VARCHAR(16)` | Vorgeschlagen: `CLEAN`, `REQUIRED`, `RUNNING`, `FAILED` |
| `process_instance_id` | `VARCHAR(36)` | Kennung des aktuellen Starts |
| `started_at`, `updated_at` | `BIGINT` | UTC-Zeitpunkte |
| `last_clean_shutdown_at` | `BIGINT` | Optionaler letzter sauberer Shutdown |
| `version` | `INTEGER` | Optimistic-Locking-Version |

Die Initialmigration legt genau eine Zeile an. Ein Prozessstart setzt Dispatch
zunächst auf `PAUSED_RECOVERY`; nur erfolgreicher Abgleich darf auf `ACTIVE`
wechseln. `control_state` enthält keine Lease- oder Job-Zähler.

### 5.10 Tabellen `power_operations` und `power_attempts`

**Zweck:** Persistente Wake-/Sleep-Steuerung, begrenzte Wake-Retries,
Force-Sleep-Grace-Period und Auditierbarkeit.

`power_operations` enthält:

| Spalte | SQL-Typ | Bedeutung |
| --- | --- | --- |
| `operation_id` | `VARCHAR(36)` | PK |
| `worker_id` | `VARCHAR(63)` | FK auf Worker |
| `trigger_job_id` | `VARCHAR(36)` | Optionaler auslösender Job |
| `kind` | `VARCHAR(16)` | `WAKE`, `AUTO_SLEEP`, `MANUAL_SLEEP`, `FORCE_SLEEP` |
| `status` | `VARCHAR(16)` | Vorgeschlagen: `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `TIMED_OUT`, `CANCELLED` |
| `requested_by`, `reason_redacted` | `TEXT` | Auditkontext ohne Zugangsdaten |
| `requested_at`, `started_at`, `grace_deadline_at`, `finished_at` | `BIGINT` | UTC-Zeitpunkte |
| `error_class`, `error_code` | `VARCHAR` | Redigierter Fehler |
| `version` | `INTEGER` | Optimistic-Locking-Version |

Ein partieller Unique-Index begrenzt nicht terminale Power-Operationen auf eine
pro Worker. Indizes liegen auf `(worker_id, status)` und `requested_at`.

`power_attempts` speichert jeden konkreten Wake-/Sleep-Versuch separat:

| Spalte | SQL-Typ | Bedeutung |
| --- | --- | --- |
| `power_attempt_id` | `VARCHAR(36)` | PK |
| `operation_id` | `VARCHAR(36)` | FK auf Power-Operation |
| `attempt_number` | `INTEGER` | Bei 1 beginnende Sequenz |
| `status` | `VARCHAR(16)` | `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `TIMED_OUT`, `INTERRUPTED` |
| `started_at`, `finished_at`, `duration_ms` | `BIGINT` | Zeitmessung |
| `error_class`, `error_code` | `VARCHAR` | Redigierter Fehler |

Unique `(operation_id, attempt_number)` verhindert Überschreiben früherer
Versuche. Force Sleep setzt `control_state.dispatch_mode` und die Operation in
einer Transaktion, bevor die Grace Period außerhalb der Transaktion abgewartet
wird.

### 5.11 Engine, Sessions und SQLite-Pragmas

Die geplante Factory akzeptiert einen validierten absoluten Datenbankpfad und
erzeugt daraus intern die SQLite-URL. Beliebige URLs oder Zugangsdaten werden
nicht übernommen. Der Pfad soll künftig als Control-Plane-Storage-Einstellung
in der externen Runtime-Konfiguration stehen; ob dies `schema_version: 2`
erfordert, ist vor Implementierung zu entscheiden.

Die Datei muss auf einem lokalen persistenten Volume liegen. Das Verzeichnis
muss das Erzeugen der SQLite-Datei sowie der `-wal`- und `-shm`-Dateien erlauben
und restriktive Rechte besitzen. Die Datenbank darf weder im Secret-Verzeichnis
noch auf NFS/SMB oder einem anderen Netzwerkdateisystem liegen.

Geplante Verbindungsinitialisierung:

- Python-3.12-`sqlite3` mit nicht legacyhaftem Transaktionsmodus;
- `PRAGMA foreign_keys=ON` auf jeder Verbindung und Verifikation des Ergebnisses;
- persistentes `PRAGMA journal_mode=WAL` bei Initialisierung und Prüfung, dass
  SQLite tatsächlich `wal` zurückliefert;
- `PRAGMA busy_timeout=5000` als vorläufiger, später validierter Wert auf jeder
  Verbindung;
- `PRAGMA synchronous=FULL` als sicherheitsorientierter MVP-Default;
- kleiner begrenzter Connection Pool; konkrete Poolgröße bleibt bis zum
  Lasttest offen.

Foreign-Key-Aktivierung und Python-3.12-Transaktionsmodus müssen anhand der
[SQLAlchemy-SQLite-Hinweise](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)
implementiert und getestet werden. Unbekannte oder nicht wirksame Pragmas
dürfen nicht stillschweigend akzeptiert werden; kritische Werte werden nach dem
Setzen zurückgelesen.

Die Session Factory verwendet SQLAlchemy-2-Stil und `expire_on_commit=False`.
Normale Use Cases verwenden `Session.begin()`; der Queue-Claim erhält eine
kleine, separat getestete `BEGIN IMMEDIATE`-Primitive. Ein `SQLITE_BUSY` wird
nur begrenzt und mit kurzem Backoff erneut versucht und anschließend als
operativer Persistenzfehler gemeldet. DB-Lock-Retries sind nicht identisch mit
der fachlichen Job-Retry-Policy.

### 5.12 Transaktionsgrenzen

Folgende Änderungen sind jeweils atomar geplant:

- **Job anlegen:** Job, Service-Anforderungen, Abhängigkeiten und initiales
  Event.
- **Job claimen:** Status-/Versionswechsel, neuer Attempt und Claim-Event.
- **Attempt abschließen:** Attempt-Endstatus/Telemetrie, Job-Endstatus oder
  `RETRY_WAIT`, `next_eligible_at` und Event.
- **Lease:** Acquire, einzelner Heartbeat sowie Release/Expiry-Cleanup jeweils
  als kurze Transaktion.
- **Sleep Gate:** Dispatch pausieren und innerhalb derselben Transaktion erneut
  aktive Leases, fällige Jobs, Idle-Zeit und Worker-State prüfen. Erst danach
  erfolgt der externe Sleep-Aufruf.
- **Force Sleep:** Dispatch-Pause und Power-Operation atomar persistieren;
  spätere Interrupt-Markierungen werden nach der Grace Period in einer neuen
  Transaktion geschrieben.
- **Recovery-Start:** `PAUSED_RECOVERY`, Prozessinstanz und Worker `UNKNOWN`
  atomar setzen, bevor reale Probes beginnen.

Lange Wartezeiten, Polling und externe Kommunikation dürfen keine
DB-Transaktion offenhalten.

### 5.13 Alembic und Upgrade-Strategie

Die geplante Initialmigration erstellt alle Tabellen, benannten Constraints,
Indizes, die `control_state`-Singleton-Zeile und Alembics eigene
`alembic_version`. Das Produktionsschema wird nicht über
`Base.metadata.create_all()` erzeugt.

- Es gibt zunächst genau einen linearen Alembic-Head.
- Autogenerate ist nur ein Entwurfswerkzeug; jede Migration wird manuell auf
  Constraints, Indizes, Datenmigration und Downtime geprüft.
- `render_as_batch=True` und benannte Constraints bereiten SQLite-
  Tabellenumbauten vor. Die
  [Alembic-Batch-Dokumentation](https://alembic.sqlalchemy.org/en/latest/batch.html)
  weist auf „move and copy“ und Foreign-Key-Besonderheiten hin.
- Muss eine Batch-Migration die Foreign-Key-Prüfung vorübergehend deaktivieren,
  geschieht dies ausschließlich auf der exklusiven Migrationsverbindung. Die
  Prüfung wird noch vor Freigabe der Datenbank reaktiviert und durch
  `foreign_key_check` verifiziert.
- Migrationen laufen vor dem Start von API, Dispatcher und Recovery exklusiv.
  Mehrere Prozesse dürfen nicht gleichzeitig migrieren.
- Vor destruktiven oder Tabellen kopierenden Migrationen wird ein geprüftes
  Backup erstellt. Danach folgen `foreign_key_check`, `integrity_check` und ein
  Schema-Smoke-Test.
- Die Anwendung schlägt geschlossen fehl, wenn DB-Revision und erwarteter
  Alembic-Head nicht übereinstimmen.
- Downgrades werden in Entwicklung getestet, sind aber keine garantierte
  Produktionsrollback-Strategie. Produktion rollt bei nicht sicher reversiblen
  Änderungen auf Anwendungsversion plus Backup zurück.

### 5.14 Verhalten nach Prozessneustart

Der spätere Startup-/Recovery-Ablauf ist geplant als:

1. Runtime-Konfiguration und Secrets vollständig validieren.
2. Datenbank öffnen, Pragmas prüfen und Alembic-Revision validieren.
3. Dispatch als `PAUSED_RECOVERY` markieren, neue Prozessinstanz persistieren
   und Worker-/Service-State auf `UNKNOWN` setzen.
4. Abgelaufene Leases bei Aktivitätsabfragen sofort ignorieren und später
   kontrolliert als abgelaufen bereinigen.
5. Nicht terminale Jobs, Attempts und Power-Operationen laden.
6. Worker und erforderliche Services real prüfen; `execution_ref` verwenden,
   um laufende Ausführung zweifelsfrei zu bestätigen.
7. Nicht bestätigbare Attempts in einer später implementierten Recovery-
   Transaktion `INTERRUPTED` setzen und den Job gemäß Retry-Policy kontrolliert
   auf `RETRY_WAIT`, `BLOCKED` oder `FAILED` überführen.
8. Dispatch erst nach erfolgreichem Abgleich aktivieren.

Dieses Dokument definiert nur die persistierbaren Voraussetzungen. Probe-,
Klassifikations- und Recovery-Geschäftslogik wird nicht in diesem Designschritt
implementiert.

### 5.15 Backup und Restore unter WAL

Eine laufende WAL-Datenbank darf nicht durch Kopieren nur der Hauptdatei
gesichert werden: Die WAL-Datei ist Teil des persistenten Zustands. Bevorzugt
wird ein konsistenter Online-Snapshot über Pythons Zugriff auf die
[SQLite Backup API](https://www.sqlite.org/backup.html). `VACUUM INTO` ist eine
zweite, stärker I/O-/CPU-lastige Option für einen kompakten konsistenten
Snapshot.

Backup-Ablauf:

1. Ziel auf ein getrenntes, zugriffsgeschütztes Volume schreiben.
2. SQLite Backup API mit Busy-Handling verwenden; keine rohe Live-Dateikopie.
3. Ziel schließen, Prüfsumme und Dateirechte setzen.
4. Backup mit `integrity_check`, `foreign_key_check` und erwarteter
   `alembic_version` validieren.
5. Backup erst danach als erfolgreich markieren und gemäß noch festzulegender
   Retention rotieren.

Alternativ ist bei gestoppter Control Plane eine kontrollierte Checkpoint-/
Close-Sequenz mit anschließender Dateikopie zulässig. Restore erfolgt nur bei
gestoppter Anwendung in ein leeres Datenverzeichnis, danach folgen
Integritäts-/Revisionsprüfung und der normale Recovery-Start. RPO, RTO,
Backup-Verschlüsselung, Offsite-Ablage und Retention sind offene Betriebsfragen.

## 6. Tests und Nachweise

In diesem Designschritt wurden keine Persistenztests implementiert und keine
SQLite-Datei erzeugt. Die bestehende A.1-Suite bleibt der einzige aktuelle
Anwendungsnachweis.

Für die spätere Implementierung ist mindestens folgende Testmatrix vorgesehen:

- frische Initialmigration und Upgrade von jeder unterstützten Revision,
- benannte Constraints, Foreign-Key-Enforcement und ungültige Statuswerte,
- WAL-/Busy-Timeout-/Transaktionskonfiguration je Verbindung,
- atomarer Job-Claim bei konkurrierenden Sessions ohne doppelten Attempt,
- Retry erzeugt eine neue Attempt-Zeile und erhält frühere Telemetrie,
- Crash/Rollback an jeder Transaktionsgrenze ohne Teilzustand,
- Lease Acquire/Heartbeat/Release/Expiry und Sleep Gate ohne Aktivitätszähler,
- `WAITING/EXTERNAL` mit atomarer Resumability-/Continuation-/Lease-Regel,
- Restart mit `UNKNOWN`, pausiertem Dispatch und nicht bestätigtem Attempt,
- Force-Sleep-Grace-Period und `INTERRUPTED`-Markierung,
- Backup einer aktiven WAL-Datenbank und Restore in eine leere Umgebung,
- migrationsbedingte SQLite-Batch-Umbauten mit Daten- und Constraint-Erhalt,
- Linux-CI ohne reale Infrastruktur oder produktive Daten.

SQLite-Concurrency-Tests müssen echte getrennte Verbindungen und temporäre
Dateidatenbanken verwenden; eine einzelne In-Memory-Verbindung belegt das
Locking-Verhalten nicht.

## 7. Einschränkungen, Risiken und offene Architekturfragen

### Risiken und Trade-offs

- SQLite serialisiert Writer. Lange Transaktionen oder hohe Schreiblast können
  `SQLITE_BUSY` und Dispatch-Latenz verursachen.
- Lange Reader können WAL-Checkpoints verzögern und die WAL-Datei wachsen
  lassen; Monitoring und ein späterer Checkpoint-Betriebsplan sind nötig.
- WAL ist für Netzwerkdateisysteme ungeeignet und bietet keine eingebaute HA.
- SQLite-Batch-Migrationen kopieren Tabellen und benötigen Speicherplatz sowie
  ein exklusives Wartungsfenster.
- Lease-TTL basiert nach Neustart auf der UTC-Wanduhr. Zeitsynchronisation und
  Verhalten bei Uhrsprüngen sind betriebliche Voraussetzungen.
- Job-Payload, `continuation_ref`, Fehlerdetails und Metriken können sensible
  Nutzdaten enthalten. Redigierung, Größenlimits, Retention und gegebenenfalls
  Verschlüsselung müssen vor Implementierung festgelegt werden.
- Ein SQLAlchemy-Abstraktionslayer garantiert keine verlustfreie spätere
  Migration zu PostgreSQL.

### Vor Implementierung zu klären

1. Welche UUID-Version wird für neue IDs verwendet?
2. Ist `idempotency_key` global eindeutig oder nach Client/Owner/Jobtyp
   gescoped?
3. Welche Attempt-, Service-, Power- und Recovery-Statusübergänge sind exakt
   zulässig, insbesondere für `WAITING`?
4. Benötigt die MVP-Queue Prioritäten, Deadlines oder Fairnessregeln jenseits
   deterministischem FIFO nach Fälligkeit?
5. Wird die Retry-Policy bei Joberstellung eingefroren oder gilt nach Neustart
   die jeweils aktuelle Runtime-Konfiguration?
6. Wie groß dürfen Job-Payload, Resultatreferenz, `continuation_ref`,
   Fehlerdetails und `extra_metrics` werden, und wie lange werden sie gehalten?
7. Wird der Datenbankpfad in einer neuen Runtime-`schema_version` eingeführt,
   und welche Pfad-/Berechtigungsprüfungen sind verbindlich?
8. Welche Busy-Timeout-, Pool- und DB-Lock-Retry-Werte bestehen den realen
   Lasttest?
9. Welche Heartbeat-/TTL-Werte, Purpose-Bezeichner und Owner-Formate gelten für
   Leases?
10. Welches Worker-Protokoll bestätigt eine `execution_ref` nach Restart
    zweifelsfrei?
11. Welche Retention gilt für Jobs, Events, Attempts, Leases und
    Power-Operationen, ohne Recovery/Audit zu beschädigen?
12. Welche verbindlichen RPO-/RTO-, Verschlüsselungs- und Offsite-Anforderungen
    gelten für Backup und Restore?
13. Ab welcher gemessenen Last oder Betriebsanforderung wird PostgreSQL
    verpflichtend?

Diese Punkte sind keine stillschweigenden Implementierungsannahmen. Sie werden
im Review entschieden oder ausdrücklich in die jeweilige spätere Phase
verschoben.

## 8. Abnahmestatus

Der Designentwurf ist vollständig dokumentiert, aber **nicht zur Implementierung
freigegeben**. A.2 bleibt „Design in Arbeit“, bis Schema, Statusautomaten,
Konfigurationsort, Retention und Backup-Anforderungen reviewt wurden.

Es gibt keine Aussage über funktionierende Persistenz, Migrationen, Recovery
oder Produktionsreife. Nach Review ist eine separate Freigabe für Anwendungscode,
Dependencies, Initialmigration und Tests erforderlich.

## 9. GitHub-Referenzen

- Commits: für diesen Designschritt noch keine
- Pull Requests: für diesen Designschritt noch keine
- CI-Läufe: für diesen Designschritt noch keine

Die Referenzen werden erst nach einem tatsächlichen Commit beziehungsweise Pull
Request ergänzt; es werden keine zukünftigen Links vorweggenommen.
