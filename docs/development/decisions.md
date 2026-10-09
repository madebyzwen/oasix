# Entscheidungsregister

Dieses Register dokumentiert verbindliche Architekturvorgaben und im Projekt
getroffene Implementierungsentscheidungen. Der Status „geplant“ bezeichnet
ausdrücklich noch nicht implementierte Entscheidungen.

## OASIX-DEC-001 – Trennung von Control Plane und Compute Worker

- **Status:** Verbindlich; in A.1 nur durch Konfigurationsgrenzen abgebildet
- **Kontext:** Steuerung und persistenter Zustand müssen dauerhaft verfügbar
  bleiben, während rechenintensive Arbeit auf einem austauschbaren Worker läuft.
- **Gewählte Lösung:** Die Control Plane verantwortet Orchestrierung und Zustand.
  KI-Inferenz, Agenten und rechenintensive Tools werden ausschließlich auf dem
  aktiven Compute Worker ausgeführt.
- **Begründung:** Die Trennung schützt stabile Client-Schnittstellen und erlaubt
  den Austausch der Worker-Infrastruktur ohne Änderungen an der Kernlogik.
- **Berücksichtigte Alternativen:** Eine gemeinsame Control-/Compute-Runtime
  widerspricht ARC-01 und ARC-02 und wurde ausgeschlossen.
- **Konsequenzen und Trade-offs:** Kommunikation, Readiness und Recovery müssen
  explizit modelliert werden. A.1 implementiert noch keine Worker-Ausführung.
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

- **Status:** Verbindlich; im A.1-Konfigurationsmodell implementiert
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
  [models.py](../../src/oasix/config/models.py)

## OASIX-DEC-005 – SQLite als geplante MVP-Persistenz

- **Status:** Für das A.2-Design als Ziel festgelegt; Implementierung und
  Produktionsvalidierung ausstehend
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
  aber keine spätere Datenmigration zu PostgreSQL. Anwendungscode, Migrationen
  und Datenbankdateien existieren noch nicht.
- **Quellen:** Requirement JOB-03, JOB-04 und Abschnitt 13;
  [Phase-A.2-Design](phases/A2-persistence.md),
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

- **Status:** Im A.2-Entwurf vorgeschlagen; Review und Implementierung ausstehend
- **Kontext:** Job-/Attempt-Übergänge, Lease-Operationen und Recovery dürfen bei
  Abstürzen keinen teilweise aktualisierten Zustand hinterlassen. Das Schema
  muss gemäß JOB-04 migrationsfähig sein.
- **Gewählte Lösung:** Eine gekapselte SQLAlchemy-2-Engine stellt kurzlebige
  Sessions bereit. Use-Case-Services definieren Transaktionsgrenzen; Repositories
  führen kein eigenständiges `commit()` aus. Alembic ist die einzige Quelle für
  Produktionsschemaänderungen. Constraints erhalten Namen und SQLite-Umbauten
  verwenden geprüfte Batch-Migrationen.
- **Begründung:** Fachlich zusammengehörige Zustandswechsel werden atomar und
  das Schema bleibt reproduzierbar versioniert.
- **Berücksichtigte Alternativen:** Implizite Commits, `create_all()` beim
  Produktionsstart und ungeprüfte Autogenerate-Migrationen wurden verworfen.
- **Konsequenzen und Trade-offs:** Migrationen laufen vor dem Anwendungsstart in
  einem exklusiven Wartungsfenster. Vor destruktiven Upgrades ist ein getestetes
  Backup erforderlich; Produktionsrollbacks erfolgen primär durch Restore.
- **Quellen:** Requirement JOB-04;
  [Phase-A.2-Design](phases/A2-persistence.md),
  [Alembic-Batch-Dokumentation](https://alembic.sqlalchemy.org/en/latest/batch.html)

## OASIX-DEC-010 – Persistenzgrenzen und Lease-Autorität

- **Status:** Im A.2-Entwurf vorgeschlagen; Review und Implementierung ausstehend
- **Kontext:** Die Control Plane benötigt persistenten Betriebszustand, darf
  aber Konfiguration nicht duplizieren oder parallele Aktivitätszähler führen.
- **Gewählte Lösung:** Die externe Runtime-Konfiguration bleibt autoritativ für
  Worker-Profile, Dienste und Policies. Persistiert werden Identitäten,
  beobachteter Worker-/Service-State, Jobs, Attempts, Leases, Control-/Power-
  Operationen, Recovery-Referenzen und zuordenbare Telemetrie. Aktive Nutzung
  wird ausschließlich aus nicht freigegebenen, noch nicht abgelaufenen Leases
  ermittelt; ein `active_lease_count` wird nicht gespeichert.
- **Begründung:** Diese Grenze vermeidet widersprüchliche Konfigurationskopien
  und erfüllt die Lease-Invariante aus v3.4.
- **Berücksichtigte Alternativen:** Persistierte Worker-Verbindungsdaten und
  separate Request-/Job-Aktivitätszähler wurden ausgeschlossen.
- **Konsequenzen und Trade-offs:** Beim Start müssen Konfiguration und
  persistierte IDs abgeglichen werden. Abgelaufene Leases gelten bereits vor
  ihrer späteren Bereinigung als inaktiv. Sämtliche Zeiten werden als UTC-
  Epoch-Mikrosekunden gespeichert; eine verlässliche Control-Plane-Uhr ist
  betriebliche Voraussetzung.
- **Quellen:** Requirement CFG-02, JOB-01 bis JOB-05, LSE-01 bis LSE-04,
  OBS-01 bis OBS-04 und REC-01 bis REC-04;
  [Phase-A.2-Design](phases/A2-persistence.md)
