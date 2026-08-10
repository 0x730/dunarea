#!/usr/bin/env python3
"""Backup verificat al cache.db — stdlib, fără sqlite3 CLI.

De ce nu rețeta cu `sqlite3 ... ".backup"`: utilitarul de linie de comandă NU e
instalat implicit pe un server Forge/Hetzner minimal, deci cronul ar fi eșuat
tăcut, iar operatorul ar fi crezut că are backupuri. Aici folosim API-ul
`Connection.backup()` din biblioteca standard: e online (baza poate fi în uz),
conștient de WAL și vine cu interpretorul pe care aplicația îl folosește oricum.

Un `cp` pe baza vie e greșit din două motive: poate prinde fișierul la mijlocul
unei tranzacții, iar de când baza rulează în `journal_mode=WAL` lasă în urmă
conținutul necheckpointat din `cache.db-wal`.

Backupul nu e considerat reușit până nu e și verificat: integritate, structură
și numărul de rânduri permanente (arhiva locală, cea care nu se poate reface).

Folosire:
    python3 ops/backup.py                       # implicit: ../backups
    python3 ops/backup.py --dest /home/dunarea/dunarea.info/backups --keep-days 14
    python3 ops/backup.py --verify-only FIȘIER  # doar verifică un backup

Ieșire diferită de zero = backup nereușit; cronul trebuie să vă anunțe.
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SOURCE = os.path.join(BASE_DIR, "cache.db")
DEFAULT_DEST = os.path.join(os.path.dirname(BASE_DIR), "backups")

# Prefixele care NU se pot reface dintr-o sursă externă: dacă lipsesc din copie,
# backupul e inutil chiar dacă fișierul pare valid.
PERMANENT_PREFIXES = ("hist:", "inhga_day:", "grav2:", "glofas_cell:", "analiza_ai")


def _log(message):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"{stamp} {message}", flush=True)


def _permanent_rows(conn):
    total = 0
    for prefix in PERMANENT_PREFIXES:
        total += conn.execute(
            "SELECT COUNT(*) FROM cache WHERE key LIKE ?", (prefix + "%",)
        ).fetchone()[0]
    return total


def _stats(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        return {
            "rows": conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0],
            "permanent": _permanent_rows(conn),
        }
    finally:
        conn.close()


def verify(path, expected=None):
    """Verifică un backup ca și cum ar fi singura copie rămasă."""
    if not os.path.isfile(path):
        _log(f"EȘEC: {path} nu există")
        return False

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            _log(f"EȘEC: integrity_check = {integrity}")
            return False
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "cache" not in tables:
            _log(f"EȘEC: tabela `cache` lipsește (găsite: {sorted(tables)})")
            return False
        rows = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        permanent = _permanent_rows(conn)
        # o citire reală, nu doar metadate
        conn.execute("SELECT key, payload FROM cache LIMIT 1").fetchone()
    except sqlite3.DatabaseError as exc:
        _log(f"EȘEC: baza nu se poate citi — {exc}")
        return False
    finally:
        conn.close()

    _log(f"verificat: {rows} rânduri, {permanent} permanente, integritate ok")

    if expected:
        # Baza e vie: pot apărea rânduri noi între copiere și verificare, dar
        # NU pot dispărea cele permanente.
        if permanent < expected["permanent"]:
            _log(f"EȘEC: rânduri permanente lipsă — {permanent} < "
                 f"{expected['permanent']}")
            return False
        if rows < expected["rows"] * 0.9:
            _log(f"EȘEC: copia are mult mai puține rânduri — {rows} < "
                 f"{expected['rows']}")
            return False
    return True


def rotate(dest, keep_days):
    cutoff = date.today() - timedelta(days=keep_days)
    removed = 0
    for name in sorted(os.listdir(dest)):
        if not (name.startswith("cache-") and name.endswith(".db")):
            continue
        try:
            stamp = date.fromisoformat(name[len("cache-"):-len(".db")])
        except ValueError:
            continue
        if stamp < cutoff:
            os.remove(os.path.join(dest, name))
            removed += 1
    if removed:
        _log(f"rotație: {removed} backupuri mai vechi de {keep_days} zile șterse")


def backup(source, dest, keep_days):
    if not os.path.isfile(source):
        _log(f"EȘEC: baza sursă {source} nu există")
        return False

    os.makedirs(dest, exist_ok=True)
    # Backupul conține exact datele pe care cache.db le protejează cu 0600.
    os.chmod(dest, 0o700)

    target = os.path.join(dest, f"cache-{date.today().isoformat()}.db")
    partial = target + ".partial"
    for leftover in (partial, partial + "-wal", partial + "-shm"):
        if os.path.exists(leftover):
            os.remove(leftover)

    started = time.monotonic()
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=60)
    try:
        expected = {
            "rows": src.execute("SELECT COUNT(*) FROM cache").fetchone()[0],
            "permanent": _permanent_rows(src),
        }
        dst = sqlite3.connect(partial, timeout=60)
        try:
            os.chmod(partial, 0o600)
            # backup() e online și conștient de WAL: nu trebuie oprit serverul,
            # iar conținutul necheckpointat din -wal intră în copie.
            src.backup(dst)
            # Copia moștenește journal_mode=WAL de la sursă, iar orice
            # deschidere ulterioară (inclusiv verificarea de mai jos) ar
            # recrea -wal/-shm lângă ea. Un backup trebuie să fie UN fișier:
            # altfel cine îl mută cu `cp` pierde tăcut ce e în -wal.
            dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
    except sqlite3.DatabaseError as exc:
        _log(f"EȘEC: copierea nu a reușit — {exc}")
        return False
    finally:
        src.close()

    # Fișierele auxiliare rămase după închidere nu au ce căuta lângă backup.
    for leftover in (partial + "-wal", partial + "-shm"):
        if os.path.exists(leftover):
            os.remove(leftover)

    if not verify(partial, expected):
        _log("EȘEC: copia nu a trecut verificarea; nu o promovez")
        os.remove(partial)
        return False

    os.replace(partial, target)      # promovare atomică: nu există backup pe jumătate
    os.chmod(target, 0o600)
    size_mb = os.path.getsize(target) / (1024 * 1024)
    _log(f"OK: {target} ({size_mb:.1f} MB) în {time.monotonic() - started:.1f}s")

    rotate(dest, keep_days)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--dest", default=DEFAULT_DEST)
    parser.add_argument("--keep-days", type=int, default=14)
    parser.add_argument("--verify-only", metavar="FIȘIER",
                        help="verifică un backup existent și iese")
    args = parser.parse_args()

    if args.verify_only:
        return 0 if verify(args.verify_only) else 1
    return 0 if backup(args.source, args.dest, args.keep_days) else 1


if __name__ == "__main__":
    sys.exit(main())
