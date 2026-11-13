# Betrieb als Container (z. B. Synology Container Manager)

Neben der Installation als systemd-Dienst (README) läuft Evolved PowerNexus auch als Container. Signierte Updates
(`usv-monitor-update`) gelten dann nicht; aktualisiert wird, indem das Image aus dem neuen Stand neu gebaut wird.

## Aufbau

```
powernexus/
  compose.yaml     aus docker/compose.yaml
  .env             USV_SECRET_KEY=<64 zufällige Hex-Zeichen>, z. B. openssl rand -hex 32
  src/             Inhalt des Repositorys; daraus baut Compose das Image
  app/             Programmordner im Container (/opt/usv-monitor): config.json, *.yaml, data/, models/
```

`app/` gehört in jede Sicherung. Beim Start kopiert `docker/entrypoint.sh` den Code des Images nach `app/` und
entfernt dabei den Code der vorigen Version; Einstellungen und Daten bleiben unberührt.

Der Container nutzt das Netz des Hosts: Das Dashboard ist unter `https://<host>:5443` erreichbar, Port 5080 leitet
dorthin um (5000/5001 belegt auf einer Synology DSM). Eine USV am USB-Anschluss der Synology wird über deren
NUT-Server angesprochen: USB-Profil mit NUT-Name `ups@localhost`.

## Synology

1. Ordner `powernexus` unter `/volume1/docker/` anlegen, Dateien wie oben ablegen.
2. Container Manager → Projekt → Erstellen → Pfad `/volume1/docker/powernexus` → „Vorhandene compose.yaml verwenden“ →
   Erstellen. Das Image wird gebaut (einige Minuten) und gestartet.
3. Das Einmal-Passwort für `admin` steht in `app/data/initial-admin-password.txt`; es muss beim ersten Login geändert werden.

Update: neuen Stand nach `src/` kopieren, im Projekt „Erstellen“ (bzw. `docker compose up -d --build`).

## Daten aus dem früheren USV-Dashboard

Alte `ups_history.db` nach `app/legacy/` kopieren, dann bei laufendem Container:

```bash
docker compose stop powernexus
docker compose run --rm --entrypoint sh powernexus -c \
  "cd /opt/usv-monitor && python deploy/migrate_legacy_db.py --source legacy/ups_history.db --profile <Profil> --dry-run"
# passt die Vorschau, ohne --dry-run wiederholen
docker compose start powernexus
```
