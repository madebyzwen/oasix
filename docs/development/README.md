# OASIX-Entwicklungsdokumentation

Diese Dokumentation hält den tatsächlichen Entwicklungsstand, technische
Entscheidungen und die Abnahme einzelner Phasen nachvollziehbar fest. Sie richtet
sich an Maintainer und Reviewer des OASIX-Repositories.

## Geltungsbereich

Das [OASIX Technical Requirement v3.4](../OASIX_Technical_Requirement_Reviewed_v3.4.docx)
bleibt die einzige verbindliche Architekturgrundlage. Die Dokumente in diesem
Verzeichnis ersetzen oder erweitern das Requirement nicht. Sie ordnen dessen
Vorgaben konkreten Implementierungsphasen zu und unterscheiden dabei zwischen:

- verbindlichen oder geplanten Anforderungen,
- tatsächlich im Repository implementierten Funktionen und
- durch automatisierte Tests oder CI-Läufe nachgewiesenen Ergebnissen.

## Navigation

- [Roadmap und Phasenstatus](roadmap.md)
- [Architektur- und Implementierungsentscheidungen](decisions.md)
- [Phase A.1 – Konfiguration und Secrets](phases/A1-configuration.md)
- [Phase A.2 – Persistenzdesign](phases/A2-persistence.md)
- [Phase A.3.1 – Generische Worker-Verträge](phases/A3-worker-contracts.md)
- [Phase A.3.2 – Strukturiertes Logging](phases/A3-logging.md)
- [Phase B.1 – HTTP-Service-Readiness](phases/B1-http-readiness.md)
- [Phase B.2 – Wake-on-LAN und Bereitschaft](phases/B2-wake-readiness.md)
- [Phase B.3.0 – Client-API-Authentifizierung](phases/B3-client-auth.md)
- [Phase B.3.1 – Nicht streamender LLM-Proxy](phases/B3-llm-proxy.md)
- [Phase B.4 – Streaming und Cancellation](phases/B4-streaming.md)
- [Phase B.5 – LLM-Telemetrie und Integration](phases/B5-telemetry-integration.md)
- [Abschluss Phase B](phases/B-phase-completion.md)
- [Historischer B.3-Abhängigkeitsnachweis](phases/B3-llm-proxy-blocked.md)
- [Phase C.1 – Persistenter Lease-Lifecycle](phases/C1-lease-lifecycle.md)
- [Vorlage für zukünftige Phasen](templates/phase-template.md)

Das A.2-Design ist abgeschlossen. A.2.1 implementiert Runtime-Schema v2 und das
SQLite-/SQLAlchemy-Fundament; A.2.2 implementiert die sechs Kerntabellen und
die Alembic-Initialmigration; A.2.3 implementiert Repository-Grenzen, zentrale
Anwendungsvalidierung, sichere Persistenzfehler und Revisionsschutz. Fachliche
Zustandsautomaten und konkrete Payload-/Adapterverträge bleiben späteren
Phasen vorbehalten.

A.3.1 und A.3.2 sind unabhängig geprüft und abgeschlossen. Sie implementieren
die generische, transportneutrale Worker-Vertragsgrenze mit
konfigurationsgebundener Identität, Zustandsbeobachtung, servicebezogener
Readiness und vorbereitenden Wake-/Sleep-Ports sowie ein geschlossenes
JSON-Lines-Loggingfundament. Produktive Worker-Kommunikation, Power-Ausführung,
operative Readiness und weitere Orchestrierungslogik gehörten nicht zum
A.3-Umfang. Phase B implementiert inzwischen HTTP-Readiness und Wake-on-LAN;
Sleep, vollständige Worker-Zustandsführung und weitere Orchestrierung bleiben
späteren Phasen vorbehalten. Phase A des Requirements ist damit nicht insgesamt
abgeschlossen.

B.1 implementiert den produktiven asynchronen HTTP-Readiness-Adapter, B.2 die
konfigurierte Wake-on-LAN-Ausführung und eine begrenzte servicebezogene
Bereitschaftsorchestrierung für den aktiven Worker. Beide Umfänge sind
unabhängig geprüft und abgeschlossen.
C.1 ist als persistenter Lease-Lifecycle unabhängig geprüft und abgeschlossen;
B.3.0 stellt das getrennte Client-API-Authentifizierungs- und
Berechtigungsfundament bereit. B.3.1 implementiert darauf den nicht streamenden
`POST /v1/chat/completions`-Pfad mit Authentifizierung, begrenzter Concurrency,
persistenter Lease samt Heartbeat, Wake/Readiness und abgesichertem Upstream-
Transport. B.4 ergänzt darauf inkrementelle validierte SSE-Streams,
Disconnect-Behandlung und explizites Cleanup aller verschachtelten Ressourcen.
B.5 ergänzt sichere korrelierte LLM-Telemetrie und die übergreifenden
Integrationsnachweise. B.1 bis B.5 sind im dokumentierten Umfang implementiert,
unabhängig geprüft und freigegeben; der zusammenfassende Nachweis steht im
[Abschlussdokument Phase B](phases/B-phase-completion.md). C.1 wurde als
notwendige Lease-Grundlage vorgezogen und ist ebenfalls unabhängig geprüft und
freigegeben. Echte Hardware-, Netzwerk- und LLM-Dienste wurden durch die
automatisierten Testtransporte ausdrücklich nicht verifiziert.

## Pflegeregeln

1. Neue Entwicklungsphasen verwenden die [Phasenvorlage](templates/phase-template.md).
2. Roadmap, Entscheidungsregister und Phasendokument werden im selben Commit
   beziehungsweise Pull Request wie die zugehörige wesentliche Änderung
   aktualisiert.
3. Aussagen werden als geplant, implementiert oder nachgewiesen kenntlich
   gemacht. Geplante Funktionen dürfen nicht als vorhanden beschrieben werden.
4. Architekturentscheidungen erhalten eine stabile Kennung, Status,
   Begründung, Konsequenzen und überprüfbare Quellen.
5. Testergebnisse enthalten Umgebung, Zeitpunkt beziehungsweise zugehörigen
   CI-Lauf und den tatsächlich beobachteten Ausgang.
6. Private Infrastrukturbezeichnungen, Zugangsdaten und Secrets werden nicht
   dokumentiert.
7. Inhalte werden nicht unnötig aus dem [Repository-README](../../README.md),
   aus [AGENTS.md](../../AGENTS.md) oder aus dem Requirement dupliziert; diese
   Dokumentation verweist stattdessen auf die maßgebliche Quelle.

Commits und Pull Requests bilden den Änderungsnachweis. Nicht durch die
Repository-Historie belegbare Referenzen dürfen nicht nachträglich erfunden
werden.
