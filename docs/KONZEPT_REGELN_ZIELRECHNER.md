# Konzept: Regeln, Zielrechner, Benachrichtigung und Mehrsprachigkeit

**Stand:** 19.10.2026 · **Bezug:** Evolved PowerNexus v22.3.0 · **Status:** Entwurf 3 – offene Fragen beantwortet, zur Freigabe

Ziel: Bei einem Ereignis an einer USV (z. B. Stromausfall, Akku schwach) werden definierte Zielrechner
zuverlässig, nachvollziehbar und abbrechbar heruntergefahren – über die jeweils passende Schnittstelle
(SSH, PowerShell Remoting, Redfish, IPMI, OPC-UA, HTTP-API). Jede Auslösung kann per E-Mail gemeldet
werden. Alles lässt sich gefahrlos im Testmodus durchspielen. Oberfläche, Meldungen und Hilfe sind
mehrsprachig.

**Änderungen gegenüber Entwurf 1:** Antworten zu Servern (iDRAC, iLO, Wortmann TERRA), OPC-UA
verschlüsselt und unverschlüsselt inkl. Zertifikatsspezifikation, NUT-Server und Selbst-Shutdown
optional, E-Mail über Microsoft 365, Testmodus, Hilfe und Mehrsprachigkeit ergänzt (Kap. 6–11).

---

## 1. Ausgangslage (Befunde v22.3.0)

| Bereich | Befund | Auswirkung |
|---|---|---|
| Regel-Dialog | „Speichern“/„Löschen“ zeigen nur einen Hinweistext | Regeln können nicht angelegt werden |
| Regel-Dialog | Keine Auswahl von Zielrechnern, USV/Gruppe, Abbruchbedingung, Schwellwerten | Auch eine gespeicherte Regel wüsste nicht, was sie tun soll |
| API → Policy-Engine | `add_policy`, `update_policy`, `delete_policy`, `save_policies` existieren nicht | Jede Regeländerung über die API: Fehler 500 |
| Ausführung | `_execute_on_single_host` doppelt definiert; die gültige Fassung ignoriert den Testmodus | `UPS_DRY_RUN=true` führt **echte** Shutdowns aus |
| Zugangsdaten | Windows/Linux lesen Passwörter nur aus Umgebungsvariablen | Im Dashboard eingegebene Passwörter werden nie verwendet |
| Zugangsdaten | `ENCRYPTION_KEY` auf dem Pi nicht gesetzt; zwei parallele Krypto-Module | Zielrechner mit Passwort anlegen schlägt fehl; Schlüsselrotation geht beim Neustart verloren |
| PowerShell Remoting | `pywinrm` nicht installiert; `Stop-Computer -Delay` existiert nicht | Windows-Aktionen schlagen fehl |
| IPMI | `ipmitool` fehlt; Typ im Dialog nicht wählbar | IPMI nicht nutzbar |
| Neue Typen | Redfish, OPC-UA und HTTP nicht vorhanden | – |
| Laufende Aktionen | Verzögerte Aktionen leben nur im Speicher | Neustart während der Wartezeit verwirft sie stillschweigend |
| E-Mail | Nur SMTP mit Benutzer/Passwort; nicht an Meldungen angebunden | Keine Benachrichtigung bei Auslösung |
| Sprache | Alle Texte fest auf Deutsch in HTML, JavaScript und Python | Keine Mehrsprachigkeit |
| Tests | 4 von 5 Policy-Tests veraltet | Keine Absicherung der Engine |

## 2. Ziele und Nicht-Ziele

**Ziele**
- Regeln vollständig in der Oberfläche anlegen, ändern, testen, aktivieren/deaktivieren, löschen.
- Zielrechner mit typabhängigen Feldern; Verbindung testen, ohne etwas auszulösen.
- Testmodus, der nachweislich nichts ausführt, global und je Regel.
- E-Mail-Benachrichtigung, Standard: Microsoft 365.
- Ausführliche Hilfe zu jeder Schnittstelle (Voraussetzungen am Zielsystem, Einrichtung, Fehlerbilder).
- Mehrsprachige Oberfläche, Meldungen, E-Mails und Hilfe in Deutsch und Englisch.

**Nicht-Ziele (vorerst)**
- Wiedereinschalten/Wake-on-LAN nach Netzrückkehr (Code teilweise vorhanden, eigener Schritt).
- Hypervisor-Gäste einzeln steuern (bestehende Hypervisor-Aktionen bleiben).
- Statistische/ML-Auslöser.

## 3. Schnittstellen zur Rechnersteuerung

| Schnittstelle | Ebene | Ziele | Herunterfahren | Einsatz |
|---|---|---|---|---|
| **SSH** | Betriebssystem | Linux, macOS, NAS, ESXi | `shutdown -h` (sudo) | Standard für Linux |
| **PowerShell Remoting (WinRM)** | Betriebssystem | Windows | `Stop-Computer -Force` | Standard für Windows; HTTPS 5986, gültiges Zertifikat |
| **Redfish** (DMTF, REST/JSON über HTTPS) | Out-of-Band (BMC) | Dell **iDRAC** 8/9, HPE **iLO** 4/5/6, Lenovo XClarity, Supermicro, ASRock Rack, Fujitsu iRMC | `ComputerSystem.Reset` `GracefulShutdown`, Fallback `ForceOff` | **Standard für Server mit BMC** |
| **IPMI over LAN** | Out-of-Band (BMC) | ältere Server, BMCs ohne Redfish | `chassis power soft` / `off` | Fallback |
| **OPC-UA** | Industrie | SPS (z. B. Bosch Rexroth XM42), Industrie-PCs | Variable schreiben | unverschlüsselt oder verschlüsselt |
| **HTTP-API allgemein** | Anwendung | NAS (Synology, QNAP, TrueNAS), Proxmox, vCenter, Home Assistant, Node-RED | konfigurierbarer Aufruf | Auffangtyp |
| Hypervisor-APIs | Virtualisierung | Proxmox VE, VMware | bestehender Code | wird geprüft und eingebunden |
| **NUT-Client (upsmon)** | Agent am Ziel | Rechner mit NUT-Client | Ziel fährt sich bei „FSD“ selbst herunter | **optional** (Kap. 7.4) |

**Server mit iDRAC/iLO:** Redfish. Benötigt je Server ein BMC-Konto mit Recht zur Energiesteuerung
(iDRAC: „Login“ + „System Control“ bzw. Rolle *Operator*; iLO: „Virtual Power and Reset“).

**Wortmann TERRA Server:** TERRA-Server nutzen je nach Modell Mainboards mit ASPEED-BMC
(häufig Supermicro, ASRock Rack oder Intel). Diese bieten IPMI, mit aktueller BMC-Firmware meist auch
Redfish. Ob Redfish verfügbar ist, zeigt ein Aufruf von `https://<BMC-Adresse>/redfish/v1/` im Browser.
Der Zielrechner-Dialog prüft das beim „Verbindung testen“ automatisch und schlägt bei fehlendem Redfish
IPMI vor.

Die vorhandene BMC-Firmware `egs-2-42-2-0000` deutet auf die Intel-Serverplattform „Eagle Stream“ (EGS)
mit Intel-BMC-Firmware hin.

**[verifiziert 19.10.2026]** BMC `10.162.82.21`: OpenBMC (Zertifikat `O=OpenBMC, CN=bmc-maca4bf01974e45`,
MAC-Präfix a4:bf:01 = Intel), **Redfish 1.9.0** erreichbar, `/redfish/v1/Systems` verlangt Anmeldung;
IPMI over LAN (UDP 623) antwortet nicht. → TERRA-Server werden per **Redfish** gesteuert; dieser BMC
dient als Referenz für 22.5.0. `10.162.82.18` ist ein Windows-Rechner (nur RDP 3389 offen; WinRM 5986
noch nicht eingerichtet).

**Betriebssysteme der Zielrechner (festgelegt):**

| System | Schnittstelle | Befehl | Einrichtung am Ziel (Hilfe) |
|---|---|---|---|
| Ubuntu Server | SSH | `sudo systemctl poweroff` | eigener Benutzer, `sudoers`: `usvmon ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff, /usr/bin/systemctl reboot` |
| Arch Linux | SSH | `sudo systemctl poweroff` | `sudo` ist nicht vorinstalliert (`pacman -S sudo`), sonst wie Ubuntu |
| Windows | PowerShell Remoting (WinRM, HTTPS 5986) | `Stop-Computer -Force` | HTTPS-Listener mit gültigem Zertifikat, Firewall 5986, Konto mit Remote-Shutdown-Recht |
| Server mit iDRAC, iLO, TERRA-BMC | Redfish, Fallback IPMI | `GracefulShutdown` | BMC-Konto mit Energiesteuerungsrecht |

## 4. Datenmodell

### 4.1 Zielrechner (`hosts.yaml`)

Gemeinsame Felder: `name` (eindeutig), `type`, `address`, `description`, `enabled`, `timeout_seconds`.
Zugangsdaten werden nie im Klartext gespeichert und nie über die API ausgeliefert (nur `has_password: true`).

| Typ | Zusätzliche Felder | Aktionen |
|---|---|---|
| `ssh` | `port` (22), `username`, Passwort *oder* Schlüssel, Host-Fingerprint, `sudo` | shutdown, reboot, notify |
| `winrm` | `port` (5986), `username` (`DOMÄNE\Benutzer` oder lokal), Passwort, `transport` (ntlm/kerberos), Zertifikatsprüfung an | shutdown, reboot, hibernate, notify, lock_screen |
| `redfish` | `port` (443), `username`, Passwort, `system_id` (automatisch), Zertifikatsprüfung (an; aus nur mit hinterlegtem Fingerprint) | shutdown, force_off, power_on, reboot |
| `ipmi` | `username`, Passwort, `interface` (lanplus), `privilege` (OPERATOR) | shutdown (soft), force_off, power_on |
| `opcua` | siehe Kap. 5 | shutdown (= Variable schreiben) |
| `http` | `method`, `url`, Header (z. B. Token), Body, `expected_status`, Zertifikatsprüfung | shutdown (= konfigurierter Aufruf) |
| `hypervisor` | wie bestehend (`proxmox`/`vmware`) | bestehende Aktionen |
| `local` | – (der Pi selbst) | shutdown, nur als letzter Schritt; **optional** (Kap. 7.5) |

### 4.2 Regel (`policies.yaml`)

```yaml
- name: "Lage: Server bei Stromausfall herunterfahren"
  enabled: true
  dry_run: false                       # Testmodus nur für diese Regel
  trigger:
    events: [POWER_FAILURE]
    scope: { type: group, id: "Lage" } # all | ups (Profil) | group
  conditions:                          # UND-verknüpft, optional
    battery_charge_lt: 60
    battery_runtime_lt: 15             # Minuten
  wait_seconds: 120
  cancel_on: [POWER_RESTORED]
  steps:                               # nacheinander; innerhalb eines Schritts parallel
    - targets: [app-server-1, app-server-2]
      action: shutdown
      timeout_seconds: 180
      continue_on_error: true
    - targets: [db-server-1]
      action: shutdown
      delay_seconds: 60
  notify: [it-team]                    # Empfängerliste(n), Kap. 8
  repeat_guard_minutes: 30
```

Regeln im alten Format (`trigger_event`, `actions`, `cascade_phases`) werden beim Laden übernommen.

## 5. OPC-UA im Detail

Herunterfahren = **eine Variable schreiben** (`node_id`, Datentyp, Wert). Die Steuerung fährt den
angeschlossenen Rechner bzw. sich selbst daraufhin geordnet herunter.

### 5.1 Felder

| Feld | Werte |
|---|---|
| `endpoint` | `opc.tcp://<adresse>:4840` |
| `security` | `none` (unverschlüsselt) · `sign_and_encrypt` (verschlüsselt) |
| `security_policy` | automatisch aus der Endpoint-Discovery: bevorzugt `Aes256_Sha256_RsaPss`, sonst `Basic256Sha256` |
| Anmeldung | **anonym** oder **Benutzer/Passwort** (beides vorgesehen) |
| `node_id` | z. B. `ns=2;s=Power.ShutdownRequest` |
| `data_type` / `value` | konfigurierbar: **Boolean (Standard)**, Int16, Int32, UInt16, UInt32, Float, String; Standardwert `true` |
| `verify_write` | Wert nach dem Schreiben zurücklesen (Standard: an) |

### 5.2 Verschlüsselte Verbindung (übernommen aus „OPC-UA-Zertifikate für die Bosch Rexroth XM42“, v9.6.36)

Die dort an der Anlage verifizierten Anforderungen gelten auch hier:

**Client-Zertifikat** (von Evolved PowerNexus einmalig erzeugt, je Pi ein eigenes):
- selbstsigniert, RSA 2048, Signatur SHA-256 (PKCS#1 v1.5)
- Seriennummer 16 Byte zufällig (positiv); Gültigkeit jetzt − 1 Tag bis jetzt + 5 Jahre
- BasicConstraints CA:false (kritisch); SubjectKeyIdentifier aus dem Public Key
- **AuthorityKeyIdentifier** (KeyID = eigener SKI, Issuer = eigener Subject, eigene Seriennummer) – Pflicht
- KeyUsage (kritisch): digitalSignature, nonRepudiation, keyEncipherment, dataEncipherment,
  **keyCertSign** – Pflicht, sonst lehnt die XM42 ab
- ExtendedKeyUsage (kritisch): serverAuth, clientAuth
- SubjectAlternativeName: 1. URI = ApplicationUri (zuerst), 2. DNS = Hostname, 3. alle IPv4-Adressen
- ApplicationUri `urn:<Hostname>:USVMonitor:OPCUA` zeichengenau gleich in Konfiguration und SAN;
  ändert sich der Hostname, wird das Zertifikat neu erzeugt (mit Hinweis in der Oberfläche)
- Selbstprüfung beim Erzeugen: entspricht `openssl verify -CAfile c.pem -purpose sslclient c.pem` → OK

**Ablage auf dem Pi:** `/etc/usv-monitor/pki/` mit `own/` (Zertifikat + privater Schlüssel, Rechte 600),
`trusted/` (vertraute Server-Zertifikate), `rejected/` (abgelehnte). Der eigene Code prüft dieselben
Ordner, die die OPC-UA-Bibliothek tatsächlich verwendet (Lehre aus Kap. 5 des PDF).

**Vertrauen in beide Richtungen** (Oberfläche führt Schritt für Schritt):
1. *Client → Steuerung:* Client-Zertifikat herunterladen (DER) und auf der Steuerung vertrauen
   (XM42: Import in IndraWorks, OPC-UA-Server / Zertifikatsverwaltung). Angezeigt wird der
   **SHA-1-Thumbprint**, ausdrücklich so beschriftet.
2. *Steuerung → Client:* Server-Zertifikat beim Test aus der Discovery lesen, Subject, SHA-1, Gültigkeit,
   Signaturalgorithmus und Schlüssellänge anzeigen; nach Abgleich per Klick in `trusted/` übernehmen.
   Automatisches Akzeptieren ist nur im Testbetrieb und nur mit Warnhinweis möglich.

**Fehlerbilder nach Richtung** (Meldung nennt die verantwortliche Seite):

| Statuscode | Bedeutung | Maßnahme in der Oberfläche |
|---|---|---|
| `BadSecurityChecksFailed` (0x80130000) | Steuerung lehnt unser Client-Zertifikat ab | Client-Zertifikat auf der Steuerung vertrauen; Zertifikat entspricht der Spezifikation |
| `BadCertificateUntrusted` (0x801A0000) | **Wir** vertrauen dem Server-Zertifikat nicht – die Steuerung hat uns bereits akzeptiert | Server-Zertifikat prüfen und übernehmen |
| `BadCertificatePolicyCheckFailed` | SHA-1-Signatur oder Schlüssel < 2048 Bit | Zertifikat auf der Seite mit dem Mangel erneuern |
| Port 4840 zu, Ping ok | Verbindungstabelle der Steuerung erschöpft | OPC-UA-Dienst der Steuerung neu starten |

**Schonender Umgang mit der Steuerung:** Verbindung nur bei Aktion und Test aufbauen, danach Session und
Discovery-Client immer schließen; Discovery-Ergebnis 5 Minuten zwischenspeichern; keine Dauerverbindung,
keine parallelen Sessions. Das Server-Zertifikat generischer Steuerungen (z. B. `CN=IndraControl`) enthält
nicht die Anlagen-IP – die Hostnamenprüfung entfällt, die Thumbprint-Bindung bleibt.

**Technische Umsetzung:** Python-Bibliothek `asyncua`. Unterstützung von `Aes256_Sha256_RsaPss` und das
Verhalten mit der XM42 werden vor der Umsetzung an der Anlage verifiziert; `Basic256Sha256` ist der
Fallback.

## 6. Testmodus (Dry Run)

- **Global** (Schalter in der Kopfzeile, nur Admin) und **je Regel**. Bei aktivem Testmodus zeigt die
  Kopfzeile ein deutliches Banner „TESTMODUS – es werden keine Aktionen ausgeführt“.
- Es gibt genau **eine** Ausführungsfunktion; der Testmodus wird dort geprüft, bevor eine Verbindung
  mit Schreib- oder Steuerzugriff entsteht. Der doppelte Code entfällt.
- Im Testmodus läuft alles wie im Ernstfall (Auslösung, Bedingungen, Wartezeit, Abbruch, Schritte,
  E-Mail mit Kennzeichnung „[TEST]“), protokolliert wird je Zielrechner der Befehl, der ausgeführt
  *würde* (z. B. Redfish-URL und Reset-Typ, OPC-UA NodeId und Wert).
- „Regel testen“ und „Verbindung testen“ laufen immer im Testmodus, unabhängig vom globalen Schalter.
- Automatischer Test: Ein Test stellt sicher, dass im Testmodus keine Netzwerkverbindung zu einem
  Zielrechner geöffnet wird, die etwas auslösen könnte.

## 7. Ablauf und Sicherheit

### 7.1 Ablauf
1. Ereignis → passende Regeln anhand Ereignis, Geltungsbereich, Bedingungen.
   Bedingungen werden beim Start der Wartezeit **und** vor der Ausführung erneut geprüft.
2. Wartezeit sichtbar mit Countdown und „Abbrechen“; Abbruch auch durch Gegenereignis und Pause-Schalter.
3. Schritte nacheinander, Zeitlimit je Aufruf, Ergebnis je Zielrechner.
4. Protokoll als Meldung und im Audit-Log; E-Mail gemäß Regel.

### 7.2 Schutzmechanismen
- Pause-Schalter blockiert jede Ausführung (besteht).
- Mehrfachauslösung: höchstens einmal je Regel und USV innerhalb `repeat_guard_minutes`.
- Wiederanlauf: Laufende Wartezeiten werden in der Datenbank vermerkt und nach einem Neustart fortgesetzt,
  wenn das Ereignis weiter besteht.
- Rechte: Regeln, Zielrechner, E-Mail-Einstellungen nur Admin; Testen auch Operator.

### 7.3 Zugangsdaten
- Ein Verschlüsselungsmodul (Fernet), Schlüssel `/etc/usv-monitor/secret.key` (600, usv-monitor),
  beim ersten Start erzeugt; das zweite Krypto-Modul entfällt.
- Schlüsselrotation verschlüsselt alle Geheimnisse neu und speichert den neuen Schlüssel dauerhaft.
- Entschlüsselung erst unmittelbar vor dem Aufruf. Zertifikatsprüfung ist Standard.

### 7.4 NUT-Server (optional, Standard: aus)
Der Pi stellt die USV-Zustände per NUT im Netz bereit; Rechner mit NUT-Client (upsmon) fahren sich bei
niedrigem Akku oder auf Anweisung („FSD“) selbst herunter. Einstellbar: freigegebene USVs, Zugangsdaten
für Clients, erlaubte Netze. Eine Regelaktion „FSD an NUT-Clients senden“ ergänzt die Schritte.

### 7.5 Selbst-Shutdown des Pi (optional, Standard: aus)
Nur wenn in den Einstellungen freigegeben; in Regeln nur als letzter Schritt wählbar.

## 8. E-Mail-Benachrichtigung

**Einstellungen** (Admin): Versandart, Absender, Empfängerlisten (z. B. „it-team“), Sprache je Liste,
Ereignisse/Schweregrade, die immer gemeldet werden (unabhängig von Regeln), Button „Test-E-Mail senden“.

| Versandart | Für | Anmeldung |
|---|---|---|
| **Microsoft 365 – Graph API** *(Standard)* | Exchange Online | App-Registrierung in Entra ID, Berechtigung `Mail.Send` (Anwendung), per Richtlinie auf das Absenderpostfach beschränkt |
| Microsoft 365 – SMTP mit OAuth 2.0 | Exchange Online | `smtp.office365.com:587`, STARTTLS, XOAUTH2 mit App-Registrierung (`SMTP.SendAsApp`) |
| SMTP allgemein | andere Anbieter, interner Relay | Server, Port, STARTTLS/SSL, optional Benutzer/Passwort |

**Eingaben für Microsoft 365** (App-Registrierung ist möglich, Werte sind in der Oberfläche konfigurierbar):

| Feld | Beispiel | Speicherung |
|---|---|---|
| Verzeichnis-ID (Tenant ID) | `00000000-…` | Klartext |
| Anwendungs-ID (Client ID) | `00000000-…` | Klartext |
| Anmeldung | Geheimer Clientschlüssel **oder** Zertifikat (PFX + Passwort) | verschlüsselt (Kap. 7.3), nie über die API ausgeliefert |
| Ablaufdatum des Geheimnisses | aus der App-Registrierung | Warnung als Meldung und E-Mail 30 Tage vorher |
| Absenderpostfach | `powernexus@firma.de` | Klartext |
| Versandweg | Graph API (Standard) oder SMTP mit OAuth | – |

„Test-E-Mail senden“ prüft Token-Abruf und Versand getrennt und nennt bei Fehlern die Ursache
(z. B. fehlende Admin-Zustimmung, abgelaufenes Geheimnis, Postfach nicht freigegeben).

**Hinweis:** Microsoft hat angekündigt, die Standardauthentifizierung (Benutzer/Passwort) für SMTP AUTH
in Exchange Online im Jahr 2026 abzuschalten. Deshalb ist für Microsoft 365 die OAuth-basierte Anmeldung
vorgesehen; der aktuelle Stand im eigenen Tenant ist bei der Einrichtung zu prüfen. Die Hilfe beschreibt
die App-Registrierung Schritt für Schritt.

**Inhalt:** Betreff `[Evolved PowerNexus] <Schweregrad>: <Meldung>` (Testmodus: `[TEST]`), Text mit USV, Standort,
Messwerten, ausgelösten Aktionen und Ergebnis je Zielrechner, Link zum Dashboard. Versand mit
Wiederholung; Fehlschläge erscheinen als Meldung.

## 9. Hilfe

Neuer, mehrsprachiger Hilfebereich mit je einer Seite pro Schnittstelle und für E-Mail, jeweils:
1. Wofür geeignet, Voraussetzungen
2. **Einrichtung am Zielsystem** mit kopierbaren Befehlen, z. B.
   - WinRM: HTTPS-Listener mit Zertifikat, Firewall-Regel 5986, Benutzerrechte
   - SSH: Benutzer, `sudoers`-Eintrag nur für `shutdown`, Host-Fingerprint
   - Redfish: Konto und Rolle bei iDRAC, iLO, Supermicro/ASRock Rack; Prüfung über `/redfish/v1/`
   - IPMI: Benutzer mit Operator-Recht, IPMI over LAN aktivieren
   - OPC-UA: Variable anlegen, Zertifikatsaustausch in beide Richtungen (nach Kap. 5), XM42-Beispiel
   - HTTP: Beispiele (Synology, Proxmox-API-Token)
   - Microsoft 365: App-Registrierung, Berechtigung, Beschränkung auf Absenderpostfach
3. Einrichtung in Evolved PowerNexus (Felder erklärt)
4. **Fehlerbilder → Ursache → Maßnahme**
5. Sicherheitshinweise

Die Hilfeseiten liegen als Markdown je Sprache im Repository (`help/de/…`, `help/en/…`) und werden in der
Oberfläche angezeigt; Feldbeschriftungen verlinken auf den passenden Abschnitt.

## 10. Mehrsprachigkeit

- **Sprachen:** Deutsch und Englisch (festgelegt). Die Struktur erlaubt später weitere Sprachen durch eine zusätzliche Übersetzungsdatei.
- **Texte:** Übersetzungskataloge je Sprache (`i18n/de.json`, `i18n/en.json`) mit Schlüsseln
  (z. B. `events.POWER_FAILURE`). Seiten (Jinja) und JavaScript verwenden dieselben Kataloge.
- **Sprachwahl:** je Benutzer (Benutzerverwaltung), sonst Browsersprache, sonst Deutsch; Umschalter in
  der Kopfzeile.
- **Meldungen und Bewertungen** werden als Code mit Parametern gespeichert (z. B.
  `BATTERY_REPLACE {sku: RBC43}`) und erst bei der Anzeige übersetzt – so erscheinen alte Meldungen
  in der gewählten Sprache. E-Mails in der Sprache der Empfängerliste.
- **Formate:** Datum, Uhrzeit und Zahlen je Sprache (`Intl`/Babel).
- **Umstellung:** Neue und überarbeitete Bereiche (Regeln, Zielrechner, E-Mail, Hilfe, Meldungen)
  werden sofort mehrsprachig gebaut; die übrigen Seiten werden schrittweise umgestellt. Ein Test
  meldet Schlüssel, die in einer Sprache fehlen.

## 11. Speicherung und API

- `hosts.yaml`, `policies.yaml`, `notifications.yaml` (lesbar, sicherbar); Schreiben atomar mit Sicherung.
- Laufzustände (Wartezeiten, Ausführungen) in SQLite.
- API (Admin):
  - Zielrechner: `GET/POST/PUT/DELETE /api/hosts`, `POST /api/hosts/{name}/test`
  - OPC-UA: `GET /api/opcua/client-certificate`, `POST /api/hosts/{name}/trust-server-certificate`
  - Regeln: `GET/POST/PUT/DELETE /api/policies`, `POST /api/policies/{name}/toggle`,
    `POST /api/policies/{name}/test`, `GET /api/policies/runs`, `POST /api/policies/runs/{id}/cancel`
  - Testmodus: `GET/POST /api/config/dry-run`
  - E-Mail: `GET/PUT /api/notifications`, `POST /api/notifications/test`
  - Sprache: `GET /api/i18n/{lang}`

## 12. Abhängigkeiten auf dem Pi

| Zweck | Paket |
|---|---|
| SSH | `asyncssh` (vorhanden) |
| PowerShell Remoting | `pywinrm` (neu) |
| Redfish, HTTP, Graph API | `httpx` (vorhanden) |
| IPMI | `ipmitool` (apt, neu) |
| OPC-UA | `asyncua` (neu) |
| E-Mail SMTP | `aiosmtplib` (vorhanden) |
| Microsoft 365 OAuth | `msal` (neu) |
| NUT-Server (optional) | `nut-server` (apt) |
| Sprache/Formate | `babel` (neu) |

## 13. Tests

- Engine: Auslösung, Geltungsbereich, Bedingungen, Wartezeit, Abbruch, Mehrfachauslösung, Wiederanlauf.
- Je Zieltyp gegen Attrappen (SSH-, WinRM-, Redfish-, IPMI-Simulation, OPC-UA-Testserver unverschlüsselt
  und verschlüsselt); OPC-UA zusätzlich an der XM42.
- Client-Zertifikat: alle Extensions laut Kap. 5.2 und `openssl verify`.
- Testmodus: nachweislich keine auslösende Verbindung.
- E-Mail gegen SMTP-Attrappe; Microsoft 365 einmal real.
- Mehrsprachigkeit: fehlende Schlüssel; Oberfläche in beiden Sprachen im Browser.

## 14. Umsetzung in Schritten

| Version | Inhalt |
|---|---|
| **22.4.0** ✅ | Grundlage: Mehrsprachigkeits-Gerüst (DE/EN), Regel-CRUD mit neuem Format und Migration, **Testmodus** (global/je Regel, Banner) und eine Ausführungsfunktion, Zugangsdaten-Schlüssel, Zielrechner SSH/WinRM/IPMI mit „Verbindung testen“, Regel-Dialog mit „Regel testen“, Hilfeseiten dazu, Tests |
| **22.5.0** ✅ | Redfish (iDRAC, iLO, TERRA/Supermicro/ASRock Rack) und HTTP-Typ mit Hilfe; TLS immer geprüft (CA oder angehefteter Fingerprint) |
| **22.6.0** ✅ | E-Mail: Microsoft 365 (Graph, SMTP-OAuth) und SMTP allgemein, Empfängerlisten, Test-E-Mail, Hilfe |
| **22.7.0** ✅ (XM42 steht aus) | OPC-UA unverschlüsselt und verschlüsselt inkl. Zertifikatsverwaltung, Verifikation an der XM42, Hilfe |
| **22.8.0** ✅ | Laufende Ausführungen (Countdown, Abbrechen), Wiederanlauf; optional NUT-Server und Selbst-Shutdown |
| **22.10.0** ✅ | Übrige Seiten auf Mehrsprachigkeit umstellen |

## 15. Offene Fragen

Keine. Beantwortet am 19.10.2026:

1. Zielrechner: Ubuntu Server, Arch Linux, Windows; Server mit iDRAC, iLO und Wortmann TERRA
   (BMC-Firmware `egs-2-42-2-0000`) → Kap. 3.
2. OPC-UA: anonym und mit Benutzer; Datentyp konfigurierbar, Standard Boolean → Kap. 5.1.
3. Microsoft 365: App-Registrierung möglich; Tenant ID, Client ID, Geheimnis/Zertifikat und Absender
   konfigurierbar → Kap. 8.
4. Sprachen: Deutsch und Englisch → Kap. 10.
