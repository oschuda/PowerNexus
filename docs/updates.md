# Signierte Updates

**Für:** Betreiber des Geräts und die Person, die Versionen freigibt.

Evolved PowerNexus spielt Updates nur ein, wenn das Paket eine gültige Ed25519-Signatur trägt. Dafür gibt es ein Schlüsselpaar:

| Schlüssel | Liegt wo | Zweck |
|---|---|---|
| **Privat** | nur beim Freigebenden, verschlüsselt, **nie auf dem Gerät und nie im Repository** | signiert Pakete |
| **Öffentlich** | auf jedem Gerät unter `/etc/usv-monitor-update/keys/` | prüft Signaturen, ist nicht geheim |

Wer den privaten Schlüssel besitzt, kann Software auf alle Geräte bringen, die ihm vertrauen. Ein Gerät braucht ihn nicht: Auch ein vollständig übernommenes Gerät kann dann keine gültigen Pakete erzeugen.

## 1. Schlüsselpaar erzeugen (einmalig, am Signier-Rechner)

In einer Git Bash, in einem Ordner außerhalb des Repositorys, möglichst auf einem verschlüsselten oder Wechseldatenträger:

```bash
umask 077
openssl genpkey -algorithm ed25519 -aes-256-cbc -out usv-update-signing.key
openssl pkey -in usv-update-signing.key -pubout -out usv-update-2026.pub
openssl pkey -pubin -in usv-update-2026.pub -outform DER | sha256sum
```

Der erste Befehl fragt eine Passphrase ab. Erscheint in der Git Bash keine Abfrage, `winpty openssl …` verwenden. Passphrase im Passwort-Tresor ablegen, die Schlüsseldatei getrennt davon sichern, und die Prüfsumme aus der letzten Zeile notieren.

Empfohlen ist ein zweites Paar für den Notfall (`usv-update-notfall.key`), das offline verwahrt wird. Sein öffentlicher Teil kommt mit auf jedes Gerät. Geht der Hauptschlüssel verloren, lassen sich die Geräte dann ohne Zugang vor Ort weiter aktualisieren.

## 2. Öffentlichen Schlüssel auf dem Gerät hinterlegen

Datei auf das Gerät kopieren:

```bash
scp usv-update-2026.pub kiroot@<gerät>:/tmp/
```

Auf dem Gerät als root:

```bash
install -d -o root -g root -m 755 /etc/usv-monitor-update /etc/usv-monitor-update/keys
install -o root -g root -m 644 /tmp/usv-update-2026.pub /etc/usv-monitor-update/keys/
rm /tmp/usv-update-2026.pub
openssl pkey -pubin -in /etc/usv-monitor-update/keys/usv-update-2026.pub -outform DER | sha256sum
```

**Die Prüfsumme muss mit der aus Schritt 1 übereinstimmen.** Das ist der Schutz dagegen, dass beim Kopieren ein fremder Schlüssel untergeschoben wird.

Der Ordner liegt bewusst nicht in `/etc/usv-monitor`: Dort darf der Dienstbenutzer schreiben. Das Update-Werkzeug verweigert die Arbeit, wenn der Schlüssel oder ein Ordner darüber nicht root gehört oder für andere schreibbar ist.

## 3. Update-Werkzeug installieren (einmalig)

Pakete ab 22.15.1 enthalten das Werkzeug und halten es danach selbst aktuell. Beim ersten Mal wird es von Hand aus einem Paket geholt, dessen Signatur vorher von Hand geprüft wird. Als root im Ordner mit Paket und Signatur:

```bash
K=/etc/usv-monitor-update/keys/usv-update-2026.pub
P=usv-monitor-22.15.1.tar.gz
openssl pkeyutl -verify -pubin -inkey $K -rawin -in $P -sigfile $P.sig
```

Nur bei `Signature Verified Successfully` weitermachen:

```bash
apt install openssl python3
tar -xzOf $P deploy/usv_monitor_update.py > /tmp/usv-monitor-update
install -o root -g root -m 755 /tmp/usv-monitor-update /usr/local/sbin/usv-monitor-update
rm /tmp/usv-monitor-update
```

## 4. Version bauen und signieren

Am Signier-Rechner, im Repository:

```bash
python scripts/release_check.py        # Schwachstellen und SBOM; Ergebnis mit committen
git tag v22.15.1                       # auf dem Commit mit Versionsnummer und CHANGELOG
python scripts/build_release.py --key /pfad/usv-update-signing.key --pub /pfad/usv-update-2026.pub
```

Das Skript bricht ab, wenn das Arbeitsverzeichnis nicht sauber ist, das Tag oder der CHANGELOG-Eintrag fehlt, ein Test scheitert oder das Paket einen privaten Schlüssel, Testcode oder Windows-Zeilenenden enthielte. Es entstehen `dist/usv-monitor-<version>.tar.gz` und `.tar.gz.sig`. Bei gleichem Commit ist das Archiv Byte für Byte gleich.

Nicht im Paket sind `tests/`, `scripts/`, `docs/compliance/`, `data/`, `pytest.ini`, `requirements-dev.txt`, `.gitignore` und `.gitattributes`.

## 5. Update einspielen

Beide Dateien in denselben Ordner auf das Gerät kopieren, dann als root:

```bash
usv-monitor-update usv-monitor-22.15.1.tar.gz --dry-run
usv-monitor-update usv-monitor-22.15.1.tar.gz
```

`--dry-run` prüft alles und listet, was neu, geändert und entfernt würde. Das erste signierte Update zeigt auch Altlasten früherer Installationen. **Die Liste „entfernt“ vor dem ersten echten Lauf auf eigene Dateien durchsehen.**

Das Update unterbricht die Überwachung für etwa eine Minute. Offene Wartezeiten laufen danach weiter, trotzdem nicht während eines Stromausfalls einspielen. Fehlen neue Pakete auf dem Gerät, lädt es sie von PyPI und prüft sie gegen die Prüfsummen im signierten `requirements.lock`; dafür ist Internetzugang nötig.

Auf 32-Bit-ARM werden geänderte Pakete auf dem Gerät kompiliert, bei `numpy` auf einem Pi 3B+ über eine Stunde. Der Dienst läuft währenddessen weiter und stoppt erst danach. Die Prüfsummen im `requirements.lock` sichern dabei die Quellpakete, nicht die Bauwerkzeuge, die `pip` zum Kompilieren von PyPI holt.

| Exit-Code | Bedeutung |
|---|---|
| 0 | eingespielt, neue Version antwortet |
| 2 | abgelehnt, nichts geändert: Signatur fehlt oder ist falsch, Inhalt passt nicht zum Manifest, Version nicht neuer |
| 3 | fehlgeschlagen, alter Stand wiederhergestellt |
| 4 | Zurückrollen fehlgeschlagen, von Hand prüfen |

### Was das Update ändert

- Dateien aus dem Manifest werden ersetzt oder neu angelegt.
- Dateien, die nicht im Manifest stehen, wandern nach `/opt/usv-monitor/.update-backups/`. `__pycache__` und `.cache` werden gelöscht. Die letzten drei Sicherungen bleiben.
- Pakete in der venv, die nicht in `requirements.lock` stehen, werden deinstalliert, darunter `pytest` und `aiosmtpd`.
- `deploy/usv-monitor.service` wird übernommen, wenn es sich unterscheidet. Eigene Anpassungen gehören deshalb in ein Drop-in (`systemctl edit usv-monitor`).

**Nicht angefasst werden** `.venv` (außer den Paketen), `data/`, `models/`, `pki/`, `tls/`, `config.json`, `hosts.yaml`, `policies.yaml`, `notifications.yaml`, `services.yaml`, `solar.yaml`, `secret.key`, `users.json` und `.env`, jeweils mit ihren Sicherungskopien, sowie alles unter `/etc/usv-monitor`.

Der geschützte Versionsstand und das Protokoll aller Läufe, auch der abgelehnten, liegen in `/var/lib/usv-monitor-update/` (`state.json`, `history.jsonl`) und im Systemprotokoll: `journalctl -t usv-monitor-update`. Im Sicherheitsprotokoll der Oberfläche erscheinen sie nicht.

Eine ältere Version lässt sich nur mit `--allow-downgrade` einspielen. Das schützt davor, dass eine alte, korrekt signierte Version mit bekannter Lücke erneut eingespielt wird.

## Schlüssel wechseln

1. Neues Paar erzeugen und den öffentlichen Teil auf allen Geräten zusätzlich ablegen (Schritt 2). Es gelten alle `*.pub` im Ordner.
2. Ab jetzt mit dem neuen Schlüssel signieren.
3. Den alten öffentlichen Schlüssel auf allen Geräten löschen.

Pakete können Schlüssel absichtlich nicht ändern. **Ist der private Schlüssel kompromittiert,** den alten öffentlichen Schlüssel sofort auf allen Geräten löschen, den Vorfall nach `SECURITY.md` behandeln und bereits eingespielte Pakete anhand ihrer Prüfsummen prüfen.

## Betriebssystem-Upgrade

Ein Upgrade auf eine neue Python-Version macht `.venv` unbrauchbar, und das Update-Werkzeug baut sie nicht neu auf. Deshalb von Hand:

1. Die aktuelle Version vorab mit der neuen Python-Version testen (`pytest`).
2. Die SD-Karte als Image sichern. Das ist der einzige vollständige Rückweg.
3. Das Upgrade ausführen, per SSH in `tmux`. Ein 32-Bit-System bleibt dabei 32-Bit.
4. Die Umgebung neu bauen. Ohne fertige Pakete dauert das auf einem Pi 3B+ über eine Stunde, solange läuft keine USV-Überwachung:

```bash
systemctl stop usv-monitor
sudo -u usv-monitor python3 -m venv --clear /opt/usv-monitor/.venv
sudo -u usv-monitor /opt/usv-monitor/.venv/bin/pip install --no-cache-dir --require-hashes -r /opt/usv-monitor/requirements.lock
systemctl start usv-monitor
```

Einstellungen und Daten bleiben erhalten. Den Stand danach mit `curl -k https://127.0.0.1:5443/api/version` prüfen.

## Grenzen

- Die Signatur sichert den Weg bis zum Einspielen. Im Betrieb kann der Dienstbenutzer seine Programmdateien weiterhin ändern; eine laufende Prüfung gibt es nicht.
- Pakete werden nicht verschlüsselt. Sie enthalten keine Zugangsdaten.
- Das Gerät sucht nicht selbst nach Updates und benachrichtigt nicht über neue Versionen.
- Das Betriebssystem bleibt außen vor (`apt`).
