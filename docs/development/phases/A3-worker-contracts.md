# Phase A.3.1 – Generische Worker-Verträge

Status: implementiert, unabhängig geprüft und freigegeben

## 1. Ziel und Abgrenzung

A.3.1 stellt die kleinste transportneutrale Schnittstellenbasis zwischen der
Control Plane und einem konfigurierten Compute Worker bereit. Die Phase bindet
eine generische Worker-ID an das durch `active_worker` ausgewählte, bereits
validierte Profil und definiert getrennte Verträge für Zustandsbeobachtung,
servicebezogene Readiness sowie die spätere Auslösung von Wake und Sleep.

Nicht enthalten sind produktive Transportadapter, Netzwerkzugriffe,
Wake-on-LAN-, SSH- oder HTTP-Ausführung, Zustandsübergänge, Persistenzzugriffe,
Timeout-/Retry-Orchestrierung, Dispatch, Leases, Recovery, LLM-Proxy,
Agenten-Ausführung und Worker-Service. Es wurden keine Tabellen oder
Infrastrukturkomponenten ergänzt.

## 2. Verbindliche Anforderungen

| Anforderung | Umsetzung in A.3.1 |
| --- | --- |
| ARC-01 bis ARC-04, DEP-02, DEP-04 | Die Control-/Compute-Grenze wird durch schlanke Control-Plane-Ports abgebildet; die Implementierung enthält keine Compute-Ausführung und keine Infrastrukturannahmen. |
| ALIAS-01 bis ALIAS-03 | Verträge verwenden ausschließlich `WorkerId`, `ServiceId` und das extern ausgewählte Profil; Host, IP, Hardware, Betriebssystem, Container und Transport erscheinen nicht in Methodensignaturen. |
| CFG-02 bis CFG-04 | `resolve_active_worker()` übernimmt ID und Profil aus `RuntimeConfig`; es gibt keine zweite Konfigurationsquelle, keine Datenbankkopie und keine neuen Secret-Typen. |
| WRK-01, WRK-02 | `WorkerLifecycleState` enthält exakt `UNKNOWN`, `WAKING`, `READY`, `BUSY`, `IDLE`, `SLEEPING` und `UNAVAILABLE`. Ein technischer Probe-Fehler ist eine Exception und kein bestätigter Zustand. |
| WRK-03 | Readiness wird über eine eigene Abfrage je konfigurierter `ServiceId` modelliert. |
| WRK-05 | Ein eigener Timeout-Fehlertyp ist vorhanden. Zeitbudget, Begrenzung und Retry bleiben Verantwortung späterer Orchestrierungslogik. |
| PWR-02 | Wake und Sleep besitzen getrennte, mechanismusunabhängige Ports. Die konkrete Methode stammt weiterhin ausschließlich aus dem Worker-Profil. |
| SEC-02, SEC-03, AC-10 | Workerfehler haben feste, sichere Meldungen ohne frei übergebbare Transportdetails; Ziel-Repräsentationen verbergen Profil-, Verbindungs- und Secret-Referenzdaten. |
| REC-04 | Kommunikations- und Timeoutfehler sind technisch unterscheidbar. Die spätere kontextabhängige Zuordnung zu `WAKE`, `READINESS` oder weiteren persistierten Fehlerklassen ist nicht Teil dieser Phase. |

AC-01 wird durch die Konfigurationsbindung vorbereitet, ist aber ohne konkrete
Adapter und Serverwechsel-Smoke-Test noch nicht vollständig nachgewiesen.

## 3. Architektur und Schnittstellen

`resolve_active_worker(runtime)` liefert ein unveränderliches
`ActiveWorkerTarget`. Es enthält die generische `worker_id` und das genau dazu
gehörige validierte `WorkerProfile`. Das Profil enthält nur die bereits in A.1
definierten Secret-Referenzen, niemals aufgelöste Secret-Werte. Seine
Repräsentation gibt keine Verbindungsdetails aus.

Die Ports sind nach Fähigkeiten getrennt:

| Vertrag | Operation | Semantik |
| --- | --- | --- |
| `WorkerIdentity` | `worker_id` | Bindet eine Adapterinstanz an genau eine generische konfigurierte Worker-ID. |
| `WorkerStateObserver` | `observe_state()` | Liefert nur nach erfolgreicher Beobachtung einen der sieben verbindlichen Zustände. |
| `ServiceReadinessProbe` | `check_readiness(service_id)` | Liefert ein typisiertes `ready`-Ergebnis für genau einen konfigurierten Dienst. |
| `WorkerWakeController` | `wake()` | Übergibt den konfigurierten Wake-Befehl; Erfolg behauptet noch keine Readiness. |
| `WorkerSleepController` | `sleep()` | Übergibt den konfigurierten Sleep-Befehl erst nach außerhalb des Adapters geprüften Schutzbedingungen. |

Alle Operationen sind asynchron. Damit blockieren spätere Netzwerkadapter den
Ausführungskontext der Control Plane nicht; Adapter für blockierende Bibliotheken
müssen deren Aufruf intern geeignet auslagern. Timeout und Retry werden nicht in
die Ports eingebaut, sondern später mit den bereits konfigurierten Policies um
die jeweilige Operation gelegt.

`WorkerStateObservation` und `ServiceReadinessObservation` sind unveränderlich
und prüfen ihre Laufzeittypen. Ein bekanntes `ready=False` ist von
`WorkerCommunicationError` und dessen Spezialisierung `WorkerTimeoutError`
getrennt. Dadurch kann ein nicht erreichbarer Worker nicht versehentlich als
`SLEEPING` interpretiert werden.

Die Persistenz verwendet denselben kanonischen Enum zur Erzeugung ihres
unveränderten `WORKER_STATES`-Tupels. Migration und Datenbankschema wurden nicht
geändert.

Ein Ausführungsport wurde bewusst nicht definiert. v3.4 bestimmt noch nicht, ob
spätere Worker-Ausführung als Request/Response, Start plus Statusabfrage,
Streaming oder opake Recovery-Referenz erfolgt. Eine generische `execute()`-
Methode würde diesen Vertrag vorzeitig festlegen. Die gemeinsame
`WorkerIdentity` ist die einzige Vorbereitung, bis Job-, LLM- oder
Agentenadapter ihren jeweils eindeutigen Fachvertrag erhalten.

## 4. Entscheidungen

- [OASIX-DEC-015](../decisions.md#oasix-dec-015--fähigkeitsgetrennte-asynchrone-worker-verträge)
  dokumentiert die Capability-Trennung, asynchrone I/O-Grenze und bewusst
  verschobene Ausführungssemantik.
- OASIX-DEC-001 und OASIX-DEC-004 werden durch die implementierte
  Worker-Grenze konkretisiert, ohne ihre Architekturvorgaben zu ändern.

Ein einzelnes umfassendes Worker-Interface wurde verworfen, weil Beobachtung,
Readiness und Power unterschiedliche Verantwortlichkeiten und spätere Adapter
haben können. Transportparameter in Methoden wurden verworfen, weil sie die
externe Worker-Konfiguration duplizieren würden. Freie Fehlertexte und das
Weiterreichen ursprünglicher Transportexceptions wurden wegen möglicher
Offenlegung von Hosts, URLs, Tokens oder Schlüsseldaten ausgeschlossen.

## 5. Umsetzung

- `src/oasix/worker/contracts.py`: IDs, verbindlicher State-Enum,
  unveränderliche Ergebnisse und vier Capability-Ports.
- `src/oasix/worker/errors.py`: feste transportneutrale Kommunikations- und
  Timeoutfehler.
- `src/oasix/worker/resolution.py`: ausschließliche Auflösung des aktiven
  Worker-Ziels aus `RuntimeConfig` mit sicherer Repräsentation.
- `src/oasix/worker/__init__.py`: kleine öffentliche Importoberfläche.
- `src/oasix/persistence/models.py`: Ableitung der unveränderten zulässigen
  DB-Zustandswerte vom kanonischen Worker-Enum.

## 6. Tests und Nachweise

| Prüfung | Umgebung | Ergebnis | Nachweis |
| --- | --- | --- | --- |
| Neue Worker-Vertragstests | lokal, macOS, Python 3.12 | 7 bestanden | `tests/test_worker_contracts.py` |
| Vollständige pytest-Suite | lokal, macOS, Python 3.12 | 201 bestanden | Feature-Branch |
| Ruff Linting und Formatprüfung | lokal, macOS, Python 3.12 | bestanden, 42 Dateien formatiert | Feature-Branch |
| Paketabhängigkeiten und Diff-Whitespace | lokal | `pip check` und `git diff --check` bestanden | Feature-Branch |
| Linux-CI | GitHub Actions, Ubuntu, Python 3.12 | erfolgreich | [PR #5 – Checks](https://github.com/madebyzwen/oasix/pull/5/checks) |

Die Tests verwenden ausschließlich In-Memory-Fakes. Sie prüfen zwei generische
Worker-Zuordnungen, strukturelle Austauschbarkeit, getrennte Fähigkeiten,
State-/Readiness-Ergebnisse, Unerreichbarkeit, Fehlerredaktion und die
gemeinsame State-Quelle. Reale Netzwerke, Secrets, Datenbankzugriffe und
Worker-Aktionen werden nicht verwendet.

## 7. Einschränkungen und Risiken

- Es existiert noch kein produktiver Adapter; Verhalten von SSH, HTTP,
  Wake-on-LAN und konkreten Worker-Runtimes ist nicht getestet.
- Die spätere Orchestrierung muss Timeouts begrenzen, Transportexceptions ohne
  ursprüngliche Detailtexte übersetzen und bekannte Ergebnisse von
  Kommunikationsfehlern getrennt verarbeiten.
- Ein erfolgreicher Wake-/Sleep-Aufruf bestätigt nur die Befehlsübergabe. Der
  tatsächliche Zustand muss separat beobachtet werden.
- Service-State-Persistenz, Gültigkeitsdauer einer Probe sowie kombinierte
  Readiness mehrerer Dienste sind weiterhin offen und gehören frühestens in
  Phase B.
- Start-/Status-/Abbruch- und Streamingverträge für Jobs, LLM und Agenten
  bleiben offen, bis die jeweilige Fachphase eindeutige Semantik festlegt.
- Für Adapterimplementierungen ist noch zu entscheiden, wie blockierende
  Bibliotheken ausgelagert und Cancellation/Deadlines plattformübergreifend
  nachgewiesen werden.

## 8. Abnahmestatus

Der A.3.1-Implementierungsumfang ist lokal umgesetzt, durch Linux-CI bestätigt
und auf GitHub unabhängig ohne blockierende Beanstandungen geprüft und
freigegeben. Phase A insgesamt ist nicht abgeschlossen; produktive
Worker-Funktionen bleiben weiterhin späteren Teilphasen vorbehalten.

## 9. GitHub-Referenzen

- Commits: [`766af94962cd6fc99c42fc9fe7e784acb00f92d9`](https://github.com/madebyzwen/oasix/commit/766af94962cd6fc99c42fc9fe7e784acb00f92d9)
  (`feat(worker): define generic worker contracts`)
- Pull Requests: [PR #5](https://github.com/madebyzwen/oasix/pull/5), offen und
  nicht gemergt
- CI-Läufe: [PR #5 – Checks](https://github.com/madebyzwen/oasix/pull/5/checks)
