#!/usr/bin/env python3
"""Add just the managed printer share; validate before replacing smb.conf."""
import argparse
import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

SHARE = "formularkopf-klebchen"
BEGIN = "# BEGIN formularkopf-klebchen (installer)"
END = "# END formularkopf-klebchen (installer)"
BLOCK = f"""{BEGIN}
[{SHARE}]
    comment = T2med Formularkopf auf Brother QL-800
    path = /var/spool/samba/formularkopf-klebchen
    printable = yes
    printer name = {SHARE}
    printing = cups
    cups options = raw
    use client driver = yes
    browseable = yes
    guest ok = no
    read only = yes
    create mask = 0600
{END}
"""
BASE = """[global]
    workgroup = WORKGROUP
    server role = standalone server
    security = user
    map to guest = Never
    server min protocol = SMB2
    printing = cups
    printcap name = cups
    load printers = no
"""


def unmanaged(text):
    """Do not silently delete foreign or damaged configuration blocks."""
    if BEGIN not in text and END not in text:
        return text
    if text.splitlines().count(BEGIN) != 1 or text.splitlines().count(END) != 1:
        raise ValueError("Mehrdeutiger Installer-Block in smb.conf; bitte prüfen.")
    start, end = text.index(BEGIN), text.index(END)
    if start >= end:
        raise ValueError("Beschädigter Installer-Block in smb.conf.")
    return text[:start] + text[end + len(END):].lstrip("\r\n")


def candidate(text):
    clean = unmanaged(text)
    if re.search(r"^\s*\[\s*formularkopf-klebchen\s*\]", clean, re.I | re.M):
        raise ValueError("Die Freigabe formularkopf-klebchen existiert außerhalb des Installer-Blocks.")
    return clean.rstrip() + "\n\n" + BLOCK


def testparm(path, parameter=None):
    args = ["testparm", "-s"]
    if parameter:
        args += ["--parameter-name", parameter]
    result = subprocess.run(args + [str(path)], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("Samba-Konfiguration ist ungültig. Bitte lokal mit testparm -s prüfen.")
    return result.stdout.strip()


def preflight(path):
    text = path.read_text() if path.exists() else BASE
    clean = unmanaged(text)
    # Ask Samba to resolve includes as well, so foreign shares are not overwritten.
    fd, temporary = tempfile.mkstemp(prefix=".klebchen-check-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(clean)
        effective = testparm(temporary)
        if re.search(r"^\[formularkopf-klebchen\]", effective, re.I | re.M):
            raise ValueError("Eine fremde Freigabe formularkopf-klebchen existiert (ggf. in einer Include-Datei).")
        if testparm(temporary, "disable spoolss").lower() == "yes":
            raise ValueError("Samba hat 'disable spoolss = yes'; Druckfreigaben zuerst in Samba aktivieren.")
        if "active directory domain controller" in testparm(temporary, "server role").lower():
            raise ValueError("Samba-AD-Domain-Controller: Bitte einen separaten Druckserver verwenden.")
    finally:
        os.unlink(temporary)
    return text


def configure(path, new_server=False):
    text = preflight(path)
    updated = candidate(BASE if new_server else text)
    if path.exists() and updated == text:
        return None
    fd, temporary = tempfile.mkstemp(prefix=".klebchen-new-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(updated)
        testparm(temporary)
        backup = None
        if path.exists():
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = path.with_name(path.name + ".klebchen-backup-" + stamp)
            shutil.copy2(path, backup)
            original = path.stat()
            os.chmod(temporary, original.st_mode & 0o777)
            os.chown(temporary, original.st_uid, original.st_gid)
        else:
            os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        return backup
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--new-server", action="store_true")
    args = parser.parse_args()
    try:
        if args.check:
            preflight(args.path)
        else:
            backup = configure(args.path, new_server=args.new_server)
            if backup:
                print(f"Samba-Sicherung: {backup}")
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"FEHLER: {exc}\n")


if __name__ == "__main__":
    main()
