# OASIX

OASIX ist eine hardwareunabhängige KI-Orchestrierungsplattform. Ihre dauerhaft
verfügbare Control Plane stellt stabile Schnittstellen für interaktive
LLM-Anfragen und asynchrone Jobs bereit, verwaltet den Arbeitszustand persistent
und steuert einen bedarfsgesteuerten Compute Worker einschließlich Wake-up,
Readiness und Sleep.

Ziel ist eine klare technische Trennung von Orchestrierung und Rechenleistung.
Worker-Hardware, Betriebssysteme, Modelle und Agent-Frameworks bleiben über
externe Konfiguration, Worker-Profile und providerneutrale Adapter
austauschbar. Ein Wechsel des Compute-Servers erfordert weder Änderungen am
OASIX-Anwendungscode noch an stabilen Client-APIs.

## Architekturüberblick

```text
Open WebUI und weitere Clients
             |
             | /v1/... und /api/v1/...
             v
Control Plane: API-Gateway, Persistenz, Dispatch,
               Leases, Power-Steuerung und Telemetrie
             |
             | validierte Worker-Profile und Service-Endpunkte
             v
Compute Worker: LLM-Inferenz, Agenten und rechenintensive Tools
```

Die Control Plane entscheidet, orchestriert und speichert den Zustand. Queues,
Jobs, Attempts, Leases, Control-State und zuordenbare Telemetrie überleben dort
einen Neustart. Jobs und ihre einzelnen Ausführungsversuche sind getrennte
Entitäten: Ein Job behält seine Identität, während jeder Retry einen neuen
Attempt erzeugt.

Der aktive Compute Worker führt KI-Inferenz, Agenten und rechenintensive Tools
aus. Jobs werden erst übergeben, wenn der Worker und die jeweils benötigten
Dienste bereit sind. Agent-Rollen wie Research, Dokumentation und Coding sind
logische Fähigkeiten und nicht an ein bestimmtes Framework oder eine feste
Prozessaufteilung gekoppelt.

## Beispieldeployment

Das aktuelle Deployment ist ein Beispiel und keine zwingende Voraussetzung
für andere OASIX-Installationen:

| Rolle und Beispielhost | Stack / Dienst | Aufgabe |
| --- | --- | --- |
| Control Plane – NAS mit Docker | `ai-oasix` | Eigenständige OASIX-Control-Plane mit API-Gateway, Persistenz, Dispatch, Lease Registry, Retry-/Sleep-Logik, Recovery und Telemetrie |
| NAS mit Docker | Open WebUI | Eigenständiger Client-Dienst; bleibt vom OASIX-Stack getrennt |
| Compute Worker – Ubuntu-Server mit Docker | `ai-llm` | Bestehender, eigenständiger LLM-Stack mit llama.cpp |
| Compute Worker – Ubuntu-Server mit Docker | `ai-worker` | Geplanter, eigenständiger Stack für Agent-, Tool- und Worker-Runtime |

Im Beispiel läuft die dauerhaft verfügbare Control Plane im Stack `ai-oasix`
auf einem NAS mit Docker. Open WebUI bleibt dort ein eigenständiger Dienst. Ein
Ubuntu-Server mit Docker ist der aktuelle, bedarfsgesteuerte Compute Worker und
betreibt `ai-llm` sowie später `ai-worker`. Die Deployment-Einheiten bleiben
getrennt; insbesondere sind `ai-oasix` und `ai-worker` unabhängig versionierbar
und deploybar.

Andere Hosts oder Betriebssysteme können über passende Worker-Profile und
Adapter angebunden werden. Private Hostnamen, IP-Adressen, lokale DNS-Namen und
konkrete Hardware sind keine Bestandteile der Architektur oder öffentlichen
Schnittstellen.

## Schnittstellen, Konfiguration und Sicherheit

OpenAI-kompatible Client-Endpunkte bleiben unter `/v1/...` stabil. Management-
und Job-Endpunkte liegen getrennt und versioniert unter `/api/v1/...`.
Technische Rollen und IDs wie `control_plane`, `active_worker` und `worker_id`
ersetzen installationsspezifische Bezeichnungen in Geschäftslogik,
Datenmodell und API-Verträgen.

Worker-Endpunkte, Authentifizierungsreferenzen, Wake-/Sleep-Methoden,
Readiness-Probes, Services und Policies werden extern konfiguriert und beim
Start vollständig validiert. Client- und Management-APIs erfordern
Authentifizierung; administrative Power-Aktionen sind nicht mit einem
unprivilegierten Inference-Key zulässig. Secrets stehen weder im Repository
noch im Klartext in der Hauptkonfiguration, in Logs oder Fehlerantworten.

## Worker-Lebenszyklus und Energiemanagement

Ein zunächst unbekannter oder schlafender Worker wird bei Bedarf beispielsweise
per Wake-on-LAN geweckt. OASIX wartet anschließend mit begrenztem Timeout auf
die servicebezogene Readiness. Erst wenn Worker und benötigte Dienste bereit
sind, wird eine Anfrage oder ein Job übergeben. Die Zustände des MVP sind
`UNKNOWN`, `WAKING`, `READY`, `BUSY`, `IDLE`, `SLEEPING` und `UNAVAILABLE`.

Die Lease Registry ist die einzige maßgebliche Quelle für aktive
Worker-Nutzung. Jede LLM-Anfrage, Agent-Ausführung sowie Build-, Test- oder
Development-Aktivität erhält eine Lease mit TTL beziehungsweise Heartbeat.
Dadurch halten abgestürzte Clients oder Prozesse den Worker nicht dauerhaft
wach.

Automatischer Sleep ist nur erlaubt, wenn:

- keine aktive Lease existiert,
- keine unmittelbar ausführbare oder fällige Arbeit vorliegt,
- das konfigurierte Idle-Timeout abgelaufen ist und
- der Worker den Zustand `READY` oder `IDLE` hat.

Manual Sleep wendet dieselben Schutzbedingungen an. Force Sleep pausiert neuen
Dispatch, beachtet eine konfigurierbare Grace Period, protokolliert den Eingriff
und markiert nicht sauber beendete Attempts als `INTERRUPTED`. Nach einem
Neustart werden Worker-Zustand, aktive Attempts und Leases mit der Realität
abgeglichen. Retry- und Wake-up-Versuche sind begrenzt und konfigurierbar.

## Geplante MVP-Phasen

| Phase | Ergebnis |
| --- | --- |
| A – Fundament | Validiertes Konfigurationsschema und Secret-Referenzen, persistente Datenbank, generischer Worker und strukturierte Logs |
| B – LLM-Pfad | Health/Readiness, Wake-on-LAN, OpenAI-kompatibler Proxy mit Streaming sowie Wake- und Token-Telemetrie |
| C – Job/Power | Persistente Jobs und getrennte Attempts, Retry, Leases mit TTL, Idle/Manual/Force Sleep und Recovery |
| D – Agenten | Providerneutraler Agent-Adapter und erste Research-/Dokumentations-Runtime; Rollen werden konfiguriert |
| E – Development-Schutz | Konfigurierbarer Activity-Probe beziehungsweise Lease für VS Code/SSH, damit aktive Entwicklung Automatic Sleep blockiert |

Das Control-Plane-Fundament wird in der Aufbauphase ausnahmsweise lokal auf dem
Mac entwickelt und getestet. Langfristig finden Worker-, Agent- und
Coding-Arbeiten per VS Code Remote SSH direkt auf dem aktiven Compute Worker
statt.

## Linux-CI

GitHub Actions prüft Pushes auf `main`, Pull Requests gegen `main` sowie manuell
gestartete Läufe unter `ubuntu-latest` mit Python 3.12. Die Pipeline installiert
das Projekt einschließlich Entwicklungsabhängigkeiten und führt die vollständige
pytest-Suite, Ruff-Linting, den Ruff-Format-Check und einen Import-Smoke-Test aus.
Sie verwendet weder Repository-Secrets noch selbst gehostete Runner und führt
kein Deployment durch.

## Verbindliche Grundlage

Die vollständigen Architektur-, Sicherheits- und Akzeptanzanforderungen stehen
im [OASIX Technical Requirement v3.4](docs/OASIX_Technical_Requirement_Reviewed_v3.4.docx).
Es ist die einzige verbindliche Architekturgrundlage. Ergänzende Regeln für
Änderungen in diesem Repository enthält [AGENTS.md](AGENTS.md).

## Konfigurationsfundament

Die Control Plane lädt ihre Bootstrap-Informationen ausschließlich aus der
Umgebung. `OASIX_CONFIG_FILE` verweist auf die externe Runtime-Konfiguration;
`OASIX_SECRETS_DIRECTORY` verweist auf das externe Secret-Verzeichnis und hat
den Standardwert `/run/secrets`. Worker, Services, Power-Methoden und Policies
werden nicht über Bootstrap-Variablen definiert, sondern ausschließlich über
die vollständig validierte YAML-Konfiguration. Fremde Umgebungsvariablen werden
ignoriert; unbekannte Namen im reservierten Präfix `OASIX_` verhindern dagegen
den Start, ohne den unbekannten Namen in der Fehlermeldung wiederzugeben.

Eine neutrale Vorlage liegt unter
[`config/oasix.example.yaml`](config/oasix.example.yaml). Sie enthält nur
Secret-Referenzen und reservierte `.invalid`-Domains. Eine Anwendung lädt die
Konfiguration vor dem Aufbau weiteren Zustands atomar:

```python
from oasix.config import load_startup_configuration

configuration = load_startup_configuration()
```

Fehlende Pflichtfelder, unbekannte Felder, ungültige Policies und nicht
auflösbare Secrets verhindern den Start. Secret-Inhalte werden in
Fehlermeldungen und Debug-Repräsentationen nicht ausgegeben.

Service-Basis-URLs dürfen weder URL-Userinfo noch Query-Parameter oder Fragmente
enthalten. Legitime API-Pfade bleiben erlaubt. Zugangsdaten dürfen jedoch auch
nicht in URL-Pfadsegmenten abgelegt werden, sondern ausschließlich über
Secret-Referenzen bereitgestellt werden. Da beliebige Pfadbestandteile nicht
zuverlässig als Zugangsdaten erkennbar sind, setzt OASIX hierfür bewusst keine
scheinbar sichere Token-Heuristik ein; die Einhaltung dieser Regel liegt bei der
Konfiguration und ihrem Deployment-Prozess.

Secret-Dateien werden relativ zu einem geöffneten Verzeichnis-Descriptor
geöffnet. Anschließend werden Typ, kanonischer Pfad sowie Device- und Inode-ID
des tatsächlich geöffneten Datei-Descriptors geprüft, bevor aus genau diesem
Descriptor gelesen wird. Linux verwendet dafür `/proc/self/fd`, macOS
`F_GETPATH`; kann der geöffnete Pfad nicht sicher bestimmt werden, schlägt der
Start geschlossen fehl.

Eine einzelne Secret-Datei darf höchstens 1 MiB (1.048.576 Bytes) groß sein.
OASIX prüft sowohl die über den geöffneten Descriptor gemeldete Dateigröße als
auch die tatsächlich gelesene Datenmenge; dadurch bleibt das Einlesen selbst
bei einer nachträglich wachsenden Datei begrenzt.

Die Descriptor-Prüfung verhindert keinen in-place Schreibzugriff auf eine
bereits geöffnete Datei. Das Secret-Verzeichnis und seine Dateien müssen daher
für die Control Plane und nicht vertrauenswürdige Prozesse unveränderlich sein
und sollen im Deployment read-only mit restriktiven Berechtigungen
bereitgestellt werden.
