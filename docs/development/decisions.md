# Entscheidungsregister

Dieses Register dokumentiert verbindliche Architekturvorgaben und im Projekt
getroffene Implementierungsentscheidungen. Der Status „geplant“ bezeichnet
ausdrücklich noch nicht implementierte Entscheidungen.

## OASIX-DEC-001 – Trennung von Control Plane und Compute Worker

- **Status:** Verbindlich; durch A.1-Konfiguration und A.3.1-Verträge abgebildet
- **Kontext:** Steuerung und persistenter Zustand müssen dauerhaft verfügbar
  bleiben, während rechenintensive Arbeit auf einem austauschbaren Worker läuft.
- **Gewählte Lösung:** Die Control Plane verantwortet Orchestrierung und Zustand.
  KI-Inferenz, Agenten und rechenintensive Tools werden ausschließlich auf dem
  aktiven Compute Worker ausgeführt.
- **Begründung:** Die Trennung schützt stabile Client-Schnittstellen und erlaubt
  den Austausch der Worker-Infrastruktur ohne Änderungen an der Kernlogik.
- **Berücksichtigte Alternativen:** Eine gemeinsame Control-/Compute-Runtime
  widerspricht ARC-01 und ARC-02 und wurde ausgeschlossen.
- **Konsequenzen und Trade-offs:** A.3.1 modelliert Kommunikation und Readiness
  nur als technische Verträge. Produktive Adapter, Zustandslogik und Recovery
  bleiben noch zu implementieren.
- **Quellen:** Requirement ARC-01 bis ARC-04, DEP-01 bis DEP-04;
  [AGENTS.md](../../AGENTS.md)

## OASIX-DEC-002 – Externe, vollständig validierte Runtime-Konfiguration

- **Status:** Akzeptiert und in A.1 implementiert
- **Kontext:** Worker-Ziele, Dienste und Policies sind installationsabhängig und
  dürfen nicht in Anwendungscode oder öffentliche Schnittstellen gelangen.
- **Gewählte Lösung:** Eine minimale Bootstrap-Konfiguration bestimmt nur YAML-
  und Secret-Quelle. Pydantic-v2-Modelle validieren die gesamte Runtime-
  Konfiguration mit `schema_version`, `active_worker`, Worker-Profilen, Services
  und Policies; unbekannte Felder sind verboten.
- **Begründung:** Startup-Validierung verhindert teilweise initialisierten
  Zustand und hält Installationsdetails außerhalb der Geschäftslogik.
- **Berücksichtigte Alternativen:** Einzelne Runtime-Werte aus
  Umgebungsvariablen oder fest codierte Worker-Daten wurden verworfen.
- **Konsequenzen und Trade-offs:** Änderungen erfordern im MVP einen
  kontrollierten Neustart; Hot Reload ist nicht implementiert.
- **Quellen:** Requirement CFG-01, CFG-02, CFG-04 bis CFG-06;
  [bootstrap.py](../../src/oasix/config/bootstrap.py),
  [models.py](../../src/oasix/config/models.py),
  [loader.py](../../src/oasix/config/loader.py)

## OASIX-DEC-003 – Secret-Referenzen statt Klartext-Secrets

- **Status:** Akzeptiert und in A.1 implementiert
- **Kontext:** Zugangsdaten dürfen weder in der Hauptkonfiguration noch in
  Fehlern oder Debug-Repräsentationen erscheinen.
- **Gewählte Lösung:** Runtime-Konfigurationen enthalten ausschließlich
  `SecretReference`-Objekte. Ein providerneutraler `SecretSource`-Vertrag wird
  zunächst durch eine abgesicherte dateibasierte Quelle implementiert; Werte
  werden als `SecretStr` und in einer opaken, unveränderlichen Sammlung gehalten.
- **Begründung:** Referenz und Secret-Wert bleiben getrennt und alle benötigten
  Secrets werden vor Bereitstellung der Runtime-Konfiguration aufgelöst.
- **Berücksichtigte Alternativen:** Klartextwerte in YAML und direkte Ausgabe
  von Pydantic-Validierungsfehlern wurden aus Sicherheitsgründen ausgeschlossen.
- **Konsequenzen und Trade-offs:** Das Deployment muss ein geschütztes,
  möglichst read-only eingebundenes Secret-Verzeichnis bereitstellen. Weitere
  Secret Stores benötigen später nur eine neue `SecretSource`-Implementierung.
- **Quellen:** Requirement CFG-03, SEC-02, SEC-03 und AC-10;
  [secrets.py](../../src/oasix/config/secrets.py),
  [Phase A.1](phases/A1-configuration.md)

## OASIX-DEC-004 – Hardware- und Provider-Unabhängigkeit

- **Status:** Verbindlich; in A.1-Konfiguration und A.3.1-Verträgen implementiert
- **Kontext:** Worker-Hardware, Betriebssystem, Hostnamen, Modelle und spätere
  Agent-Runtimes müssen austauschbar bleiben.
- **Gewählte Lösung:** Generische Worker- und Service-IDs, konfigurierbare
  Verbindungs-/Power-Daten sowie logische Capabilities werden verwendet. Das
  Schema unterstützt mehrere Worker-Profile, aber genau einen `active_worker`.
- **Begründung:** Infrastrukturwechsel bleiben Konfigurationsänderungen und
  erzwingen keine Änderung stabiler APIs.
- **Berücksichtigte Alternativen:** Hardware-, Hersteller- oder
  Framework-spezifische Kernmodelle wurden ausgeschlossen.
- **Konsequenzen und Trade-offs:** Konkrete Provideradapter und
  Multi-Worker-Scheduling sind noch nicht implementiert.
- **Quellen:** Requirement ARC-03, ARC-04, AGT-01, ALIAS-01 bis ALIAS-04;
  [Konfigurationsmodelle](../../src/oasix/config/models.py),
  [Worker-Verträge](../../src/oasix/worker/contracts.py)

## OASIX-DEC-005 – SQLite als MVP-Persistenz

- **Status:** Akzeptiert; technisches Fundament und Initialschema in A.2.1/A.2.2
  implementiert, Produktionsvalidierung ausstehend
- **Kontext:** Jobs, Attempts, Queues, Leases, Control-State und zuordenbare
  Telemetrie benötigen eine transaktionale, migrationsfähige Persistenz auf der
  Control Plane.
- **Gewählte Lösung:** Für den Single-Control-Plane-MVP ist SQLite im WAL-Modus
  mit SQLAlchemy 2 und Alembic vorgesehen. Die Datenbank liegt auf einem lokalen
  Dateisystem der Control Plane; Netzwerkdateisysteme sind ausgeschlossen.
- **Begründung:** Eine lokale transaktionale Datenbank erfüllt den im Requirement
  beschriebenen MVP-Rahmen ohne zusätzlichen Datenbankdienst. WAL erlaubt
  parallele Leser während eines Schreibers; kurze Transaktionen passen zum
  erwarteten niedrigen Schreibvolumen.
- **Berücksichtigte Alternativen:** PostgreSQL unterstützt mehrere konkurrierende
  Writer, Zeilensperren, bessere horizontale Skalierung und etablierte HA-
  Verfahren, benötigt aber einen separat betriebenen Dienst. Es wird erneut
  bewertet, sobald mehrere Control-Plane-Instanzen, hohe Schreiblast oder HA
  erforderlich werden.
- **Konsequenzen und Trade-offs:** SQLite bleibt Single-Writer. Schreibende
  Transaktionen müssen kurz sein, Lock-Konflikte begrenzt behandelt und
  WAL-sichere Backups verwendet werden. SQLAlchemy kapselt den Zugriff, ersetzt
  aber keine spätere Datenmigration zu PostgreSQL. A.2.1 initialisiert eine
  leere SQLite-WAL-Datei, ohne ein Anwendungsschema anzulegen. Die separat
  auszuführende A.2.2-Initialmigration ist auf sechs fachliche Kerntabellen
  begrenzt.
- **Quellen:** Requirement JOB-03, JOB-04 und Abschnitt 13;
  [Phase-A.2-Design](phases/A2-persistence.md),
  [Persistence-Initialisierung](../../src/oasix/persistence/database.py),
  [Initialmigration](../../alembic/versions/0001_a2_2_initial_persistence.py),
  [SQLite-WAL-Dokumentation](https://www.sqlite.org/wal.html)

## OASIX-DEC-006 – Kein zusätzlicher Message-Broker im MVP

- **Status:** Verbindliche MVP-Vorgabe; Queue-Ausgestaltung vorgeschlagen und
  noch nicht implementiert
- **Kontext:** Die asynchrone Jobverwaltung soll persistent sein, ohne für das
  MVP einen weiteren Infrastrukturservice vorauszusetzen.
- **Gewählte Lösung:** Die Queue wird als Abfrage auf `jobs.status` und
  `next_eligible_at` modelliert. Es gibt keine zweite Queue-Tabelle. Ein kurzer,
  serialisierter Claim-Vorgang aktualisiert den Job und legt den nächsten
  Attempt atomar an; Netzwerk- oder Worker-Aufrufe finden erst nach Commit statt.
- **Begründung:** Dies hält Deployment und Betrieb der ersten Ausbaustufe
  schlank.
- **Berücksichtigte Alternativen:** Ein externer Broker ist nicht grundsätzlich
  für spätere Phasen ausgeschlossen, für den MVP aber nicht vorgesehen.
- **Konsequenzen und Trade-offs:** SQLite bietet keine Broker-Benachrichtigung
  und keine Zeilensperren. Der Dispatcher benötigt Polling und für den Claim
  eine kurze `BEGIN IMMEDIATE`-Transaktion. Hohe Queue-Parallelität wäre ein
  Wechselkriterium zu PostgreSQL oder einem späteren Broker.
- **Quellen:** Requirement Abschnitt 1.1 und JOB-03 bis JOB-05;
  [Phase-A.2-Design](phases/A2-persistence.md)

## OASIX-DEC-007 – GitHub Actions als unabhängige Linux-CI

- **Status:** Akzeptiert, implementiert und in `main` integriert
- **Kontext:** Die initiale Entwicklung erfolgt lokal auf macOS; die
  Konfigurationsschicht muss zusätzlich reproduzierbar unter Linux geprüft
  werden.
- **Gewählte Lösung:** Ein minimaler Workflow verwendet `ubuntu-latest`, Python
  3.12, SHA-fixierte offizielle Actions und führt pytest, Ruff-Linting,
  Ruff-Formatprüfung sowie einen Import-Smoke-Test aus.
- **Begründung:** Linux-Prüfungen sind von der lokalen Entwicklungsumgebung
  unabhängig und benötigen keine selbst gehosteten Runner oder Secrets.
- **Berücksichtigte Alternativen:** Lokale Tests allein decken die Zielplattform
  nicht ab; zusätzliche CI-Dienste oder Deployment-Schritte sind für A.1 nicht
  erforderlich.
- **Konsequenzen und Trade-offs:** Die Pipeline installiert Abhängigkeiten aus
  öffentlichen Paketquellen. Sie prüft keine Produktionsmounts oder reale
  Infrastruktur.
- **Quellen:** [ci.yml](../../.github/workflows/ci.yml),
  [PR #1](https://github.com/madebyzwen/oasix/pull/1),
  [Merge-Commit `0aea2f0`](https://github.com/madebyzwen/oasix/commit/0aea2f0c16278ee770546597c355a851d7f856b2)

## OASIX-DEC-008 – Descriptorbasierter Zugriff auf Secret-Dateien

- **Status:** Akzeptiert und in A.1.1 implementiert
- **Kontext:** Eine reine Pfadprüfung vor dem Öffnen wäre anfällig für
  Symlink-Wechsel und andere Check/Use-Lücken.
- **Gewählte Lösung:** Secret-Verzeichnis und Datei werden descriptorbasiert
  geöffnet. Typ, kanonischer Pfad sowie Device-/Inode-Identität des geöffneten
  Objekts werden geprüft; gelesen wird aus demselben Descriptor. Linux nutzt
  `/proc/self/fd`, macOS `F_GETPATH`; nicht unterstützte Plattformen schlagen
  geschlossen fehl.
- **Begründung:** Prüfung und Nutzung beziehen sich auf dasselbe geöffnete
  Objekt. Zusätzlich begrenzen `fstat` und ein beschränkter Lesepfad die Datei
  auf 1 MiB.
- **Berücksichtigte Alternativen:** `Path.resolve()` mit anschließendem separatem
  Öffnen wurde wegen der verbleibenden Race Condition verworfen.
- **Konsequenzen und Trade-offs:** Gleichzeitige In-place-Schreibzugriffe können
  nicht vollständig verhindert werden; Dateisystemrechte und read-only Mounts
  bleiben eine betriebliche Voraussetzung.
- **Quellen:** [secrets.py](../../src/oasix/config/secrets.py),
  [README](../../README.md#konfigurationsfundament),
  [Commit `c4bdf95`](https://github.com/madebyzwen/oasix/commit/c4bdf9527a117468c61132607a9baa82e8f8a0b5)

## OASIX-DEC-009 – Explizite Transaktionen und versionierte Alembic-Migrationen

- **Status:** Akzeptiert; Engine und Transaktionskontext in A.2.1, Alembic-
  Initialmigration in A.2.2 sowie Repository- und Revisionsgrenzen in A.2.3
  implementiert; fachliche Zustandsautomaten ausstehend
- **Kontext:** Job-/Attempt-Übergänge, Lease-Operationen und Recovery dürfen bei
  Abstürzen keinen teilweise aktualisierten Zustand hinterlassen. Das Schema
  muss gemäß JOB-04 migrationsfähig sein.
- **Gewählte Lösung:** Eine gekapselte SQLAlchemy-2-Engine stellt kurzlebige
  Sessions bereit. Use-Case-Services definieren Transaktionsgrenzen; Repositories
  führen kein eigenständiges `commit()` aus. Alembic ist die einzige Quelle für
  Produktionsschemaänderungen. Constraints erhalten Namen und SQLite-Umbauten
  verwenden geprüfte Batch-Migrationen. Die Initialmigration enthält genau
  `jobs`, `attempts`, `leases`, `worker_states`, `control_state` und
  `job_events`; weitere Fachtabellen folgen nur mit ihren Komponenten.
- **Begründung:** Fachlich zusammengehörige Zustandswechsel werden atomar und
  das Schema bleibt reproduzierbar versioniert.
- **Berücksichtigte Alternativen:** Implizite Commits, `create_all()` beim
  Produktionsstart und ungeprüfte Autogenerate-Migrationen wurden verworfen.
- **Konsequenzen und Trade-offs:** Migrationen laufen vor dem Anwendungsstart in
  einem exklusiven Wartungsfenster. Vor destruktiven Upgrades ist ein getestetes
  Backup erforderlich; Produktionsrollbacks erfolgen primär durch Restore.
- **Quellen:** Requirement JOB-04;
  [Phase-A.2-Design](phases/A2-persistence.md),
  [Persistence-Initialisierung](../../src/oasix/persistence/database.py),
  [Repositories](../../src/oasix/persistence/repositories.py),
  [Alembic-Umgebung](../../alembic/env.py),
  [Alembic-Batch-Dokumentation](https://alembic.sqlalchemy.org/en/latest/batch.html)

## OASIX-DEC-010 – Persistenzgrenzen und Lease-Autorität

- **Status:** Persistenzschema in A.2.2 implementiert; fachliche Nutzung und
  Abgleichlogik ausstehend
- **Kontext:** Die Control Plane benötigt persistenten Betriebszustand, darf
  aber Konfiguration nicht duplizieren oder parallele Aktivitätszähler führen.
- **Gewählte Lösung:** Die externe Runtime-Konfiguration bleibt autoritativ für
  Worker-Profile, Dienste und Policies. Die sechs Kerntabellen persistieren
  Jobs, Attempts, Leases, beobachteten Worker-State, Control-State,
  Recovery-Referenzen und zuordenbare Job-Events. Service-State,
  Job-Abhängigkeiten/-Serviceanforderungen und Power-Operationen werden erst mit
  ihren späteren Komponenten ergänzt. Aktive Nutzung wird ausschließlich aus
  nicht freigegebenen, noch nicht abgelaufenen Leases ermittelt; ein
  `active_lease_count` wird nicht gespeichert.
- **Begründung:** Diese Grenze vermeidet widersprüchliche Konfigurationskopien
  und erfüllt die Lease-Invariante aus v3.4.
- **Berücksichtigte Alternativen:** Persistierte Worker-Verbindungsdaten und
  separate Request-/Job-Aktivitätszähler wurden ausgeschlossen.
- **Konsequenzen und Trade-offs:** Beim Start müssen Konfiguration und
  persistierte IDs abgeglichen werden. Abgelaufene Leases gelten bereits vor
  ihrer späteren Bereinigung als inaktiv. Die kleinere Initialmigration
  vermeidet vorzeitig festgelegte Service-/Power-Semantik, verlangt dafür
  gezielte Folgemigrationen vor deren Nutzung. Sämtliche Zeiten werden als
  UTC-Epoch-Mikrosekunden gespeichert; eine verlässliche Control-Plane-Uhr ist
  betriebliche Voraussetzung.
- **Quellen:** Requirement CFG-02, JOB-01 bis JOB-05, LSE-01 bis LSE-04,
  OBS-01 bis OBS-04 und REC-01 bis REC-04;
  [Phase-A.2-Design](phases/A2-persistence.md),
  [Persistenzmodelle](../../src/oasix/persistence/models.py)

## OASIX-DEC-011 – Runtime-Schema Version 2 für Persistenz

- **Status:** Akzeptiert und in A.2.1 implementiert
- **Kontext:** Die bisherige Runtime-Version 1 enthält Worker, Services und
  Policies, aber keinen sicheren, extern konfigurierten Datenbankpfad. Ein
  stiller Default oder eine neue Bootstrap-Variable würde die vorhandene
  Versions- beziehungsweise Quellengrenze verletzen.
- **Gewählte Lösung:** Runtime-`schema_version: 2` ergänzt eine strikt
  validierte `persistence`-Sektion mit absolutem lokalem `database_path` und
  positivem `busy_timeout_ms` mit 5.000 ms Default. Bootstrap bleibt
  unverändert auf YAML- und Secret-Quelle beschränkt. Die persistenzfähige
  Control Plane weist Version 1 ausdrücklich ab; sie deutet Version 1 weder um
  noch ergänzt sie automatisch.
- **Begründung:** Der DB-Pfad bleibt installationsspezifische Runtime-
  Konfiguration, während die Versionsgrenze eine vollständige Startup-
  Validierung ohne versteckte Defaults ermöglicht.
- **Berücksichtigte Alternativen:** Fest codierter Pfad, Datenbank-URL,
  automatische Version-1-Aufwertung und eine weitere `OASIX_`-
  Umgebungsvariable wurden verworfen.
- **Konsequenzen und Trade-offs:** Deployments müssen ihre YAML manuell auf
  Version 2 anheben. Der Zielpfad benötigt ein lokales persistentes Volume,
  restriktive Rechte und Platz für DB, WAL und SHM. Der aktuelle Loader weist
  Version 1 sicher ab; es gibt keinen stillen Fallback und keine automatische
  Aktualisierung der YAML-Datei.
- **Quellen:** Requirement CFG-01, CFG-02, JOB-03 und JOB-04;
  [Phase-A.2-Design](phases/A2-persistence.md#511-runtime-konfiguration-schema_version-2),
  [Runtime-Modelle](../../src/oasix/config/models.py)

## OASIX-DEC-012 – Scope-bezogene, digestbasierte Job-Idempotenz

- **Status:** Datenbankconstraints in A.2.2 implementiert; API-Validierung,
  Scope-Ableitung und Retention-Frist ausstehend
- **Kontext:** Ein global eindeutiger, im Klartext gespeicherter
  `idempotency_key` kollidiert zwischen unabhängigen Clients und vergrößert die
  Datenschutz- und Logging-Risiken.
- **Gewählte Lösung:** `jobs` speichert einen internen Caller-/Tenant-Scope,
  den 32-Byte-SHA-256-Digest eines zufälligen, opaken Schlüssels und einen
  Request-Fingerprint. Ein partieller Unique-Index gilt für Scope plus Digest.
  Rohschlüssel und Auth-Identität werden weder persistiert noch geloggt.
- **Begründung:** Die Datenbank erkennt Wiederholungen auch nach Neustart,
  trennt aber unabhängige Identitätsräume. Der Fingerprint ermöglicht der
  späteren API, denselben Schlüssel mit abweichender Payload sicher als
  Konflikt abzulehnen.
- **Berücksichtigte Alternativen:** Globale Eindeutigkeit, Klartextschlüssel und
  ausschließlich flüchtige Deduplizierung wurden verworfen. Eine eigene
  Tombstone-Tabelle wird nicht in die erste Migration aufgenommen.
- **Konsequenzen und Trade-offs:** Der API-Vertrag verlangt Erzeugung aus
  mindestens 128 Bit Zufall; serverseitig prüfbar sind nur Format und Länge.
  Stabile Scope-Ableitung und kanonische Fingerprints sind verbindlich zu
  definieren. Die Idempotenzzuordnung endet zunächst mit der kontrollierten
  Löschung der Jobzeile; numerische Retention und ein möglicher längerer
  Replay-Schutz bleiben vor der Job-API zu entscheiden.
- **Quellen:** Requirement JOB-01, SEC-01, SEC-03 und AC-10;
  [Phase-A.2-Design](phases/A2-persistence.md#512-idempotenzvertrag)

## OASIX-DEC-013 – Einfache, begrenzte SQLite-Verbindungsbasis

- **Status:** Akzeptiert; Verbindungsbasis in A.2.1 und UUID-Spaltentypen im
  A.2.2-Schema implementiert
- **Kontext:** Python 3.12, SQLite und SQLAlchemy benötigen explizite
  Transaktions-, Foreign-Key- und Poolvorgaben, damit Plattformdefaults nicht
  unbemerkt die Persistenzsemantik verändern.
- **Gewählte Lösung:** IDs verwenden UUIDv4. `sqlite3` läuft mit
  `autocommit=False`; ein SQLAlchemy-Connect-Hook aktiviert und verifiziert
  `PRAGMA foreign_keys=ON` auf jeder Verbindung. Der initiale Busy-Timeout
  beträgt 5.000 ms. Der file-basierte Engine-Pool ist ein `QueuePool` mit
  `pool_size=5`, `max_overflow=0` und `pool_timeout=5` Sekunden. Pfad-, Datei-
  und POSIX-Rechteprüfungen erfolgen vor Bereitstellung der Persistenz.
- **Begründung:** Die Werte bilden eine kleine, deterministische und testbare
  Ausgangsbasis für genau eine Control Plane, ohne adaptive Poolsteuerung oder
  zusätzliche Locking-Abstraktionen.
- **Berücksichtigte Alternativen:** Legacy-Transaktionsmodus, implizite
  Foreign-Key-Aktivierung, unbegrenzter Overflow und ein komplexer eigener
  Connection Manager wurden verworfen.
- **Konsequenzen und Trade-offs:** SQLite bleibt Single-Writer. Linux ist die
  Produktions-, macOS die Entwicklungsplattform. Symlink-, Typ- und POSIX-
  Prüfungen sind testbar; eine vollständige automatische Erkennung aller
  Netzwerkdateisysteme ist nicht portabel und bleibt zusätzlich eine
  Deployment-Verantwortung. Andere Pool-/Timeout-Werte benötigen Messdaten.
- **Quellen:** Requirement JOB-03, JOB-04 und CFG-01;
  [Phase-A.2-Design](phases/A2-persistence.md#513-engine-sessions-und-sqlite-pragmas),
  [Engine-Implementierung](../../src/oasix/persistence/database.py),
  [Pfadprüfung](../../src/oasix/persistence/paths.py),
  [PR #3](https://github.com/madebyzwen/oasix/pull/3)

## OASIX-DEC-014 – Geschlossene Nutzdaten- und Referenzverträge

- **Status:** Akzeptiert und in A.2.3 implementiert; konkrete Fachschemata und
  Adapterverträge ausstehend
- **Kontext:** Die sechs Kerntabellen besitzen flexible JSON- und
  Referenzfelder. Freie Objekt-Dumps oder beliebige angeblich sichere
  Referenzstrings würden Secrets, instabile Providerdaten und unkontrollierte
  Formate in die Control-Plane-Persistenz tragen.
- **Gewählte Lösung:** Repository-Schreibzugriffe durchlaufen eine gemeinsame
  Pydantic-v2-Validierung. Job- und Eventtypen verwenden eine explizite
  Registry vollständig geschlossener Schemata; optionale Zusatzmetriken
  benötigen ebenfalls ein geschlossenes Modell. Nicht leere Result-,
  Execution- und Continuation-Referenzen benötigen jeweils einen ausdrücklich
  registrierten, feldspezifischen Nicht-Secret-Adapter. Ohne freigegebenen
  Vertrag schlägt der Schreibzugriff geschlossen fehl.
- **Begründung:** A.2.3 kann Struktur, Grenzen und Fehlerredaktion absichern,
  ohne noch nicht entschiedene Jobtypen, Eventtypen oder Adapterformate zu
  erfinden. Eine Secret-Schlüsselwortsuche wird nicht als Sicherheitsgarantie
  eingesetzt.
- **Berücksichtigte Alternativen:** Beliebiges valides JSON, offene Pydantic-
  Modelle, freie Referenzstrings und Token-Heuristiken wurden ausgeschlossen.
- **Konsequenzen und Trade-offs:** Neue fachliche Typen sind erst nutzbar,
  nachdem ihre Schemata beziehungsweise Adapter explizit registriert wurden.
  Bytegrenzen und geschlossene Modelle ersetzen weder fachliche Datenklassifikation
  noch Zugriffs-, Backup- und Logging-Schutz.
- **Quellen:** Requirement SEC-03, OBS-03, OBS-04 und AC-10;
  [Phase-A.2-Design](phases/A2-persistence.md#52-schutz-persistierter-nutzdaten),
  [Validierung](../../src/oasix/persistence/validation.py)

## OASIX-DEC-015 – Fähigkeitsgetrennte asynchrone Worker-Verträge

- **Status:** In A.3.1 implementiert; unabhängige Review-Abnahme ausstehend
- **Kontext:** Die Control Plane benötigt eine stabile Worker-Grenze, ohne
  Hardware, Hostnamen, Transport, Power-Mechanismus oder noch ungeklärte
  Ausführungssemantik in die Kernlogik zu übernehmen.
- **Gewählte Lösung:** Ein aus der validierten Runtime-Konfiguration aufgelöstes
  `ActiveWorkerTarget` bindet die generische Worker-ID an ihr Profil. Kleine
  asynchrone `Protocol`-Ports trennen State-Beobachtung, servicebezogene
  Readiness, Wake und Sleep. Ergebnisse sind unveränderlich; technische
  Kommunikation und Timeout besitzen feste, sichere Fehlertypen. Der
  verbindliche Worker-State-Enum ist zugleich Quelle der unveränderten
  Persistenzwerte.
- **Begründung:** Getrennte Fähigkeiten erlauben austauschbare Adapter und
  verhindern, dass ein monolithischer Vertrag unbeteiligte Komponenten an
  SSH, HTTP oder Power-Techniken koppelt. Asynchrone Ports passen zu entferntem
  I/O; blockierende Bibliotheken müssen später im Adapter ausgelagert werden.
  Ein Fehler bleibt von einem erfolgreich beobachteten Zustand beziehungsweise
  Readiness-Ergebnis unterscheidbar.
- **Berücksichtigte Alternativen:** Ein Gesamtadapter, Transportparameter in
  jeder Methode, freie Fehlertexte und eine generische `execute()`-Operation
  wurden verworfen. Insbesondere ist Start-/Status-/Streaming-Semantik für
  spätere Ausführung noch nicht eindeutig festgelegt.
- **Konsequenzen und Trade-offs:** Timeout, Retry, Zustandsübergänge und
  Persistierung liegen außerhalb der Ports und müssen spätere
  Orchestrierungsdienste übernehmen. Wake-/Sleep-Erfolg bestätigt nur die
  Befehlsübergabe. Konkrete Ausführungs-, Cancellation- und
  Recovery-Referenzverträge bleiben bis zu ihren Fachphasen offen.
- **Quellen:** Requirement ARC-01 bis ARC-04, ALIAS-01 bis ALIAS-03, CFG-02 bis
  CFG-04, WRK-01 bis WRK-05, PWR-02, SEC-02, SEC-03 und REC-04;
  [Phase A.3.1](phases/A3-worker-contracts.md),
  [Worker-Verträge](../../src/oasix/worker/contracts.py)
