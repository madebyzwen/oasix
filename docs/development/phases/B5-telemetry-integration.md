# Phase B.5 – LLM-Telemetrie und Integration

Status: abgeschlossen, unabhängig geprüft und freigegeben

## 1. Ziel und Abgrenzung

B.5 ergänzt den in B.3.1 und B.4 implementierten LLM-Pfad um sichere,
korrelierbare Betriebsmetriken und übergreifende Integrationstests. Erfasst
werden ausschließlich technisch verfügbare Werte. Prompt- und Antwortinhalte,
Zugangsdaten, Authorization-Header, interne Endpunkte und rohe Exceptions
bleiben außerhalb der Logging-Grenze.

Die Phase führt weder eine neue Persistenztabelle noch vollständige Job-,
Attempt-, Recovery-, Sleep- oder Power-Steuerung ein. Attempt-Telemetrie folgt
mit dem persistenten Job-Lifecycle; B.5 protokolliert den interaktiven
Requestpfad strukturiert.

## 2. Verbindliche Anforderungen

| Anforderung | Umsetzung in B.5 |
| --- | --- |
| OBS-01 | Jedes angenommene LLM-Ereignis korreliert die vorhandene `request_id`, `worker_id` und nach Acquire die `lease_id`. Nicht vorhandene IDs werden nicht erzeugt. |
| OBS-02 und OBS-03 | Gesamtdauer, Wake-, Readiness- und First-Token-Latenz sowie Provider-Tokenzahlen werden nur bei tatsächlicher Verfügbarkeit ausgegeben. Fehlende Streaming-Tokenzahlen bleiben leer. |
| SEC-03 und AC-10 | Die bestehende zentrale Allowlist akzeptiert nur feste technische Felder und streng typisierte nicht negative Ganzzahlen. Statische Ereignisdefinitionen und Fehlercodes verhindern die Übernahme von Nutzdaten oder Exceptiontexten. |
| AC-03 bis AC-05 | Integrationstests führen den Pfad Authentifizierung, Admission, persistente Lease, Wake, Readiness, Streaming und Release mit kontrollierten Transporten aus. Heartbeat, Überlast und Abbruch bleiben abgedeckt. |
| Portabilität | Zeitmessungen verwenden eine monotone Uhr. Worker- und Service-Ziele stammen ausschließlich aus der validierten Runtime-Konfiguration. |

## 3. Architektur und Schnittstellen

`LlmTelemetry` erzeugt pro angenommener Anfrage einen `LlmRequestSpan`. Dieser
Span hält nur freigegebene Korrelationskennungen, monotone Zeitmarken und
optionale numerische Metriken. Er emittiert genau ein terminales Ereignis:

- `llm.request_completed` mit `completion_status: succeeded`,
- `llm.request_failed` mit einer geschlossenen technischen Fehlerklasse und
  einem statischen Fehlercode oder
- `llm.request_cancelled` mit `completion_status: cancelled`.

Die Gesamtdauer beginnt an der HTTP-Grenze vor Authentifizierung und endet nach
vollständigem Serviceabschluss. Der Span entsteht erst nach erfolgreicher
Authentifizierung, damit abgewiesene Credentials keine operative LLM-Nutzung
erzeugen. Die Lease-ID wird erst nach erfolgreichem Acquire ergänzt. Der
Readiness-Orchestrator liefert monotone Readiness- und, falls tatsächlich
geweckt wurde, Wake-Latenz. Der Streaming-Pfad setzt TTFT beim ersten
validierten Upstream-Ereignis. Nicht streamende Tokenzahlen stammen nur aus
der validierten Providerantwort.

Das Logging bleibt lokal, synchron und unabhängig von Datenbank oder Netzwerk.
Die Logger-Factory verändert weder Root-Logger noch globale Handler.

## 4. Entscheidungen

Die Telemetriegrenze ist in
[OASIX-DEC-023](../decisions.md#oasix-dec-023--geschlossene-llm-request-telemetrie)
dokumentiert.

- Dauerwerte werden als ganzzahlige Millisekunden aus einer monotonen Uhr
  gebildet; UTC-Zeitstempel bleiben Aufgabe des vorhandenen JSON-Formatters.
- Die zentrale Logging-Allowlist wurde explizit um die stabilen Kernfelder
  erweitert. Beliebige `extra_metrics`, Payloads oder dynamische Feldnamen
  bleiben für diesen Requestpfad verboten.
- Streaming-Tokenzahlen werden nicht aus Chunks geschätzt. Die unterstützte
  geschlossene Streaming-Teilmenge liefert sie nicht, daher bleiben sie aus.
- Ein erster terminaler Status gewinnt. Spätere Cleanup-Pfade können dasselbe
  Ereignis nicht doppelt oder widersprüchlich protokollieren.

## 5. Umsetzung

- `src/oasix/llm/telemetry.py` implementiert den sicheren Requestspan und die
  drei statischen Ereignisdefinitionen.
- `src/oasix/llm/gateway.py` ordnet Status und feste technische Fehlercodes an
  der HTTP-/Streaming-Grenze zu.
- `src/oasix/llm/service.py` ergänzt Lease-, Readiness-, First-Token- und
  validierte Usage-Daten dort, wo sie entstehen.
- `src/oasix/worker/orchestration.py` gibt die gemessenen Wake- und Readiness-
  Latenzen eines erfolgreichen Durchlaufs zurück.
- `src/oasix/logging/core.py` validiert die neue geschlossene Menge numerischer
  Felder und den Abschlussstatus.

## 6. Tests und Nachweise

| Prüfung | Umgebung | Ergebnis | Nachweis |
| --- | --- | --- | --- |
| Sichere Logging- und Telemetrieverträge | lokal, macOS, Python 3.12 | bestanden | `tests/test_structured_logging.py`, `tests/test_llm_telemetry.py` |
| Vollständiger LLM-Pfad und Abbruchpfade | lokal, macOS, Python 3.12 | bestanden | `tests/test_llm_proxy.py`, `tests/test_llm_streaming.py` |
| B.5-fokussierte Suite | lokal, macOS, Python 3.12 | 88 bestanden | B.5-Arbeitsstand vor Commit |
| Vollständige pytest-Suite | lokal, macOS, Python 3.12 | 358 bestanden | B.5-Arbeitsstand vor Commit |
| Ruff, Paket-, Import-, Link- und Diff-Prüfung | lokal | bestanden | B.5-Arbeitsstand vor Commit |
| Linux-CI | GitHub Actions, Ubuntu, Python 3.12 | bestanden | [Lauf `38008622994`](https://github.com/madebyzwen/oasix/actions/runs/38008622994) |

Die Tests verwenden temporäre SQLite-Datenbanken, injizierte monotone Uhren,
kontrollierte HTTP-Transporte und einen lokalen Wake-Sender. Sie benötigen
weder produktive Netzwerkziele noch reale Secrets. Abgedeckt sind typisierte
Metriken, fehlende Providerwerte, technische Fehler, Sentinel-Redaktion,
Konfigurationswechsel des aktiven Workers, gleichzeitige Clients, Überlast,
Heartbeat/Renew, Release-Fehler sowie Cancellation während Readiness und
Streaming.

## 7. Einschränkungen und Risiken

- Die Metriken werden in strukturierten Logs ausgegeben und noch nicht in
  einer separaten Request-Telemetrietabelle persistiert. Persistente
  Attempt-Telemetrie gehört zum späteren Job-Lifecycle.
- Der Streaming-Vertrag enthält keine Usage-Felder. B.5 erfindet deshalb keine
  Streaming-Tokenzahlen.
- Millisekundenwerte sind für Betriebsbeobachtung bestimmt und keine
  Echtzeitgarantie. Scheduler-, Netzwerk- und Datenbankeffekte sind Bestandteil
  der gemessenen Ende-zu-Ende-Dauer.
- Authentifizierungsfehler erzeugen bewusst keinen LLM-Requestspan. Sie nutzen
  den Worker nicht und enthalten keine sichere korrelierbare Clientidentität.
- Reale Wake-Netze, DNS, TLS und konkrete Provider bleiben durch einen
  Deployment-Smoke-Test zu prüfen.

## 8. Abnahmestatus

B.5 ist implementiert, lokal und unter Linux geprüft sowie unabhängig geprüft
und freigegeben. Damit sind B.3.1, B.4 und B.5 im dokumentierten Umfang
abgeschlossen.

## 9. GitHub-Referenzen

- Implementierung: Commit
  `feat(observability): complete LLM path telemetry and integration tests` in
  [PR #6](https://github.com/madebyzwen/oasix/pull/6)
- Pull Request: [PR #6](https://github.com/madebyzwen/oasix/pull/6), offen und
  nicht gemergt
- Implementierungs-Commit:
  [`d9a17cb`](https://github.com/madebyzwen/oasix/commit/d9a17cb8487b6258624b8ea5ccbd694903320bfb)
- Linux-CI: [Lauf `38008622994`](https://github.com/madebyzwen/oasix/actions/runs/38008622994), bestanden
