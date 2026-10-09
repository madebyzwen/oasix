# Phase B.1 – HTTP-Service-Readiness

Status: implementiert, unabhängige Review-Abnahme ausstehend

## 1. Ziel und Abgrenzung

B.1 implementiert einen produktiven asynchronen HTTP-Adapter für den bereits in
A.3.1 definierten `ServiceReadinessProbe`. Er prüft einen benannten,
konfigurierten Dienst des aktiven Workers und liefert entweder ein bestätigtes
Readiness-Ergebnis oder einen festen transportneutralen Fehler.

Nicht enthalten sind Wake-on-LAN, Readiness-Orchestrierung über mehrere
Versuche, Worker-Zustandsübergänge, LLM-Weiterleitung, Leases, Streaming,
Telemetrie, Sleep-Ausführung oder externe Infrastruktur. Tests verwenden nur
HTTPX-Testtransporte und stellen keine Netzwerkverbindung her.

## 2. Verbindliche Anforderungen

| Anforderung | Umsetzung in B.1 |
| --- | --- |
| ARC-03, ALIAS-01 bis ALIAS-03 | Der Adapter wird aus `RuntimeConfig` an den generischen `active_worker` gebunden. Es gibt keine festen Hosts, Ports oder Hardwareannahmen. |
| CFG-02, CFG-04 | Endpoint, GET/HEAD-Methode, Readiness-Pfad, erwartete Statuscodes und Authentifizierungsreferenz stammen ausschließlich aus dem validierten Serviceprofil. |
| CFG-03, SEC-02, SEC-03, AC-10 | Bearer- und Header-Werte werden erst aus `ResolvedSecrets` in das Request-Objekt übernommen. Repräsentation, öffentliche Fehler und geprüfte Produktions-Traceback-Frames enthalten weder Secret noch Endpoint oder Transportmeldung. |
| CFG-05, WRK-05 | Alle HTTP-Operationen verwenden `policies.readiness_timeout_seconds`; HTTPX-Timeouts werden als `WorkerTimeoutError` übersetzt. |
| WRK-03 | `check_readiness(service_id)` prüft genau den konfigurierten Dienst. Ein unbekannter oder deaktivierter Dienst wird vor Netzwerkzugriff kontrolliert abgewiesen. |
| WRK-04 | Ein erwarteter HTTP-Status liefert `ready=True`; jeder andere empfangene Status liefert `ready=False`. Dispatch wird in dieser Phase nicht implementiert. |

## 3. Architektur und Schnittstellen

`create_http_service_readiness_adapter(runtime, secrets)` löst ausschließlich
den aktiven Worker auf und übernimmt dessen validiertes Profil sowie den
konfigurierten Timeout. `HttpServiceReadinessAdapter` erfüllt den bestehenden
`ServiceReadinessProbe` und besitzt einen expliziten asynchronen Lebenszyklus
über `async with` beziehungsweise `aclose()`.

Der Probeablauf ist geschlossen:

```text
RuntimeConfig + ResolvedSecrets
              |
              v
aktiver Worker -> benannter, aktivierter Service
              |
              v
GET/HEAD ohne Redirects und Umgebungs-Proxies
              |
              v
erwarteter Status -> ready=True
anderer Status    -> ready=False
Timeout/Transport -> fester Workerfehler
```

Der absolute Readiness-Pfad wird am Origin des Service-Endpunkts aufgelöst. Ein
vorhandener API-Pfad im Endpoint wird dabei nicht als Präfix interpretiert. Der
Adapter liest keine Response-Payload; für Readiness ist ausschließlich der
konfigurierte Statuscodevertrag maßgeblich.

## 4. Entscheidungen

[OASIX-DEC-017](../decisions.md#oasix-dec-017--isolierter-asynchroner-http-readiness-adapter)
dokumentiert die Wahl von HTTPX, den isolierten Client, die Netzwerksicherheits-
grenzen und den Lebenszyklus.

## 5. Umsetzung

- `src/oasix/worker/readiness.py`: asynchroner Adapter, aktive
  Worker-Auflösung, URL-Aufbau, Authentifizierung und sichere Fehlerübersetzung.
- `src/oasix/worker/errors.py`: fester `WorkerConfigurationError` für lokal
  nicht zulässige Worker-Operationen.
- `src/oasix/worker/__init__.py`: öffentliche Adapter- und Factory-Exporte.
- `pyproject.toml`: HTTPX als begrenzte Laufzeitabhängigkeit.
- `tests/test_http_readiness.py`: isolierte GET-/HEAD-, Auth-, Redirect-,
  Proxy-, Status-, Fehler- und Redaktionsnachweise.

Bestehende Konfigurations-, Persistenz-, Logging- und Worker-Verträge bleiben
kompatibel; es wurde keine Datenbankmigration angelegt.

## 6. Tests und Nachweise

| Prüfung | Umgebung | Ergebnis | Nachweis |
| --- | --- | --- | --- |
| HTTP-Readiness-Tests | lokal, macOS, Python 3.12 | 11 bestanden | `tests/test_http_readiness.py` |
| Vollständige pytest-Suite | lokal, macOS, Python 3.12 | 238 bestanden | `feature/b-llm-path` |
| Ruff Linting und Formatprüfung | lokal, macOS, Python 3.12 | bestanden, 49 Dateien geprüft | `feature/b-llm-path` |
| Paket-, Import- und Diff-Prüfung | lokal | bestanden | `feature/b-llm-path` |
| Linux-CI | GitHub Actions, Ubuntu, Python 3.12 | nach Push ausstehend | zukünftiger Phase-B-Pull-Request |

Die Tests verwenden keine echten Worker, Proxies, Secrets außerhalb der
temporären Testverzeichnisse oder Netzwerkdienste. Verhalten gegen einen realen
HTTP-/TLS-Dienst bleibt bis zu einem Deployment-Smoke-Test unbestätigt.

## 7. Einschränkungen und Risiken

- Ein externer TLS-Reverse-Proxy oder internes HTTP wird ausschließlich durch
  den konfigurierten Endpoint bestimmt; B.1 ergänzt keine eigene TLS-Terminierung.
- Der aufrufende Lebenszyklus muss `aclose()` ausführen, um den HTTP-Pool sicher
  freizugeben.
- Benutzerdefinierte Auth-Header sind Teil der bereits validierten
  Konfiguration. Ihr serverseitiger Vertrag kann ohne reale Gegenstelle nicht
  geprüft werden.
- Readiness wird weder persistiert noch zeitlich gecacht. Wake-Retries und die
  Aggregation mehrerer benötigter Dienste folgen erst in B.2.

## 8. Abnahmestatus

B.1 ist implementiert und lokal vollständig geprüft, aber bis zum unabhängigen
Code-Review nicht freigegeben. B.2 bis B.5 wurden in diesem Commit nicht
begonnen.

## 9. GitHub-Referenzen

- Commits: `feat(worker): implement HTTP service readiness adapter` auf
  `feature/b-llm-path`; vollständiger SHA nach Commit im Pull Request
- Pull Requests: nach dem ersten Push zu erstellen
- CI-Läufe: nach dem ersten Push ausstehend
