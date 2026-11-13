# SSH (Ubuntu Server, Arch Linux)

Evolved PowerNexus meldet sich per SSH an und führt aus:

| Aktion | Befehl auf dem Zielrechner |
|---|---|
| Herunterfahren | `sudo -n /usr/bin/systemctl --no-block poweroff` |
| Neu starten | `sudo -n /usr/bin/systemctl --no-block reboot` |

## Einrichtung am Zielrechner

### 1. Benutzer anlegen

Ubuntu Server:

```bash
sudo adduser --disabled-password --gecos "Evolved PowerNexus" usvmon
```

Arch Linux (`sudo` ist nicht vorinstalliert):

```bash
pacman -S sudo
useradd -m -c "Evolved PowerNexus" usvmon
```

### 2. Anmeldung per Schlüssel (empfohlen) oder Passwort

Schlüssel: Den öffentlichen Schlüssel von Evolved PowerNexus in
`/home/usvmon/.ssh/authorized_keys` eintragen (Rechte: Ordner 700, Datei 600) und im Zielrechner-Dialog
den Pfad zum privaten Schlüssel auf dem USV-Server angeben.

Passwort: `sudo passwd usvmon` und das Passwort im Zielrechner-Dialog eintragen.

### 3. Recht zum Herunterfahren – nur für diese beiden Befehle

```bash
sudo visudo -f /etc/sudoers.d/usv-monitor
```

Inhalt (genau so, `sudo` vergleicht die Argumente exakt):

```
usvmon ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block poweroff, /usr/bin/systemctl --no-block reboot
```

Prüfen:

```bash
sudo -l -U usvmon
```

### 4. Host-Fingerprint ermitteln

Auf dem Zielrechner:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Beim **Verbindung testen** zeigt Evolved PowerNexus den Fingerprint, den der Rechner meldet. Stimmt er
mit diesem Wert überein, **übernehmen**. Aktionen werden nur auf Rechnern mit bestätigtem Fingerprint
ausgeführt.

## Einrichtung in Evolved PowerNexus

| Feld | Wert |
|---|---|
| Schnittstelle | SSH |
| Adresse / Port | IP oder Name, Port 22 |
| Benutzer | `usvmon` |
| Passwort oder Schlüsselpfad | siehe Schritt 2 |
| sudo verwenden | an (bei Anmeldung als root aus) |

## Fehlerbilder

| Meldung | Ursache | Maßnahme |
|---|---|---|
| sudo-Recht für den Shutdown fehlt | sudoers-Eintrag fehlt oder weicht ab | Schritt 3, Zeile exakt übernehmen |
| Host-Fingerprint stimmt NICHT überein | Rechner neu installiert – oder fremder Rechner unter der Adresse | Fingerprint am Rechner prüfen, erst dann neu übernehmen |
| Zeitüberschreitung | Rechner aus, Firewall, falsche Adresse | `ssh usvmon@<adresse>` vom USV-Server testen |
| Permission denied | falsches Passwort oder Schlüssel | Anmeldung manuell testen |
