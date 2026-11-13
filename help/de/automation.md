# Automatisierung: Regeln und Zielrechner

Bei einem Ereignis an einer USV (z. B. Stromausfall) kann Evolved PowerNexus Zielrechner geordnet
herunterfahren. Dafür gibt es zwei Bausteine:

- **Zielrechner** – *wie* ein Rechner gesteuert wird (Schnittstelle, Adresse, Anmeldung).
- **Regeln** – *wann* und *was* passiert (Ereignis, Bedingungen, Wartezeit, Schritte).

## Schnittstellen

| Schnittstelle | Für | Aktionen | Hilfe |
|---|---|---|---|
| SSH | Ubuntu Server, Arch Linux | Herunterfahren, Neu starten | [SSH](ssh) |
| PowerShell Remoting (WinRM) | Windows | Herunterfahren, Neu starten | [WinRM](winrm) |
| Redfish | Server-Fernwartungskarten (iDRAC, iLO, TERRA/OpenBMC, Supermicro) | Herunterfahren, Neu starten, Ausschalten, Einschalten | [Redfish](redfish) |
| IPMI | ältere Fernwartungskarten ohne Redfish | Herunterfahren, Ausschalten, Einschalten | [IPMI](ipmi) |
| OPC-UA | Steuerungen (z. B. Bosch Rexroth XM42), Industrie-PCs | Herunterfahren (= Variable schreiben) | [OPC-UA](opcua) |
| NUT-Client (FSD) | Rechner mit `upsmon` am eingebauten NUT-Server | FSD setzen, Rechner fahren sich selbst herunter | [NUT-Server](nut) |
| USV-Server selbst | dieser Rechner (optional) | Herunterfahren, nur als letzter Schritt | [Selbst-Shutdown](self-shutdown) |
| HTTP-API | NAS, Proxmox, Home Assistant, Node-RED u. a. | Herunterfahren (= konfigurierter Aufruf) | [HTTP-API](http) |

Eine laufende Wartezeit erscheint auf jeder Seite mit Countdown und „Abbrechen“. Nach einem Neustart des Dienstes läuft sie weiter, wenn das Ereignis noch besteht (mindestens 30 Sekunden, damit noch abgebrochen werden kann). War die Ausführung schon begonnen, wird sie nicht wiederholt, sondern als unterbrochen gemeldet.

Regeln können Empfängerlisten per E-Mail benachrichtigen, siehe [E-Mail und Microsoft 365](email).

## Aufbau einer Regel

| Teil | Bedeutung |
|---|---|
| Auslöser | ein oder mehrere Ereignisse, z. B. *Stromausfall*, *Batterie schwach* |
| Geltungsbereich | alle USVs, eine bestimmte USV oder eine USV-Gruppe |
| Bedingungen (optional) | z. B. *Akkuladung unter 60 %*, *Restlaufzeit unter 15 min* – alle müssen erfüllt sein |
| Wartezeit | Sekunden bis zur Ausführung; in dieser Zeit kann abgebrochen werden |
| Abbruch bei | z. B. *Netzspannung wieder da* – beendet eine laufende Wartezeit |
| Schritte | werden nacheinander ausgeführt; die Zielrechner eines Schritts parallel |
| Mehrfachschutz | dieselbe Regel löst für dieselbe USV höchstens einmal je Zeitraum aus |

### Ablauf

1. Ein Ereignis tritt auf (sichtbar unter **Meldungen**).
2. Passende, aktive Regeln starten ihre Wartezeit.
3. Vor der Ausführung werden die Bedingungen **erneut geprüft**. Ist z. B. die Akkuladung wieder
   gestiegen, wird abgebrochen.
4. Die Schritte werden ausgeführt; jedes Ergebnis erscheint als Meldung.

Der Schalter **AUTOMATION AKTIV / PAUSIERT** in der Kopfzeile blockiert jede Ausführung.

## Testen

- **Verbindung testen** (Zielrechner): prüft Anmeldung und Berechtigung, löst aber nichts aus.
- **Regel testen**: zeigt, welche Befehle ausgeführt *würden*, und prüft die Verbindung zu jedem
  Zielrechner – ohne etwas auszulösen.
- **Testmodus**: siehe [Testmodus](dry-run).
- **Regel von Hand auslösen** (nur Admin, per API): `POST /api/policies/<Regel>/trigger` mit `{"ups_id": "<USV>"}` startet die Regel, als wäre ihr Ereignis eingetreten. Wartezeit, Abbruch und Testmodus gelten wie im Ernstfall. **Ohne Testmodus werden die Schritte wirklich ausgeführt.** Ob ein echter Ausfall erkannt wird, belegt nur ein Test mit der USV selbst.

## Ablage

Zielrechner liegen in `hosts.yaml`, Regeln in `policies.yaml` im Programmverzeichnis. Passwörter
sind verschlüsselt; der Schlüssel liegt in `/etc/usv-monitor/secret.key`. Geht dieser Schlüssel
verloren, müssen alle Passwörter neu eingegeben werden.
