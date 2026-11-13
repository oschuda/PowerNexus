# Sicherheit

> **Entwurf:** Die mit `[festzulegen]` markierten Angaben müssen vor der Auslieferung ergänzt werden.

## Schwachstellen melden

Bitte melden Sie vermutete Schwachstellen vertraulich an: **[festzulegen: Adresse, möglichst mit PGP-Schlüssel]**

Hilfreich sind die betroffene Version (`/api/version`), Schritte zum Nachvollziehen und mögliche Folgen. Bitte veröffentlichen Sie Details erst nach Abstimmung.

| Schritt | Frist |
|---|---|
| Eingangsbestätigung | [festzulegen, Vorschlag: 3 Werktage] |
| Erste Bewertung | [festzulegen, Vorschlag: 10 Werktage] |
| Behebung kritischer Schwachstellen | [festzulegen] |

Aktiv ausgenutzte Schwachstellen und schwere Vorfälle werden nach Art. 14 CRA binnen 24 Stunden über die ENISA-Meldeplattform gemeldet.

## Unterstützte Versionen

| Version | Sicherheitsupdates |
|---|---|
| 22.x | ja, bis [festzulegen; CRA: in der Regel mindestens 5 Jahre] |
| älter | nein |

## Sicherer Betrieb

- Evolved PowerNexus in ein Verwaltungsnetz stellen und nur Port 5443 für Benutzer öffnen.
- Eigenes Zertifikat einer internen CA verwenden (`USV_TLS_CERT`, `USV_TLS_KEY`).
- Den Reiter „Verwaltung“ prüfen: Der Sicherheitsstatus nennt unsichere Einstellungen.
- SNMPv3 verwenden, wo die USV es kann. SNMP v1/v2c ist im Netz fälschbar und kann eine Abschaltung auslösen.
- OPC-UA verschlüsselt und mit Benutzer betreiben.
- Den Speicher vor physischem Zugriff schützen: Der Datenträger ist nicht verschlüsselt, der Schlüssel zu den Zugangsdaten liegt darauf.
- `secret.key`, `users.json`, `hosts.yaml`, `notifications.yaml`, `services.yaml`, `solar.yaml`, `config.json` und `pki/` sichern.
- Betriebssystem aktuell halten; es liegt in der Verantwortung des Betreibers.
- Updates nur als signiertes Paket einspielen ([`docs/updates.md`](docs/updates.md)). Der private Signaturschlüssel gehört nie auf ein Gerät.

## Bekannte Grenzen

Siehe [`docs/compliance/anforderungsmatrix.md`](docs/compliance/anforderungsmatrix.md), insbesondere: kein zweiter Faktor, keine Rotation von `secret.key`, keine laufende Integritätsprüfung der installierten Dateien.
