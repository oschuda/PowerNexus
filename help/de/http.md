# HTTP-API (allgemeiner Aufruf)

Für Geräte und Anwendungen, die sich über eine Web-Schnittstelle herunterfahren lassen – z. B.
Proxmox VE, TrueNAS, Home Assistant oder ein Node-RED-Flow. Evolved PowerNexus sendet bei der Aktion
*Herunterfahren* genau einen konfigurierten HTTP-Aufruf und wertet den Statuscode aus.

Der Aufruf muss **in einem Schritt** auslösen. Schnittstellen, die zuerst eine Anmeldung mit
Sitzungs-ID verlangen (z. B. Synology DSM), werden so nicht unterstützt – dafür [SSH](ssh) verwenden.

## Felder

| Feld | Bedeutung |
|---|---|
| Adresse, Port | Server; Port 443 (https) bzw. 80 (http), bei Proxmox 8006 |
| Protokoll | `https` (empfohlen) oder `http` (unverschlüsselt – Token gehen im Klartext über das Netz) |
| Methode | meist `POST` |
| Pfad | Pfad mit Abfrage, z. B. `/api2/json/nodes/pve1/status` |
| Inhalt (Body), Content-Type | was gesendet wird, z. B. `{"entity_id": "script.nas_aus"}` mit `application/json` |
| Weitere Header | je Zeile `Name: Wert`; **keine Geheimnisse** – sie werden unverschlüsselt gespeichert |
| Anmeldung | *keine*; *Basic* (Felder Benutzer und Passwort); *Bearer-Token* (Token im Feld Passwort, gesendet als `Authorization: Bearer …`); *Token in eigenem Header* (Header-Name eintragen, Token im Feld Passwort) |
| Erwarteter Status | Statuscodes, die als Erfolg gelten: `200-299` (Standard) oder z. B. `200,204` |
| Testpfad | optional: ein **lesender** Pfad (GET), mit dem „Verbindung testen“ die Anmeldung prüft. Ohne Testpfad prüft der Test nur die Erreichbarkeit und das Zertifikat. |
| Zertifikat-Fingerprint | wie bei [Redfish](redfish): leer = Prüfung über CA, sonst angeheftetes Zertifikat |

Token und Passwort werden verschlüsselt gespeichert und nie über die Oberfläche ausgeliefert. Im
Protokoll und im Testmodus erscheinen sie nur als `***`.

**Wichtig:** „Verbindung testen“ sendet **nie** den konfigurierten Aufruf, sondern höchstens einen
GET auf den Testpfad. Als Testpfad deshalb nie einen Pfad eintragen, der etwas auslöst.

## Beispiele

### Proxmox VE (Knoten herunterfahren)

1. In Proxmox: *Rechenzentrum → Berechtigungen → API-Token* für einen Benutzer anlegen; dem Token
   auf `/nodes/<knoten>` eine Rolle mit dem Recht `Sys.PowerMgmt` geben (z. B. eigene Rolle).
2. In Evolved PowerNexus:

| Feld | Wert |
|---|---|
| Adresse / Port | Proxmox-Host / 8006 |
| Methode / Pfad | `POST` / `/api2/json/nodes/<knoten>/status` |
| Inhalt / Content-Type | `command=shutdown` / `application/x-www-form-urlencoded` |
| Anmeldung | Token in eigenem Header, Header-Name `Authorization` |
| Passwort/Token | `PVEAPIToken=<benutzer>@<realm>!<token-id>=<geheimnis>` |
| Testpfad | `/api2/json/nodes/<knoten>/status` |
| Zertifikat | Proxmox nutzt ab Werk ein selbstsigniertes Zertifikat → Fingerprint übernehmen |

Proxmox fährt dabei die Gäste gemäß ihrer Einstellungen herunter.

### TrueNAS (REST-API v2.0)

| Feld | Wert |
|---|---|
| Methode / Pfad | `POST` / `/api/v2.0/system/shutdown` |
| Inhalt | `{}` (je nach Version auch `{"reason": "USV"}`) |
| Anmeldung | Bearer-Token (API-Key aus *Einstellungen → API-Keys*) |
| Testpfad | `/api/v2.0/system/info` |

Neuere TrueNAS-Versionen stellen die REST-API schrittweise ein; dann [SSH](ssh) verwenden.

### Home Assistant (Skript oder Dienst auslösen)

| Feld | Wert |
|---|---|
| Port | 8123 |
| Methode / Pfad | `POST` / `/api/services/script/turn_on` |
| Inhalt | `{"entity_id": "script.server_herunterfahren"}` |
| Anmeldung | Bearer-Token (langlebiges Zugriffstoken aus dem Benutzerprofil) |
| Testpfad | `/api/` |

### Node-RED

Einen Flow mit *http in*-Knoten (z. B. `POST /usv/shutdown`) und *http response* anlegen; Absicherung
über einen eigenen Header (Anmeldung *Token in eigenem Header*), den der Flow prüft.

## Fehlerbilder

| Meldung | Ursache | Maßnahme |
|---|---|---|
| Zertifikat wird von keiner bekannten CA bestätigt | selbstsigniertes Zertifikat | Fingerprint prüfen und übernehmen oder CA hinterlegen |
| Zertifikat stimmt NICHT mit dem angehefteten überein | Zertifikat erneuert oder fremdes Gerät | Fingerprint am Gerät prüfen, dann übernehmen |
| Antwort HTTP 401 / 403 | Token falsch, abgelaufen oder ohne Recht | Token und Rechte prüfen |
| Antwort HTTP 404 | Pfad falsch | Pfad in der API-Dokumentation des Geräts prüfen |
| Aktion fehlgeschlagen trotz Wirkung | Gerät antwortet mit anderem Status | „Erwarteter Status“ anpassen |
| Zeitüberschreitung | Adresse/Port falsch, Firewall | Erreichbarkeit vom USV-Server prüfen (`curl -v https://<adresse>:<port>/`) |
