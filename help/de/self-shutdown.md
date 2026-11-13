# Selbst-Shutdown des USV-Servers (optional)

Eine Regel kann als letzten Schritt den Rechner herunterfahren, auf dem Evolved PowerNexus läuft. Standardmäßig ist das aus.

**Bedenken Sie:** Danach gibt es bis zum Wiedereinschalten keine Überwachung, keine E-Mails und keine Regeln mehr. Ein
Raspberry Pi startet erst wieder, wenn die Stromversorgung einmal unterbrochen war und wiederkehrt. Fährt er herunter
und die USV schaltet nie ab (Netz kommt zurück, bevor der Akku leer ist), bleibt er aus, bis jemand ihn neu startet.

## Einrichtung

Zwei voneinander unabhängige Freigaben sind nötig.

**1. Berechtigung im Betriebssystem.** Der Dienst läuft bewusst nicht als root. Damit das Dienstkonto `usv-monitor`
herunterfahren darf, einmalig als root die mitgelieferte polkit-Regel installieren:

```bash
cp /opt/usv-monitor/deploy/49-usv-monitor-poweroff.rules /etc/polkit-1/rules.d/
```

Prüfen:

```bash
sudo -u usv-monitor busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager CanPowerOff
```

Die Antwort muss `s "yes"` lauten.

**2. Freigabe in Evolved PowerNexus.** Im Reiter „Dienste“ den Selbst-Shutdown erlauben.

**3. Zielrechner und Regel.** Im Reiter „Zielrechner“ einen Eintrag vom Typ **USV-Server selbst** anlegen. „Verbindung
testen“ prüft beide Freigaben und löst nichts aus. In einer Regel darf er nur **allein im letzten Schritt** stehen.

Wird die Freigabe im Reiter „Dienste“ später zurückgenommen, schlägt der Schritt fehl, statt herunterzufahren.

## Empfehlung

- Vorher alle anderen Zielrechner herunterfahren und dem letzten Schritt eine angemessene Verzögerung geben, damit
  Ergebnisse und E-Mails noch abgehen.
- Als Bedingung einen niedrigen Akkustand setzen, z. B. „Akkuladung unter 20 %“.
- Im Testmodus wird nichts heruntergefahren, nur protokolliert. Die Regel so zuerst durchspielen.

## Rücknahme

```bash
rm /etc/polkit-1/rules.d/49-usv-monitor-poweroff.rules
```

## Fehlerbilder

| Meldung | Ursache | Maßnahme |
|---|---|---|
| Selbst-Shutdown ist nicht erlaubt | Freigabe im Reiter „Dienste“ fehlt | erlauben |
| Das Dienstkonto darf nicht herunterfahren (`challenge`, `no`) | polkit-Regel fehlt | Schritt 1 |
| „muss allein im letzten Schritt stehen“ | Regel hat danach weitere Schritte oder andere Zielrechner im selben Schritt | Regel umstellen |
