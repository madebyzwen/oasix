# Phase B.3 – Historischer Abhängigkeitsnachweis

Status: historischer Nachweis; Blocker aufgelöst und B.3 freigegeben

## 1. Ziel und Abgrenzung

B.3 soll einen authentifizierten OpenAI-kompatiblen LLM-Pfad unter stabilen
`/v1/...`-Endpunkten bereitstellen. Die ursprüngliche Vorprüfung hatte den
fehlenden persistenten Lease-Lifecycle und die fehlende Client-
Authentifizierung als zwingende Blocker festgestellt. C.1 und B.3.0 haben
diese Voraussetzungen inzwischen geschaffen. B.3.1 implementiert ihre sichere
Komposition und ist in [Phase B.3.1](B3-llm-proxy.md) dokumentiert.

B.1 und B.2 bleiben als eigenständig geprüfte Commits erhalten. Dieses Dokument
bleibt als historischer Entscheidungsnachweis bestehen und beschreibt nicht
mehr den aktuellen Implementierungsstatus.

## 2. Aufgelöste Blocker

| Anforderung | Auflösung |
| --- | --- |
| LSE-01 bis LSE-03 | C.1 implementiert den unabhängig geprüften persistenten Acquire-/Heartbeat-/Release-Lifecycle ohne Schemaänderung. B.3.1 bindet ihn vor Wake, Readiness und Upstream ein. |
| SEC-01 | B.3.0 stellt die getrennte Client-Authentifizierung und Berechtigungsprüfung bereit. B.3.1 wendet sie auf jede implementierte Inference-Route an. |
| AC-03 | B.1/B.2 stellen Wake und Readiness bereit. B.3.1 führt beide ausschließlich innerhalb einer gültigen Lease aus. |
| SEC-02, SEC-03, AC-10 | B.3.1 nutzt Provider-Credentials nur am konfigurierten Upstream und gibt sie weder an Clients noch an Fehler oder Logs weiter. |

## 3. Umgesetzte Architekturentscheidungen

Vor B.3.1 waren folgende Verträge festzulegen und zu implementieren:

1. Die transaktionale Lease-Komponente ist in C.1 implementiert und unabhängig
   freigegeben.
2. Die Reihenfolge für interaktive LLM-Anfragen: Authentifizierung und
   Concurrency-Gate, Lease-Acquire vor Wake/Readiness/Upstream-Nutzung,
   Heartbeat während langer Nutzung und Freigabe erst nach vollständigem Ende
   beziehungsweise kontrollierter Fehler- oder Abbruchbehandlung.
3. Der externe Secret- und Berechtigungsvertrag aus B.3.0 wird an der
   HTTP-Grenze konsequent auf jede Operation angewandt.
4. C.1 hat bestätigt, dass die vorhandenen Lease-Spalten für den ersten
   Lifecycle ausreichen; es ist keine Migration erforderlich.

Diese Punkte dürfen nicht durch einen flüchtigen Aktivitätszähler, eine
In-Memory-Dummy-Lease oder einen ungeschützten Proxy ersetzt werden.

## 4. Entscheidungen

Der ursprüngliche Stopp folgt direkt aus v3.4 und der bestehenden
[OASIX-DEC-010](../decisions.md#oasix-dec-010--persistenzgrenzen-und-lease-autorität).
[OASIX-DEC-019](../decisions.md#oasix-dec-019--persistenter-idempotenter-lease-lifecycle)
entscheidet den Lease-Vertrag;
[OASIX-DEC-020](../decisions.md#oasix-dec-020--additives-runtime-schema-3-und-client-api-authentifizierung)
entscheidet den Client-Authentifizierungsvertrag.
[OASIX-DEC-021](../decisions.md#oasix-dec-021--lease-geschützter-llm-gateway-pfad)
dokumentiert die inzwischen implementierte B.3.1-Ablaufkomposition.

## 5. Umsetzung

Die aktuelle Implementierung und ihre Grenzen stehen ausschließlich in
[Phase B.3.1](B3-llm-proxy.md). Der Authentifizierungsbaustein bleibt getrennt
in [Phase B.3.0](B3-client-auth.md) dokumentiert. B.3.1 benötigt weder neue
Datenbanktabellen noch eine Migration.

## 6. Tests und Nachweise

Nachweise für Authentifizierung und Proxy stehen in
[Phase B.3.0](B3-client-auth.md) und [Phase B.3.1](B3-llm-proxy.md).

## 7. Weiterhin geltende Invarianten

- Keine Worker-Nutzung ohne maßgebliche aktive Lease.
- Keine Freigabe vor dem tatsächlichen Ende der Upstream-Nutzung.
- Authentifizierung und explizite Inference-Berechtigung vor jeder Operation.
- Keine provisorischen Aktivitätszähler neben der Lease Registry.

## 8. Abnahmestatus

Der hier dokumentierte Blocker ist aufgelöst. Der aktuelle Implementierungs-
und Abnahmestatus steht in [Phase B.3.1](B3-llm-proxy.md). B.3.0, B.3.1, B.4
und B.5 sind unabhängig geprüft und freigegeben. Dieses Dokument bleibt
ausschließlich als Entwicklungshistorie erhalten.

## 9. GitHub-Referenzen

- Abgeschlossene B.1-/B.2-Commits:
  [`462fb7b`](https://github.com/madebyzwen/oasix/commit/462fb7b1c1d31ac5d5413eda686e9bf17896889e),
  [`c155dbd`](https://github.com/madebyzwen/oasix/commit/c155dbdad82305cde53ed37e09cdf51b8f508e87)
- Abgeschlossene C.1-Commits:
  [`017c94d`](https://github.com/madebyzwen/oasix/commit/017c94dda1a1cc4ff12db065edce124801704287),
  [`811afd3`](https://github.com/madebyzwen/oasix/commit/811afd3ec901e86990fbb0895757291063ba64ce)
- B.3.0: [`846c3cb`](https://github.com/madebyzwen/oasix/commit/846c3cbbe95306ab3705bb641bdf9c67da35b396)
- Pull Request: [PR #6](https://github.com/madebyzwen/oasix/pull/6), offen und
  nicht gemergt
- CI: [Linux-CI](https://github.com/madebyzwen/oasix/actions/runs/38002248222)
