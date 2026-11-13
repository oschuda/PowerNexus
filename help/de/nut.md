# NUT-Server (optional)

Evolved PowerNexus stellt freigegebene USVs im Netz per NUT-Protokoll bereit. Rechner mit NUT-Client (`upsmon`) fahren
sich dann selbst herunter, wenn die USV auf Batterie läuft und der Akku schwach wird, oder wenn eine Regel es anweist
(FSD, „Forced Shutdown“). Das ist die einfachste Anbindung vieler Rechner: NAS-Geräte und Linux-Server bringen einen
NUT-Client meist mit, und auf Evolved PowerNexus sind keine Zugangsdaten je Rechner nötig.

Der Server ist Teil von Evolved PowerNexus. Das Paket `nut-server` wird nicht gebraucht, die Werte stammen aus der laufenden
Überwachung. Er ist **nur lesend**: Clients können nichts schalten, FSD setzt ausschließlich eine Regel.

## Einrichtung in Evolved PowerNexus (Reiter „Dienste“)

| Feld | Wert |
|---|---|
| Freigegebene USVs | in Klammern steht der NUT-Name, den die Clients verwenden |
| Erlaubte Netze | z. B. `172.16.11.0/24`. Verbindungen von anderswo werden sofort getrennt |
| Port | 3493 (Standard von NUT) |
| Benutzer, Passwort | gemeinsam für alle Clients; das Passwort wird nur als Hash gespeichert |

**Wichtig:** NUT überträgt Passwort und Werte unverschlüsselt. Deshalb nur vertrauenswürdige Netze eintragen und ein
eigenes Passwort verwenden, das nirgends sonst gilt.

Prüfen, ob es läuft (auch von einem anderen Rechner):

```bash
upsc -l <usv-server>
upsc <nut-name>@<usv-server> ups.status
```

## Einrichtung am Client

Auf Ubuntu Server und Arch Linux das Paket `nut-client` bzw. `nut` installieren.

`/etc/nut/nut.conf`:

```
MODE=netclient
```

`/etc/nut/upsmon.conf` (die Rolle muss `secondary` sein, ältere Versionen schreiben `slave`):

```
MONITOR <nut-name>@<usv-server> 1 <benutzer> <passwort> secondary
SHUTDOWNCMD "/sbin/shutdown -h +0"
```

```bash
systemctl restart nut-monitor      # Arch Linux: nut-monitor.service
```

Unter *NUMLOGINS* bzw. im Reiter „Dienste“ erscheint der Client als angemeldet.

## FSD über eine Regel

Im Reiter „Zielrechner“ einen Eintrag vom Typ **NUT-Clients (FSD)** anlegen, wahlweise für eine USV oder alle
freigegebenen. In einer Regel lautet die Aktion **FSD an NUT-Clients senden**. Die angemeldeten Clients fahren sich
daraufhin selbst herunter.

FSD bleibt mindestens 10 Minuten stehen und endet danach, sobald die USV nicht mehr auf Batterie läuft. Sonst würden
Rechner nach der Netzrückkehr beim Hochfahren gleich wieder herunterfahren. Im Reiter „Dienste“ lässt es sich auch von
Hand aufheben. Ein FSD übersteht keinen Neustart von Evolved PowerNexus.

Im Testmodus wird FSD nicht gesetzt, sondern nur protokolliert.

## Verhalten bei Ausfall

Hat Evolved PowerNexus für eine USV keine aktuellen Werte, antwortet der Server mit `DATA-STALE`. `upsmon` meldet dann
nur „Kommunikation verloren“ und fährt nicht herunter. Fällt Evolved PowerNexus oder sein Rechner ganz aus, gilt das Gleiche:
Dann schützt der Client nichts mehr. Ein laufender FSD wird auch ohne aktuelle Werte gemeldet.

## Fehlerbilder

| Meldung am Client | Ursache | Maßnahme |
|---|---|---|
| `Connection failure` / `Connection refused` | Server aus, Port gesperrt, Client nicht in den erlaubten Netzen | Reiter „Dienste“ prüfen, Firewall öffnen |
| `Access denied` | Benutzer oder Passwort falsch, oder Rolle `primary`/`master` | Zugangsdaten prüfen, Rolle `secondary` verwenden |
| `Unknown UPS` | falscher NUT-Name, USV nicht freigegeben | Name aus dem Reiter „Dienste“ verwenden |
| `Data stale` | Evolved PowerNexus erreicht die USV nicht | Reiter „Dashboard“ und Meldungen prüfen |
