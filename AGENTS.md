# OASIX – verbindliche Entwicklungsregeln

Diese Regeln gelten für alle Änderungen in diesem Repository. Einzige
verbindliche Architekturgrundlage ist das vollständige
[`OASIX Technical Requirement v3.4`](docs/OASIX_Technical_Requirement_Reviewed_v3.4.docx).
Bei Widersprüchen oder Unklarheiten ist vor der Implementierung nachzufragen.

## Architektur und Deployment

- OASIX ist eine hardwareunabhängige KI-Orchestrierungsplattform. Control Plane
  und Compute Worker sind technisch und betrieblich getrennt.
- Die dauerhaft verfügbare Control Plane stellt das API-Gateway bereit und
  verantwortet Persistenz, Dispatch, Worker-Steuerung, Lease Registry,
  Retry-/Sleep-Logik, Recovery und Telemetrie.
- KI-Inferenz, Agenten-Ausführung und rechenintensive Tool-Ausführung finden
  ausschließlich auf dem jeweils aktiven Compute Worker statt.
- Das aktuelle Beispieldeployment betreibt `ai-oasix` auf einem NAS mit Docker,
  den bestehenden Stack `ai-llm` und den geplanten Stack `ai-worker` auf einem
  Ubuntu-Server mit Docker sowie Open WebUI als eigenständigen Dienst auf dem
  NAS. Diese Hosts sind keine zwingenden Voraussetzungen für andere
  Installationen.
- `ai-oasix`, `ai-llm`, `ai-worker` und Open WebUI bleiben eigenständige
  Deployment-Einheiten. Insbesondere sind `ai-oasix` und `ai-worker`
  unabhängig versionierbar, deploybar und aktualisierbar.

## Rollen, Portabilität und Konfiguration

- Geschäftslogik, Datenmodell, öffentliche API-Pfade und Adapter-Verträge
  dürfen nicht von konkreter Hardware, privaten Hostnamen, IP-Adressen,
  lokalen DNS-Namen oder anderen installationsspezifischen Bezeichnungen
  abhängen.
- Technische Komponenten verwenden generische Rollen und Identifikatoren wie
  `control_plane`, `active_worker` und `worker_id`.
- Reale Zielhosts werden ausschließlich über externe, validierte
  Worker-Profile zugeordnet. Host, Ports, SSH, Authentifizierungsreferenzen,
  Wake-/Sleep-Methoden, Service-Endpunkte, Readiness-Probes und Policies gehören
  in die externe Konfiguration und werden nicht im Code fest verdrahtet.
- Konfigurationen besitzen eine `schema_version` und werden beim Start
  vollständig validiert. Bei ungültigen Pflichtwerten oder fehlenden Secrets
  darf die Anwendung nicht halbkonfiguriert starten.
- Hardware, Betriebssysteme, Modelle und Agent-Frameworks bleiben
  austauschbar. Das Umbenennen oder Austauschen eines Hosts sowie der Wechsel
  des aktiven Compute Workers dürfen keine Änderungen am Anwendungscode oder
  an stabilen Client-APIs erfordern.
- LLM- und Agent-Dienste werden als benannte Services mit Endpoint,
  Authentifizierungsreferenz und Readiness-Probe konfiguriert. Die
  Agent-Anbindung erfolgt über providerneutrale Adapter.
- Agent-Rollen sind logische Capabilities. Die Kernlogik darf weder ein
  bestimmtes Agent-Framework noch getrennte Prozesse pro Rolle voraussetzen.

## APIs und Sicherheitsgrenzen

- Die OpenAI-kompatible Client-API verwendet stabile Pfade unter `/v1/...`.
- Management- und Job-APIs verwenden generische, versionierte Pfade unter
  `/api/v1/...`. Die Endpunkte `/api/v1/worker`,
  `/api/v1/worker/wake`, `/api/v1/worker/sleep` und
  `/api/v1/worker/force-sleep` sind ausdrücklich zulässig. Private Hostnamen
  oder installationsspezifische Service-Aliasse sind in API-Verträgen
  unzulässig.
- Client- und Management-APIs erfordern Authentifizierung. Administrative
  Power-Aktionen dürfen nicht mit einem unprivilegierten Inference-Key
  ausgelöst werden.
- Secrets dürfen niemals in Git, Quelltext, Hauptkonfiguration, Logs oder
  Fehlerantworten erscheinen. Konfigurationen enthalten nur
  Secret-Referenzen; Werte stammen aus vorgesehenen Secret-Quellen.
- Logs redigieren Secrets, Authorization-Header, private Schlüssel und
  vergleichbare Zugangsdaten.

## Jobs, Attempts, Persistenz und Recovery

- Queues, Jobs, Attempts, Leases, Control-State und zuordenbare Telemetrie
  werden persistent und migrationsfähig auf der Control Plane verwaltet.
- Jobs und Ausführungsversuche sind getrennte Entitäten. Ein Job behält seine
  stabile `job_id`; jeder konkrete Versuch erhält eine eigene `attempt_id`.
  Ein Retry erzeugt einen neuen Attempt und überschreibt keine früheren Mess-
  oder Fehlerdaten.
- Nach einem Neustart werden zuvor aktive Jobs und Attempts mit dem realen
  Worker- und Service-Zustand abgeglichen. Ein nicht nachweisbar laufender
  Attempt darf nicht stillschweigend `RUNNING` bleiben.
- Retry- und Wake-up-Versuche sind begrenzt. Timeout, Backoff, optionaler Jitter,
  Grace Period und Concurrency-Limits sind konfigurierbar und nicht fest im
  Code verdrahtet.

## Leases und Energiemanagement

- Die Lease Registry ist die einzige maßgebliche Quelle dafür, ob ein Compute
  Worker aktiv genutzt wird. Zusätzliche parallele Aktivitätszähler sind
  unzulässig.
- Jede aktive Nutzung – einschließlich LLM-Anfragen, Agent-Ausführung, Builds,
  Tests und Development – benötigt eine Lease mit `lease_id`, `worker_id`,
  Owner/Purpose und TTL beziehungsweise Heartbeat. Verwaiste Leases müssen
  auslaufen oder bei Recovery bereinigt werden.
- Automatic Sleep ist nur zulässig, wenn keine aktive Lease und keine
  unmittelbar ausführbare oder fällige Arbeit existiert, das konfigurierte
  Idle-Timeout abgelaufen ist und der Worker `READY` oder `IDLE` ist.
- Manual Sleep prüft dieselben Bedingungen. Force Sleep pausiert neuen
  Dispatch, beachtet eine konfigurierbare Grace Period, protokolliert den
  Eingriff und markiert nicht sauber beendete Attempts als `INTERRUPTED`.
- Eine externe Wartephase darf eine Lease nur nach expliziter Bestätigung der
  sicheren Wiederaufnehmbarkeit und Persistierung einer opaken
  Fortsetzungsreferenz freigeben.

## Entwicklung und Qualität

- Die Initialentwicklung des Control-Plane-Fundaments erfolgt derzeit
  ausnahmsweise lokal auf dem Mac. Langfristig werden Worker-, Agent- und
  Coding-Workloads per VS Code Remote SSH direkt auf dem aktiven Compute Worker
  entwickelt und getestet.
- Aktive Development-Nutzung verhindert Automatic Sleep über eine Lease oder
  einen konfigurierbaren Activity-Probe. Die Kernlogik darf nicht an einen
  bestimmten VS-Code-Prozessnamen gekoppelt sein.
- Neue Funktionen und Fehlerkorrekturen benötigen passende automatisierte
  Tests. Diese decken insbesondere die betroffenen Persistenz-, Recovery-,
  Lease-, Retry-, API- und Sicherheitsinvarianten ab.
- Neue Entwicklungsphasen und wesentliche Architekturentscheidungen müssen in
  `docs/development/` dokumentiert und mit den zugehörigen Änderungen im selben
  Commit beziehungsweise Pull Request synchron gehalten werden.
- Jede Implementierung muss das Technical Requirement v3.4 erfüllen. Dessen
  MUSS-Anforderungen sind verbindlich; SOLL-Abweichungen werden begründet und
  dokumentiert.
