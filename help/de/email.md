# E-Mail und Microsoft 365

Evolved PowerNexus meldet per E-Mail

- **Meldungen**, die eine Empfängerliste abonniert hat (z. B. Stromausfall), unabhängig von Regeln,
- **Regeln** mit Empfängerliste: bei Beginn der Wartezeit den Plan, am Ende das Ergebnis je Zielrechner,
- den **Ablauf** der Microsoft-365-Anmeldung (ab 30 Tage vorher wöchentlich, in der letzten Woche täglich).

Der Versand läuft unabhängig von der Regel: Eine nicht zustellbare E-Mail hält keinen Shutdown auf. Fehlgeschlagene
E-Mails werden nach 15 s, 1, 5 und 15 Minuten wiederholt und erscheinen danach als Meldung. Warteschlange und
Wiederholungen liegen im Arbeitsspeicher und gehen bei einem Neustart des Dienstes verloren.

**Bei Stromausfall:** E-Mails kommen nur an, wenn auch Switch, Firewall und Internetzugang an der USV hängen.

| Versandweg | Für | Anmeldung |
|---|---|---|
| Microsoft 365 – Graph API (empfohlen) | Exchange Online | App-Registrierung, Berechtigung `Mail.Send` |
| Microsoft 365 – SMTP mit OAuth 2.0 | Exchange Online, wenn Graph nicht erlaubt ist | App-Registrierung, Berechtigung `SMTP.SendAsApp` |
| SMTP allgemein | andere Anbieter, interner Relay | optional Benutzer und Passwort |

Für Microsoft 365 ist die Anmeldung per App-Registrierung vorgesehen, weil Microsoft die Anmeldung mit Benutzer und
Passwort für SMTP in Exchange Online abschaltet. Den aktuellen Stand im eigenen Tenant bitte prüfen.

## App-Registrierung in Microsoft Entra ID

1. [Entra Admin Center](https://entra.microsoft.com) → *App-Registrierungen* → *Neue Registrierung*.
   Name z. B. `Evolved PowerNexus`, Kontotyp „Nur dieses Verzeichnis“, keine Umleitungs-URI.
2. Auf der Übersichtsseite notieren: **Anwendungs-ID (Client)** und **Verzeichnis-ID (Mandant)**.
3. *Zertifikate & Geheimnisse*, eine der beiden Varianten:
   - **Geheimer Clientschlüssel:** *Neuer geheimer Clientschlüssel*. Einzutragen ist der **Wert**, nicht die „Geheime
     ID“. Der Wert wird nur einmal angezeigt. Ablaufdatum notieren.
   - **Zertifikat:** Öffentlichen Teil (`.cer`) hochladen. In Evolved PowerNexus wird die PFX-Datei mit privatem Schlüssel
     hinterlegt, das Ablaufdatum wird daraus gelesen.

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 730 -subj "/CN=Evolved PowerNexus" -keyout usv.key -out usv.cer
openssl pkcs12 -export -inkey usv.key -in usv.cer -out usv.pfx
```

4. Berechtigung nach Versandweg vergeben (siehe unten) und **Administratorzustimmung erteilen**.

### Graph API

*API-Berechtigungen* → *Berechtigung hinzufügen* → *Microsoft Graph* → *Anwendungsberechtigungen* → `Mail.Send` →
*Administratorzustimmung erteilen*.

**Wichtig:** Ohne Einschränkung darf die Anwendung im Namen **jedes** Postfachs senden. Daher auf das Absenderpostfach
beschränken. Microsoft empfiehlt dafür inzwischen RBAC für Anwendungen in Exchange Online. In diesem Fall die
Berechtigung `Mail.Send` aus der App-Registrierung wieder entfernen, denn Entra- und Exchange-Rechte addieren sich:

```powershell
Connect-ExchangeOnline
# Objekt-ID: Entra → Unternehmensanwendungen → Evolved PowerNexus
New-ServicePrincipal -AppId <client-id> -ObjectId <objekt-id> -DisplayName "Evolved PowerNexus"
New-ManagementScope -Name "Evolved PowerNexus" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'powernexus@firma.de'"
New-ManagementRoleAssignment -App <client-id> -Role "Application Mail.Send" -CustomResourceScope "Evolved PowerNexus"
Test-ServicePrincipalAuthorization -Identity <client-id> -Resource powernexus@firma.de
```

Alternativ, mit `Mail.Send` in der App-Registrierung, eine Anwendungszugriffsrichtlinie auf eine E-Mail-aktivierte
Sicherheitsgruppe, die das Absenderpostfach enthält:

```powershell
New-ApplicationAccessPolicy -AppId <client-id> -PolicyScopeGroupId usv-absender@firma.de -AccessRight RestrictAccess -Description "Evolved PowerNexus"
```

Änderungen an diesen Rechten können bis zu etwa einer Stunde brauchen, bis sie wirken.

### SMTP mit OAuth 2.0

*API-Berechtigungen* → *Berechtigung hinzufügen* → *Von meiner Organisation verwendete APIs* → *Office 365 Exchange
Online* → *Anwendungsberechtigungen* → `SMTP.SendAsApp` → *Administratorzustimmung erteilen*. Dann das Postfach
freigeben:

```powershell
Connect-ExchangeOnline
New-ServicePrincipal -AppId <client-id> -ObjectId <objekt-id>
Add-MailboxPermission -Identity powernexus@firma.de -User <objekt-id> -AccessRights FullAccess
Set-CASMailbox -Identity powernexus@firma.de -SmtpClientAuthenticationDisabled $false
```

## Einrichtung in Evolved PowerNexus (Reiter „E-Mail“)

| Feld | Wert |
|---|---|
| Versandweg | Graph API, SMTP mit OAuth oder SMTP allgemein |
| Absenderpostfach | z. B. `powernexus@firma.de`; muss zur Freigabe oben passen |
| Link zum Dashboard | erscheint in jeder E-Mail, optional |
| Verzeichnis-ID, Anwendungs-ID | aus Schritt 2 |
| Anmeldung | geheimer Clientschlüssel mit Ablaufdatum oder PFX-Datei mit Passwort |
| Server, Port, Verschlüsselung | nur SMTP allgemein, meist Port 587 mit STARTTLS |
| Empfängerlisten | Name, Adressen, Sprache der E-Mails und Meldungen, die immer gemeldet werden |

Eine Regel verwendet Listen über „E-Mail an“ im Regel-Dialog. Eine Liste, die eine Regel verwendet, lässt sich nicht
löschen.

Geheimnisse werden verschlüsselt in `notifications.yaml` gespeichert und nie über die Oberfläche ausgeliefert. Leere
Felder lassen den gespeicherten Wert unverändert.

Das Zertifikat eines SMTP-Servers wird immer gegen die CA-Liste des USV-Servers und den Servernamen geprüft. Für eine
interne CA das CA-Zertifikat hinterlegen (`update-ca-certificates`, siehe [Redfish](redfish)). „Keine“ Verschlüsselung
ist nur ohne Zugangsdaten möglich, also für einen internen Relay.

**„Test-E-Mail senden“** verwendet die Werte im Formular, auch ungespeichert, und prüft bei Microsoft 365 Anmeldung und
Versand getrennt. Benötigt wird Zugang vom USV-Server nach außen: `login.microsoftonline.com` und
`graph.microsoft.com` auf Port 443 bzw. `smtp.office365.com` auf Port 587.

## Testmodus

Im Testmodus werden E-Mails zu Regeln wirklich gesendet, damit sich der Ablauf prüfen lässt. Sie tragen `[TEST]` im
Betreff und den Hinweis, dass nichts ausgeführt wurde. „Regel testen“ sendet keine E-Mail, sondern nennt nur die Listen.

## Fehlerbilder

| Meldung | Ursache | Maßnahme |
|---|---|---|
| geheimer Clientschlüssel falsch | „Geheime ID“ statt „Wert“ eingetragen | Wert eintragen, ggf. neuen Schlüssel erzeugen |
| geheimer Clientschlüssel ist abgelaufen | Ablaufdatum erreicht | neuen Schlüssel erzeugen, Wert und Datum eintragen |
| Anwendungs-ID nicht gefunden | falsche ID oder falsches Verzeichnis | beide IDs aus der Übersicht kopieren |
| Administratorzustimmung fehlt | Zustimmung nicht erteilt | Schritt 4 |
| Zertifikat wird nicht akzeptiert | `.cer` nicht hochgeladen, abgelaufen | Zertifikat in der App-Registrierung prüfen |
| durch bedingten Zugriff blockiert | Richtlinie schließt die Anwendung ein | Richtlinie anpassen |
| Versand verweigert | Berechtigung oder Postfach-Freigabe fehlt, Änderung noch nicht wirksam | Freigabe prüfen, bis zu einer Stunde warten |
| Absenderpostfach nicht gefunden | Adresse falsch oder keine Exchange-Lizenz | Adresse und Postfach prüfen |
| SMTP-Anmeldung abgelehnt | SMTP AUTH fürs Postfach aus, Freigabe fehlt | `Set-CASMailbox`, `Add-MailboxPermission` |
| Zertifikat des SMTP-Servers nicht bestätigt | Servername passt nicht, interne CA fehlt | Servernamen wie im Zertifikat eintragen, CA hinterlegen |
| Keine Verbindung | Firewall, Proxy, DNS | Erreichbarkeit vom USV-Server prüfen |
