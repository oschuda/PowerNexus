#!/usr/bin/python3 -I
# -*- coding: utf-8 -*-
"""
Spielt ein signiertes Update ein. Installiert als /usr/local/sbin/usv-monitor-update, Aufruf als root:

    usv-monitor-update usv-monitor-22.15.0.tar.gz            # erwartet daneben .tar.gz.sig
    usv-monitor-update usv-monitor-22.15.0.tar.gz --dry-run  # prüft und zeigt die Änderungen

Ablauf: Signatur (Ed25519) prüfen, bevor das Archiv geöffnet wird; Inhalt gegen das Manifest prüfen; Version prüfen; Pakete
installieren; Dienst stoppen; Dateien tauschen, nicht mehr geführte Dateien in die Sicherung verschieben; Testwerkzeuge und andere
nicht festgelegte Pakete entfernen; Dienst starten und die neue Version abfragen. Schlägt etwas fehl, wird der alte Stand zurückgeholt.

Sicherheit:
- Nur Standardbibliothek und /usr/bin/openssl. Root führt nichts aus dem Programmordner aus, weil der Dienstbenutzer dort schreiben darf.
- Alles im Programmordner und in der venv läuft als Dienstbenutzer. Ein Symlink dort führt deshalb nie zu einem Schreibzugriff als root.
- Der öffentliche Schlüssel und der Versionsstand liegen außerhalb der Reichweite des Dienstes. Ein Update ändert den Schlüssel nie.

Exit-Codes: 0 eingespielt, 2 abgelehnt (nichts geändert), 3 fehlgeschlagen und zurückgerollt, 4 Zurückrollen fehlgeschlagen.
"""

import argparse
import fnmatch
import gzip
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

try:
    import fcntl
    import syslog
except ImportError:      # Windows, nur für die Tests
    fcntl = None
    syslog = None

PRODUCT = "usv-monitor"
MANIFEST = "MANIFEST.json"
STAGE = ".update-new"
BACKUPS = ".update-backups"
CHANGES = "CHANGES.json"
WORKER_FLAG = "--internal-worker"
BACKUPS_KEPT = 3
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
PIP_INSTALL_TIMEOUT = 6 * 3600
ED25519_SIGNATURE_BYTES = 64
VERSION = re.compile(r"^\d+\.\d+\.\d+$")

# Laufzeitdaten im Programmordner (erster Pfadteil): Ein Paket darf sie nicht liefern, die Bereinigung fasst sie nicht an
KEEP = (".venv", "data", "models", "pki", "tls", "config.json*", "hosts.yaml*", "policies.yaml*", "notifications.yaml*", "services.yaml*", "solar.yaml*",
        "secret.key", "users.json*", "initial-admin-password.txt", ".env", ".update-*")
CACHE_DIRS = {"__pycache__", ".pytest_cache", ".cache"}


class Rejected(Exception):
    """Paket abgelehnt, am System wurde nichts geändert"""


class UpdateFailed(Exception):
    """Einspielen fehlgeschlagen, alter Stand wiederhergestellt"""


class RollbackFailed(Exception):
    """Der alte Stand konnte nicht wiederhergestellt werden"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def version_tuple(version: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def is_protected(path: str) -> bool:
    if path.endswith(".example"):
        return False
    first = path.split("/")[0]
    return any(fnmatch.fnmatch(first, pattern) for pattern in KEEP)


def check_member_path(name: str) -> None:
    path = PurePosixPath(name)
    if not name or name != str(path) or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise Rejected(f"unzulässiger Pfad im Paket: {name!r}")


def read_package(data: bytes) -> Tuple[dict, Dict[str, bytes]]:
    """Liest das Archiv aus dem Speicher und prüft es gegen sein Manifest. Nur nach geprüfter Signatur aufrufen."""
    members: Dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar:
                if not member.isreg():
                    raise Rejected(f"nur normale Dateien erlaubt: {member.name!r}")
                check_member_path(member.name)
                if member.name in members:
                    raise Rejected(f"Datei doppelt im Paket: {member.name!r}")
                members[member.name] = tar.extractfile(member).read()
    except (tarfile.TarError, gzip.BadGzipFile, EOFError, OSError) as e:
        raise Rejected(f"Archiv nicht lesbar: {e}")

    try:
        manifest = json.loads(members.pop(MANIFEST))
        version, listed = manifest["version"], manifest["files"]
        product = manifest["product"]
    except (KeyError, ValueError, TypeError):
        raise Rejected("Manifest fehlt oder ist unvollständig")
    if product != PRODUCT:
        raise Rejected(f"Paket gehört zu einem anderen Produkt: {product!r}")
    if not isinstance(version, str) or not VERSION.match(version):
        raise Rejected(f"unzulässige Version: {version!r}")
    if set(listed) != set(members):
        raise Rejected(f"Paket und Manifest stimmen nicht überein: {sorted(set(listed) ^ set(members))[:5]}")
    for path, entry in listed.items():
        if sha256(members[path]) != entry.get("sha256"):
            raise Rejected(f"falsche Prüfsumme: {path}")
        if is_protected(path):
            raise Rejected(f"Paket darf Laufzeitdaten nicht ersetzen: {path}")
    return manifest, members


def verify_signature(archive: Path, signature: Path, keys: List[Path], openssl: str) -> str:
    """Name des Schlüssels, dessen Signatur gültig ist. Sonst Rejected."""
    if not keys:
        raise Rejected("kein öffentlicher Schlüssel hinterlegt")
    if signature.stat().st_size != ED25519_SIGNATURE_BYTES:
        raise Rejected("Signatur hat nicht das Format Ed25519")
    for key in keys:
        result = subprocess.run([openssl, "pkeyutl", "-verify", "-pubin", "-inkey", str(key), "-rawin", "-in", str(archive), "-sigfile", str(signature)],
                                capture_output=True, text=True)
        if result.returncode == 0 and "Signature Verified Successfully" in result.stdout:
            return key.name
    raise Rejected("Signatur ungültig: Paket verändert oder mit fremdem Schlüssel signiert")


def anchor_problem(path: str, stat_fn=os.stat) -> Optional[str]:
    """Ein Vertrauensanker taugt nur, wenn ihn und seine Ordner allein root ändern kann."""
    current = path
    while True:
        info = stat_fn(current)
        if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return f"{current} muss root gehören und darf nur für root schreibbar sein"
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def extra_packages(installed: List[str], lock_text: str) -> List[str]:
    """Installierte Pakete, die nicht in der festen Liste stehen (z. B. pytest)"""
    normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()
    locked = {normalize(name) for name in re.findall(r"^([A-Za-z0-9][\w.-]*)(?:\[[^\]]*\])?==", lock_text, re.M)}
    if not locked:
        raise UpdateFailed("feste Paketliste ist leer")
    return sorted(name for name in installed if normalize(name) not in locked and normalize(name) != "pip")


# ---------------------------------------------------------------- läuft als Dienstbenutzer

def compute_plan(root: Path, files: Dict[str, dict]) -> dict:
    remove, caches = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath).relative_to(root).as_posix()
        prefix = "" if base == "." else base + "/"
        descend = []
        for name in dirnames:
            rel = prefix + name
            if is_protected(rel):
                continue
            if (root / rel).is_symlink():
                remove.append(rel)
            elif name in CACHE_DIRS:
                caches.append(rel)
            else:
                descend.append(name)
        dirnames[:] = descend
        for name in filenames:
            rel = prefix + name
            if rel not in files and rel != MANIFEST and not is_protected(rel):
                remove.append(rel)
    add, change = [], []
    for rel, entry in files.items():
        target = root / rel
        if not target.exists() and not target.is_symlink():
            add.append(rel)
        elif target.is_symlink() or not target.is_file() or sha256(target.read_bytes()) != entry["sha256"]:
            change.append(rel)
    return {"add": sorted(add), "change": sorted(change), "remove": sorted(remove), "caches": sorted(caches)}


def move(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)


def remove_empty_dirs(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        path = Path(dirpath)
        rel = path.relative_to(root).as_posix()
        if rel != "." and not is_protected(rel) and not path.is_symlink() and not any(path.iterdir()):
            path.rmdir()


def worker_stage(root: Path, data: bytes) -> None:
    manifest, members = read_package(data)
    stage = root / STAGE
    shutil.rmtree(stage, ignore_errors=True)
    for rel, content in members.items():
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o755 if manifest["files"][rel].get("mode") == "755" else 0o644)
    (stage / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")


def worker_discard(root: Path) -> None:
    shutil.rmtree(root / STAGE, ignore_errors=True)


def worker_plan(root: Path) -> None:
    manifest = json.loads((root / STAGE / MANIFEST).read_text(encoding="utf-8"))
    print(json.dumps(compute_plan(root, manifest["files"])))


def worker_swap(root: Path, backup_name: str) -> None:
    stage = root / STAGE
    manifest = json.loads((stage / MANIFEST).read_text(encoding="utf-8"))
    plan = compute_plan(root, manifest["files"])
    backup = root / BACKUPS / backup_name
    backup.mkdir(parents=True)
    # Zuerst festhalten, was neu ist: Das Zurückrollen funktioniert dann auch nach einem Abbruch mittendrin
    (backup / CHANGES).write_text(json.dumps({"added": plan["add"] + ([] if (root / MANIFEST).exists() else [MANIFEST])}), encoding="utf-8")

    for rel in plan["remove"]:
        move(root / rel, backup / "files" / rel)
    for rel in plan["caches"]:
        shutil.rmtree(root / rel, ignore_errors=True)
    remove_empty_dirs(root)
    for rel in plan["change"] + plan["add"]:
        target = root / rel
        if target.is_symlink() or target.exists():
            move(target, backup / "files" / rel)
        move(stage / rel, target)
    if (root / MANIFEST).exists():
        shutil.copy2(root / MANIFEST, _fresh(backup / "files" / MANIFEST))
    os.replace(stage / MANIFEST, root / MANIFEST)
    shutil.rmtree(stage, ignore_errors=True)
    remove_empty_dirs(root)

    kept = sorted((root / BACKUPS).iterdir(), key=lambda entry: entry.stat().st_mtime)[:-BACKUPS_KEPT]
    for old in kept:
        shutil.rmtree(old, ignore_errors=True)


def _fresh(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def worker_rollback(root: Path, backup_name: str) -> None:
    backup = root / BACKUPS / backup_name
    shutil.rmtree(root / STAGE, ignore_errors=True)
    if not (backup / CHANGES).exists():
        return
    for rel in json.loads((backup / CHANGES).read_text(encoding="utf-8"))["added"]:
        (root / rel).unlink(missing_ok=True)
    stored = backup / "files"
    if stored.exists():
        for dirpath, dirnames, filenames in os.walk(stored):
            for name in filenames + [d for d in dirnames if (Path(dirpath) / d).is_symlink()]:
                source = Path(dirpath) / name
                move(source, root / source.relative_to(stored))
    shutil.rmtree(backup, ignore_errors=True)
    remove_empty_dirs(root)


def worker_main(argv: List[str]) -> int:
    command, root = argv[0], Path(argv[1])
    if command == "stage":
        worker_stage(root, sys.stdin.buffer.read())
    elif command == "plan":
        worker_plan(root)
    elif command == "swap":
        worker_swap(root, argv[2])
    elif command == "rollback":
        worker_rollback(root, argv[2])
    elif command == "discard":
        worker_discard(root)
    else:
        return 64
    return 0


# ---------------------------------------------------------------- läuft als root

@dataclass
class Settings:
    root: Path = Path("/opt/usv-monitor")
    keys_dir: Path = Path("/etc/usv-monitor-update/keys")
    state_dir: Path = Path("/var/lib/usv-monitor-update")
    unit_path: Path = Path("/etc/systemd/system/usv-monitor.service")
    self_path: Path = Path("/usr/local/sbin/usv-monitor-update")
    service: str = "usv-monitor"
    service_user: Optional[str] = "usv-monitor"
    health_url: str = "https://127.0.0.1:5443/api/version"
    health_timeout: float = 120
    openssl: str = "/usr/bin/openssl"
    require_root: bool = True


class Updater:
    def __init__(self, settings: Settings):
        self.s = settings

    # -- Hilfen, die in den Tests ersetzt werden
    def systemctl(self, *args: str) -> None:
        subprocess.run(["/usr/bin/systemctl", *args], check=True, capture_output=True, timeout=180)

    def as_service(self, command: List[str], data: Optional[bytes] = None, timeout: float = 900) -> str:
        """Führt einen Befehl als Dienstbenutzer aus, ohne dessen Umgebung und ohne weitere Gruppen"""
        user = self.s.service_user
        drop = {"user": user, "group": user, "extra_groups": [], "umask": 0o022} if user else {}
        env = {key: value for key, value in os.environ.items() if key in ("PATH", "SYSTEMROOT", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")}
        env.update(HOME=str(self.s.root), PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1")
        result = subprocess.run(command, input=data, capture_output=True, cwd=str(self.s.root), env=env, timeout=timeout, **drop)
        if result.returncode != 0:
            raise UpdateFailed(f"{Path(command[0]).name} {' '.join(command[1:4])}: {result.stderr.decode('utf-8', 'replace')[-1500:]}")
        return result.stdout.decode("utf-8", "replace")

    def worker(self, command: str, *args: str, data: Optional[bytes] = None) -> str:
        return self.as_service([sys.executable, "-I", os.path.abspath(__file__), WORKER_FLAG, command, str(self.s.root), *args], data=data)

    def pip(self, *args: str, timeout: float = 900) -> str:
        return self.as_service([str(self.s.root / ".venv" / "bin" / "python"), "-m", "pip", *args], timeout=timeout)

    def pip_install(self, lock: Path) -> None:
        # Ohne fertige Pakete (32-Bit-ARM) wird auf dem Gerät kompiliert, das dauert über eine Stunde
        self.pip("install", "--no-cache-dir", "--require-hashes", "-r", str(lock), timeout=PIP_INSTALL_TIMEOUT)

    def pip_remove_extras(self, lock: Path) -> List[str]:
        """lock ist die private Kopie aus dem geprüften Paket"""
        installed = [entry["name"] for entry in json.loads(self.pip("list", "--format=json"))]
        extras = extra_packages(installed, lock.read_text(encoding="utf-8"))
        if extras:
            self.pip("uninstall", "--yes", *extras)
        return extras

    def health(self, expected: str) -> bool:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE     # nur die eigene Version auf 127.0.0.1 abfragen
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
        deadline = time.monotonic() + self.s.health_timeout
        while time.monotonic() < deadline:
            try:
                with opener.open(self.s.health_url, timeout=5) as response:
                    if json.loads(response.read()).get("version") == expected:
                        return True
            except (OSError, ValueError, http.client.HTTPException):
                pass
            time.sleep(2)
        return False

    # -- Stand und Protokoll
    def state_file(self) -> Path:
        return self.s.state_dir / "state.json"

    def installed_version(self) -> Optional[str]:
        try:
            return json.loads(self.state_file().read_text(encoding="utf-8"))["version"]
        except (OSError, ValueError, KeyError):
            pass
        # Erstes signiertes Update: Es gibt noch keinen geschützten Stand
        try:
            found = re.search(r'__version__\s*=\s*"([\d.]+)"', (self.s.root / "version.py").read_text(encoding="utf-8"))
        except OSError:
            return None
        return found.group(1) if found else None

    def write_atomic(self, target: Path, content: bytes, mode: int) -> None:
        temporary = target.with_name(target.name + ".new")
        temporary.write_bytes(content)
        temporary.chmod(mode)
        os.replace(temporary, target)

    def record(self, outcome: str, version: str, detail: str = "") -> None:
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "outcome": outcome, "version": version, "detail": detail}
        with (self.s.state_dir / "history.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if syslog:
            syslog.openlog("usv-monitor-update")
            syslog.syslog(f"{outcome} {version} {detail}"[:500])

    # -- Ablauf
    def check_environment(self) -> None:
        if not self.s.require_root:
            return
        if os.geteuid() != 0:
            raise Rejected("muss als root laufen")
        for anchor in [str(self.s.keys_dir), os.path.abspath(__file__), *map(str, self.s.keys_dir.glob("*.pub"))]:
            problem = anchor_problem(anchor)
            if problem:
                raise Rejected(problem)

    def load(self, archive: Path, signature: Path) -> Tuple[bytes, dict, Dict[str, bytes], str]:
        """Prüft die Signatur an einer privaten Kopie: Was geprüft wurde, ist genau das, was danach gelesen wird"""
        if not archive.is_file():
            raise Rejected(f"Paket nicht gefunden: {archive}")
        if not signature.is_file():
            raise Rejected(f"Paket ohne Signatur abgelehnt, {signature} fehlt")
        if archive.stat().st_size > MAX_ARCHIVE_BYTES:
            raise Rejected("Paket zu groß")
        data = archive.read_bytes()
        with tempfile.TemporaryDirectory(dir=self.s.state_dir) as private:
            copy, copied_signature = Path(private) / "package", Path(private) / "signature"
            copy.write_bytes(data)
            copied_signature.write_bytes(signature.read_bytes())
            key = verify_signature(copy, copied_signature, sorted(self.s.keys_dir.glob("*.pub")), self.s.openssl)
        manifest, members = read_package(data)
        return data, manifest, members, key

    def check_version(self, new: str, allow_downgrade: bool) -> Optional[str]:
        current = self.installed_version()
        if current and not allow_downgrade and version_tuple(new) <= version_tuple(current):
            raise Rejected(f"Version {new} ist nicht neuer als {current}")
        return current

    def install_system_file(self, path: Path, members: Dict[str, bytes], name: str, mode: int) -> Optional[bytes]:
        """Ersetzt eine Systemdatei, wenn sie schon existiert. Gibt den alten Inhalt zurück, sofern sich etwas änderte."""
        if name not in members or not path.exists():
            return None
        old = path.read_bytes()
        if old == members[name]:
            return None
        self.write_atomic(path, members[name], mode)
        return old

    def run(self, archive: Path, signature: Optional[Path] = None, dry_run: bool = False, allow_downgrade: bool = False) -> dict:
        signature = signature or archive.with_name(archive.name + ".sig")
        self.check_environment()
        self.s.state_dir.mkdir(parents=True, exist_ok=True)
        self.s.state_dir.chmod(0o700)
        with (self.s.state_dir / "lock").open("w") as lock:
            if fcntl:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise Rejected("ein anderes Update läuft gerade")
            try:
                data, manifest, members, key = self.load(archive, signature)
                current = self.check_version(manifest["version"], allow_downgrade)
            except (Rejected, OSError) as e:
                self.record("abgelehnt", archive.name, str(e))
                raise Rejected(str(e))
            self.worker("stage", data=data)
            plan = json.loads(self.worker("plan"))
            if dry_run:
                self.worker("discard")
                return {"version": manifest["version"], "from": current, "key": key, "plan": plan, "dry_run": True}
            return self.apply(manifest, members, key, current, plan, sha256(data))

    def apply(self, manifest: dict, members: Dict[str, bytes], key: str, current: Optional[str], plan: dict, digest: str) -> dict:
        new = manifest["version"]
        backup_name = f"{current or 'unbekannt'}-auf-{new}-{int(time.time())}"
        stage_lock = self.s.root / STAGE / "requirements.lock"
        old_unit = None
        removed: List[str] = []
        try:
            if "requirements.lock" in members:
                self.pip_install(stage_lock)
            self.systemctl("stop", self.s.service)
            old_unit = self.install_system_file(self.s.unit_path, members, "deploy/usv-monitor.service", 0o644)
            if old_unit is not None:
                self.systemctl("daemon-reload")
            self.worker("swap", backup_name)
            if "requirements.lock" in members:
                with tempfile.TemporaryDirectory(dir=self.s.state_dir) as private:
                    locked = Path(private) / "requirements.lock"
                    locked.write_bytes(members["requirements.lock"])
                    removed = self.pip_remove_extras(locked)
            self.systemctl("start", self.s.service)
            if not self.health(new):
                raise UpdateFailed(f"Dienst meldet nach {self.s.health_timeout:.0f} s nicht Version {new}")
            self.install_system_file(self.s.self_path, members, "deploy/usv_monitor_update.py", 0o755)
        except (UpdateFailed, subprocess.SubprocessError, OSError) as e:
            self.record("fehlgeschlagen", new, str(e)[:400])
            try:
                self.roll_back(backup_name, old_unit, current)
            except (RollbackFailed, UpdateFailed, subprocess.SubprocessError, OSError) as failure:
                self.record("zurückrollen fehlgeschlagen", current or "?", str(failure)[:400])
                raise RollbackFailed(str(failure))
            raise UpdateFailed(str(e))

        self.write_atomic(self.state_file(), json.dumps({"version": new, "archive_sha256": digest, "key": key, "backup": backup_name}).encode(), 0o600)
        self.record("eingespielt", new, f"von {current}, Schlüssel {key}, {len(plan['remove'])} Dateien entfernt, Pakete entfernt: {removed}")
        return {"version": new, "from": current, "key": key, "plan": plan, "removed_packages": removed, "backup": backup_name}

    def roll_back(self, backup_name: str, old_unit: Optional[bytes], current: Optional[str]) -> None:
        self.systemctl("stop", self.s.service)
        self.worker("rollback", backup_name)
        if old_unit is not None:
            self.write_atomic(self.s.unit_path, old_unit, 0o644)
            self.systemctl("daemon-reload")
        package_problem = ""
        if (self.s.root / "requirements.lock").exists():
            try:
                self.pip_install(self.s.root / "requirements.lock")
            except (UpdateFailed, subprocess.SubprocessError, OSError) as e:      # z. B. kein Netz: Der Dienst soll trotzdem wieder laufen
                package_problem = f"; Pakete: {e}"
        self.systemctl("start", self.s.service)
        if current and not self.health(current):
            raise RollbackFailed(f"alter Stand {current} antwortet nicht{package_problem}")
        self.record("zurückgerollt", current or "?", backup_name)


def print_result(result: dict) -> None:
    plan = result["plan"]
    prefix = "PRÜFUNG, nichts geändert: " if result.get("dry_run") else "Eingespielt: "
    print(f"{prefix}{result['from']} → {result['version']} (Signatur gültig, Schlüssel {result['key']})")
    print(f"  neu {len(plan['add'])}, geändert {len(plan['change'])}, entfernt {len(plan['remove'])}")
    for rel in plan["remove"]:
        print(f"  entfernt: {rel}")
    if result.get("removed_packages"):
        print(f"  Pakete entfernt: {', '.join(result['removed_packages'])}")
    if result.get("backup"):
        print(f"  Sicherung: {BACKUPS}/{result['backup']}")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == WORKER_FLAG:
        return worker_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description="Spielt ein signiertes Evolved-PowerNexus-Update ein")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--signature", type=Path, help="Standard: <archiv>.sig")
    parser.add_argument("--dry-run", action="store_true", help="prüfen und Änderungen zeigen, nichts einspielen")
    parser.add_argument("--allow-downgrade", action="store_true", help="auch eine gleiche oder ältere Version einspielen")
    parser.add_argument("--health-url", default=Settings.health_url)
    args = parser.parse_args()
    updater = Updater(Settings(health_url=args.health_url))
    try:
        print_result(updater.run(args.archive, args.signature, args.dry_run, args.allow_downgrade))
        return 0
    except Rejected as e:
        print(f"ABGELEHNT: {e}", file=sys.stderr)
        return 2
    except UpdateFailed as e:
        print(f"FEHLGESCHLAGEN, alter Stand wiederhergestellt: {e}", file=sys.stderr)
        return 3
    except (RollbackFailed, subprocess.SubprocessError, OSError) as e:
        print(f"ZURÜCKROLLEN FEHLGESCHLAGEN, bitte von Hand prüfen: {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
