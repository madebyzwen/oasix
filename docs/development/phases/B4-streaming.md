# Phase B.4 – Streaming und Cancellation

Status: abgeschlossen, unabhängig geprüft und freigegeben

## 1. Ziel und Abgrenzung

B.4 erweitert ausschließlich den in B.3.1 geschützten
`POST /v1/chat/completions`-Pfad um `stream: true`. Erfolgreiche Antworten
werden inkrementell als OpenAI-kompatible Server-Sent Events übertragen. Der
Concurrency-Slot, die persistente Lease, ihr Heartbeat und die Upstream-
Verbindung bleiben bis zum tatsächlichen Streamende oder bis zur vollständigen
Abbruchbereinigung aktiv.

Weitere OpenAI-Routen, neue Requestparameter, WebSockets, Stream-Persistenz,
Resume und Telemetrie sind nicht Teil von B.4.

## 2. Verbindliche Anforderungen

| Anforderung | Umsetzung in B.4 |
| --- | --- |
| Inkrementelles Streaming | SSE-Daten werden in begrenzten Chunks verarbeitet, als geschlossenes Chunk-Schema validiert und ohne vollständiges Puffern des Upstream-Streams weitergegeben. |
| Begrenzte Ressourcen | Ein einzelnes SSE-Ereignis ist auf 1 MiB, der gesamte Stream auf 64 MiB und die Gesamtdauer auf den konfigurierten Request-Timeout begrenzt. Komprimierte Streams und übergroße deklarierte Responses werden abgewiesen. |
| Korrekte Terminierung | Das Gateway sendet `[DONE]` erst, nachdem der Upstream sein eigenes `[DONE]` geliefert sowie Upstream-Schluss und Lease-Release erfolgreich abgeschlossen sind. Unvollständige oder fehlerhafte Streams erhalten keinen Erfolgsmarker. |
| Cancellation | Die ASGI-Antwort schließt ihren Body-Iterator auch bei Disconnect explizit. Jede verschachtelte Generator- und HTTPX-Ebene wird geschlossen; Lease und Admission werden im selben kontrollierten Abbau freigegeben. |
| Lease und Concurrency | Heartbeats laufen während langsamer Streams weiter. Lease und Concurrency-Slot umfassen die gesamte reale Nutzung und werden nicht vorzeitig freigegeben. |
| Sicherheitsgrenze | Upstream- oder Cleanup-Details werden nach Streambeginn nur als statisches SSE-Fehlerobjekt gemeldet. Prompts, Antworten, Secrets, URLs und Exceptiontexte werden nicht in Fehler übernommen. |

## 3. Architektur und Schnittstellen

Die B.3.1-Admission ist ein explizites, idempotent schließbares Token. Die
HTTP-Grenze reserviert es vor dem begrenzten Body-Parsing und übergibt bei
`stream: true` seine Lebensdauer an die verwaltete Streaming-Antwort.

Der Stream besitzt vier explizite Cleanup-Ebenen:

1. `_ManagedStreamingResponse` schließt den Body-Iterator bei normalem Ende,
   ASGI-Disconnect oder Sendefehler.
2. Die Gateway-Hülle hält Admission und sendet erst nach erfolgreichem
   Serviceabschluss `[DONE]`.
3. `LlmProxyService.stream_admitted()` hält Lease und Heartbeat und schließt
   seinen Upstream-Iterator explizit.
4. `HttpLlmUpstream.stream()` schließt die HTTPX-Response in allen Pfaden.

Die SSE-Grenze akzeptiert Datenereignisse mit der geschlossenen
`ChatCompletionChunk`-Teilmenge. Kommentare werden nicht weitergeleitet;
unbekannte SSE-Felder, doppelte JSON-Schlüssel, ungültiges UTF-8, unzulässige
Chunk-Felder und EOF ohne `[DONE]` schlagen geschlossen fehl. Erfolgreiche
Chunks werden kanonisch neu serialisiert. Vor dem HTTP-Response-Start wird
höchstens das erste validierte Ereignis gelesen. Dadurch behalten Setup-,
Readiness- und Upstream-Fehler ihren kontrollierten HTTP-Status; danach bleibt
der Pfad inkrementell und puffert nie die vollständige Antwort.

## 4. Entscheidungen

Die Streaming- und Cleanup-Semantik ist in
[OASIX-DEC-022](../decisions.md#oasix-dec-022--inkrementelles-sse-streaming-mit-explizitem-cleanup)
dokumentiert.

- Der Upstream-`[DONE]`-Marker wird konsumiert, aber nicht sofort
  weitergeleitet. Nur nach erfolgreichem Lease-Release erzeugt die Control
  Plane den finalen Client-Marker.
- Fehler nach dem ersten übertragenen Ereignis werden bei bereits gesendeten
  HTTP-Headern als statisches SSE-Fehlerobjekt beendet. Der Stream enthält dann
  ausdrücklich kein `[DONE]`; der HTTP-Status kann nach Headerbeginn nicht mehr
  geändert werden. Fehler vor dem ersten validierten Ereignis werden dagegen
  über die bestehende sichere HTTP-Fehlerabbildung gemeldet.
- Client-Disconnect wird über die ASGI-Streaming-Semantik erkannt. Die eigene
  Response-Hülle garantiert zusätzlich `aclose()` für den Body-Iterator, weil
  ein bloßes abgebrochenes `async for` verschachtelte Async-Generatoren nicht
  zuverlässig schließt.

## 5. Umsetzung

- `src/oasix/llm/models.py` ergänzt die geschlossene Chunk-Teilmenge.
- `src/oasix/llm/transport.py` implementiert begrenztes, inkrementelles
  SSE-Framing, Validierung, Timeout und Upstream-Cleanup.
- `src/oasix/llm/service.py` hält Lease und Heartbeat über den gesamten Stream
  und schließt verschachtelte Iteratoren explizit.
- `src/oasix/llm/gateway.py` überträgt Admission-Eigentum an eine verwaltete
  Streaming-Antwort und liefert sichere Abschluss- beziehungsweise
  Fehlerereignisse.

## 6. Tests und Nachweise

| Prüfung | Umgebung | Ergebnis | Nachweis |
| --- | --- | --- | --- |
| B.4-Streaming- und Cancellation-Tests | lokal, macOS, Python 3.12 | 11 bestanden | `tests/test_llm_streaming.py` |
| B.3/B.4-fokussierte Suite | lokal, macOS, Python 3.12 | 41 bestanden | B.4-Arbeitsstand vor Commit |
| Vollständige pytest-Suite | lokal, macOS, Python 3.12 | 340 bestanden | B.4-Arbeitsstand vor Commit |
| Ruff, Paket-, Import-, Link- und Diff-Prüfung | lokal | bestanden | B.4-Arbeitsstand vor Commit |
| Linux-CI | GitHub Actions, Ubuntu, Python 3.12 | bestanden | [Lauf 38007804479](https://github.com/madebyzwen/oasix/actions/runs/38007804479) |

Die Tests decken mehrteilige und fragmentierte SSE-Daten, inkrementellen
Verbrauch, langsame Streams mit Heartbeat, Client-Disconnect, Upstream-Abbruch,
EOF ohne `[DONE]`, Größenüberschreitung, Timeout, Renew- und Release-Fehler,
sichere Ressourcenbereinigung sowie das Concurrency-Limit über die gesamte
Streaming-Dauer ab.

## 7. Einschränkungen und Risiken

- Nach Beginn einer HTTP-200-Streaming-Antwort kann der Statuscode nicht mehr
  geändert werden. Der statische Fehlerdatensatz ohne `[DONE]` ist deshalb die
  verbindliche Fehleranzeige für späte Fehler.
- B.4 puffert keinen vollständigen Stream, setzt aber harte Größenlimits. Sehr
  große legitime Ausgaben oberhalb 64 MiB werden kontrolliert beendet.
- SSE-Kommentare werden akzeptiert und verworfen. Eventtypen, Retry-Felder,
  Binärdaten sowie offene providerabhängige Erweiterungen werden nicht
  unterstützt.
- Das prozesslokale Concurrency-Limit und die Single-Process-Annahme aus B.3.1
  bleiben bestehen.
- Zeit- und Token-Telemetrie wurde aufbauend in B.5 integriert.

## 8. Abnahmestatus

B.4 ist implementiert, lokal und unter Linux geprüft sowie unabhängig geprüft
und freigegeben. B.5 ist ebenfalls unabhängig geprüft und freigegeben.

## 9. GitHub-Referenzen

- Implementierung: Commit `feat(llm): implement streaming and cancellation`
  in [PR #6](https://github.com/madebyzwen/oasix/pull/6)
- Pull Request: [PR #6](https://github.com/madebyzwen/oasix/pull/6), offen und
  nicht gemergt
- Implementierungs-Commit:
  [`607acd2`](https://github.com/madebyzwen/oasix/commit/607acd2551f00fd7bc333effe8f66362d0020b7e)
- Linux-CI: [Lauf 38007804479](https://github.com/madebyzwen/oasix/actions/runs/38007804479), bestanden
