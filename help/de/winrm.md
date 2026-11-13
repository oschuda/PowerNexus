# PowerShell Remoting / WinRM (Windows)

Verbindung über **HTTPS, Port 5986** mit Zertifikatsprüfung. Ausgeführt wird:

| Aktion | Befehl |
|---|---|
| Herunterfahren | `shutdown.exe /s /f /t 10 /c "Evolved PowerNexus"` |
| Neu starten | `shutdown.exe /r /f /t 10 /c "Evolved PowerNexus"` |

`shutdown.exe` kehrt sofort zurück; Windows fährt nach 10 Sekunden herunter. Angemeldete Benutzer
sehen den Hinweis „Evolved PowerNexus“. Abbrechen am Rechner: `shutdown /a`.

## Einrichtung am Windows-Rechner (PowerShell als Administrator)

### 1. Zertifikat

Benötigt wird ein Zertifikat für **Serverauthentifizierung**, ausgestellt auf den Namen, unter dem
Evolved PowerNexus den Rechner anspricht (FQDN oder IP im Feld *Antragstellername/SAN*), von einer CA,
der der USV-Server vertraut. Thumbprint ermitteln:

```powershell
Get-ChildItem Cert:\LocalMachine\My | Format-List Subject, DnsNameList, Thumbprint, NotAfter
```

### 2. HTTPS-Listener und Firewall

```powershell
New-Item -Path WSMan:\localhost\Listener -Transport HTTPS -Address * `
    -CertificateThumbPrint <THUMBPRINT> -Force
New-NetFirewallRule -DisplayName "WinRM HTTPS (Evolved PowerNexus)" -Direction Inbound `
    -Protocol TCP -LocalPort 5986 -Action Allow
Enable-PSRemoting -SkipNetworkProfileCheck -Force
```

Prüfen:

```powershell
winrm enumerate winrm/config/listener
```

### 3. Konto

Ein eigenes Konto, Mitglied der lokalen Administratoren (oder Domänenkonto mit entsprechendem Recht).

- **Domäne:** Benutzer als `DOMÄNE\usvmon`, Anmeldeverfahren *ntlm* oder *kerberos*.
- **Lokales Konto (Arbeitsgruppe):** Remote-Verwaltung mit lokalen Konten zulassen:

```powershell
New-ItemProperty -Path HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System `
    -Name LocalAccountTokenFilterPolicy -Value 1 -PropertyType DWord -Force
```

## Einrichtung auf dem USV-Server (interne CA)

Stammt das Zertifikat von einer internen CA, deren Stammzertifikat auf dem USV-Server hinterlegen:

```bash
sudo cp interne-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

## Einrichtung in Evolved PowerNexus

| Feld | Wert |
|---|---|
| Schnittstelle | PowerShell Remoting (WinRM) |
| Adresse | Name/IP **wie im Zertifikat** |
| Port | 5986 |
| Benutzer / Passwort | siehe Schritt 3 |
| Anmeldeverfahren | ntlm (Standard) oder kerberos |

## Fehlerbilder

| Meldung | Ursache | Maßnahme |
|---|---|---|
| `SSLError … certificate verify failed` | CA unbekannt oder Name passt nicht zum Zertifikat | interne CA auf dem USV-Server hinterlegen; Adresse wie im Zertifikat |
| `InvalidCredentialsError` / 401 | Benutzer/Passwort falsch, lokales Konto ohne Schritt 3 | Anmeldung prüfen, LocalAccountTokenFilterPolicy |
| Zeitüberschreitung | Firewall, Listener fehlt | Schritt 2, `Test-NetConnection <adresse> -Port 5986` |
| Benötigtes Paket fehlt | `pywinrm` nicht installiert | Administrator: Paket in Evolved PowerNexus nachinstallieren |
