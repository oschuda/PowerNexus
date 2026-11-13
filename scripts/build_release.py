#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Baut und signiert ein Update-Paket aus dem Git-Tag der aktuellen Version

    python scripts/build_release.py --key <privater-schlüssel> --pub <öffentlicher-schlüssel>

Voraussetzungen: sauberes Arbeitsverzeichnis, HEAD trägt das Tag v<version>, Eintrag im CHANGELOG, Tests grün.
Schreibt dist/usv-monitor-<version>.tar.gz und .tar.gz.sig. Ablauf und Schlüsselverwaltung: docs/updates.md
"""

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Dict, Tuple

ROOT = Path(__file__).resolve().parent.parent
PRODUCT = "usv-monitor"
MANIFEST = "MANIFEST.json"

# Entwicklungsdateien und Nachweise kommen nicht ins Produkt
EXCLUDED_DIRS = ("tests/", "scripts/", "docs/compliance/", "data/")
EXCLUDED_FILES = {"pytest.ini", "requirements-dev.txt", ".gitignore", ".gitattributes"}
PRIVATE_KEY = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----\s+[A-Za-z0-9+/]{32}")
TEST_IMPORT = re.compile(rb"^\s*(?:import|from)\s+(?:pytest|aiosmtpd)\b", re.M)
# Auf dem Gerät ausgeführt oder gelesen: Ein CR macht z. B. die erste Zeile eines Skripts unbrauchbar
UNIX_ONLY = (".py", ".service", ".rules", ".lock", ".txt", ".sh", ".example")


class ReleaseError(Exception):
    pass


def belongs_in_release(path: str) -> bool:
    return path not in EXCLUDED_FILES and not path.startswith(EXCLUDED_DIRS)


def check_payload(files: Dict[str, Tuple[bytes, int]]) -> None:
    for path, (content, _) in files.items():
        if not belongs_in_release(path):
            raise ReleaseError(f"gehört nicht ins Paket: {path}")
        if path.endswith((".key", ".pem")) or Path(path).name == "secret.key" or PRIVATE_KEY.search(content):
            raise ReleaseError(f"Paket enthielte einen privaten Schlüssel: {path}")
        if path.endswith(".py") and TEST_IMPORT.search(content):
            raise ReleaseError(f"Programmcode importiert ein Testwerkzeug: {path}")
        if path.endswith(UNIX_ONLY) and b"\r\n" in content:
            raise ReleaseError(f"Windows-Zeilenenden: {path}")


def build_package(files: Dict[str, Tuple[bytes, int]], version: str, commit: str, commit_time: int) -> bytes:
    """Gleiche Eingaben ergeben das gleiche Paket"""
    check_payload(files)
    manifest = {"product": PRODUCT, "version": version, "commit": commit,
                "files": {path: {"sha256": hashlib.sha256(content).hexdigest(), "mode": "755" if mode & 0o100 else "644"}
                          for path, (content, mode) in sorted(files.items())}}
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        entries = [(MANIFEST, json.dumps(manifest, indent=1).encode("utf-8"), 0o644)]
        entries += [(path, content, 0o755 if mode & 0o100 else 0o644) for path, (content, mode) in sorted(files.items())]
        for name, content, mode in entries:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(content), mode, commit_time
            tar.addfile(info, io.BytesIO(content))
    packed = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=packed, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return packed.getvalue()


def find_openssl() -> str:
    found = shutil.which("openssl") or next((c for c in (r"C:\Program Files\Git\usr\bin\openssl.exe",) if Path(c).exists()), None)
    if not found:
        raise ReleaseError("openssl nicht gefunden")
    return found


def sign(openssl: str, key: Path, archive: Path, signature: Path) -> None:
    subprocess.run([openssl, "pkeyutl", "-sign", "-rawin", "-inkey", str(key), "-in", str(archive), "-out", str(signature)], check=True)


def verify(openssl: str, public_key: Path, archive: Path, signature: Path) -> bool:
    result = subprocess.run([openssl, "pkeyutl", "-verify", "-pubin", "-inkey", str(public_key), "-rawin", "-in", str(archive), "-sigfile", str(signature)],
                            capture_output=True, text=True)
    return result.returncode == 0 and "Signature Verified Successfully" in result.stdout


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def read_tree(commit: str) -> Dict[str, Tuple[bytes, int]]:
    archive = subprocess.run(["git", "archive", "--format=tar", commit], cwd=ROOT, check=True, capture_output=True).stdout
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            if member.isdir() or not belongs_in_release(member.name):
                continue
            if not member.isreg():
                raise ReleaseError(f"nur normale Dateien erlaubt: {member.name}")
            files[member.name] = (tar.extractfile(member).read(), member.mode)
    return files


def check_repository(version: str, commit: str) -> None:
    if git("status", "--porcelain"):
        raise ReleaseError("Arbeitsverzeichnis ist nicht sauber")
    try:
        tagged = git("rev-parse", f"v{version}^{{commit}}")
    except subprocess.CalledProcessError:
        raise ReleaseError(f"Tag v{version} fehlt")
    if tagged != commit:
        raise ReleaseError(f"HEAD ist nicht Tag v{version}")
    if f"## v{version}" not in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"):
        raise ReleaseError(f"kein Eintrag v{version} im CHANGELOG")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key", type=Path, required=True, help="privater Ed25519-Schlüssel, außerhalb des Repositorys")
    parser.add_argument("--pub", type=Path, required=True, help="zugehöriger öffentlicher Schlüssel, prüft die fertige Signatur")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args()

    try:
        commit = git("rev-parse", "HEAD")
        version = re.search(r'__version__\s*=\s*"([\d.]+)"', git("show", "HEAD:version.py")).group(1)
        check_repository(version, commit)
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "--tb=short"], cwd=ROOT, check=True)
        files = read_tree(commit)
        package = build_package(files, version, commit, int(git("show", "-s", "--format=%ct", commit)))
        openssl = find_openssl()
        args.out.mkdir(parents=True, exist_ok=True)
        archive = args.out / f"{PRODUCT}-{version}.tar.gz"
        signature = args.out / f"{archive.name}.sig"
        archive.write_bytes(package)
        sign(openssl, args.key, archive, signature)
        if not verify(openssl, args.pub, archive, signature):
            signature.unlink(missing_ok=True)
            raise ReleaseError("Signatur passt nicht zum öffentlichen Schlüssel")
    except (ReleaseError, subprocess.CalledProcessError) as e:
        print(f"ABGEBROCHEN: {e}", file=sys.stderr)
        return 1
    print(f"{archive}\n{signature}\n{len(files)} Dateien, SHA-256 {hashlib.sha256(package).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
