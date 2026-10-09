# Entwicklungsroadmap

Stand: 10. Oktober 2026

Die Roadmap beschreibt den Repository-Stand, nicht den gesamten Zielumfang des
[Technical Requirement v3.4](../OASIX_Technical_Requirement_Reviewed_v3.4.docx).
„Abgeschlossen“ bedeutet hier nur, dass der für die genannte Teilphase
festgelegte Umfang implementiert und geprüft wurde.

## Aktueller Phasenstatus

| Phase | Status | Tatsächlicher Stand | Nachweis |
| --- | --- | --- | --- |
| A.1 – Konfiguration und Secrets | Abgeschlossen | Bootstrap- und Runtime-Konfiguration, Worker-/Service-Profile, Policies, Secret-Referenzen, atomarer Startup-Ladevorgang und Beispielkonfiguration sind implementiert. | [Phase A.1](phases/A1-configuration.md), [Commit `6296d62`](https://github.com/madebyzwen/oasix/commit/6296d6267776fd35827fc59acf6793b93657643e) |
| A.1.1 – Security & Quality Hardening | Abgeschlossen | Fehlerredaktion, sichere Secret-Dateizugriffe, Größenlimit, URL-Regeln, MAC-Prüfung und Immutability wurden gehärtet und getestet. | [Commit `6296d62`](https://github.com/madebyzwen/oasix/commit/6296d6267776fd35827fc59acf6793b93657643e), [Commit `c4bdf95`](https://github.com/madebyzwen/oasix/commit/c4bdf9527a117468c61132607a9baa82e8f8a0b5) |
| A.1.2 – Linux-CI | Implementiert und in `main` integriert | GitHub Actions führt Tests, Linting, Formatprüfung und Import-Smoke-Test mit Python 3.12 auf `ubuntu-latest` aus. | [PR #1](https://github.com/madebyzwen/oasix/pull/1), [Merge-Commit `0aea2f0`](https://github.com/madebyzwen/oasix/commit/0aea2f0c16278ee770546597c355a851d7f856b2) |
| A.2 – SQLite-Persistenzfundament | Abgeschlossen und unabhängig geprüft | Design, Runtime-Schema v2, abgesicherte SQLite-Verbindung, sechs migrierte Kerntabellen, Repository-Grenzen, zentrale Nutzdatenvalidierung und Integritätsnachweise sind im freigegebenen A.2-Umfang implementiert. | [Phase A.2](phases/A2-persistence.md), [Design-PR #3](https://github.com/madebyzwen/oasix/pull/3), [Implementierungs-PR #4](https://github.com/madebyzwen/oasix/pull/4) |
| A.2.1 – Runtime-Konfiguration und SQLite-Fundament | Abgeschlossen | Runtime-Schema v2, sichere Datenbankpfadprüfung, begrenzte SQLAlchemy-Engine, verifizierte SQLite-Pragmas sowie Session- und Transaktionskontexte sind implementiert und getestet. | [Phase A.2](phases/A2-persistence.md), [Commit `cba7be6`](https://github.com/madebyzwen/oasix/commit/cba7be6) |
| A.2.2 – Schema und Initialmigration | Abgeschlossen und unabhängig geprüft | Sechs SQLAlchemy-Modelle, ihre benannten Constraints und Indizes sowie die lineare Alembic-Initialrevision `0001_a2_2` sind implementiert und freigegeben. Runtime-Initialisierung führt keine automatische Migration aus. | [Phase A.2](phases/A2-persistence.md#4-entscheidungen-und-migrationsumfang), [Commit `09ea7f3`](https://github.com/madebyzwen/oasix/commit/09ea7f33347bc2763cae71ba8af5f8802288267b), [Linux-CI](https://github.com/madebyzwen/oasix/actions/runs/37993363281/job/114032986707) |
| A.2.3 – Persistenzzugriff und Integritätsnachweise | Abgeschlossen und unabhängig geprüft | Session-gebundene Repositories, zentrale typisierte Nutzdatenvalidierung, sichere Fehlerkategorien, lesender Revisionsschutz und die verbleibenden A.2-Integritätsnachweise sind implementiert und freigegeben. Nicht definierte Payload- und Referenzverträge werden geschlossen abgewiesen. | [Phase A.2](phases/A2-persistence.md#6-tests-und-nachweise), [Commit `c8f53b8`](https://github.com/madebyzwen/oasix/commit/c8f53b8b274bad3748aea48ad4172fb13dbfa60c), [Linux-CI](https://github.com/madebyzwen/oasix/actions/runs/37995302736/job/114039691204) |
| A.3.1 – Generische Worker-Verträge | Abgeschlossen und unabhängig geprüft | Konfigurationsgebundene Worker-Auflösung, kanonische Worker-Zustände sowie getrennte asynchrone Verträge für Beobachtung, servicebezogene Readiness, Wake und Sleep sind implementiert und freigegeben. Produktive Adapter und Orchestrierungslogik sind nicht enthalten. | [Phase A.3.1](phases/A3-worker-contracts.md), [Commit `766af949`](https://github.com/madebyzwen/oasix/commit/766af94962cd6fc99c42fc9fe7e784acb00f92d9), [PR #5](https://github.com/madebyzwen/oasix/pull/5) |
| A.3.2 – Strukturiertes Logging | Abgeschlossen und unabhängig geprüft | Isolierte JSON-Lines-Logger, statische Ereignisdefinitionen, zentrale Feld-Allowlist, Korrelationskennungen und sichere Fehlergrenzen sind implementiert und freigegeben. Operative Logaufrufe und externe Logging-Infrastruktur sind nicht enthalten. | [Phase A.3.2](phases/A3-logging.md), [Commit `647ca76`](https://github.com/madebyzwen/oasix/commit/647ca76eb9e0f881586a78d799def684ddbe7a84), [Linux-CI](https://github.com/madebyzwen/oasix/actions/runs/38000156438), [PR #5](https://github.com/madebyzwen/oasix/pull/5) |
| B.1 – HTTP-Service-Readiness | Abgeschlossen und unabhängig geprüft | Ein asynchroner HTTP-Adapter prüft GET-/HEAD-Readiness des aktiven Workers mit konfigurierten Statuscodes, Timeout und externer Authentifizierung. Redirects und Umgebungs-Proxies sind deaktiviert; Fehler bleiben transportneutral. | [Phase B.1](phases/B1-http-readiness.md), [Commit `462fb7b`](https://github.com/madebyzwen/oasix/commit/462fb7b1c1d31ac5d5413eda686e9bf17896889e), [PR #6](https://github.com/madebyzwen/oasix/pull/6) |
| B.2 – Wake-on-LAN und Worker-Bereitschaft | Abgeschlossen und unabhängig geprüft | Der aktive Worker wird ausschließlich über seine konfigurierte Wake-Methode angesprochen. Wake-Versuche, Backoff, optionaler Jitter und servicebezogene Readiness sind begrenzt; Wake-Erfolg gilt nicht als Bereitschaft. | [Phase B.2](phases/B2-wake-readiness.md), [Commit `c155dbd`](https://github.com/madebyzwen/oasix/commit/c155dbdad82305cde53ed37e09cdf51b8f508e87), [PR #6](https://github.com/madebyzwen/oasix/pull/6) |
| B.3 – OpenAI-kompatibler LLM-Proxy | Vor Implementierung weiter blockiert | C.1 stellt den persistenten Lease-Lifecycle bereit, ist aber noch nicht unabhängig freigegeben. Der verpflichtende Client-API-Authentifizierungs- und Berechtigungsvertrag fehlt weiterhin; B.3 enthält keinen API- oder Proxy-Code. | [B.3-Abhängigkeitsnachweis](phases/B3-llm-proxy-blocked.md), [Phase C.1](phases/C1-lease-lifecycle.md), [PR #6](https://github.com/madebyzwen/oasix/pull/6) |
| C.1 – Persistenter Lease-Lifecycle | Implementiert, Review ausstehend | Acquire, Lease-Heartbeat/Renew, idempotentes Release und persistente Aktivitätsauswertung sind transaktional implementiert. Die Lease Registry bleibt ohne redundante Zähler die einzige Aktivitätsquelle. | [Phase C.1](phases/C1-lease-lifecycle.md), [PR #6](https://github.com/madebyzwen/oasix/pull/6) |

Der festgelegte A.3-Umfang aus generischen Worker-Verträgen und strukturiertem
Logging ist abgeschlossen und unabhängig geprüft. Phase A des Requirements ist
trotz abgeschlossener A.1, abgeschlossenem A.2-Persistenzfundament und
abgeschlossenem A.3-Umfang noch nicht insgesamt abgeschlossen: Produktive
Worker-Kommunikation, Wake-/Sleep-Ausführung, operative Readiness, die
Integration operativer Logereignisse und weitere Orchestrierungsfunktionen
folgen in späteren Phasen.

## Weitere MVP-Stufen

| Stufe | Status | Geplanter Requirement-Umfang |
| --- | --- | --- |
| B – LLM-Pfad | B.1 und B.2 abgeschlossen; B.3 weiter blockiert; B.4/B.5 nicht begonnen | Health/Readiness, Wake-on-LAN-Ausführung, OpenAI-kompatibler Proxy, Streaming sowie Wake- und Token-Telemetrie |
| C – Job/Power | C.1 implementiert, Review ausstehend; weiterer Umfang nicht begonnen | Persistente Jobs und Attempts, Retry-Ausführung, Leases mit TTL, Idle-/Manual-/Force-Sleep und Recovery |
| D – Agenten | Geplant, nicht begonnen | Providerneutraler Agent-Adapter und erste Research-/Dokumentations-Runtime |
| E – Development-Schutz | Geplant, nicht begonnen | Konfigurierbarer Activity-Probe beziehungsweise Lease für Remote-Development |

Die Detailplanung dieser Stufen erfolgt erst beim tatsächlichen Phasenstart. Aus
der Roadmap folgt keine Aussage, dass die genannten Funktionen bereits im Code
vorhanden sind.
