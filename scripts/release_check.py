#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prüfung vor jeder Version: bekannte Schwachstellen und SBOM

    pip install pip-audit cyclonedx-bom
    python scripts/release_check.py

Schreibt docs/compliance/sbom.cdx.json (CycloneDX: Python-Pakete aus requirements.lock und Bibliotheken aus static/vendor) und
docs/compliance/schwachstellen.json. Mit Exit-Code 1, wenn eine Schwachstelle nicht in AKZEPTIERT begründet ist.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements.lock"
OUT = ROOT / "docs" / "compliance"

# Bewertete Schwachstellen: {"ID": "Begründung, warum sie hier nicht ausnutzbar ist"}
AKZEPTIERT: dict = {}


def vendor_components() -> list:
    table = (ROOT / "static" / "vendor" / "README.md").read_text(encoding="utf-8")
    components = []
    for file_name, library, license_id, checksum in re.findall(r"\| `([^`]+)` \| ([^|]+) \| (\w+) \| [^|]+ \| `([0-9a-f]{64})` \|", table):
        name, version = library.strip().rsplit(" ", 1)
        components.append({"type": "library", "name": name, "version": version, "licenses": [{"license": {"id": license_id}}],
                           "hashes": [{"alg": "SHA-256", "content": checksum}], "properties": [{"name": "file", "value": file_name}]})
    return components


def build_sbom() -> None:
    target = OUT / "sbom.cdx.json"
    subprocess.run([sys.executable, "-m", "cyclonedx_py", "requirements", str(LOCK), "--of", "JSON", "-o", str(target)], check=True)
    sbom = json.loads(target.read_text(encoding="utf-8"))
    sbom["components"] = sbom.get("components", []) + vendor_components()
    target.write_text(json.dumps(sbom, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"SBOM: {len(sbom['components'])} Komponenten")


def audit() -> int:
    result = subprocess.run([sys.executable, "-m", "pip_audit", "-r", str(LOCK), "--require-hashes", "--disable-pip", "-f", "json"],
                            capture_output=True, text=True, encoding="utf-8")
    report = json.loads(result.stdout)
    open_findings = []
    entries = []
    for dependency in report["dependencies"]:
        for vulnerability in dependency.get("vulns", []):
            entry = {"package": dependency["name"], "version": dependency["version"], "id": vulnerability["id"],
                     "fixed_in": vulnerability.get("fix_versions", []), "accepted": AKZEPTIERT.get(vulnerability["id"])}
            entries.append(entry)
            if not entry["accepted"]:
                open_findings.append(entry)
    (OUT / "schwachstellen.json").write_text(json.dumps({"packages": len(report["dependencies"]), "findings": entries}, indent=1) + "\n", encoding="utf-8")
    print(f"Schwachstellenprüfung: {len(report['dependencies'])} Pakete, {len(entries)} Befunde, {len(open_findings)} offen")
    for entry in open_findings:
        print(f"  OFFEN {entry['package']} {entry['version']}: {entry['id']} → {entry['fixed_in']}")
    return 1 if open_findings else 0


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    build_sbom()
    sys.exit(audit())
