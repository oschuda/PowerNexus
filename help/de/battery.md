# Batterie

Der Reiter „Batterie“ beurteilt jede USV in drei getrennten Punkten. So bleibt erkennbar, ob eine gute Bewertung belegt
ist oder nur Daten fehlen.

| Punkt | Grundlage |
|---|---|
| Technischer Zustand | was die USV selbst meldet: Lebensdauer der Batterie, „Batterie austauschen“, Selbsttest |
| Restlebensdauer | empfohlenes Austauschdatum der USV, sonst Einbaudatum plus typische 5 Jahre |
| Datenbasis | welche Angaben vorliegen und welche für eine belastbare Aussage fehlen |

## Einbaudatum

Im Reiter „Batterie“ die USV wählen und das Datum eintragen oder nach einem Tausch **Heute eingebaut** klicken (Rollen
Admin und Operator). Ein leeres Datum entfernt die Angabe; dann gilt, was die USV meldet.

Ist das Austauschdatum der USV älter als das eingetragene Einbaudatum, stammt es noch von der alten Batterie und wird
nicht mehr verwendet. Stellen Sie das Datum nach einem Tausch trotzdem auch an der USV um, wenn sie das anbietet. Eine
Meldung „Batterie austauschen“ der USV selbst bleibt bestehen, bis die USV sie aufhebt, oft nach einem Selbsttest.

## Batterie im Betrieb prüfen

Die Restlaufzeit in Minuten ist eine Schätzung der USV selbst, keine Messung von Evolved PowerNexus. Bei einer alternden Batterie kann sie deutlich zu hoch sein. Belastbar sind:

- **Selbsttest:** regelmäßig auslösen, zum Beispiel vierteljährlich. Bei einer USV an USB mit NUT: `upscmd <usv> test.battery.start.quick` (fragt nach einem NUT-Benutzer mit Berechtigung `instcmds`), Ergebnis mit `upsc <usv> ups.test.result`. Die USV schaltet dabei kurz auf Batterie.
- **Entladetest unter echter Last:** die tatsächliche Laufzeit stoppen und mit der gemeldeten vergleichen, zum Beispiel einmal im Jahr und nur mit abgesicherten Zielrechnern. Das ist der einzige direkte Beleg. Eine Restlaufzeit ohne Last sagt wenig aus.
- **Verlauf:** Restlaufzeit nur bei vergleichbarer Last vergleichen. Bricht die Batteriespannung im Batteriebetrieb schnell ein, ist das ein Alterungszeichen.

## Batterievorhersage

Der Trend aus Messdaten erscheint erst mit einem trainierten Modell und ausreichend langer Messhistorie. Vorher zeigt die
Seite bewusst keine Prognose, sondern die Bewertung aus den drei Punkten.
