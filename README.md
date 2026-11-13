# Evolved PowerNexus

Überwacht unterbrechungsfreie Stromversorgungen und fährt bei Stromausfall oder schwachem Akku definierte Rechner geordnet
herunter. Oberfläche und Hilfe gibt es auf Deutsch und Englisch.

Die aktuelle Version steht in [`version.py`](version.py), die Änderungen in [`CHANGELOG.md`](CHANGELOG.md).

## Funktionen

- **Überwachung** beliebig vieler USVs per SNMP (UPS-MIB, APC PowerNet) oder USB über NUT, mit Standorten und Gruppen
- **Auswertung:** Historie, Netzqualität je Standort, Batterie-Bewertung (Zustand, Restlebensdauer, Datenbasis), Meldungen
- **Regeln:** Auslöser, Geltungsbereich, Bedingungen, Wartezeit mit Countdown und Abbruch, mehrere Schritte; offene
  Wartezeiten laufen nach einem Neustart weiter
- **Testmodus** global oder je Regel: Es wird nichts ausgeführt, nur protokolliert
- **E-Mail** über Microsoft 365 (Graph API oder SMTP mit OAuth 2.0) oder SMTP allgemein
- **Optional:** eingebauter NUT-Server für `upsmon`-Clients, Selbst-Shutdown des USV-Servers
- **PV-Wechselrichter (optional)** per MQTT mit Home-Assistant-Discovery: Leistung, Einspeisung, Ertrag, Verlauf und Vergleich mit einer USV

### Zielrechner

| Schnittstelle | Für | Aktionen |
|---|---|---|
| SSH | Linux (Ubuntu Server, Arch Linux) | Herunterfahren, Neu starten |
| PowerShell Remoting (WinRM, HTTPS) | Windows | Herunterfahren, Neu starten |
| Redfish | Server-BMC (iDRAC, iLO, OpenBMC/TERRA, Supermicro) | Herunterfahren, Neu starten, Ausschalten, Einschalten |
| IPMI | ältere BMC ohne Redfish | Herunterfahren, Ausschalten, Einschalten |
| OPC-UA | Steuerungen (z. B. Bosch Rexroth XM42) | Variable schreiben, unverschlüsselt oder verschlüsselt |
| HTTP-API | Proxmox, TrueNAS, Home Assistant, Node-RED | ein konfigurierter Aufruf |
| NUT (FSD) | Rechner mit `upsmon` | Anweisung zum Herunterfahren |

Die Einrichtung jeder Schnittstelle am Zielsystem beschreibt die Hilfe in der Oberfläche (Quelle: [`help/`](help)).

## Aufbau

| Pfad | Inhalt |
|---|---|
| `app.py`, `web/` | Litestar-Anwendung, Seiten und API |
| `runtime.py` | Abfrage der USVs, Erkennung von Meldungen |
| `snmp_managers.py` | SNMP- und NUT-Abfrage |
| `database.py` | SQLite: Messwerte, Meldungen, offene Regel-Läufe |
| `battery_assessment.py`, `battery_ml.py`, `grid_quality.py` | Auswertungen |
| `automation/` | Regeln, Engine, Treiber je Schnittstelle, E-Mail, NUT-Server, OPC-UA-Zertifikate |
| `solar/` | PV-Wechselrichter: MQTT-Anbindung, Discovery, Abfragen |
| `i18n/`, `help/` | Übersetzungen und Hilfeseiten (`de`, `en`) |
| `templates/`, `static/` | Oberfläche |
| `deploy/` | systemd-Dienst, Update-Werkzeug, polkit-Regel für den Selbst-Shutdown, Übernahme aus dem früheren USV-Dashboard |
| `scripts/` | Prüfung und Bau einer Version |
| `docs/` | [Signierte Updates](docs/updates.md), Konzept zu Regeln und Zielrechnern, Nachweise in `docs/compliance/` |

## Installation

Getestet mit Python 3.13 auf Ubuntu 25.04 (Raspberry Pi CM4, 64 Bit) und Python 3.10 auf Ubuntu 22.04 (Raspberry Pi 3B+, 32 Bit).

**32-Bit-ARM:** Einige Pakete gibt es dort nicht fertig. Sie werden beim Installieren auf dem Gerät kompiliert, was auf einem Pi 3B+ über eine Stunde dauert und `build-essential` und `python3-dev` braucht.
`scikit-learn` entfällt dort, die Batterie-Prognose nutzt nur die Heuristik.

Als root:

```bash
apt install python3-venv openssl snmp nut-client ipmitool
useradd --system --home /opt/usv-monitor --shell /usr/sbin/nologin usv-monitor
mkdir -p /opt/usv-monitor /etc/usv-monitor
# Programmdateien nach /opt/usv-monitor kopieren, dann:
chown -R usv-monitor:usv-monitor /opt/usv-monitor
chown root:usv-monitor /etc/usv-monitor && chmod 770 /etc/usv-monitor
sudo -u usv-monitor python3 -m venv /opt/usv-monitor/.venv
sudo -u usv-monitor /opt/usv-monitor/.venv/bin/pip install --require-hashes -r /opt/usv-monitor/requirements.lock
```

Umgebungsvariablen nach dem Muster von [`.env.example`](.env.example) in `/etc/usv-monitor/env` eintragen. Dann den Dienst starten:

```bash
cp /opt/usv-monitor/deploy/usv-monitor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now usv-monitor
```

Beim ersten Start entsteht das Konto `admin`. Sein Einmal-Passwort steht in `/etc/usv-monitor/initial-admin-password.txt` und
muss beim ersten Login geändert werden; danach verschwindet die Datei.

Das Dashboard läuft per HTTPS auf Port 5443, Port 5000 leitet dorthin um. Ohne eigenes Zertifikat (`USV_TLS_CERT`, `USV_TLS_KEY`)
entsteht ein selbstsigniertes; sein SHA-256-Fingerprint steht im Log, und der Browser fragt beim ersten Aufruf nach. Die Rollen sind `admin`, `operator`, `user` und `guest`.

Ist Port 5000 belegt, den Umleitungsport in einem Drop-in ändern (`systemctl edit usv-monitor`), das Update überschreibt es nicht:

```ini
[Service]
ExecStart=
ExecStart=/opt/usv-monitor/.venv/bin/python /opt/usv-monitor/app.py --port 5080
```

## Updates

Updates kommen als signiertes Paket und werden als root eingespielt: `usv-monitor-update usv-monitor-<version>.tar.gz`.
Pakete ohne gültige Signatur werden abgelehnt, nicht mehr geführte Dateien entfernt, Einstellungen und Daten bleiben erhalten.
Einrichtung, Schlüssel und Ablauf: [`docs/updates.md`](docs/updates.md).

## Konfiguration

Vieles lässt sich in der Oberfläche einstellen. Gespeichert wird im Programmordner:

| Datei | Inhalt |
|---|---|
| `config.json` | USV-Profile, Standorte, Testmodus; entsteht beim ersten Start |
| `hosts.yaml` | Zielrechner (Beispiel: [`hosts.yaml.example`](hosts.yaml.example)) |
| `policies.yaml` | Regeln (Beispiel: [`policies.yaml.example`](policies.yaml.example)) |
| `notifications.yaml` | E-Mail-Einstellungen |
| `services.yaml` | NUT-Server, Selbst-Shutdown |
| `solar.yaml` | PV-Anbindung: MQTT-Broker, Zugangsdaten, Zuordnung der Sensoren |
| `data/` | SQLite-Datenbank, Sicherungen |

**Messwert-Puffer:** Um die SD-Karte zu schonen, sammelt der Dienst Messwerte im RAM und schreibt sie alle 15 Minuten gemeinsam (`USV_DB_FLUSH_SECONDS`, 0 = sofort).
Sofort geschrieben wird im Batteriebetrieb, bei jedem Ereignis und beim Beenden des Dienstes; Ereignisse und das Sicherheitsprotokoll werden nie gepuffert. Von Hand: Benutzermenü, „Messwerte jetzt speichern“, oder `POST /api/database/sync` (ab Rolle `operator`).
Die Oberfläche zeigt auch gepufferte Werte. Fällt der Strom ohne USV-Schutz aus oder stürzt das Gerät ab, gehen höchstens die Messwerte seit dem letzten Schreiben verloren.

**Aufbewahrung:** Messwerte bleiben 24 Monate in voller Auflösung (`USV_DB_RETENTION_MONTHS`, 0 = nie verdichten), danach als Stundenwerte. Ein Lauf einmal täglich; eine Stunde mit Batteriebetrieb bleibt als solche erkennbar. Ereignisse und das Sicherheitsprotokoll werden nie gelöscht. Die Datei schrumpft dadurch nicht, der frei werdende Platz wird wiederverwendet.

Passwörter und Schlüssel liegen nur verschlüsselt in diesen Dateien. Der Schlüssel dazu steht in `USV_SECRET_KEY_FILE`, die
OPC-UA-Zertifikate liegen in `USV_PKI_DIR`. Diese Dateien gehören in jede Sicherung; ohne den Schlüssel müssen alle
Zugangsdaten neu eingegeben werden.

**Dell UPS Rack 1920W HV:** Die Standard-OID liefert keine brauchbare Temperatur. Im Profil steht deshalb
`custom_temp_oid`: `1.3.6.1.4.1.674.10902.2.120.7.3.5.0`.

## Umstieg vom früheren USV-Dashboard

Evolved PowerNexus ersetzt das frühere USV-Dashboard (`ups_monitor`, Flask). Nach der Installation die USV als Profil anlegen,
die PV-Anbindung im Reiter „PV“ einrichten und die alten Daten übernehmen, mit angehaltenem Dienst und als Dienstbenutzer:

```bash
systemctl stop usv-monitor
sudo -u usv-monitor /opt/usv-monitor/.venv/bin/python /opt/usv-monitor/deploy/migrate_legacy_db.py \n    --source /pfad/zum/alten/ups_history.db --profile <USV-Profil> --dry-run
# passt die Vorschau, ohne --dry-run wiederholen
systemctl start usv-monitor
```

Übernommen werden Verlauf (Laufzeit in Minuten, NUT-Status als Klartext), PV-Werte und Meldungen. Benutzer werden neu angelegt.
Das Skript sichert die Datenbank vorher nach `data/backups/` und überspringt bereits übernommene Zeilen.

## Entwicklung

```bash
pip install -r requirements-dev.txt
pytest
python scripts/release_check.py     # Schwachstellen und SBOM
python scripts/build_release.py --key <privat> --pub <öffentlich>     # signiertes Paket, siehe docs/updates.md
```

Die Testwerkzeuge gehören nur in die Entwicklungsumgebung: Auf dem Gerät sind sie nicht installiert und die Tests nicht vorhanden.

Nach einer Änderung an `requirements.txt` die feste Liste neu erzeugen:
`uv pip compile requirements.txt --universal --python-version 3.10 --generate-hashes -o requirements.lock`.

Jede Änderung an der Plattform bekommt eine neue Version in `version.py` (SemVer), einen Eintrag im `CHANGELOG.md` und ein
Git-Tag. Texte stehen in beiden Katalogen unter `i18n/`; ein Test meldet fehlende Einträge.

## Bekannte Grenzen

- Eine Schlüsselrotation für `secret.key` und einen zweiten Anmeldefaktor gibt es derzeit nicht. Siehe [`SECURITY.md`](SECURITY.md) und [`docs/compliance/`](docs/compliance).
- Der eingebaute NUT-Server ist unverschlüsselt und deshalb nur für vertrauenswürdige Netze gedacht.
- Das Protokoll der Regeln unter „Meldungen“ steht auf Deutsch, auch in der englischen Oberfläche.
