"""Zentrale Versionsnummer von Evolved PowerNexus.

Einzige Quelle für die Version: Web-Oberfläche (Titel, Kopfzeile, Login), /api/version
und Log-Ausgaben lesen sie von hier.

Bei jeder Änderung an der Plattform:
- MAJOR: inkompatible Änderung (z. B. Framework-Wechsel, geänderte API)
- MINOR: neue Funktion oder sichtbare Verhaltensänderung
- PATCH: reine Fehlerbehebung
und einen Eintrag in CHANGELOG.md ergänzen.
"""

__version__ = "22.22.0"
