# Phase A.1 – Konfiguration und Secrets

## 1. Ziel und Abgrenzung

Phase A.1 stellt das validierte Konfigurationsfundament der OASIX-Control-Plane
bereit. Sie umfasst Bootstrap-Quellen, externe YAML-Runtime-Konfiguration,
Worker-/Service-Profile, Policies, Secret-Referenzen und atomare
Startup-Validierung.

Nicht Bestandteil dieser Phase sind Persistenz, Datenbankmigrationen, APIs,
Netzwerkkommunikation, Worker-Steuerung, Wake-/Sleep-Ausführung, KI-Inferenz,
Agent-Ausführung, Docker-Konfiguration und Deployment. Die entsprechenden
Felder beschreiben nur zukünftiges Runtime-Verhalten; sie führen es nicht aus.

## 2. Verbindliche Anforderungen

Grundlage ist das
[Technical Requirement v3.4](../../OASIX_Technical_Requirement_Reviewed_v3.4.docx).

| Requirement | Umsetzung in A.1 |
| --- | --- |
| CFG-01 | `schema_version: 1`, vollständige Pydantic-Validierung und fataler Startup-Fehler bei ungültiger Konfiguration |
| CFG-02 | `active_worker` und generische Worker-Profile mit Connection-, SSH-, Power-, Service- und Policy-Daten |
| CFG-03 | Ausschließlich Secret-Referenzen in der Runtime-Konfiguration; externe dateibasierte Auflösung |
| CFG-04 | Benannte Services mit Endpoint, Authentifizierungsreferenz und Readiness-Probe |
| CFG-05 | Konfigurierbare Idle-/Readiness-Timeouts, Retry/Backoff/Jitter, Grace Period und Concurrency-Limits |
| ARC-03, ARC-04 und ALIAS-01 bis ALIAS-03 | Keine fest codierte Infrastruktur; mehrere Profile sind modellierbar, genau eines ist aktiv |
| PWR-02 | Wake-/Sleep-Methoden einschließlich SSH-Kommandos werden konfiguriert; ihre Ausführung ist noch nicht implementiert |
| AC-02 | Ungültige Pflichtfelder und fehlende Secrets verhindern die Rückgabe einer `LoadedConfiguration` |
| SEC-02, SEC-03 und AC-10 | Secret-Werte werden in den für A.1 implementierten Fehler- und Debug-Pfaden redigiert; ein vollständiges Logging-System folgt später |

AC-01 ist durch die konfigurierbare Worker-Auswahl strukturell vorbereitet, aber
ein realer Serverwechsel wurde nicht als End-to-End-Szenario getestet.

## 3. Architektur und Modulstruktur

| Modul | Verantwortung |
| --- | --- |
| [`bootstrap.py`](../../../src/oasix/config/bootstrap.py) | Minimale, unveränderliche Quellkonfiguration aus `OASIX_`-Umgebungsvariablen |
| [`models.py`](../../../src/oasix/config/models.py) | Strikte, hardwareunabhängige Pydantic-v2-Runtime-Modelle |
| [`loader.py`](../../../src/oasix/config/loader.py) | UTF-8-/YAML-Laden, sichere Fehlerformatierung und atomarer Startup-Ablauf |
| [`secrets.py`](../../../src/oasix/config/secrets.py) | Providervertrag, dateibasierte Auflösung und opake Secret-Sammlung |
| [`errors.py`](../../../src/oasix/config/errors.py) | Getrennte Bootstrap-, Runtime- und Secret-Startup-Fehler |
| [`config/__init__.py`](../../../src/oasix/config/__init__.py) | Öffentliche Schnittstelle des Konfigurationspakets |

`load_startup_configuration()` liefert erst dann eine `LoadedConfiguration`,
wenn Bootstrap, YAML, Runtime-Modelle und sämtliche referenzierten Secrets
erfolgreich validiert beziehungsweise aufgelöst wurden.

## 4. Bootstrap- und Runtime-Konfiguration

Bootstrap und Runtime sind bewusst getrennt:

- `BootstrapSettings` kennt nur `config_file` und `secrets_directory`.
- `OASIX_CONFIG_FILE` ist erforderlich; `OASIX_SECRETS_DIRECTORY` hat den
  Standard `/run/secrets`.
- Fremde Umgebungsvariablen werden ignoriert. Unbekannte Variablen im
  reservierten Präfix `OASIX_` führen zu einem redigierten Bootstrap-Fehler.
- Worker, Services, Power-Methoden und Policies werden ausschließlich aus der
  Runtime-YAML geladen.
- Hot Reload ist nicht implementiert; Änderungen werden durch einen
  kontrollierten Neustart übernommen.

## 5. Konfigurationsschema und Validierung

Die Runtime-Konfiguration enthält:

- `schema_version` mit dem derzeit einzig erlaubten Wert `1`,
- `active_worker` als Verweis auf einen vorhandenen Worker,
- mindestens ein generisches Worker-Profil,
- Connection-Daten einschließlich optionaler SSH-Konfiguration,
- konfigurierbare Wake- und Sleep-Methoden,
- mindestens einen benannten Service mit Typ, Endpoint, Authentifizierung und
  HTTP-Readiness-Probe,
- Policies für Idle-/Readiness-Timeout, Force-Sleep-Grace-Period,
  Job-/Wake-Retries mit Backoff und optionalem Jitter sowie Concurrency-Limits.

Alle Modelle verwenden `extra="forbid"` und `frozen=True`. Worker- und
Service-Mappings werden zusätzlich durch `MappingProxyType` gegen nachträgliche
Mutation geschützt. IDs, Ports, Zeitwerte, Statuscodes, Retry-Grenzen,
Capabilities und abhängige Feldkombinationen werden strikt validiert.

Die YAML-Verarbeitung basiert auf `SafeLoader` und weist doppelte
Mapping-Schlüssel zurück. Ungültiges UTF-8 wird kontrolliert behandelt. Fehler
enthalten sichere strukturelle Pfade; dynamische Worker-/Service-IDs,
unbekannte Feldnamen und Eingabewerte werden nicht ungefiltert übernommen.

Die neutrale [`Beispielkonfiguration`](../../../config/oasix.example.yaml)
verwendet ausschließlich reservierte `.invalid`-Domains und Secret-Referenzen.

## 6. Secret-Referenzen und sichere Dateizugriffe

`SecretReference` erlaubt derzeit die Quelle `file` und einen eingeschränkten,
nicht relativen Dateinamen. Alle Referenzen werden aus der vollständig
validierten Runtime-Konfiguration gesammelt und vor Rückgabe des Startup-
Ergebnisses aufgelöst.

`FileSecretSource`:

1. kanonisiert das erlaubte Secret-Verzeichnis und speichert dessen Device-/
   Inode-Identität,
2. öffnet das Verzeichnis mit Schutzflags und verifiziert seine Identität,
3. öffnet die referenzierte Datei relativ zum Verzeichnis-Descriptor,
4. prüft regulären Dateityp, kanonischen Pfad und Device-/Inode-Identität des
   tatsächlich geöffneten Objekts,
5. liest ausschließlich aus diesem Descriptor und dekodiert als UTF-8,
6. weist fehlende, leere, nicht reguläre, nicht verifizierbare oder außerhalb
   liegende Dateien kontrolliert zurück.

Symlinks innerhalb des erlaubten Verzeichnisses sind zulässig; aus dem
Verzeichnis herausführende Symlinks werden abgewiesen. Linux ermittelt den
Descriptorpfad über `/proc/self/fd`, macOS über `F_GETPATH`. Auf Plattformen
ohne sichere Verifikation schlägt die Auflösung geschlossen fehl.

Aufgelöste Werte werden als Pydantic `SecretStr` in `ResolvedSecrets` gehalten.
Deren Debug-Repräsentation zeigt nur die Anzahl der Werte.

## 7. Security-Hardening A.1.1

Der gehärtete Stand umfasst:

- verbotene URL-Userinfo, Query-Parameter und Fragmente in Service-Endpunkten,
- erlaubte legitime API-Pfade, verbunden mit der dokumentierten Regel, niemals
  Zugangsdaten in Pfadsegmenten abzulegen; es gibt bewusst keine Token-Heuristik,
- absolute Readiness-Pfade ohne Query oder Fragment,
- Redigieren dynamischer Mapping-Schlüssel und sämtlicher Eingabewerte aus
  Validierungsfehlern,
- kontrollierte Behandlung ungültiger UTF-8-Konfiguration und -Secrets,
- descriptorbasierte Symlink-/Race-Prüfungen sowie Fail-closed-Verhalten,
- eine maximale Secret-Dateigröße von 1 MiB, geprüft über `fstat` und zusätzlich
  durch einen begrenzten Lesepfad mit einem Prüfbyte,
- Zurückweisen von Broadcast- und Multicast-MAC-Adressen bei Wake-on-LAN bei
  gleichzeitiger Unterstützung global und lokal administrierter Unicast-MACs,
- unveränderliche Pydantic-Modelle sowie Worker-, Service- und Secret-Mappings,
- Sentinel-Tests gegen Secret-Leaks in Meldung, `repr` und relevanten
  Traceback-Locals.

## 8. Linux-CI A.1.2

Der [CI-Workflow](../../../.github/workflows/ci.yml) läuft bei Pushes auf
`main`, Pull Requests gegen `main` und manueller Auslösung. Er verwendet
`ubuntu-latest`, Python 3.12, nur Leserechte auf Repository-Inhalte und
SHA-fixierte offizielle Actions.

Nach `python -m pip install -e ".[dev]"` laufen nacheinander:

1. `python -m pytest`
2. `python -m ruff check .`
3. `python -m ruff format --check .`
4. ein Import-Smoke-Test des Konfigurationspakets

Der Workflow verwendet keine Repository-Secrets, keine selbst gehosteten Runner
und keine Deployment-Schritte. [PR #1](https://github.com/madebyzwen/oasix/pull/1)
wurde mit einem bestandenen Check in `main` integriert.

## 9. Technische Entscheidungen und Alternativen

- **Pydantic v2 und pydantic-settings:** einheitliche strikte Typ- und
  Bootstrap-Validierung statt eigener Parserlogik.
- **PyYAML SafeLoader plus Duplicate-Key-Prüfung:** YAML bleibt für Betreiber
  lesbar; mehrdeutige Mappings werden nicht stillschweigend akzeptiert.
- **Providervertrag für Secrets:** die erste Quelle ist dateibasiert, ohne das
  Runtime-Schema an einen künftigen Secret Store zu koppeln.
- **Descriptorverifikation:** höhere Sicherheit als eine getrennte
  Pfadprüfung; nicht unterstützte Plattformen werden zugunsten von Fail-closed
  nicht best-effort bedient.
- **Keine URL-Pfadheuristik:** mögliche Tokens sind in beliebigen Pfaden nicht
  zuverlässig erkennbar. Eine Heuristik würde falsche Sicherheit erzeugen.
- **`MappingProxyType`:** einfache Immutability verschachtelter Mappings ohne
  zusätzliche projektspezifische Collection-Abstraktion.

Siehe auch das [Entscheidungsregister](../decisions.md).

## 10. Tests und Qualitätssicherung

Die Tests verwenden nur temporäre Dateien und neutrale Konfigurationsdaten; sie
benötigen keine produktiven Secret-Dateien und führen keine externe
Netzwerkkommunikation aus.

Abgedeckt sind unter anderem:

- gültige Konfiguration, Beispielkonfiguration, mehrere Worker und aktive
  Worker-Auswahl,
- Bootstrap-Umgebungsvariablen und Trennung der Konfigurationsschichten,
- Pflichtfelder, unbekannte Felder, Wertebereiche, abhängige Felder, doppelte
  YAML-Schlüssel und ungültiges UTF-8,
- URL-, Readiness- und MAC-Validierung,
- Immutability der Runtime-Mappings,
- Secret-Auflösung, fehlende/leere/ungültige/zu große Secrets,
- Path Traversal, interne und externe Symlinks, ersetzte Verzeichnisse,
  Descriptor-Fail-closed und während des Lesens wachsende Dateien,
- Secret-Redaktion in Fehlern und Debug-Repräsentationen.

Nachgewiesener Stand am 9. Oktober 2026:

- lokal auf der macOS-Entwicklungsumgebung: **53 pytest-Tests bestanden**,
  Ruff-Linting und Ruff-Formatprüfung bestanden, Import-Smoke-Test bestanden;
- Linux-CI: der Check von [PR #1](https://github.com/madebyzwen/oasix/pull/1)
  ist auf GitHub als bestanden ausgewiesen.

Die Testanzahl ist eine Momentaufnahme und muss bei späteren Änderungen anhand
des jeweiligen CI-Laufs aktualisiert oder als historischer Wert gekennzeichnet
werden.

## 11. Einschränkungen und verbleibende Risiken

- In-place-Schreibzugriffe auf bereits geöffnete Secret-Dateien können nicht
  vollständig verhindert werden. Secret-Mount und Dateien müssen im Betrieb
  read-only und restriktiv berechtigt sein.
- Zugangsdaten in URL-Pfadsegmenten werden nicht automatisch erkannt; ihre
  Vermeidung ist eine Betreiber- und Review-Regel.
- Descriptorpfade werden nur unter Linux und macOS unterstützt; andere
  Plattformen schlagen geschlossen fehl.
- Reale Secret-Mounts, Dateisystemrechte und Containerumgebungen wurden nicht
  in einem Produktionsdeployment getestet.
- Die Konfiguration modelliert Wake, Sleep und Readiness, führt aber keine
  Netzwerk- oder Power-Aktion aus.
- Persistenz, Migrationen, Recovery, Leases, APIs und strukturierte
  Produktionslogs sind noch nicht implementiert.

## 12. Relevante Commits und Pull Requests

| Referenz | Inhalt |
| --- | --- |
| [Commit `6296d62`](https://github.com/madebyzwen/oasix/commit/6296d6267776fd35827fc59acf6793b93657643e) | Konfigurationsfundament und erster Hardening-Stand |
| [Commit `c4bdf95`](https://github.com/madebyzwen/oasix/commit/c4bdf9527a117468c61132607a9baa82e8f8a0b5) | Secret-Größenlimit, URL-/MAC-Hardening und Grenztests |
| [Commit `470204a`](https://github.com/madebyzwen/oasix/commit/470204a2d0c3b9c9603d418e69c6fb087466e973) | Linux-CI-Workflow |
| [PR #1](https://github.com/madebyzwen/oasix/pull/1) | Integration der Linux-CI in `main` |
| [Merge-Commit `0aea2f0`](https://github.com/madebyzwen/oasix/commit/0aea2f0c16278ee770546597c355a851d7f856b2) | Merge von PR #1 |

Für A.1 und A.1.1 existieren in der geprüften Historie keine separaten Pull
Requests; deshalb werden keine solchen Links behauptet.

## 13. Abnahmestatus

**A.1, A.1.1 und A.1.2 sind für ihren dokumentierten Umfang abgeschlossen.**

Die Abnahme stützt sich auf den implementierten Repository-Stand, 53 lokal
bestandene Tests, erfolgreiche lokale Qualitätsprüfungen und den bestandenen
Linux-CI-Check von PR #1. Sie ist keine Abnahme späterer Requirement-Phasen und
keine Bestätigung eines Produktionsdeployments.
