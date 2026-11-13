# PowerShell Remoting / WinRM (Windows)

Connection via **HTTPS, port 5986** with certificate validation. Executed commands:

| Action | Command |
|---|---|
| Shut down | `shutdown.exe /s /f /t 10 /c "Evolved PowerNexus"` |
| Reboot | `shutdown.exe /r /f /t 10 /c "Evolved PowerNexus"` |

`shutdown.exe` returns immediately; Windows shuts down after 10 seconds. Logged-on users see the
notice "Evolved PowerNexus". To abort on the computer: `shutdown /a`.

## Setup on the Windows computer (PowerShell as administrator)

### 1. Certificate

A certificate for **server authentication** is required, issued to the name Evolved PowerNexus uses for
the computer (FQDN or IP in *subject/SAN*), by a CA the UPS server trusts. Find the thumbprint:

```powershell
Get-ChildItem Cert:\LocalMachine\My | Format-List Subject, DnsNameList, Thumbprint, NotAfter
```

### 2. HTTPS listener and firewall

```powershell
New-Item -Path WSMan:\localhost\Listener -Transport HTTPS -Address * `
    -CertificateThumbPrint <THUMBPRINT> -Force
New-NetFirewallRule -DisplayName "WinRM HTTPS (Evolved PowerNexus)" -Direction Inbound `
    -Protocol TCP -LocalPort 5986 -Action Allow
Enable-PSRemoting -SkipNetworkProfileCheck -Force
```

Check:

```powershell
winrm enumerate winrm/config/listener
```

### 3. Account

A dedicated account, member of the local administrators (or a domain account with equivalent rights).

- **Domain:** user as `DOMAIN\usvmon`, authentication *ntlm* or *kerberos*.
- **Local account (workgroup):** allow remote administration with local accounts:

```powershell
New-ItemProperty -Path HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System `
    -Name LocalAccountTokenFilterPolicy -Value 1 -PropertyType DWord -Force
```

## Setup on the UPS server (internal CA)

If the certificate was issued by an internal CA, install its root certificate on the UPS server:

```bash
sudo cp internal-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

## Setup in Evolved PowerNexus

| Field | Value |
|---|---|
| Interface | PowerShell Remoting (WinRM) |
| Address | name/IP **as in the certificate** |
| Port | 5986 |
| User / password | see step 3 |
| Authentication | ntlm (default) or kerberos |

## Troubleshooting

| Message | Cause | Action |
|---|---|---|
| `SSLError … certificate verify failed` | unknown CA or name does not match the certificate | install the internal CA on the UPS server; use the address from the certificate |
| `InvalidCredentialsError` / 401 | wrong user/password, local account without step 3 | check login, LocalAccountTokenFilterPolicy |
| Timeout | firewall, listener missing | step 2, `Test-NetConnection <address> -Port 5986` |
| Required package missing | `pywinrm` not installed | administrator: install the package for Evolved PowerNexus |
