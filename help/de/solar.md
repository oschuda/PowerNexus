# PV-Wechselrichter (optional)

Evolved PowerNexus zeigt die Werte eines PV-Wechselrichters neben denen der USVs: Leistung, Einspeisung, Bezug, Ertrag,
Temperatur. Im Verlauf lässt sich ablesen, ob die Netzspannung mit der PV-Einspeisung steigt und ob die USV auf Batterie
lief, während die Anlage Strom erzeugte.

Die Werte kommen per **MQTT** von einem System, das den Wechselrichter ausliest und seine Sensoren nach dem Muster von
**Home Assistant** ankündigt (MQTT-Discovery). Evolved PowerNexus spricht den Wechselrichter nicht selbst an.

## Einrichtung (Reiter „PV“, nur Admins)

| Feld | Wert |
|---|---|
| Broker | Name oder IP-Adresse des MQTT-Brokers |
| Port | 1883, mit TLS meist 8883 |
| TLS, Zertifikat prüfen | empfohlen; ohne TLS gehen Benutzer und Passwort im Klartext über das Netz |
| Benutzer, Passwort | wie am Broker eingerichtet; das Passwort wird verschlüsselt gespeichert |
| Discovery-Präfix | `homeassistant`, sofern am liefernden System nicht anders eingestellt |
| Gerät | leer = alle angekündigten Sensoren; sonst ein Teil des Gerätenamens oder die Gerätekennung |
| Messintervall | Abstand der gespeicherten Werte in Sekunden (Standard 60) |
| USV für den Vergleich | die USV am selben Netzanschluss |

„Verbindung prüfen“ versucht die eingegebenen Werte, ohne sie zu speichern, und zählt die angekündigten Sensoren.
Nach dem Speichern erscheinen die gefundenen Sensoren unten in der Liste, mit dem Feld, dem sie zugeordnet sind.

## Zuordnung der Sensoren

Die Zuordnung ergibt sich aus Einheit, Geräteklasse und Name des Sensors: Leistung in W oder kW wird zur AC-Leistung,
Namen mit „feed“, „einspeis“ oder „supplied“ zur Einspeisung, „bezug“, „consumption“ oder „absorbed“ zum Bezug,
„pv_power“, „dc“ oder „string“ zur DC-Leistung (mehrere Strings werden addiert), Energie mit „day“ oder „today“ zum
Tagesertrag, mit „total“ zum Gesamtertrag. kW und kWh werden umgerechnet.

Passt eine Zuordnung nicht, unter „Zuordnung von Hand“ für das Feld die **Kennung** des richtigen Sensors eintragen
(Spalte „Kennung“ der Sensorliste; die Eingabe schlägt sie vor). Senden alle Sensoren eines Geräts auf ein gemeinsames
Topic, ist das ihre `unique_id`, sonst ihr `state_topic`. Ein Topic, das nicht angekündigt ist, geht auch; dann muss die
Nachricht den Wert in der Einheit des Felds enthalten.

Meldet ein Gerät über sein `availability`-Topic „offline“ (etwa der Wechselrichter nachts), werden seine Werte weder
angezeigt noch gespeichert, auch wenn der Broker die letzten noch liefert.

## InverterLink (SMA über Bluetooth)

InverterLink kündigt seine Sensoren unter `homeassistant/sensor/<Seriennummer>/<Sensor>/config` an und sendet alle 30 s
alle Werte als ein JSON an `inverterlink/<Seriennummer>/state`. Einzustellen sind nur Broker, Port und Zugangsdaten wie
in der `appsettings.json` von InverterLink (`BrokerHost`, `BrokerPort`, `Username`, `Password`); der Präfix bleibt
`homeassistant`. Die Zuordnung ergibt sich von selbst: „AC-Leistung“, „DC1-“ + „DC2-Leistung“ (addiert),
„Tagesertrag“, „Gesamtertrag“, „Temperatur“, „DC1-/DC2-Spannung“ und „-Strom“, „Betriebsstatus“. Die „Status-ID“ wird
nicht als Status verwendet. Von Hand zuordnen lässt sich ein Sensor über seine Kennung, z. B.
`inverterlink_sma_<Seriennummer>_dc2_voltage`. Nachts sendet InverterLink nur einen Teil der Werte (AC-Leistung 0 W,
Ertrag, Status); die übrigen Felder bleiben dann leer. Einspeisung und Netzbezug liefert InverterLink nicht, dafür
braucht es einen Zähler mit eigenem Sensor.

## Prüfen am Broker

```bash
mosquitto_sub -h <broker> -u <benutzer> -P <passwort> -t 'homeassistant/sensor/#' -v
```

Jeder Sensor erscheint mit einer `…/config`-Nachricht (JSON mit `state_topic` und `unit_of_measurement`).

## Daten

Gespeichert wird wie bei den USVs gesammelt (Messwert-Puffer) und nach der Aufbewahrungsfrist zu Stundenwerten
verdichtet. Werte aus dem früheren USV-Dashboard übernimmt `deploy/migrate_legacy_db.py`.
