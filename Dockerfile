# Evolved PowerNexus als Container (z. B. Synology Container Manager), Beschreibung: docs/docker.md
FROM python:3.12-slim-bookworm

# upsc (NUT), snmpget, ipmitool; openssl für das selbstsignierte Zertifikat
RUN apt-get update \
    && apt-get install -y --no-install-recommends nut-client snmp ipmitool openssl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /tmp/requirements.lock
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock

# Der Code liegt im Image unter /opt/src und wird beim Start in den Programmordner übernommen (docker/entrypoint.sh)
COPY . /opt/src
RUN install -m 755 /opt/src/docker/entrypoint.sh /usr/local/bin/entrypoint.sh

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 5443 5080
ENTRYPOINT ["entrypoint.sh"]
CMD ["--port", "5080"]
