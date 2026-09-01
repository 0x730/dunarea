#!/usr/bin/env python3
"""Monitor de presiune pentru hostul Forge comun Danube + Portfolio."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import html
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from ops import offsite_backup as ob
except ModuleNotFoundError:  # invocare directă: python3 ops/runtime_hygiene.py
    import offsite_backup as ob


HOST_LABEL = "Forge 949568 · Danube + Portfolio"
DEFAULT_STATE = "/home/dunarea/dunarea.info/runtime-hygiene-status.json"
WARNING_PERCENT = 80
CRITICAL_PERCENT = 90
RECOVERY_PERCENT = 75
JOURNAL_WARNING_BYTES = 256 * 1024 * 1024
JOURNAL_CRITICAL_BYTES = 512 * 1024 * 1024
REALERT_SECONDS = 6 * 60 * 60


class HygieneError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class Observation:
    disk_percent: int
    inode_percent: int
    journal_bytes: int


@dataclasses.dataclass(frozen=True)
class AlertDecision:
    state: str
    severity: str
    send: str | None
    reasons: tuple[str, ...]


def _used_percent(total: int, free: int, available: int) -> int:
    used = total - free
    denominator = used + available
    if total <= 0 or used < 0 or denominator <= 0:
        raise HygieneError("host_capacity_unavailable")
    return min(100, math.ceil(used * 100 / denominator))


def _journal_bytes(output: str) -> int:
    match = re.search(
        r"([0-9]+(?:\.[0-9]+)?)\s*(?:(?P<unit>[KMGT])(?:i?B)?|B)(?=[.\s]|$)",
        output,
        re.I,
    )
    if not match:
        raise HygieneError("host_journal_unavailable")
    unit = (match.group("unit") or "").upper()
    multiplier = {
        "": 1,
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
        "T": 1024**4,
    }[unit]
    return math.ceil(float(match.group(1)) * multiplier)


def collect_observation() -> Observation:
    try:
        filesystem = os.statvfs("/")
        journal = subprocess.run(
            ["journalctl", "--disk-usage", "--quiet"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HygieneError("host_capacity_unavailable") from exc
    return Observation(
        disk_percent=_used_percent(
            filesystem.f_blocks, filesystem.f_bfree, filesystem.f_bavail
        ),
        inode_percent=_used_percent(
            filesystem.f_files, filesystem.f_ffree, filesystem.f_favail
        ),
        journal_bytes=_journal_bytes(journal.stdout),
    )


def _severity(observation: Observation) -> tuple[str, tuple[str, ...]]:
    critical = []
    warning = []
    if observation.disk_percent >= CRITICAL_PERCENT:
        critical.append(f"disk={observation.disk_percent}%")
    elif observation.disk_percent >= WARNING_PERCENT:
        warning.append(f"disk={observation.disk_percent}%")
    if observation.inode_percent >= CRITICAL_PERCENT:
        critical.append(f"inodes={observation.inode_percent}%")
    elif observation.inode_percent >= WARNING_PERCENT:
        warning.append(f"inodes={observation.inode_percent}%")
    if observation.journal_bytes >= JOURNAL_CRITICAL_BYTES:
        critical.append(f"journal={_bytes_label(observation.journal_bytes)}")
    elif observation.journal_bytes >= JOURNAL_WARNING_BYTES:
        warning.append(f"journal={_bytes_label(observation.journal_bytes)}")
    if critical:
        return "critical", tuple(critical + warning)
    if warning:
        return "warning", tuple(warning)
    return "healthy", ()


def _fully_recovered(observation: Observation) -> bool:
    return (
        observation.disk_percent < RECOVERY_PERCENT
        and observation.inode_percent < RECOVERY_PERCENT
        and observation.journal_bytes < JOURNAL_WARNING_BYTES
    )


def decide_alert(
    observation: Observation,
    previous: dict[str, object],
    now: dt.datetime,
) -> AlertDecision:
    severity, reasons = _severity(observation)
    active = previous.get("active") is True
    previous_severity = previous.get("severity")
    last_alert = previous.get("lastAlertAt")
    last_alert_at = None
    if isinstance(last_alert, str):
        try:
            last_alert_at = dt.datetime.fromisoformat(last_alert.replace("Z", "+00:00"))
        except ValueError:
            pass
        if last_alert_at is not None and (
            last_alert_at.tzinfo is None or last_alert_at > now
        ):
            last_alert_at = None

    if severity == "healthy":
        if active and _fully_recovered(observation):
            return AlertDecision("recovered", "healthy", "recovery", ())
        if active:
            return AlertDecision("recovering", "warning", None, ())
        return AlertDecision("healthy", "healthy", None, ())

    should_send = not active
    if severity == "critical" and previous_severity != "critical":
        should_send = True
    if last_alert_at is None or (now - last_alert_at).total_seconds() >= REALERT_SECONDS:
        should_send = True
    return AlertDecision(
        severity if should_send else f"{severity}_muted",
        severity,
        "incident" if should_send else None,
        reasons,
    )


def _bytes_label(value: int) -> str:
    if value >= 1024**3:
        return f"{value / 1024**3:.1f} GiB"
    if value >= 1024**2:
        return f"{value / 1024**2:.1f} MiB"
    if value >= 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value} B"


def _message(
    observation: Observation,
    decision: AlertDecision,
    checked_at: str,
    *,
    test: bool,
) -> dict[str, str]:
    if test:
        label = "test"
        headline = "Shared-host alert delivery test"
        action = "Operator-requested delivery test; no incident was opened."
        accent = "#2563eb"
        accent_soft = "#dbeafe"
    elif decision.send == "recovery":
        label = "recovered"
        headline = "Shared host recovered below the fleet threshold"
        action = "The warning lifecycle is closed after the 75% recovery gate."
        accent = "#0f766e"
        accent_soft = "#ccfbf1"
    elif decision.severity == "critical":
        label = "critical"
        headline = "Shared host reached critical pressure"
        action = "Inspect exact release and log paths before any further cleanup."
        accent = "#b91c1c"
        accent_soft = "#fee2e2"
    else:
        label = "warning"
        headline = "Shared host reached warning pressure"
        action = "Review release retention, process logs, and journal usage."
        accent = "#b45309"
        accent_soft = "#fef3c7"

    values = {
        "action": html.escape(action),
        "checked": html.escape(checked_at),
        "disk": f"{observation.disk_percent}%",
        "headline": html.escape(headline),
        "host": html.escape(HOST_LABEL),
        "inodes": f"{observation.inode_percent}%",
        "journal": html.escape(_bytes_label(observation.journal_bytes)),
        "label": html.escape(label.upper()),
    }
    text = "\n".join(
        (
            f"0x730 shared-host monitor: {label}.",
            f"Host: {HOST_LABEL}.",
            f"Disk: {values['disk']} (warning 80%, critical 90%, recovery below 75%).",
            f"Inodes: {values['inodes']} (warning 80%, critical 90%, recovery below 75%).",
            f"Journal: {values['journal']} (warning 256 MiB, critical 512 MiB).",
            f"Checked: {checked_at}.",
            action,
        )
    )
    body = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{values["headline"]}</title></head>
<body style="margin:0;background:#f3f5f4;color:#17221f;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td align="center" style="padding:32px 16px;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:600px;background:#fff;border:1px solid #dce4e1;border-radius:18px;overflow:hidden;">
      <tr><td style="padding:24px 28px;background:#102923;color:#fff;font-size:20px;font-weight:700;">0x730 <span style="float:right;font-size:11px;letter-spacing:1.2px;color:#b8d8cf;">SHARED HOST</span></td></tr>
      <tr><td style="padding:30px 28px 14px;"><div style="font-size:11px;font-weight:700;letter-spacing:1.2px;color:#60736d;">{values["host"]}</div><h1 style="margin:10px 0 14px;font-size:27px;line-height:1.2;">{values["headline"]}</h1><span style="display:inline-block;padding:7px 12px;border-radius:999px;background:{accent_soft};color:{accent};font-size:12px;font-weight:700;">{values["label"]}</span></td></tr>
      <tr><td style="padding:16px 28px 10px;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f7f9f8;border:1px solid #e1e8e5;border-radius:12px;"><tr><td style="padding:13px 16px;">Disk</td><td style="padding:13px 16px;font-weight:700;">{values["disk"]}</td></tr><tr><td style="padding:13px 16px;">Inodes</td><td style="padding:13px 16px;font-weight:700;">{values["inodes"]}</td></tr><tr><td style="padding:13px 16px;">Journal</td><td style="padding:13px 16px;font-weight:700;">{values["journal"]}</td></tr><tr><td style="padding:13px 16px;">Checked</td><td style="padding:13px 16px;">{values["checked"]}</td></tr></table></td></tr>
      <tr><td style="padding:16px 28px 30px;"><div style="padding:14px 16px;border-left:4px solid {accent};background:{accent_soft};font-size:14px;line-height:1.5;">{values["action"]}</div></td></tr>
    </table>
  </td></tr></table>
</body>
</html>"""
    return {"subject": f"[0x730] shared host {label}", "text": text, "html": body}


def _read_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    ob._secure_regular_file(path, "host_alert_state_insecure")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HygieneError("host_alert_state_invalid") from exc
    if not isinstance(value, dict):
        raise HygieneError("host_alert_state_invalid")
    return value


def _write_state(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, partial_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".partial",
    )
    partial = Path(partial_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, path)
        os.chmod(path, 0o600)
    finally:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=ob.DEFAULT_CONFIG)
    parser.add_argument("--state-file", default=DEFAULT_STATE)
    parser.add_argument("--alert", action="store_true")
    parser.add_argument("--test-alert", action="store_true")
    return parser


def _signal_handler(_signum, _frame):
    raise InterruptedError


def main(argv: list[str] | None = None) -> int:
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _signal_handler)
    args = _parser().parse_args(argv)
    state_path = Path(args.state_file)
    try:
        now = dt.datetime.now(dt.timezone.utc)
        checked_at = _iso(now)
        observation = collect_observation()
        previous = _read_state(state_path)
        decision = decide_alert(observation, previous, now)

        if args.test_alert:
            ob._send_email(
                ob.load_alert_config(args.config),
                _message(observation, decision, checked_at, test=True),
            )
            print(
                json.dumps(
                    {
                        "event": "host_hygiene",
                        "state": "test",
                        "testAlertAccepted": True,
                        **dataclasses.asdict(observation),
                    },
                    sort_keys=True,
                )
            )
            return 0

        alert_accepted = False
        if args.alert and decision.send:
            ob._send_email(
                ob.load_alert_config(args.config),
                _message(observation, decision, checked_at, test=False),
            )
            alert_accepted = True

        next_state = {
            **previous,
            "active": previous.get("active") is True,
            "severity": decision.severity,
            "state": decision.state,
            "checkedAt": checked_at,
            "observation": dataclasses.asdict(observation),
        }
        if decision.send == "incident" and alert_accepted:
            next_state.update(
                {
                    "active": True,
                    "lastAlertAt": checked_at,
                    "severity": decision.severity,
                }
            )
        elif decision.send == "recovery" and alert_accepted:
            next_state.update(
                {"active": False, "lastRecoveryAt": checked_at, "severity": "healthy"}
            )
        _write_state(state_path, next_state)

        print(
            json.dumps(
                {
                    "event": "host_hygiene",
                    "state": decision.state,
                    "severity": decision.severity,
                    "reasons": list(decision.reasons),
                    "alertAccepted": alert_accepted,
                    **dataclasses.asdict(observation),
                },
                sort_keys=True,
            )
        )
        return 0 if decision.state in {"healthy", "recovered"} else 1
    except (HygieneError, InterruptedError, ob.BackupError) as exc:
        reason = getattr(exc, "code", str(exc) or "host_hygiene_failed")
        print(
            json.dumps(
                {"event": "host_hygiene_failed", "reason": reason}, sort_keys=True
            )
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
