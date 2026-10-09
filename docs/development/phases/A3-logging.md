# Phase A.3.2 – Strukturiertes Logging und Sicherheitsgrenzen

Status: implementiert, unabhängig geprüft und freigegeben

## 1. Ziel und Abgrenzung

A.3.2 stellt ein zentrales, wiederverwendbares Fundament für strukturierte und
sichere Control-Plane-Logs bereit. Es erzeugt kompakte JSON-Lines auf einem
lokalen Textstream, validiert Ereignisdefinitionen und lässt pro Ereignis nur
explizit freigegebene Korrelations- und Fehlerfelder zu.

Nicht enthalten sind operative Logaufrufe in noch nicht implementierten
Control-Plane-Komponenten, externe Logserver, Persistenz, Netzwerktransport,
OpenTelemetry, Dashboards, produktive Worker-Kommunikation, Dispatcher,
Recovery, Lease-Lifecycle, Power-Ausführung oder LLM-/Agenten-Funktionen. Es
wurden keine Datenbanktabellen, Runtime-Einstellungen oder Dependencies
ergänzt.

## 2. Verbindliche Anforderungen

| Anforderung | Umsetzung in A.3.2 |
| --- | --- |
| ARC-03, ALIAS-01 bis ALIAS-03 | Komponente, Worker und Ereignisse werden ausschließlich über generische technische Kennungen beschrieben. Host-, Transport-, Hardware- und Frameworkdaten sind keine Logfelder. |
| SEC-02, SEC-03, AC-10 | Die Emissionsgrenze akzeptiert weder freie Objekte noch beliebige Zusatzfelder, Exceptions, Payloads oder Referenzen. Validierungsfehler und Debug-Repräsentationen enthalten keine abgelehnten Werte. |
| OBS-01 | `request_id`, `job_id`, `attempt_id` und `worker_id` können einzeln und nur sofern vorhanden korreliert werden; zusätzlich ist die in v3.4 definierte `lease_id` zulässig. |
| OBS-03 | Nicht vorhandene optionale Werte werden weggelassen und nicht erzeugt oder geschätzt. |
| REC-04 | Die sieben ausdrücklich genannten Fehlerklassen `CONFIG`, `WAKE`, `READINESS`, `LLM`, `AGENT`, `TOOL` und `TIMEOUT` sind als geschlossener Enum verfügbar; kontrollierte technische Fehlercodes bleiben separat. |

OBS-02, OBS-04 und OBS-05 sind nicht vollständig umgesetzt: Attempt-Metriken,
`extra_metrics` und Host-Metriken gehören nicht in die freie Logging-Grenze
dieser Phase. Sie bleiben in der Persistenz beziehungsweise ihren späteren
fachlichen Komponenten verortet.

## 3. Architektur und Schnittstellen

Der Datenfluss ist bewusst klein:

```text
statische EventDefinition + allowlisted fields
                    |
                    v
       StructuredLogger.emit()
       Typ-, Format- und Feldprüfung
                    |
                    v
        privater logging.LogRecord
                    |
                    v
        JsonLinesFormatter -> Textstream
```

`create_structured_logger()` erzeugt eine direkte `logging.Logger`-Instanz,
einen StreamHandler und den JSON-Formatter. Die Instanz wird nicht über
`logging.getLogger()` global registriert, propagiert nicht zum Root-Logger und
ändert weder dessen Level noch Handler oder Filter. Der Stream ist lokal und
eignet sich mit dem Standard `stderr` für spätere Container-Logsammlung; ein
Teststream kann explizit übergeben werden.

Aufrufer definieren Ereignisse als unveränderliche, vorzugsweise modulweit
konstante `EventDefinition`. `emit()` besitzt keinen freien Message-Parameter,
keine Formatargumente, kein `exc_info` und kein beliebiges `extra`. Dadurch
bleiben Ereigniscode und Beschreibung statisch prüfbar. Zulässig sind nur die
Standardlevel `DEBUG`, `INFO`, `WARNING`, `ERROR` und `CRITICAL`.

Jede erfolgreiche Emission erzeugt genau ein kompaktes JSON-Objekt und einen
abschließenden Zeilenumbruch. Die Ausgabe enthält folgende zentrale Allowlist:

| Feld | Pflicht | Vertrag |
| --- | --- | --- |
| `timestamp` | ja | UTC nach RFC 3339 mit Millisekunden und `Z` |
| `level` | ja | Standard-Loglevel in Großbuchstaben |
| `component` | ja | Generischer Kleinbuchstaben-Identifier, höchstens 63 Zeichen |
| `event_code` | ja | Statischer technischer Code, höchstens 127 Zeichen |
| `message` | ja | Statische druckbare Beschreibung, höchstens 512 UTF-8-Bytes |
| `request_id` | nein | Begrenzte technische Korrelationskennung |
| `job_id`, `attempt_id`, `lease_id` | nein | Kanonische UUIDv4 entsprechend der A.2-ID-Konvention |
| `worker_id` | nein | Generische konfigurierte Worker-ID |
| `error_class` | nein | Wert aus `LogErrorClass` |
| `error_code` | nein | Begrenzter stabiler technischer Code |

Optionale Felder mit `None` werden ausgelassen. Es werden keine IDs,
Beziehungen, Zeitpunkte oder Messwerte erfunden.

### Sicherheitsgrenze

- Der Kontext muss ein exaktes `dict` sein. Unbekannte oder nicht textuelle
  Schlüssel werden abgewiesen, ohne Schlüssel oder Wert zu nennen.
- Außer `LogErrorClass` sind nur exakte Strings mit feldspezifischem Format
  erlaubt. Exceptions, Pydantic-Modelle, SQLAlchemy-Objekte, Secret-Wrapper,
  Payloads, Antworten und Referenzobjekte werden nicht serialisiert.
- Die öffentliche Emissionsmethode ersetzt interne Validierungsfehler durch
  eine neue feste `LogValidationError`; ihre Traceback-Frames halten den
  abgelehnten Event- oder Kontextwert nicht fest.
- Der Formatter verwendet niemals `record.getMessage()`, `args`, `exc_info`
  oder beliebige Record-Attribute. Erhält er einen fremden Record, erzeugt er
  ausschließlich das feste Ereignis `logging.invalid_record`.
- Es findet keine nachträgliche Regex-Suche nach vermeintlichen Secrets statt.
  Der primäre Schutz ist die geschlossene Struktur und Datenklassifikation vor
  der Emission.

## 4. Entscheidungen

[OASIX-DEC-016](../decisions.md#oasix-dec-016--geschlossene-json-lines-logging-grenze)
dokumentiert JSON Lines, statische Ereignisdefinitionen, die Feld-Allowlist und
isolierte Standardbibliotheks-Logger.

Freie Textlogs mit nachträglicher Redaktion wurden verworfen, weil Secrets
bereits vor einer unsicheren Serialisierung ausgeschlossen werden müssen. Eine
globale `basicConfig()`-Konfiguration wurde wegen schwer kontrollierbarer
Seiteneffekte verworfen. Externe Logging-Frameworks und Collector wurden nicht
benötigt. Ein freies Metadatenobjekt wurde ebenso ausgeschlossen wie die
Serialisierung vollständiger Exceptions oder Domänenmodelle.

## 5. Umsetzung

- `src/oasix/logging/core.py`: Ereignisdefinition, Fehlerklassen, zentrale
  Allowlist, Validierung, isolierter Logger und sicherer JSON-Lines-Formatter.
- `src/oasix/logging/__init__.py`: explizite öffentliche Importoberfläche.
- `tests/test_structured_logging.py`: Format-, Korrelations-, Ablehnungs-,
  Redaktions-, Konsistenz- und Seiteneffektnachweise.

Bestehende Konfigurations-, Persistenz- und Worker-Verträge bleiben fachlich
unverändert.

## 6. Tests und Nachweise

| Prüfung | Umgebung | Ergebnis | Nachweis |
| --- | --- | --- | --- |
| Neue Logging-Tests | lokal, macOS, Python 3.12 | 26 bestanden | `tests/test_structured_logging.py` |
| Vollständige pytest-Suite | lokal, macOS, Python 3.12 | 227 bestanden | Feature-Branch |
| Ruff Linting und Formatprüfung | lokal, macOS, Python 3.12 | bestanden, 46 Dateien formatiert | Feature-Branch |
| Paket-, Import-, Link- und Diff-Prüfung | lokal | bestanden | Feature-Branch |
| Linux-CI | GitHub Actions, Ubuntu, Python 3.12 | erfolgreich | [PR #5 – Checks](https://github.com/madebyzwen/oasix/pull/5/checks) |

Die Tests benötigen weder Netzwerk, Datenbankverbindung, reale Secret-Dateien
noch globale Logging-Konfiguration. Produktionsintegration und Verhalten eines
externen Logsammlers sind nicht getestet.

## 7. Einschränkungen und Risiken

- Die Schicht ist keine Data-Loss-Prevention-Engine. Ein Plaintext-Secret, das
  ein Aufrufer fälschlich als syntaktisch gültige Korrelations-ID klassifiziert,
  ist nicht zuverlässig von einer echten ID unterscheidbar. Aufrufer dürfen nur
  bereits klassifizierte technische IDs und kontrollierte Codes übergeben.
- `EventDefinition` ist für statische Quelltextkonstanten bestimmt. Dynamische
  Nutzdaten, Providerantworten und Exceptiontexte dürfen nicht zur Laufzeit als
  Beschreibung aufgebaut werden.
- Die Phase integriert noch keine Ereignisse in Anwendungskomponenten und
  trifft keine Retention-, Rotation- oder Collector-Entscheidung.
- Zeitstempel verwenden die Systemuhr des Control-Plane-Prozesses; deren
  korrekter UTC-Bezug bleibt eine betriebliche Voraussetzung.
- Der feste Formatter-Fallback schützt vor Inhaltsausgabe, signalisiert aber
  nur `logging.invalid_record`; Diagnose eines fehlerhaften Aufrufers erfolgt
  über Tests und Code-Review, nicht durch Wiedergabe des abgelehnten Records.

## 8. Abnahmestatus

Der A.3.2-Umfang ist implementiert und lokal sowie durch Linux-CI nachgewiesen.
Die Implementierung wurde auf GitHub unabhängig ohne blockierende
Beanstandungen geprüft und freigegeben. Es wurde keine weitere Teilphase
begonnen. Phase A insgesamt ist nicht abgeschlossen.

## 9. GitHub-Referenzen

- Commits: [`647ca76eb9e0f881586a78d799def684ddbe7a84`](https://github.com/madebyzwen/oasix/commit/647ca76eb9e0f881586a78d799def684ddbe7a84)
  (`feat(logging): add structured secure logging foundation`)
- Pull Requests: [PR #5](https://github.com/madebyzwen/oasix/pull/5), offen und
  nicht gemergt
- CI-Läufe: [Linux-CI A.3.2](https://github.com/madebyzwen/oasix/actions/runs/38000156438)
