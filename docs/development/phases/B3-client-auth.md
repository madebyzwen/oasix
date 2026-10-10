# Phase B.3.0 – Client-API-Authentifizierung und Berechtigungsmodell

Status: abgeschlossen, unabhängig geprüft und freigegeben

## 1. Ziel und Abgrenzung

B.3.0 implementiert einen transportneutralen, wiederverwendbaren Baustein für
Bearer-Authentifizierung und explizite Berechtigungsprüfung für Client- und
Management-APIs. Mehrere generisch benannte Client-Identitäten und
überlappende Schlüssel für eine kontrollierte Rotation werden unterstützt.

Nicht enthalten sind HTTP-Routen, OpenAI-Proxy, Streaming, Power-Endpunkte,
Lease-Orchestrierung, Benutzerverwaltung, SSO oder eine persistente
API-Schlüsseldatenbank. Insbesondere wurde die eigentliche Phase B.3 nicht
begonnen.

## 2. Verbindliche Anforderungen

| Anforderung | Umsetzung in B.3.0 |
| --- | --- |
| SEC-01 | `inference` und `administration` sind explizite, geschlossene Capabilities. Ein authentifizierter Schlüssel gewährt ausschließlich seine konfigurierten Rechte. |
| CFG-01, CFG-03 | Runtime-Schema 3 verlangt eine vollständige `client_auth`-Sektion. Die YAML-Datei enthält nur Secret-Referenzen; alle referenzierten Werte werden über die bestehende Secret-Quelle aufgelöst. |
| SEC-02 | Provider- und Client-Referenzen dürfen nicht identisch sein. Auch verschiedene Referenzen mit demselben aufgelösten Wert werden beim Aufbau der Authentifizierung abgewiesen. |
| SEC-03, AC-10 | Schlüsselwerte werden weder gespeichert noch geloggt oder in öffentliche Exceptions übernommen. Authentifizierungsfehler haben unabhängig von Ursache und Schlüsselbestand denselben Text. |
| ALIAS-01 bis ALIAS-03 | Client-IDs und Berechtigungen sind generisch; Hostnamen, Frontends und Deployment-Bezeichnungen beeinflussen keine Prüfung. |

## 3. Architektur und Schnittstellen

Der Startup-Ablauf lädt zuerst die vollständige Runtime-Konfiguration und alle
Secret-Referenzen. Noch vor dem Aufbau einer API wird daraus der
`ClientAuthenticator` erzeugt:

```python
from oasix.auth import ClientAuthorizer, create_client_authenticator
from oasix.config import load_startup_configuration

configuration = load_startup_configuration()
authenticator = create_client_authenticator(
    configuration.runtime,
    configuration.secrets,
)
authorizer = ClientAuthorizer()
```

Der Authenticator akzeptiert später den Wert eines HTTP-`Authorization`-
Headers und liefert ausschließlich bei genau einer passenden Zuordnung ein
typisiertes, unveränderliches `AuthenticatedClient`-Objekt.
`ClientAuthorizer.require()`
prüft davon getrennt genau eine angeforderte `ClientPermission`. Ein
Administrationsrecht impliziert weder Inference noch künftige, nicht definierte
Capabilities.

Die Komponente hängt weder von einem Webframework noch von Netzwerk,
Persistenz, Worker-Transport oder Logging ab. Das in B.3.1 implementierte
Gateway übernimmt den Header aus seinem Transport, führt die Authentifizierung
einmal aus und prüft vor der Operation die konkrete Capability.

## 4. Entscheidungen

Die additive Runtime-Version und die Sicherheitsgrenze sind in
[OASIX-DEC-020](../decisions.md#oasix-dec-020--additives-runtime-schema-3-und-client-api-authentifizierung)
festgehalten.

- Runtime-Version 2 bleibt für die bereits implementierten Komponenten gültig,
  kann aber keinen Client-Authenticator erzeugen.
- Runtime-Version 3 verlangt `client_auth`; eine Version-2-Datei darf diese
  Sektion nicht stillschweigend interpretieren.
- Ein Client besitzt mindestens eine Secret-Referenz und mindestens eine
  explizite Capability. Mehrere Referenzen derselben Identität ermöglichen
  eine Rotation mit kontrollierter Überlappung.
- Der Authenticator erzeugt bei jedem Prozessstart einen zufälligen Pepper und
  hält Schlüssel nur als HMAC-SHA-256-Digests. Beim Request werden alle
  konfigurierten Digests mit `hmac.compare_digest()` geprüft, ohne nach dem
  ersten Treffer abzubrechen.
- Client-Schlüssel verwenden das ASCII-`b64token`-Alphabet für Bearer-
  Credentials und sind auf 4.096 Zeichen begrenzt; ein übergebener
  Authorization-Wert ist zusätzlich auf 8.192 Zeichen begrenzt.
- Fehlende, syntaktisch ungültige, mit falschem Schema übermittelte und
  unbekannte Credentials ergeben dieselbe Authentifizierungsfehlerklasse und
  dieselbe Meldung. Fehlende Rechte werden separat als Autorisierungsfehler
  behandelt.

Ein Passwort-Hashverfahren wurde nicht eingeführt: Die Werte sind zufällige,
extern verwaltete API-Schlüssel, die beim Start für direkte Request-Prüfungen
verfügbar sein müssen. Die HMAC-Digests begrenzen die Aufbewahrung von
Klartextwerten im langlebigen Authentifizierungsobjekt; Schutz und Entropie der
Quelldateien bleiben Deployment-Verantwortung.

## 5. Umsetzung

Das Konfigurationsformat für Version 3 lautet in gekürzter Form:

```yaml
schema_version: 3
client_auth:
  clients:
    inference-client:
      key_secrets:
        - source: file
          name: client_inference_key
        - source: file
          name: client_inference_next_key
      permissions:
        - inference
    operations-client:
      key_secrets:
        - source: file
          name: client_admin_key
      permissions:
        - administration
```

Es gibt keine implizite Identität, keinen Klartextschlüssel und kein
Standardrecht. Unbekannte Felder, leere Zuordnungen, unbekannte Permissions,
doppelte Referenzen, wiederverwendete Provider-Referenzen und fehlende
Secret-Dateien verhindern den Startup. Doppelte aufgelöste Werte und
Wertgleichheit mit Provider-Credentials werden beim unmittelbar anschließenden
Aufbau des Authenticators geschlossen abgewiesen.

Für eine Rotation wird zunächst eine zusätzliche Secret-Referenz derselben
Identität ausgerollt und die Control Plane kontrolliert neu gestartet. Nachdem
Clients umgestellt sind, wird die alte Referenz entfernt und erneut gestartet.
Hot Reload und automatische Rotation sind nicht implementiert.

Betroffene Module:

- `src/oasix/auth/`: typisierte Identität, Authenticator, Authorizer und feste
  sichere Fehlerklassen,
- `src/oasix/config/models.py`: Runtime-Version 3, Client-Identitäten,
  Secret-Referenzen und Capabilities,
- `src/oasix/config/secrets.py`: vollständige Auflösung der Client-Referenzen,
- `config/oasix.example.yaml`: neutrale Version-3-Beispielkonfiguration ohne
  Schlüsselwerte.

## 6. Tests und Nachweise

| Prüfung | Umgebung | Ergebnis | Nachweis |
| --- | --- | --- | --- |
| Client-Authentifizierung und Autorisierung | lokal, macOS, Python 3.12 | 28 bestanden | `tests/test_client_auth.py` |
| Vollständige pytest-Suite | lokal, macOS, Python 3.12 | 298 bestanden | `feature/b-llm-path` vor Commit |
| Ruff, Paket-, Import-, Dokumentationslink- und Diff-Prüfung | lokal | bestanden | dieser Implementierungsauftrag |
| Linux-CI | GitHub Actions, Ubuntu, Python 3.12 | bestanden | [Lauf `38005483797`](https://github.com/madebyzwen/oasix/actions/runs/38005483797) |

Die Tests prüfen beide Capabilities, mehrere Clients, einheitliche
Fehlergrenzen, fehlende und ungültige Header, doppelte Zuordnungen,
Provider-Trennung, Rotation sowie Secret-Redaktion in Meldungen,
Repräsentationen, Logs und produktionsnah erfassten Traceback-Locals. Sie
verwenden weder Netzwerk noch externe Identitätsanbieter.

## 7. Einschränkungen und Risiken

- B.3.0 stellt selbst keine Middleware oder Route bereit. Die in B.3.1
  implementierte HTTP-Schicht begrenzt die Eingabe, loggt keine Authorization-
  Werte und weist mehrfach vorhandene Authorization-Header als mehrdeutig ab.
- Die Authentifizierungsprüfung ist pro Prozess lokal. Konfigurationsänderungen
  und Rotation werden erst nach einem kontrollierten Neustart wirksam.
- Starke, zufällig erzeugte Schlüssel, restriktive Dateirechte und sichere
  Verteilung sind betriebliche Voraussetzungen; B.3.0 implementiert keine
  Schlüsselgenerierung oder Benutzerverwaltung.
- Gleichförmige Fehler und vollständige Digest-Vergleiche verhindern eine
  direkte Existenzoffenlegung. Allgemeine Laufzeit-Seitenkanäle eines
  Python-Prozesses werden nicht als kryptografisch vollständig ausgeschlossen
  behauptet.
- B.3.1 komponiert Authentifizierung, Concurrency-Gate, Lease, Wake/Readiness,
  Upstream-Nutzung und die nicht streamenden Abbruch-/Fehlerpfade. B.4 ergänzt
  die streamingspezifischen Grenzen.

## 8. Abnahmestatus

B.3.0 ist implementiert, unabhängig geprüft und als Grundlage des
B.3.1-Gateways freigegeben. B.3.1 nutzt den Baustein produktiv; B.4 und B.5
sind ebenfalls implementiert, unabhängig geprüft und freigegeben.

## 9. GitHub-Referenzen

- Implementierung: [Commit `846c3cb`](https://github.com/madebyzwen/oasix/commit/846c3cbbe95306ab3705bb641bdf9c67da35b396)
  in [PR #6](https://github.com/madebyzwen/oasix/pull/6)
- Pull Request: [PR #6](https://github.com/madebyzwen/oasix/pull/6), offen und
  nicht gemergt
- Linux-CI: [Lauf `38005483797`](https://github.com/madebyzwen/oasix/actions/runs/38005483797), bestanden
