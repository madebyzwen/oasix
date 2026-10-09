# Phase B.3 – OpenAI-kompatibler LLM-Proxy: Abhängigkeitsnachweis

Status: vor Proxy-Implementierung blockiert

## 1. Ziel und Abgrenzung

B.3 soll einen authentifizierten OpenAI-kompatiblen LLM-Pfad unter stabilen
`/v1/...`-Endpunkten bereitstellen. Die verpflichtende Vorprüfung hat ergeben,
dass eine sichere produktive Implementierung auf dem aktuellen Stand nicht
möglich ist. Deshalb wurden weder API-, Proxy- noch LLM-Transportcode begonnen.

B.1 und B.2 bleiben als eigenständig geprüfte Commits erhalten. B.4 Streaming
und B.5 Telemetrie wurden gemäß der sequenziellen Stop-Regel nicht begonnen.

## 2. Verbindliche Anforderungen und Blocker

| Anforderung | Aktueller Stand und Blocker |
| --- | --- |
| LSE-01 bis LSE-03 | C.1 implementiert den unabhängig geprüften persistenten Acquire-/Heartbeat-/Release-Lifecycle ohne Schemaänderung. Die Einbindung in den vollständigen B.3-Anfrageablauf ist nicht implementiert. |
| SEC-01 | B.3.0 implementiert einen eigenständigen Client-Key- und Berechtigungsvertrag für zukünftige `/v1/...`- und Management-Routen. Der Baustein ist bis zum unabhängigen Review unfreigegeben und noch in keine HTTP-Route integriert. |
| AC-03 | Wake und Readiness sind in B.2 vorhanden. Die anschließende Weiterleitung darf jedoch erst innerhalb einer gültigen Lease stattfinden. |
| SEC-02, SEC-03, AC-10 | Provider-Credentials können sicher aufgelöst werden, dürfen aber erst in einem vollständig lease- und auth-geschützten Proxy genutzt werden. |

Der in C.1 implementierte `LeaseLifecycle` definiert atomare Acquire-Semantik,
Heartbeat/Renew, idempotente Freigabe und Expiry-Auswertung. B.3.0 definiert
davon getrennt Bearer-Authentifizierung und explizite Inference-/
Administrationsrechte. Beide Bausteine lösen noch nicht ihre sichere Komposition
mit Streaming-, Abbruch- oder Upstream-Fehlerpfaden.

## 3. Erforderliche Architekturentscheidungen vor Fortsetzung

Vor B.3 müssen mindestens folgende Verträge festgelegt und implementiert sein:

1. Die transaktionale Lease-Komponente ist in C.1 implementiert und unabhängig
   freigegeben.
2. Die Reihenfolge für interaktive LLM-Anfragen: Authentifizierung und
   Concurrency-Gate, Lease-Acquire vor Wake/Readiness/Upstream-Nutzung,
   Heartbeat während langer Nutzung und Freigabe erst nach vollständigem Ende
   beziehungsweise kontrollierter Fehler- oder Abbruchbehandlung.
3. Der externe Secret- und Berechtigungsvertrag ist in B.3.0 implementiert,
   muss aber vor produktiver B.3-Nutzung unabhängig freigegeben und an der
   späteren HTTP-Grenze konsequent auf jede Operation angewandt werden.
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
entscheidet den Client-Authentifizierungsvertrag. Die B.3-spezifische
Ablaufkomposition bleibt offen.

## 5. Umsetzung

Für den B.3-Proxy wurden weiterhin keine Python-Dateien, Tests, Dependencies,
API-Routen, Datenbankmodelle oder Migrationen angelegt beziehungsweise
verändert. Der getrennte B.3.0-Baustein ist in
[Phase B.3.0](B3-client-auth.md) dokumentiert. Dieses Dokument hält weiterhin
den verbleibenden Stopp vor der Proxy-Implementierung fest.

## 6. Tests und Nachweise

Nachweise für den Authentifizierungsbaustein stehen in
[Phase B.3.0](B3-client-auth.md). Für den eigentlichen B.3-Proxy existieren
weiterhin keine Implementierungstests, weil keine Produktivimplementierung
begonnen wurde.

## 7. Risiken bei Missachtung des Stopps

- Ein LLM-Request könnte den Worker ohne maßgebliche aktive Lease nutzen und
  damit Automatic Sleep nicht sicher blockieren.
- Bei Streaming oder Client-Abbruch könnte die Nutzung zu früh freigegeben oder
  dauerhaft verwaist bleiben.
- Eine HTTP-Schicht, die den B.3.0-Baustein nicht vor jeder Operation anwendet,
  würde SEC-01 unmittelbar verletzen.
- Ein provisorischer Aktivitätszähler würde der alleinigen Autorität der Lease
  Registry widersprechen und später konkurrierende Zustände erzeugen.

## 8. Abnahmestatus

B.3 ist nicht implementiert und nicht zur Abnahme bereit. C.1 beseitigt den
Lease-Lifecycle-Teil des Blockers. B.3.0 stellt die Client-Authentifizierung
bereit, bleibt aber bis zum unabhängigen Review unfreigegeben. Danach ist vor
dem Proxy-Code weiterhin die sichere B.3-Ablaufkomposition festzulegen. B.4
und B.5 sind nicht begonnen.

## 9. GitHub-Referenzen

- Abgeschlossene B.1-/B.2-Commits:
  [`462fb7b`](https://github.com/madebyzwen/oasix/commit/462fb7b1c1d31ac5d5413eda686e9bf17896889e),
  [`c155dbd`](https://github.com/madebyzwen/oasix/commit/c155dbdad82305cde53ed37e09cdf51b8f508e87)
- Abgeschlossene C.1-Commits:
  [`017c94d`](https://github.com/madebyzwen/oasix/commit/017c94dda1a1cc4ff12db065edce124801704287),
  [`811afd3`](https://github.com/madebyzwen/oasix/commit/811afd3ec901e86990fbb0895757291063ba64ce)
- B.3.0: Commit `feat(auth): add client API authentication and authorization`
  in PR #6; unabhängige Prüfung ausstehend
- Pull Request: [PR #6](https://github.com/madebyzwen/oasix/pull/6), offen und
  nicht gemergt
- CI: [Linux-CI](https://github.com/madebyzwen/oasix/actions/runs/38002248222)
