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
- [Vorlage für zukünftige Phasen](templates/phase-template.md)

Das A.2-Design ist abgeschlossen. A.2.1 implementiert Runtime-Schema v2 und das
SQLite-/SQLAlchemy-Fundament; A.2.2 implementiert die sechs Kerntabellen und
die Alembic-Initialmigration; A.2.3 implementiert Repository-Grenzen, zentrale
Anwendungsvalidierung, sichere Persistenzfehler und Revisionsschutz. Fachliche
Zustandsautomaten und konkrete Payload-/Adapterverträge bleiben späteren
Phasen vorbehalten.

A.3.1 ist unabhängig geprüft und implementiert die generische,
transportneutrale Worker-Vertragsgrenze mit konfigurationsgebundener Identität,
Zustandsbeobachtung, servicebezogener Readiness sowie vorbereitenden
Wake-/Sleep-Ports. A.3.2 ergänzt ein implementiertes, noch nicht unabhängig
freigegebenes JSON-Lines-Loggingfundament mit geschlossener Feld-Allowlist.
Produktive Adapter und Orchestrierungslogik bleiben späteren Teilphasen
vorbehalten.

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
