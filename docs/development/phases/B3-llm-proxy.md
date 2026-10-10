# Phase B.3.1 – Authentifizierter, Lease-geschützter LLM-Proxy

Status: implementiert, unabhängige Review-Abnahme ausstehend

## 1. Ziel und Abgrenzung

B.3.1 stellt auf der Control Plane den authentifizierten Endpunkt
`POST /v1/chat/completions` für eine ausdrücklich begrenzte, nicht streamende
OpenAI-kompatible Teilmenge bereit. Jede angenommene Anfrage belegt sofort
einen begrenzten Concurrency-Slot und nutzt den aktiven Compute Worker nur
innerhalb einer persistenten, per Heartbeat verlängerten Lease.

Streaming, weitere OpenAI-Routen, Tool Calls, Bild-/Audioeingaben, persistente
Requestdaten und Telemetrie sind nicht Teil von B.3.1. `stream: true` wird bis
B.4 kontrolliert abgewiesen.

## 2. Verbindliche Anforderungen

| Anforderung | Umsetzung in B.3.1 |
| --- | --- |
| SEC-01 bis SEC-03 | Genau ein Bearer-Header wird über den bestehenden Authenticator geprüft; `inference` wird explizit verlangt. Client- und Provider-Schlüssel bleiben getrennt. Sichere statische Fehler enthalten weder Credentials noch Nutzdaten oder interne URLs. |
| LSE-01 bis LSE-03 | Jede Upstream-Nutzung erhält eine persistente UUIDv4-Lease mit Worker, Owner, Purpose und TTL. Eine Heartbeat-Task verlängert sie bis zum tatsächlichen Ende; Fehler und Abbruch führen zur kontrollierten Freigabe. |
| AC-03 | Wake und servicebezogene Readiness laufen erst nach erfolgreichem Lease-Acquire. Erst bestätigte Readiness erlaubt den Upstream-Aufruf. |
| CFG-01, CFG-02, CFG-05 | Runtime-Schema 4 ergänzt ausschließlich die erforderlichen Inference-Zeitparameter. Worker, Service, Endpoint, Provider-Authentifizierung und Limits stammen aus validierter Konfiguration. |
| API- und Portabilitätsregeln | Der stabile Pfad liegt unter `/v1/...`; Service und aktiver Worker werden über generische konfigurierte Kennungen gewählt. Es gibt keine Bindung an Host, Hardware, Betriebssystem, llama.cpp oder ein Modell. |

## 3. Architektur und Schnittstellen

Die Requestreihenfolge ist fest:

1. genau einen Authorization-Header authentifizieren und `inference` prüfen,
2. einen prozesslokalen Concurrency-Slot ohne Warteschlange belegen,
3. den Request bis höchstens 1 MiB einlesen und geschlossen validieren,
4. in einer kurzen SQLite-Transaktion eine persistente Lease erwerben,
5. ohne offene Datenbanktransaktion Wake und Readiness ausführen,
6. den konfigurierten LLM-Service ohne Redirects und Umgebungs-Proxy aufrufen,
7. die geschlossene Antwort bis höchstens 8 MiB validieren,
8. nach dem tatsächlichen Abschluss die Lease in einer kurzen Transaktion
   freigeben und danach den Concurrency-Slot zurückgeben.

`PersistentInferenceLeaseRegistry` ist ein asynchroner Adapter um den in C.1
freigegebenen `LeaseLifecycle`; jede Acquire-, Renew- und Release-Operation
besitzt eine eigene kurze Transaktion. Netzwerk-I/O läuft nie innerhalb dieser
Transaktionen. Die vorhandene Lease Registry bleibt die einzige maßgebliche
Aktivitätsquelle.

Die Factory `create_llm_gateway_app()` unterstützt kontrollierte Tests und
Embedding. `create_configured_llm_gateway_app()` lädt Bootstrap, Runtime und
Secrets, initialisiert die bereits migrierte Datenbank und übergibt deren
Lebensdauer an die ASGI-Anwendung. Ein nicht migriertes oder ungültiges Setup
schlägt vor Bereitstellung des Gateways geschlossen fehl.

## 4. Entscheidungen

Die Ablauf-, Transport- und Runtime-Grenzen sind in
[OASIX-DEC-021](../decisions.md#oasix-dec-021--lease-geschützter-llm-gateway-pfad)
dokumentiert.

- Runtime-Schema 4 verlangt `policies.inference` mit Request-Timeout,
  Lease-TTL und Heartbeat-Intervall. Version 2 kann kein Gateway öffnen;
  Version 3 bleibt für den isolierten Authentifizierungsbaustein gültig.
- Genau ein aktivierter Service mit `kind: llm` ist für B.3.1 erforderlich.
  Die Service-ID selbst ist frei konfigurierbar.
- Überlast wird sofort mit HTTP 429 beantwortet. Es gibt keine unbeschränkte
  In-Memory-Warteschlange und keinen Aktivitätszähler.
- Der Upstream-Transport folgt keinen Redirects, ignoriert Proxy-Variablen und
  setzt ausschließlich dessen konfigurierte Provider-Authentifizierung.
- Lease-Renew- und Release-Fehler sind fail-closed. Insbesondere wird eine
  fehlgeschlagene Freigabe nicht als Erfolg gemeldet.

## 5. Implementierte OpenAI-Teilmenge

`POST /v1/chat/completions` akzeptiert ein geschlossenes JSON-Objekt mit:

- `model`, einer nicht leeren Liste einfacher `system`-, `user`- oder
  `assistant`-Nachrichten und optional `stream: false`,
- optional `temperature`, `top_p` und `max_tokens` innerhalb dokumentierter
  Grenzen.

Die Antwort unterstützt `id`, `object: chat.completion`, `created`, `model`,
eine begrenzte Choice-Liste mit Assistant-Nachricht, optional standardisierte
Usage-Zähler und `system_fingerprint`. Unbekannte oder doppelte JSON-Felder,
unkontrollierte Objekttypen und inkonsistente Token-Summen werden abgewiesen.
Es werden keine darüber hinausgehenden OpenAI-Routen oder Parameter behauptet.

Betroffene Implementierung:

- `src/oasix/llm/gateway.py`: ASGI-Grenze, Authentifizierung, Request-Limit,
  sichere Fehlerabbildung und Ressourcenlebensdauer,
- `src/oasix/llm/service.py`: Ablaufkomposition und sofortiges
  Concurrency-Gate,
- `src/oasix/llm/leases.py`: persistente Lease-Session und Heartbeat,
- `src/oasix/llm/transport.py`: begrenzter, providerneutraler HTTP-Transport,
- `src/oasix/llm/models.py`: geschlossene Request-/Response-Teilmenge.

## 6. Tests und Nachweise

| Prüfung | Umgebung | Ergebnis | Nachweis |
| --- | --- | --- | --- |
| B.3.1-Konfiguration und LLM-Pfad | lokal, macOS, Python 3.12 | bestanden | `tests/test_llm_configuration.py`, `tests/test_llm_proxy.py` |
| Vollständige pytest-Suite | lokal, macOS, Python 3.12 | 329 bestanden | B.3.1-Arbeitsstand vor Commit |
| Ruff, Paket-, Import-, Link- und Diff-Prüfung | lokal | bestanden | B.3.1-Arbeitsstand vor Commit |
| Linux-CI | GitHub Actions, Ubuntu, Python 3.12 | bestanden | [Lauf 38007072518](https://github.com/madebyzwen/oasix/actions/runs/38007072518) |

Die Tests verwenden kontrollierte HTTP-Transporte und temporäre SQLite-
Datenbanken. Sie prüfen Authentifizierung und Rechte, Überlast vor Body-Parsing,
Lease-Reihenfolge, Heartbeat, Wake-/Readiness- und Upstream-Fehler, Timeout,
Abbruch, Renew-/Release-Fehler, kurze Transaktionen und Redaktionsgrenzen. Es
werden keine produktiven Hosts oder Secret-Dateien verwendet.

## 7. Einschränkungen und Risiken

- B.3.1 ist bewusst nicht streamend; SSE und seine Abbruchsemantik folgen in
  B.4.
- Das Concurrency-Limit gilt pro Control-Plane-Prozess. Der SQLite-MVP und
  diese Implementierung setzen einen einzelnen Gateway-Prozess voraus; mehrere
  ASGI-Worker würden ein verteiltes Admission-Verfahren erfordern.
- Der Heartbeat muss betrieblich mit ausreichendem Abstand zur TTL
  konfiguriert werden. Das Schema erzwingt, dass das Intervall kleiner als die
  TTL ist; Scheduler- und Datenbanklatenzen können dennoch keine harte
  Echtzeitgarantie erhalten.
- Reale DNS-, TLS-, Wake-on-LAN- und Providerkompatibilität benötigen einen
  Deployment-Smoke-Test. Automatische Redirects und Umgebungs-Proxies bleiben
  absichtlich deaktiviert.
- Operative Telemetrie wurde aufbauend in B.5 integriert. B.3.1 selbst loggt
  weiterhin weder Request- noch Antwortinhalte.

## 8. Abnahmestatus

B.3.1 ist implementiert und lokal geprüft, aber bis zum unabhängigen
GitHub-Code-Review nicht freigegeben. B.4 und B.5 sind inzwischen
implementiert und warten ebenfalls auf unabhängige Review-Abnahme.

## 9. GitHub-Referenzen

- Implementierung: Commit
  `feat(llm): implement authenticated lease-protected proxy` in
  [PR #6](https://github.com/madebyzwen/oasix/pull/6)
- Pull Request: [PR #6](https://github.com/madebyzwen/oasix/pull/6), offen und
  nicht gemergt
- Implementierungs-Commit:
  [`e5247b1`](https://github.com/madebyzwen/oasix/commit/e5247b17fc5c869be8928a224f992ff86fa806e0)
- Linux-CI: [Lauf 38007072518](https://github.com/madebyzwen/oasix/actions/runs/38007072518), bestanden
