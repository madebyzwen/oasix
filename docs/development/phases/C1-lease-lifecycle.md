# Phase C.1 – Persistenter Lease-Lifecycle

Status: abgeschlossen, unabhängig geprüft und freigegeben

## 1. Ziel und Abgrenzung

C.1 implementiert den persistenten und transaktionalen Lifecycle für aktive
Worker-Nutzung. LLM-, Agenten-, Build-, Test- und Development-Aufrufer können
damit eine Lease anlegen, per Heartbeat erneuern, freigeben und anhand ihrer
persistierten Gültigkeit als aktiv auswerten. Die Lease Registry bleibt die
einzige Autorität; es wurde kein Aktivitätszähler ergänzt.

Nicht enthalten sind Client-API-Authentifizierung, LLM-Proxy, Worker-
Heartbeat-Protokoll, automatische Sleep-Entscheidung, Job-Dispatcher,
vollständige Recovery-Orchestrierung oder Netzwerkzugriffe. Ein Lease-
Heartbeat ist ausschließlich eine Erneuerung der persistenten Lease.

## 2. Verbindliche Anforderungen

| Anforderung | Umsetzung in C.1 |
| --- | --- |
| LSE-01 | Jede Lease besitzt eine kanonische UUIDv4, konfigurierte `worker_id`, generische Owner-/Purpose-IDs sowie persistierte Erzeugungs-, Heartbeat- und Ablaufzeiten. Optionale Job-/Attempt-Bezüge verwenden die bestehenden Fremdschlüssel und Korrelationsprüfungen. |
| LSE-02 | Positive TTLs werden auf einer injizierbaren UTC-Zeitbasis in UTC-Epoch-Mikrosekunden umgerechnet. Abgelaufene und freigegebene Leases sind inaktiv und können nicht durch Renew oder wiederholtes Acquire reaktiviert werden. |
| LSE-03 | Der Vertrag unterscheidet nicht nach Nutzungsart. Mehrere unabhängige Leases pro Worker sind möglich; LLM-, Agenten- und Development-Komponenten müssen später denselben Lifecycle verwenden. |
| PWR-01, AC-04 | `active_for_worker()` wertet ausschließlich `released_at IS NULL AND expires_at > observed_at` aus. Die eigentliche Sleep-Entscheidung bleibt außerhalb von C.1. |
| REC-01, REC-04 | Leases bleiben über Verbindungs- und Prozessneustarts erhalten. Ablauf wird aus dem persistenten Zustand ausgewertet; Bereinigung und Abgleich mit realen Worker-Diensten folgen in Recovery. |
| SEC-03, AC-10 | Geschlossene Validierung, kontrollierte Fehlerklassen und ersetzte Tracebacks geben keine Eingabewerte aus. Die Komponente schreibt keine Logs und akzeptiert in Owner/Purpose nur generische Identifikatoren. |

## 3. Architektur und Schnittstellen

`LeaseLifecycle` wird an ein sessiongebundenes `LeasesRepository` gebunden. Der
Aufrufer besitzt weiterhin die Transaktion und entscheidet gemeinsam mit
seinen fachlich zusammengehörigen Persistenzoperationen über Commit oder
Rollback. Der Lifecycle führt keine Netzwerkoperation und keinen eigenen
Commit aus.

```text
caller-owned short transaction
              |
              v
       LeaseLifecycle
       /      |      \
 acquire    renew    release
       \      |      /
              v
       LeasesRepository
              |
              v
        SQLite leases table
```

Die Verträge sind:

- **Acquire:** Ohne vorgegebene ID wird eine UUIDv4 erzeugt. Für
  Operations-Retry stellt der Aufrufer eine zuvor erzeugte stabile UUIDv4
  bereit. Dieselbe ID ist nur bei identischem Worker, Owner, Purpose sowie
  identischen Job-/Attempt-Bezügen und noch aktiver Lease idempotent. Ein
  abweichender unveränderlicher Identitätsbezug ist ein Konflikt. Die beim
  wiederholten Aufruf angegebene TTL verändert die bestehende Lease nicht;
  Verlängerungen erfolgen ausschließlich über Renew.
- **Heartbeat/Renew:** Eine einzelne bedingte SQL-Aktualisierung verlangt eine
  nicht freigegebene, zum Beobachtungszeitpunkt noch nicht abgelaufene Lease.
  Heartbeat-Zeit kann nicht zurücklaufen und das Ablaufdatum wird nie verkürzt.
- **Release:** Die erste Freigabe setzt Zeitpunkt und generischen Grund.
  Wiederholungen liefern unverändert diese erste Freigabe zurück. Andere
  Leases werden nicht berührt.
- **Expiry/Aktivität:** Eine Lease ist genau dann aktiv, wenn sie nicht
  freigegeben ist und ihr Ablaufzeitpunkt strikt nach der aktuellen UTC-Zeit
  liegt. Der Grenzzeitpunkt selbst ist bereits inaktiv. Historische Zeilen
  werden nicht gelöscht oder umgeschrieben.

Die vorhandenen Spalten, Fremdschlüssel, Zeit-Constraints und Indizes reichen
für diesen Vertrag aus. C.1 benötigt deshalb weder eine Schemaänderung noch
eine Alembic-Migration. Die Architekturentscheidung ist in
[OASIX-DEC-019](../decisions.md#oasix-dec-019--persistenter-idempotenter-lease-lifecycle)
festgehalten.

## 4. Entscheidungen

- UUIDv4 bleibt die einheitliche, plattformunabhängige Identität. Eine vom
  Aufrufer stabil gehaltene ID ist der Idempotenzschlüssel für Acquire.
- TTLs sind positive ganze Sekunden an der Schnittstelle; gespeichert werden
  die bereits in A.2 festgelegten UTC-Epoch-Mikrosekunden. Der zulässige
  Bereich wird gegen SQLite-`INTEGER` geprüft. Die ursprüngliche TTL wird nicht
  als separates unveränderliches Feld gespeichert und ist nach einem Renew aus
  `created_at` und dem veränderlichen `expires_at` nicht rekonstruierbar. Sie ist
  deshalb bewusst kein Bestandteil der Acquire-Idempotenzprüfung.
- Konkurrenz wird durch `INSERT ... ON CONFLICT DO NOTHING` und bedingte
  `UPDATE`-Anweisungen abgesichert. SQLite bleibt Single-Writer. Ein nach dem
  konfigurierten Busy-Timeout verbleibender Lock wird kontrolliert gemeldet und
  nicht innerhalb der Persistenzschicht automatisch wiederholt.
- Owner, Purpose und Release-Grund sind generische technische Identifikatoren,
  keine Freitext- oder Secret-Felder. Richtige Datenklassifikation beim
  Aufrufer bleibt Voraussetzung.

## 5. Umsetzung

- `src/oasix/persistence/leases.py`: öffentlicher Lifecycle, injizierbare
  UTC-Uhr, TTL-Umrechnung, UUIDv4-Erzeugung und sichere Traceback-Grenze.
- `src/oasix/persistence/repositories.py`: atomare Acquire-, Renew-, Release-
  und Aktivitätsabfragen innerhalb der bestehenden Aufrufertransaktion.
- `src/oasix/persistence/validation.py`: geschlossene Pydantic-Verträge für
  Beobachtung, Renewal und Release.
- `src/oasix/persistence/errors.py`: feste Lifecycle-Fehlerkategorien für
  fehlende, inaktive und widersprüchliche Leases.
- `tests/test_lease_lifecycle.py`: Lifecycle-, Konkurrenz-, Neustart-,
  Rollback-, Isolations- und Sicherheitsnachweise.

## 6. Tests und Nachweise

| Prüfung | Umgebung | Ergebnis | Nachweis |
| --- | --- | --- | --- |
| Lease-Lifecycle-Tests | lokal, macOS, Python 3.12 | 20 bestanden | `tests/test_lease_lifecycle.py` |
| Vollständige pytest-Suite | lokal, macOS, Python 3.12 | 269 bestanden | `feature/b-llm-path` vor Korrektur-Commit |
| Ruff Linting und Formatprüfung | lokal, macOS, Python 3.12 | bestanden, 58 Dateien geprüft | `feature/b-llm-path` vor Commit |
| Paket-, Import-, Dokumentationslink- und Diff-Prüfung | lokal | bestanden | `feature/b-llm-path` vor Commit |
| Linux-CI | GitHub Actions, Ubuntu, Python 3.12 | bestanden | [Lauf `38004074726`](https://github.com/madebyzwen/oasix/actions/runs/38004074726) |

Die Tests verwenden ausschließlich lokale SQLite-Dateien und injizierte Uhren.
Es werden keine Worker, Netzwerkdienste oder realen Secret-Dateien benötigt.
Reale Multi-Prozess-Last, Uhrensynchronisierung des Deployment-Hosts und
langlaufender Produktionsbetrieb sind lokal nicht nachgewiesen.

## 7. Aufgelöste B.3-Abhängigkeiten und verbleibende Risiken

C.1 erfüllt den ursprünglich in B.3 benannten persistenten Acquire-/Heartbeat-/
Release-Lifecycle und bestätigt, dass die vorhandenen Lease-Spalten ohne
Migration ausreichen. B.3.0 und B.3.1 haben die übrigen damaligen Blocker
aufgelöst. Der freigegebene LLM-Pfad erwirbt eine stabile Lease-ID vor Wake,
Readiness und Upstream-Nutzung, erneuert sie während langer Nutzung und gibt sie
erst nach vollständigem Ende frei.

Die folgenden Abgrenzungen gelten weiterhin:

- B.3.0 verantwortet den getrennten Client-API-Schlüssel- und
  Berechtigungsvertrag für `/v1/...`.
- B.3.1 und B.4 verantworten die Integration von Authentifizierung,
  Concurrency-Gate, Lease, Wake, Readiness, Upstream-Streaming sowie Abbruch-
  und Fehlerpfaden; diese Ablaufkomposition bleibt außerhalb von C.1.
- Verwaiste aktive Leases laufen zuverlässig aus; ihre spätere markierende
  Bereinigung und der reale Worker-/Service-Abgleich bleiben Recovery-Arbeit.
- SQLite kann konkurrierende Schreibvorgänge serialisieren. Nach Ablauf des
  Busy-Timeouts erhält der Aufrufer einen sicheren Locking-Fehler und muss die
  gesamte kurze Operation mit derselben Lease-ID wiederholen.
- Korrekte UTC-Systemzeit ist wie bereits in A.2 eine betriebliche
  Voraussetzung. C.1 implementiert keine verteilte Uhrensynchronisierung.

## 8. Abnahmestatus

C.1 ist implementiert, unabhängig geprüft und für seinen dokumentierten
Lifecycle-Umfang freigegeben. Die Abnahme umfasst weder B.3-Ablaufkomposition
noch Proxy, Streaming, Power- oder Recovery-Logik. Die ebenfalls freigegebenen
B.3- bis B.5-Komponenten bauen auf C.1 auf, ohne dessen Verantwortungsgrenze zu
erweitern.

## 9. GitHub-Referenzen

- Implementierung: [`017c94d`](https://github.com/madebyzwen/oasix/commit/017c94dda1a1cc4ff12db065edce124801704287)
- Review-Nachbesserung:
  [`811afd3`](https://github.com/madebyzwen/oasix/commit/811afd3ec901e86990fbb0895757291063ba64ce)
- Pull Request: [PR #6](https://github.com/madebyzwen/oasix/pull/6), offen und
  nicht gemergt
- Linux-CI der Implementierung: [Lauf `38003515144`](https://github.com/madebyzwen/oasix/actions/runs/38003515144)
- Linux-CI der Review-Nachbesserung: [Lauf `38004074726`](https://github.com/madebyzwen/oasix/actions/runs/38004074726)
