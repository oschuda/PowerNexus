# IPMI (Server-Fernwartungskarte / BMC)

IPMI steuert den Server über seine Fernwartungskarte (BMC) – unabhängig vom Betriebssystem.

| Aktion | ipmitool-Befehl | Wirkung |
|---|---|---|
| Herunterfahren | `chassis power soft` | ACPI-Signal wie kurzer Druck auf den Einschalter; das Betriebssystem fährt geordnet herunter |
| Ausschalten (hart) | `chassis power off` | sofort aus, **ohne** Herunterfahren – nur als letzter Schritt |
| Einschalten | `chassis power on` | |

Für Server mit iDRAC, iLO oder aktueller BMC-Firmware (z. B. Wortmann TERRA, Firmware `egs-…`) ist
**Redfish** die modernere Schnittstelle (siehe [Redfish](redfish)). IPMI ist der Rückfall für BMCs ohne
Redfish.

## Einrichtung an der Fernwartungskarte

1. **IPMI over LAN** aktivieren (iDRAC: *iDRAC-Einstellungen → Netzwerk → IPMI-Einstellungen*;
   iLO: *Sicherheit → Zugriffseinstellungen → IPMI/DCMI over LAN*; TERRA/Intel-BMC: Weboberfläche
   *Configuration → IPMI*).
2. Eigenes Konto mit Berechtigung **Operator** anlegen (genügt für Ein-/Ausschalten).
3. Vom USV-Server prüfen:

```bash
ipmitool -I lanplus -H <bmc-adresse> -U <benutzer> -P <passwort> -L OPERATOR chassis power status
```

## Einrichtung in Evolved PowerNexus

| Feld | Wert |
|---|---|
| Schnittstelle | IPMI |
| Adresse | IP der **Fernwartungskarte** (nicht des Betriebssystems) |
| Port | 623 |
| Benutzer / Passwort | Konto aus Schritt 2 |

Das Passwort wird `ipmitool` über eine Umgebungsvariable übergeben und erscheint nicht in der
Prozessliste.

## Fehlerbilder

| Meldung | Ursache | Maßnahme |
|---|---|---|
| ipmitool ist nicht installiert | Paket fehlt | `sudo apt install ipmitool` auf dem USV-Server |
| `Unable to establish IPMI v2 / RMCP+ session` | IPMI over LAN aus, falsches Passwort | Schritt 1 und 2 |
| `Insufficient privilege level` | Konto ohne Operator-Recht | Recht erhöhen |
| Herunterfahren ohne Wirkung | Betriebssystem reagiert nicht auf ACPI | Energieeinstellung *Netzschalter → Herunterfahren* am Server prüfen |
