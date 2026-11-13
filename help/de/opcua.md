# OPC-UA (Steuerung)

Für Steuerungen (z. B. Bosch Rexroth XM42) und Industrie-PCs. „Herunterfahren“ bedeutet hier: Evolved PowerNexus
**schreibt einen Wert in eine Variable**. Was daraufhin passiert, bestimmt das Programm auf der Steuerung. Dort muss
also eine Variable angelegt sein, die das geordnete Herunterfahren auslöst.

Im Testmodus wird nur protokolliert, was geschrieben würde. „Verbindung testen“ und „Regel testen“ lesen die Variable
und prüfen, ob sie beschreibbar ist und der Datentyp passt, schreiben aber nie.

## Felder

| Feld | Bedeutung |
|---|---|
| Adresse, Port | Steuerung, meist Port 4840 |
| Sicherheit | *Verschlüsselt (SignAndEncrypt)* (Vorgabe) oder *Unverschlüsselt* |
| Anmeldung | *Anonym* oder *Benutzer und Passwort* (Felder oben im Dialog) |
| Variable (NodeId) | z. B. `ns=2;s=Power.ShutdownRequest` oder `ns=4;i=1001` |
| Datentyp | Boolean (Standard), Int16, Int32, UInt16, UInt32, Float, String; muss zur Variable passen |
| Zu schreibender Wert | Standard `true` |
| Wert zurücklesen | prüft nach dem Schreiben den Wert (Standard: an) |

**Wert zurücklesen:** Setzt das Programm die Variable sofort selbst zurück (Impuls), meldet das Zurücklesen einen
Fehler, obwohl alles funktioniert hat. Dann die Option ausschalten. Fährt die Steuerung sofort herunter und das
Zurücklesen ist nicht mehr möglich, gilt der Schritt als erfolgreich und das Protokoll vermerkt es.

Bei *Unverschlüsselt* gehen die Daten im Klartext über das Netz. Das ist nur in einem abgetrennten Anlagennetz sinnvoll.

## Verschlüsselte Verbindung einrichten

Beide Seiten müssen sich gegenseitig vertrauen. Evolved PowerNexus verwendet bevorzugt `Aes256_Sha256_RsaPss`, sonst
`Basic256Sha256`, je nachdem, was die Steuerung anbietet.

**Schritt 1: Die Steuerung vertraut uns.** Im Zielrechner-Dialog *Verschlüsselt* wählen. Evolved PowerNexus erzeugt beim
ersten Mal sein Client-Zertifikat (RSA 2048, SHA-256, 5 Jahre). Es erfüllt die Anforderungen der XM42 (u. a. `keyCertSign`,
`AuthorityKeyIdentifier`, ApplicationUri als erster SAN-Eintrag). Das Zertifikat herunterladen (DER) und auf der
Steuerung importieren, bei der XM42 in IndraWorks unter OPC-UA-Server, Zertifikatsverwaltung. Angezeigt wird der
**SHA-1-Thumbprint**, weil die Engineering-Werkzeuge diesen Wert zeigen.

**Schritt 2: Wir vertrauen der Steuerung.** „Verbindung testen“ klicken. Das Zertifikat der Steuerung wird mit Subject,
SHA-1-Thumbprint, Gültigkeit, Signatur und Schlüssellänge angezeigt. Den Thumbprint mit dem in der Steuerung
vergleichen, erst dann „Diesem Zertifikat vertrauen“ klicken und erneut testen.

Ohne vertrautes Server-Zertifikat wird keine Sitzung aufgebaut und nichts geschrieben; automatisches Akzeptieren gibt
es nicht. Wird das Zertifikat der Steuerung erneuert, muss es erneut bestätigt werden. Das Zertifikat generischer
Steuerungen enthält oft nicht deren Adresse (z. B. `CN=IndraControl`). Der Hostname wird deshalb nicht geprüft,
maßgeblich ist das exakt vertraute Zertifikat.

**Hostname geändert?** Die ApplicationUri `urn:<Hostname>:USVMonitor:OPCUA` steht im Zertifikat. Ändert sich der
Hostname, erzeugt Evolved PowerNexus ein neues Zertifikat und weist im Dialog darauf hin. Dann Schritt 1 wiederholen.

Ablage auf dem USV-Server: `/etc/usv-monitor/pki/` mit `own/` (eigenes Zertifikat und Schlüssel), `trusted/` (vertraute
Server-Zertifikate) und `rejected/` (zuletzt gesehene, nicht vertraute).

**Schonender Umgang:** Evolved PowerNexus verbindet sich nur für Aktion und Test und trennt danach sofort. Es gibt keine
Dauerverbindung und je Steuerung nur eine Sitzung gleichzeitig.

## Fehlerbilder

Die Meldung nennt die Seite, auf der die Ursache liegt.

| Meldung | Ursache | Maßnahme |
|---|---|---|
| Die Steuerung lehnt unser Client-Zertifikat ab (`BadSecurityChecksFailed`) | Zertifikat auf der Steuerung nicht vertraut | Schritt 1 |
| Wir vertrauen dem Server-Zertifikat noch nicht (`BadCertificateUntrusted`) | die Steuerung hat uns bereits akzeptiert, uns fehlt Schritt 2 | Schritt 2 |
| Zertifikat erfüllt die Richtlinie nicht (`BadCertificatePolicyCheckFailed`) | SHA-1-Signatur oder Schlüssel unter 2048 Bit | Zertifikat auf der Seite mit dem Mangel erneuern |
| Anmeldung abgelehnt | Benutzer/Passwort falsch, Anmeldeart nicht erlaubt | Benutzer der Steuerung und erlaubte Anmeldearten prüfen |
| Variable nicht gefunden | NodeId falsch, Namespace-Index anders | NodeId mit einem OPC-UA-Client (z. B. UaExpert) prüfen |
| Variable nicht beschreibbar | Zugriffsrechte der Variable oder des Kontos | Rechte in der Steuerung vergeben |
| Datentyp passt nicht | Typ im Dialog weicht von der Variable ab | angezeigten „Datentyp der Variable“ übernehmen |
| Keine Verbindung, Ping geht aber | Verbindungstabelle der Steuerung erschöpft | OPC-UA-Dienst der Steuerung neu starten |
| Bietet keinen unverschlüsselten Zugang / keine passende Verschlüsselung | Sicherheitseinstellungen der Steuerung | angebotene Varianten stehen in der Meldung; passend einstellen |
