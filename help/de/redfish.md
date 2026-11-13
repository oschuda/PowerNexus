# Redfish (Server-Fernwartungskarte / BMC)

Redfish ist die standardisierte REST-Schnittstelle (DMTF) moderner Fernwartungskarten: Dell iDRAC 8/9,
HPE iLO 4/5/6, Lenovo XClarity, Supermicro, ASRock Rack, Fujitsu iRMC sowie OpenBMC-basierte Karten
wie bei Wortmann TERRA-Servern (Intel-Plattform, Firmware `egs-…`). Gesteuert wird über HTTPS,
unabhängig vom Betriebssystem – auch ein hängendes System lässt sich so noch ausschalten.

| Aktion | Redfish-Aufruf (`ComputerSystem.Reset`) | Wirkung |
|---|---|---|
| Herunterfahren | `ResetType: GracefulShutdown` | ACPI-Signal; das Betriebssystem fährt geordnet herunter |
| Neu starten | `ResetType: GracefulRestart` | geordneter Neustart über das Betriebssystem |
| Ausschalten (hart) | `ResetType: ForceOff` | sofort aus, **ohne** Herunterfahren – nur als letzter Schritt |
| Einschalten | `ResetType: On` | |

Der Aufruf lautet `POST https://<bmc>/redfish/v1/Systems/<id>/Actions/ComputerSystem.Reset`.
Ist der Server bereits im gewünschten Zustand (z. B. schon aus), wird nichts gesendet und der Schritt
gilt als erfolgreich.

**Empfehlung für eine Regel:** Schritt 1 *Herunterfahren*, Schritt 2 nach angemessener Verzögerung
(z. B. 300 s) *Ausschalten (hart)* mit „Bei Fehler mit nächstem Schritt fortfahren“ – so ist der Server
auch dann aus, wenn das Betriebssystem nicht reagiert. Ein bereits ausgeschalteter Server wird in
Schritt 2 nicht erneut geschaltet.

## Einrichtung an der Fernwartungskarte

1. **Redfish prüfen:** Im Browser `https://<bmc-adresse>/redfish/v1/` öffnen. Erscheint eine JSON-Antwort
   mit `RedfishVersion`, ist Redfish verfügbar. Sonst IPMI verwenden (siehe [IPMI](ipmi)).
2. **Eigenes Konto** nur für Evolved PowerNexus anlegen, mit dem kleinsten ausreichenden Recht:

   | Fernwartungskarte | Benötigtes Recht |
   |---|---|
   | Dell iDRAC 8/9 | Rolle *Operator* (enthält „Login“ und „System Control“) |
   | HPE iLO 4/5/6 | „Login“ und „Virtual Power and Reset“ |
   | OpenBMC / Wortmann TERRA (Intel) | Rolle *Operator* |
   | Supermicro, ASRock Rack | Rolle *Operator* |
   | Lenovo XClarity, Fujitsu iRMC | Rolle mit Energiesteuerung („Remote Power“/„Power Management“) |

3. **Vom USV-Server prüfen** (ohne etwas auszulösen):

```bash
curl -k -u <benutzer> https://<bmc-adresse>/redfish/v1/Systems
```

   Die Antwort listet die Systeme (meist genau eines, z. B. `system`, `1` oder `System.Embedded.1`).

## Zertifikat

Die Verbindung ist immer verschlüsselt und das Zertifikat wird immer geprüft – auf eine von zwei Arten:

- **Feld „Zertifikat-Fingerprint“ leer:** Das Zertifikat muss von einer CA stammen, der der USV-Server
  vertraut. Für eine interne CA das CA-Zertifikat auf dem USV-Server hinterlegen:

```bash
cp interne-ca.crt /usr/local/share/ca-certificates/
update-ca-certificates
systemctl restart usv-monitor
```

- **Fingerprint eingetragen (angeheftet):** Es gilt genau dieses eine Zertifikat. Das ist der übliche Weg
  für Fernwartungskarten mit selbstsigniertem Zertifikat (ab Werk bei iDRAC, iLO und OpenBMC).
  Hostname und Ablaufdatum spielen dann keine Rolle; entscheidend ist der Schlüssel.

„Verbindung testen“ zeigt bei einem nicht bestätigten Zertifikat dessen SHA-256-Fingerprint an und
bietet „Fingerprint übernehmen“ an. **Bevor** Sie übernehmen, den Fingerprint mit dem Zertifikat in der
Weboberfläche der Fernwartungskarte vergleichen (Browser: Schloss-Symbol → Zertifikat → SHA-256).
Zugangsdaten werden erst gesendet, wenn das Zertifikat bestätigt ist.

Wird das Zertifikat der Karte erneuert, schlägt die Verbindung mit „Zertifikat stimmt NICHT …“ fehl.
Dann den neuen Fingerprint prüfen und übernehmen.

## Einrichtung in Evolved PowerNexus

| Feld | Wert |
|---|---|
| Schnittstelle | Redfish (Server-BMC) |
| Adresse | IP oder Name der **Fernwartungskarte** (nicht des Betriebssystems) |
| Port | 443 |
| Benutzer / Passwort | Konto aus Schritt 2 |
| System-ID | leer lassen – wird automatisch ermittelt; nur bei mehreren Systemen (Blade-Gehäuse) eintragen |
| Zertifikat-Fingerprint | leer (CA) oder über „Verbindung testen“ übernehmen |

„Verbindung testen“ meldet Redfish-Version, System, Modell, aktuellen Zustand und die Reset-Typen, die
die Karte anbietet. Fehlt dort eine Aktion, die eine Regel verwendet, schlägt dieser Schritt fehl –
dann eine andere Aktion wählen. Ob das Konto wirklich schalten darf, zeigt erst eine echte Ausführung;
deshalb das Recht aus Schritt 2 sorgfältig vergeben.

Im Testmodus wird nur der Aufruf protokolliert, der gesendet würde, z. B.
`Redfish POST https://10.0.0.21:443/redfish/v1/Systems/<auto>/Actions/ComputerSystem.Reset {"ResetType": "GracefulShutdown"}`.

## Fehlerbilder

| Meldung | Ursache | Maßnahme |
|---|---|---|
| Zertifikat wird von keiner bekannten CA bestätigt | selbstsigniertes Zertifikat | Fingerprint prüfen und übernehmen oder CA hinterlegen |
| Zertifikat stimmt NICHT mit dem angehefteten überein | Zertifikat erneuert – oder fremdes Gerät unter dieser Adresse | Fingerprint in der Weboberfläche der Karte prüfen, erst dann übernehmen |
| Anmeldung abgelehnt | Benutzer/Passwort falsch, Konto gesperrt | Zugangsdaten prüfen; manche Karten sperren nach Fehlversuchen |
| Zugriff verweigert | Konto ohne Energiesteuerungsrecht | Rolle laut Schritt 2 vergeben |
| System nicht eindeutig | mehrere Systeme hinter einer Karte | System-ID aus der Meldung eintragen |
| Reset-Typ … wird vom BMC nicht angeboten | Karte kennt z. B. kein `GracefulRestart` | andere Aktion verwenden |
| Zeitüberschreitung | Adresse falsch, Port 443 gesperrt, Karte nicht im Netz | Erreichbarkeit vom USV-Server prüfen (`curl -k https://<bmc>/redfish/v1/`) |

## Sicherheitshinweise

- Fernwartungskarten gehören in ein eigenes Verwaltungsnetz.
- Für Evolved PowerNexus ein eigenes Konto verwenden, nie das Administratorkonto der Karte.
- Das Passwort wird verschlüsselt gespeichert und nie über die Oberfläche ausgeliefert.
