# Phase B.3 – OpenAI-kompatibler LLM-Proxy: Abhängigkeitsnachweis

Status: vor Implementierung blockiert

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
| LSE-01 bis LSE-03 | Jede produktive LLM-Nutzung benötigt eine persistente Lease mit TTL beziehungsweise Heartbeat. Das Schema existiert, aber es gibt keinen fachlichen Acquire-/Heartbeat-/Release-Lifecycle. |
| SEC-01 | Client-APIs benötigen Authentifizierung. Die Runtime-Konfiguration enthält derzeit nur Provider-/Service-Credentials, aber keinen Client-Key- oder Berechtigungsvertrag für `/v1/...`. |
| AC-03 | Wake und Readiness sind in B.2 vorhanden. Die anschließende Weiterleitung darf jedoch erst innerhalb einer gültigen Lease stattfinden. |
| SEC-02, SEC-03, AC-10 | Provider-Credentials können sicher aufgelöst werden, dürfen aber erst in einem vollständig lease- und auth-geschützten Proxy genutzt werden. |

`LeasesRepository` dokumentiert seinen aktuellen Umfang selbst als Erzeugung
und Lesen; Lifecycle-Operationen bleiben einer späteren Phase vorbehalten. Das
direkte Anlegen einer Zeile über `add()` ist kein hinreichender Ersatz: Es
definiert weder atomare Acquire-Semantik noch Heartbeat, idempotente Freigabe,
Expiry-Behandlung oder Fehler-/Abbruchpfade.

## 3. Erforderliche Architekturentscheidungen vor Fortsetzung

Vor B.3 müssen mindestens folgende Verträge festgelegt und implementiert sein:

1. Eine transaktionale Lease-Komponente für Acquire, Heartbeat und Release mit
   UTC-/TTL-Regeln, sicheren Owner-/Purpose-Werten und eindeutiger Behandlung
   abgelaufener beziehungsweise bereits freigegebener Leases.
2. Die Reihenfolge für interaktive LLM-Anfragen: Authentifizierung und
   Concurrency-Gate, Lease-Acquire vor Wake/Readiness/Upstream-Nutzung,
   Heartbeat während langer Nutzung und Freigabe erst nach vollständigem Ende
   beziehungsweise kontrollierter Fehler- oder Abbruchbehandlung.
3. Ein externer Secret- und Berechtigungsvertrag für Client-API-Schlüssel. Ein
   unprivilegierter Inference-Key darf keine administrativen Power-Aktionen
   autorisieren. Provider-Credentials und Client-Credentials bleiben getrennt.
4. Festlegung, ob die vorhandenen Lease-Spalten für den ersten Lifecycle
   ausreichen. Falls eine Migration erforderlich wird, benötigt sie vorab einen
   freigegebenen Fachvertrag.

Diese Punkte dürfen nicht durch einen flüchtigen Aktivitätszähler, eine
In-Memory-Dummy-Lease oder einen ungeschützten Proxy ersetzt werden.

## 4. Entscheidungen

Es wurde keine neue Architekturentscheidung getroffen. Der Stopp folgt direkt
aus v3.4 und der bestehenden
[OASIX-DEC-010](../decisions.md#oasix-dec-010--persistenzgrenzen-und-lease-autorität).
Die offenen Verträge müssen vor der Fortsetzung separat freigegeben werden.

## 5. Umsetzung

Für B.3 wurden keine Python-Dateien, Tests, Dependencies, API-Routen,
Datenbankmodelle oder Migrationen angelegt beziehungsweise verändert. Dieses
Dokument und die synchronisierten Statusangaben halten ausschließlich den
verpflichtenden Stopp fest.

## 6. Tests und Nachweise

Die zuletzt abgeschlossene Teilphase B.2 wurde lokal mit 249 bestandenen Tests,
Ruff-Linting, Ruff-Formatprüfung, `pip check`, Import-, Dokumentations- und
Diff-Prüfung nachgewiesen. Der zugehörige
[Linux-CI-Lauf `38002248222`](https://github.com/madebyzwen/oasix/actions/runs/38002248222)
war erfolgreich. Für B.3 existieren keine Implementierungstests, weil keine
Produktivimplementierung begonnen wurde.

## 7. Risiken bei Missachtung des Stopps

- Ein LLM-Request könnte den Worker ohne maßgebliche aktive Lease nutzen und
  damit Automatic Sleep nicht sicher blockieren.
- Bei Streaming oder Client-Abbruch könnte die Nutzung zu früh freigegeben oder
  dauerhaft verwaist bleiben.
- Ein ungeschützter `/v1/...`-Pfad würde SEC-01 unmittelbar verletzen.
- Ein provisorischer Aktivitätszähler würde der alleinigen Autorität der Lease
  Registry widersprechen und später konkurrierende Zustände erzeugen.

## 8. Abnahmestatus

B.3 ist nicht implementiert und nicht zur Abnahme bereit. Die Arbeit wurde vor
produktiven Änderungen kontrolliert gestoppt. B.4 und B.5 sind nicht begonnen.

## 9. GitHub-Referenzen

- Abgeschlossene B.1-/B.2-Commits:
  [`462fb7b`](https://github.com/madebyzwen/oasix/commit/462fb7b1c1d31ac5d5413eda686e9bf17896889e),
  [`c155dbd`](https://github.com/madebyzwen/oasix/commit/c155dbdad82305cde53ed37e09cdf51b8f508e87)
- Pull Request: [PR #6](https://github.com/madebyzwen/oasix/pull/6), offen und
  nicht gemergt
- CI: [Linux-CI](https://github.com/madebyzwen/oasix/actions/runs/38002248222)
