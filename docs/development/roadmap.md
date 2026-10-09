# Entwicklungsroadmap

Stand: 9. Oktober 2026

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
| A.2 – Persistenz | Geplant, nicht begonnen | Für Queue, Jobs, Attempts, Leases, Control-State und Telemetrie existiert noch keine Implementierung. SQLite ist die vorläufig geplante MVP-Datenbank; die konkrete Auswahl und Migrationstechnik werden zu Beginn von A.2 bestätigt. | Requirement JOB-01 bis JOB-05 und Abschnitt 13; [Entscheidung OASIX-DEC-005](decisions.md#oasix-dec-005--sqlite-als-geplante-mvp-persistenz) |

Phase A des Requirements ist trotz abgeschlossener A.1 noch nicht insgesamt
abgeschlossen: Persistenz und weitere Fundamentbestandteile folgen in späteren
Teilphasen.

## Weitere MVP-Stufen

| Stufe | Status | Geplanter Requirement-Umfang |
| --- | --- | --- |
| B – LLM-Pfad | Geplant, nicht begonnen | Health/Readiness, Wake-on-LAN-Ausführung, OpenAI-kompatibler Proxy, Streaming sowie Wake- und Token-Telemetrie |
| C – Job/Power | Geplant, nicht begonnen | Persistente Jobs und Attempts, Retry-Ausführung, Leases mit TTL, Idle-/Manual-/Force-Sleep und Recovery |
| D – Agenten | Geplant, nicht begonnen | Providerneutraler Agent-Adapter und erste Research-/Dokumentations-Runtime |
| E – Development-Schutz | Geplant, nicht begonnen | Konfigurierbarer Activity-Probe beziehungsweise Lease für Remote-Development |

Die Detailplanung dieser Stufen erfolgt erst beim tatsächlichen Phasenstart. Aus
der Roadmap folgt keine Aussage, dass die genannten Funktionen bereits im Code
vorhanden sind.
