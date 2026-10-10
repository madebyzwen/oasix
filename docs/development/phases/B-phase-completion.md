# Abschluss Phase B – LLM-Pfad

Status: abgeschlossen, unabhängig geprüft und freigegeben

## 1. Abschlussumfang

Phase B stellt den interaktiven, hardwareunabhängigen LLM-Pfad der dauerhaft
verfügbaren OASIX-Control-Plane bereit. Der implementierte öffentliche Umfang
besteht aus genau einem authentifizierten Endpunkt:

- `POST /v1/chat/completions` für die dokumentierte geschlossene
  OpenAI-kompatible Teilmenge mit nicht streamenden Antworten und
  SSE-Streaming.

Der vollständige Ablauf verbindet die in B.1 bis B.5 implementierten Bausteine
mit dem vorgezogenen und freigegebenen Lease-Lifecycle aus C.1:

```text
Client
  -> Authentifizierung und Inference-Berechtigung
  -> sofortiges Concurrency-Gate
  -> persistentes Lease-Acquire mit Heartbeat
  -> konfiguriertes Wake und servicebezogene Readiness
  -> begrenzter konfigurierter LLM-Upstream
  -> validierte Antwort oder inkrementeller SSE-Stream
  -> vollständiges Cleanup und Lease-Release
  -> sichere korrelierte Telemetrie
```

Die Implementierung bleibt unabhängig von konkreter Hardware, privaten
Hostnamen, Betriebssystemen, Modellen und LLM-Produkten. Worker, Service,
Endpoint, Provider-Authentifizierung, Wake-Methode und Policies stammen aus der
validierten Runtime-Konfiguration.

## 2. Teilphasen und Commit-Nachweise

| Umfang | Commit | Freigabestatus |
| --- | --- | --- |
| B.1 – HTTP-Service-Readiness | [`462fb7b`](https://github.com/madebyzwen/oasix/commit/462fb7b1c1d31ac5d5413eda686e9bf17896889e) | unabhängig geprüft und freigegeben |
| B.2 – Wake-on-LAN und Worker-Bereitschaft | [`c155dbd`](https://github.com/madebyzwen/oasix/commit/c155dbdad82305cde53ed37e09cdf51b8f508e87) | unabhängig geprüft und freigegeben |
| Historischer B.3-Abhängigkeitsnachweis | [`9fa168f`](https://github.com/madebyzwen/oasix/commit/9fa168f1ee90799093cedbb656c2ef231fce1935) | Blocker durch C.1 und B.3.0 aufgelöst |
| C.1 – Persistenter Lease-Lifecycle | [`017c94d`](https://github.com/madebyzwen/oasix/commit/017c94dda1a1cc4ff12db065edce124801704287), Korrektur [`811afd3`](https://github.com/madebyzwen/oasix/commit/811afd3ec901e86990fbb0895757291063ba64ce) | unabhängig geprüft und freigegeben |
| B.3.0 – Client-API-Authentifizierung | [`846c3cb`](https://github.com/madebyzwen/oasix/commit/846c3cbbe95306ab3705bb641bdf9c67da35b396) | unabhängig geprüft und freigegeben |
| B.3.1 – Nicht streamender LLM-Proxy | [`e5247b1`](https://github.com/madebyzwen/oasix/commit/e5247b17fc5c869be8928a224f992ff86fa806e0) | unabhängig geprüft und freigegeben |
| B.4 – Streaming und Cancellation | [`607acd2`](https://github.com/madebyzwen/oasix/commit/607acd2551f00fd7bc333effe8f66362d0020b7e) | unabhängig geprüft und freigegeben |
| B.5 – Telemetrie und Integration | [`d9a17cb`](https://github.com/madebyzwen/oasix/commit/d9a17cb8487b6258624b8ea5ccbd694903320bfb) | unabhängig geprüft und freigegeben |

Die ursprünglichen Abhängigkeiten bleiben im
[historischen B.3-Blockerdokument](B3-llm-proxy-blocked.md) nachvollziehbar.
Sie sind keine aktuellen Blocker mehr.

## 3. Verbindliche Anforderungen und Entscheidungen

Der Abschluss setzt insbesondere folgende Requirement-Bereiche aus v3.4 um:

- Architektur und Portabilität: ARC-03, ARC-04 und ALIAS-01 bis ALIAS-03,
- Konfiguration: CFG-01 bis CFG-05,
- Worker und Wake/Readiness: WRK-03 bis WRK-05, PWR-02 und REC-03,
- Leases: LSE-01 bis LSE-03,
- API- und Sicherheitsgrenzen: SEC-01 bis SEC-03,
- Telemetrie: OBS-01 bis OBS-03,
- relevante Akzeptanzkriterien: AC-01, AC-03 bis AC-05 und AC-10.

Es wurden für diesen Abschluss keine neuen Architekturentscheidungen getroffen.
Maßgeblich für den implementierten Umfang sind die bereits dokumentierten
Entscheidungen:

- [OASIX-DEC-016](../decisions.md#oasix-dec-016--geschlossene-json-lines-logging-grenze): sichere strukturierte Logs,
- [OASIX-DEC-017](../decisions.md#oasix-dec-017--isolierter-asynchroner-http-readiness-adapter): HTTP-Readiness,
- [OASIX-DEC-018](../decisions.md#oasix-dec-018--begrenzte-wake--und-readiness-orchestrierung-ohne-state-automat): Wake-/Readiness-Gate,
- [OASIX-DEC-019](../decisions.md#oasix-dec-019--persistenter-idempotenter-lease-lifecycle): autoritativer Lease-Lifecycle,
- [OASIX-DEC-020](../decisions.md#oasix-dec-020--additives-runtime-schema-3-und-client-api-authentifizierung): Client-Authentifizierung,
- [OASIX-DEC-021](../decisions.md#oasix-dec-021--lease-geschützter-llm-gateway-pfad): Gateway-Ablauf,
- [OASIX-DEC-022](../decisions.md#oasix-dec-022--inkrementelles-sse-streaming-mit-explizitem-cleanup): Streaming und Cancellation,
- [OASIX-DEC-023](../decisions.md#oasix-dec-023--geschlossene-llm-request-telemetrie): LLM-Telemetrie.

## 4. API, Authentifizierung und Concurrency

Das Gateway akzeptiert genau einen Bearer-`Authorization`-Header, prüft ihn mit
dem bestehenden `ClientAuthenticator` und verlangt ausdrücklich die Capability
`inference`. Ein Schlüssel mit ausschließlich `administration` erhält keinen
Inference-Zugriff. Client- und Provider-Credentials bleiben getrennt; ein
Client-Credential wird nicht an den Worker weitergereicht.

Runtime-Schema 2 kann kein ungeschütztes Gateway bereitstellen. Das für den
produktiven Pfad verwendete additive Schema 4 verlangt Client-
Authentifizierung und die Inference-Zeitparameter. Fehlende, ungültige,
mehrfache oder unzureichend berechtigte Authorization-Header werden
kontrolliert abgewiesen, ohne Schlüsselwerte offenzulegen.

Nach erfolgreicher Authentifizierung reserviert das Gateway ohne Warteschlange
einen Slot aus dem konfigurierten
`max_concurrent_inference_requests`-Limit. Bei ausgeschöpfter Kapazität folgt
eine kontrollierte HTTP-429-Antwort. Das Limit ist pro Control-Plane-Prozess;
der aktuelle SQLite-MVP setzt für diesen Vertrag einen Gateway-Prozess voraus.

## 5. Lease, Wake, Readiness und Upstream

Jede angenommene LLM-Nutzung erwirbt vor Wake, Readiness und Upstream-Zugriff
eine persistente UUIDv4-Lease für den konfigurierten aktiven Worker. Eine
Heartbeat-Task verlängert die Lease während langer nicht streamender und
streamender Aufrufe. Release erfolgt erst nach Ende der tatsächlichen Nutzung.
Acquire-, Renew- und Release-Operationen verwenden getrennte kurze SQLite-
Transaktionen; Netzwerk-I/O findet nicht in einer offenen Schreibtransaktion
statt. Renew- und Release-Fehler werden nicht als Erfolg behandelt.

Wake verwendet ausschließlich die im aktiven Worker-Profil konfigurierte
Methode. Wake-on-LAN sendet ein standardisiertes Magic Packet an das
konfigurierte Ziel; `none` ist ein kontrollierter No-op. Wake-Erfolg gilt nie
als Bereitschaft. Alle benötigten Services müssen ihre konfigurierte
Readiness-Probe innerhalb der begrenzten Retry-, Backoff-, Jitter- und
Timeout-Policies bestätigen.

Der Upstream verwendet ausschließlich den aktivierten konfigurierten Service
mit `kind: llm` und dessen getrennte Secret-Referenz. Redirects und
Umgebungs-Proxies sind deaktiviert. Requests, nicht streamende Responses,
SSE-Ereignisse, Gesamtstream und Laufzeit besitzen feste Grenzen. Unbekannte
oder doppelte JSON-Felder und nicht unterstützte OpenAI-Erweiterungen werden
geschlossen abgewiesen.

## 6. Streaming, Cancellation und Telemetrie

Bei `stream: true` werden validierte Chat-Completion-Chunks inkrementell als
SSE übertragen. Der Upstream-`[DONE]`-Marker wird erst nach erfolgreichem
Upstream-Ende und Lease-Release als finaler Client-Marker erzeugt. Ein später
Fehler beendet den bereits gestarteten HTTP-200-Stream mit einem statischen
SSE-Fehlerobjekt und ohne `[DONE]`.

Client-Disconnect, Cancellation, Upstream-Abbruch, Timeout sowie Renew- und
Release-Fehler schließen die verschachtelten Iteratoren, HTTP-Verbindungen,
Heartbeat-Task, Lease und den Admission-Slot kontrolliert. Es bleibt keine
unüberwachte Hintergrundnutzung zurück.

Die sichere strukturierte Telemetrie enthält nur vorhandene generische
Korrelationskennungen und typisierte technische Werte:

- Request-, Wake- und Readiness-Latenz,
- Time-to-first-token bei Streaming,
- Prompt-, Completion- und Gesamttokens, sofern die validierte Providerantwort
  sie liefert,
- Abschlussstatus sowie bei Fehlern eine geschlossene technische Fehlerklasse
  und einen statischen Fehlercode.

Fehlende Messwerte bleiben aus. Prompts, Antworten, Secret-Werte,
Authorization-Header, interne Endpunkte und rohe Exceptions werden nicht
geloggt.

## 7. Automatisierte Nachweise und Linux-CI

| Stand | Lokaler Nachweis | Linux-CI |
| --- | --- | --- |
| B.1/B.2 | 249 Tests nach B.2; isolierte HTTPX- und Wake-Transporte | [Lauf `38002248222`](https://github.com/madebyzwen/oasix/actions/runs/38002248222), bestanden |
| C.1 | 269 Tests nach Review-Korrektur; lokale SQLite-Dateien und injizierte Uhren | [Implementierung `38003515144`](https://github.com/madebyzwen/oasix/actions/runs/38003515144), [Korrektur `38004074726`](https://github.com/madebyzwen/oasix/actions/runs/38004074726), bestanden |
| B.3.0 | 298 Tests; Authentifizierungs-, Berechtigungs- und Redaktionsfälle | [Lauf `38005483797`](https://github.com/madebyzwen/oasix/actions/runs/38005483797), bestanden |
| B.3.1 | 329 Tests; vollständiger nicht streamender Gateway-Pfad | [Lauf `38007072518`](https://github.com/madebyzwen/oasix/actions/runs/38007072518), bestanden |
| B.4 | 340 Tests; Streaming-, Disconnect- und Cleanup-Pfade | [Lauf `38007804479`](https://github.com/madebyzwen/oasix/actions/runs/38007804479), bestanden |
| B.5 | 358 Tests; davon 88 fokussierte LLM-/Logging-/Integrationstests | [Lauf `38008622994`](https://github.com/madebyzwen/oasix/actions/runs/38008622994), bestanden |

Die Tests verwenden kontrollierte Testtransporte, Fakes, temporäre SQLite-
Datenbanken und temporäre Secret-Dateien. Sie stellen keine Verbindung zu
produktiven Hosts, echten Worker-Systemen oder realen LLM-Diensten her.

Ein grüner automatisierter Test- oder CI-Lauf ist deshalb ausdrücklich kein
Nachweis für erfolgreiches Wake-on-LAN in einem realen Netzwerk, reale DNS-/
TLS-Konfiguration, Firewall- und Broadcast-Routing, Providerkompatibilität,
Produktionslast oder tatsächliche Inferenz auf Worker-Hardware.

## 8. Bekannte Grenzen und Folgearbeiten

- Unterstützt wird ausschließlich die dokumentierte geschlossene Teilmenge von
  `POST /v1/chat/completions`; weitere `/v1/...`-Routen sind nicht vorhanden.
- Das Concurrency-Limit ist pro Prozess und keine verteilte Admission-Lösung.
- Streaming-Tokenzahlen werden nicht geschätzt, wenn der geschlossene
  Streaming-Vertrag keine Usage liefert.
- LLM-Request-Telemetrie liegt in strukturierten Logs vor; persistente Job- und
  Attempt-Telemetrie gehört zu einer späteren Phase.
- Persistierte Worker-Zustandsübergänge, Job-Dispatch, Retry-Geschäftslogik,
  Recovery, Automatic/Manual/Force Sleep sowie Agenten- und Tool-Ausführung
  sind nicht Bestandteil des Phase-B-Abschlusses.
- Reale Deployment-Smoke-Tests gegen Worker, Netzwerk und LLM-Provider bleiben
  erforderlich.

Mit diesem dokumentierten Abschluss beginnt keine Folgephase. PR #6 bleibt bis
zur abschließenden Repository-Prüfung offen und wird durch diesen Auftrag nicht
gemergt.
