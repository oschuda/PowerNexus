# Testmodus

Im Testmodus läuft alles wie im Ernstfall – Auslösung, Bedingungen, Wartezeit, Abbruch, Schritte und
Meldungen –, aber **keine Aktion wird ausgeführt** und es wird **keine Verbindung** zu einem
Zielrechner aufgebaut. Protokolliert wird der Befehl, der ausgeführt *würde*. Meldungen tragen den
Zusatz **[TEST]**.

## Arten

| Art | Wirkung | Einstellen |
|---|---|---|
| Global | gilt für alle Regeln | Schalter **TESTMODUS** in der Kopfzeile (nur Admin) |
| Je Regel | nur diese Regel | Option *Testmodus* im Regel-Dialog |
| Erzwungen | global, nicht abschaltbar | Umgebungsvariable `UPS_DRY_RUN=true` in `/etc/usv-monitor/env` |

Ist der globale Testmodus aktiv, zeigt die Kopfzeile ein deutliches Banner.

**Regel testen** und **Verbindung testen** lösen grundsätzlich nichts aus – unabhängig vom Testmodus.

## Empfohlenes Vorgehen

1. Zielrechner anlegen und **Verbindung testen**.
2. Regel mit Option *Testmodus* anlegen und **Regel testen**.
3. Einen echten Ablauf im Testmodus abwarten oder auslösen und unter **Meldungen** prüfen.
4. Erst dann den Testmodus der Regel ausschalten.
