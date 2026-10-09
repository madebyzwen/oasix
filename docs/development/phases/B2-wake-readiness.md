# Phase B.2 – Wake-on-LAN und Worker-Bereitschaft

Status: implementiert, unabhängige Review-Abnahme ausstehend

## 1. Ziel und Abgrenzung

B.2 implementiert die produktive Wake-on-LAN-Paketübergabe für den
konfigurierten aktiven Worker sowie ein begrenztes Gate, das erst nach
bestätigter Readiness aller angeforderten Services erfolgreich zurückkehrt.

Nicht enthalten sind persistierte Worker-Zustandsübergänge, Leases, Dispatch,
Sleep-Ausführung, Recovery, LLM-Weiterleitung, Streaming oder Telemetrie. Ein
erfolgreicher Datagrammversand wird ausdrücklich nicht als Worker- oder
Service-Bereitschaft interpretiert.

## 2. Verbindliche Anforderungen

| Anforderung | Umsetzung in B.2 |
| --- | --- |
| ARC-03, ALIAS-01 bis ALIAS-03, AC-01 | Controller und Orchestrator verwenden nur die generische Worker-ID und die externe Konfiguration. Hardware, Host und Netzwerkziel sind nicht fest codiert. |
| CFG-02, PWR-02 | `create_worker_wake_controller()` wählt ausschließlich die Wake-Methode des aktiven Worker-Profils. `wol` sendet ein Magic Packet; `none` bleibt ein kontrollierter No-op. |
| CFG-05, REC-03 | Die Zahl der Wake-Versuche, exponentieller Backoff, Obergrenze und optionaler Jitter stammen aus `policies.retry.wake`. Es gibt keine unbegrenzte Schleife. |
| WRK-03, WRK-04 | Alle angeforderten Services müssen einzeln `ready=True` bestätigen. Ein früher positiver Dienst ersetzt keine ausstehende Probe. |
| WRK-05 | Wake-Aufruf und jede Readiness-Probe sind zusätzlich mit `readiness_timeout_seconds` begrenzt. Ein Timeout führt in den begrenzten Retrypfad. |
| SEC-03, AC-10 | Transportfehler, Zielhost und Senderdetails werden nicht in öffentliche Exceptions oder geprüfte Produktions-Traceback-Frames übernommen. |
| AC-03 | Der Ablauf Wake plus servicebezogene Readiness ist als Gate implementiert. Die spätere Auslösung durch eine LLM-Anfrage bleibt wegen der Lease-Abhängigkeit außerhalb von B.2. |

## 3. Architektur und Schnittstellen

`create_worker_wake_controller(runtime)` bindet genau den durch
`active_worker` ausgewählten Worker. Für `wol` erzeugt der Controller aus der
validierten Unicast-MAC das standardisierte Paket aus sechs `0xFF`-Bytes und 16
Wiederholungen der MAC-Adresse. Das 102-Byte-Paket wird über einen nicht
blockierenden IPv4-UDP-Socket mit aktivierter Broadcast-Option an konfigurierten
Host und Port gesendet.

`WorkerReadinessOrchestrator` kombiniert ausschließlich bestehende Ports:

```text
initiale Readiness aller benötigten Services
              |
              +-- bereit --> Erfolg
              |
              v
Wake (begrenzt) -> Policy-Delay -> Readiness aller Services
              |                         |
              +---- höchstens max_attempts ----+
                                        |
                           erschöpft -> UNAVAILABLE-Fehler
```

Die optionale Jitter-Ratio wird symmetrisch angewendet: Eine Ratio von `0.2`
variiert den begrenzten Basisdelay innerhalb von 80 bis 120 Prozent. Ohne Jitter
bleibt der Backoff deterministisch. Die Orchestrierung setzt keinen
`WorkerLifecycleState`; dadurch wird keine in v3.4 noch nicht zugewiesene
State-Autorität vorweggenommen.

## 4. Entscheidungen

[OASIX-DEC-018](../decisions.md#oasix-dec-018--begrenzte-wake--und-readiness-orchestrierung-ohne-state-automat)
dokumentiert die Trennung von Paketübergabe, Readiness-Gate und späterer
Zustandsführung.

## 5. Umsetzung

- `src/oasix/worker/wake.py`: Wake-on-LAN- und No-Wake-Controller, Magic Packet,
  asynchrone DNS-Auflösung und UDP-Broadcast.
- `src/oasix/worker/orchestration.py`: begrenzte Versuche, Timeout,
  Backoff/Jitter und Multi-Service-Readiness-Gate.
- `src/oasix/worker/errors.py`: fester `WorkerUnavailableError`.
- `src/oasix/worker/__init__.py`: öffentliche Controller-, Factory- und
  Orchestrator-Exporte.
- `tests/test_worker_wake.py`: aktiver Worker, Paketformat, `none` und sichere
  Transportfehler.
- `tests/test_worker_readiness_orchestration.py`: Retry-, Timeout-, Jitter-,
  Multi-Service- und B.1/B.2-Integrationstests.

Es wurden keine Konfigurationsmodelle, Datenbanktabellen oder Migrationen
geändert.

## 6. Tests und Nachweise

| Prüfung | Umgebung | Ergebnis | Nachweis |
| --- | --- | --- | --- |
| Wake-/Readiness-Tests | lokal, macOS, Python 3.12 | 11 bestanden | `tests/test_worker_wake.py`, `tests/test_worker_readiness_orchestration.py` |
| Vollständige pytest-Suite | lokal, macOS, Python 3.12 | 249 bestanden | `feature/b-llm-path` |
| Ruff Linting und Formatprüfung | lokal, macOS, Python 3.12 | bestanden, 54 Dateien geprüft | `feature/b-llm-path` |
| Paket-, Import- und Diff-Prüfung | lokal | bestanden | `feature/b-llm-path` |
| Linux-CI | GitHub Actions, Ubuntu, Python 3.12 | nach Push ausstehend | zukünftiger Phase-B-Pull-Request |

Alle Tests verwenden Fakes beziehungsweise HTTPX-Testtransporte. Es werden
weder echte Broadcast-Pakete noch produktive HTTP-Anfragen gesendet.

## 7. Einschränkungen und Risiken

- Wake-on-LAN hängt von Broadcast-Routing, DNS, Firewall und Netzwerkkarte des
  konkreten Deployments ab; diese Eigenschaften sind lokal nicht verifizierbar.
- `readiness_timeout_seconds` begrenzt in B.2 sowohl die Wake-Übergabe als auch
  jede einzelne Probe, weil v3.4 keinen separaten Wake-Transport-Timeout
  konfiguriert. Ein eigener Policywert wäre eine spätere Schemaentscheidung.
- Services werden pro Versuch sequenziell geprüft. Das ist deterministisch und
  begrenzt; eine spätere Parallelisierung muss dieselben Timeout- und
  Fehlergrenzen erhalten.
- Worker-State-Persistenz und Übergänge wie `UNKNOWN -> WAKING -> READY` sind
  nicht Teil dieses Gates und müssen mit klarer State-Autorität später folgen.

## 8. Abnahmestatus

B.2 ist implementiert und lokal vollständig geprüft, aber bis zum unabhängigen
Code-Review nicht freigegeben. B.3 bis B.5 wurden in diesem Commit nicht
begonnen.

## 9. GitHub-Referenzen

- Commits: `feat(worker): implement wake and readiness orchestration` auf
  `feature/b-llm-path`; vollständiger SHA nach Commit im Pull Request
- Pull Requests: nach Abschluss beziehungsweise Stopp der sequenziellen Arbeit
  zu erstellen
- CI-Läufe: nach Push und Pull-Request-Erstellung ausstehend
