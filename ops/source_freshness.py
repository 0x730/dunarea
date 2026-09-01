#!/usr/bin/env python3
"""Monitor zilnic de prospețime a surselor servite de aplicație.

Aplicația își evaluează singură sursele: fiecare payload public poartă
``stale: true`` când servește un snapshot de rezervă în locul unui fetch
reușit, ``/api/overview`` enumeră erorile surselor de bază, iar
``/api/health`` raportează vârsta raportului de anomalii. Scriptul citește
aceste auto-evaluări de pe instanța locală (nu prin Cloudflare, ca să vadă
procesul real, nu cache-ul de margine), le agregă și:

1. iese non-zero când ceva nu e proaspăt — Forge marchează job-ul eșuat;
2. cu ``--alert`` trimite un e-mail prin același canal Cloudflare ca
   monitorul de backup, enumerând exact sursele vinovate;
3. scrie dovada în fișierul de status, lângă secțiunile de backup.

Lecția care a impus scriptul: Hydroinfo a servit o săptămână snapshotul din
25.08.2026, corect marcat ``stale: true`` în API, și nimeni nu a aflat —
singura alertă existentă privea backup-urile, nu sursele.
"""

from __future__ import annotations

import argparse
import contextlib
import html
import json
import re
import signal
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from ops import offsite_backup as ob
except ModuleNotFoundError:  # invocare directă: python3 ops/source_freshness.py
    import offsite_backup as ob


DEFAULT_BASE_URL = "http://127.0.0.1:7300"
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_LISTED_PROBLEMS = 20
MAX_PROBLEM_CHARS = 160

# Rutele cu surse vii care își declară singure prospețimea. Endpointurile
# doar-istorice sau cele condiționate de tokenuri opționale nu apar aici:
# absența lor nu e un incident de prospețime.
ENDPOINTS = (
    "/api/overview",
    "/api/afdj",
    "/api/danubeportal",
    "/api/hidmet",
    "/api/hydroinfo",
    "/api/danubehis",
    "/api/sen",
    "/api/inhga",
    "/api/hydroweb",
    "/api/opera",
    "/api/edo",
    "/api/gravimetrie",
    "/api/ape-mici",
    "/api/romania",
)


class FreshnessError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _validated_base_url(value: str) -> str:
    parts = urllib.parse.urlsplit(value)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise FreshnessError("freshness_base_url_invalid")
    return f"{parts.scheme}://{parts.netloc}"


def _public_reason(exc: Exception) -> str:
    """Categorie stabilă pentru un eșec de citire; fără detalii upstream."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, (json.JSONDecodeError, UnicodeError)):
        return "raspuns_neparsabil"
    if isinstance(exc, FreshnessError):
        return exc.code
    return "indisponibil"


def _fetch_json(base_url: str, path: str, timeout: int):
    request = urllib.request.Request(
        base_url + path, headers={"User-Agent": "danube-source-freshness/1.0"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES:
        raise FreshnessError("freshness_response_too_large")
    return json.loads(body)


def _stale_paths(node, path: str = "") -> list[str]:
    """Toate căile JSON pe care aplicația a marcat ``stale: true``."""
    found: list[str] = []
    if isinstance(node, dict):
        for key in sorted(node):
            value = node[key]
            if key == "stale":
                if value is True:
                    found.append(path or ".")
                continue
            child = f"{path}.{key}" if path else key
            found.extend(_stale_paths(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_stale_paths(value, f"{path}[{index}]"))
    return found


def collect_evidence(
    base_url: str, timeout: int, max_report_age_hours: int
) -> dict[str, object]:
    stale: list[str] = []
    failures: list[str] = []
    report_age = None

    try:
        health = _fetch_json(base_url, "/api/health", timeout)
        if health.get("status") != "ok":
            failures.append("/api/health: status neasteptat")
        if health.get("warmup_done") is not True:
            stale.append("/api/health: warmup neterminat")
        age = health.get("anomaly_report_age_s")
        if isinstance(age, (int, float)) and age >= 0:
            report_age = int(age)
            if report_age > max_report_age_hours * 3600:
                stale.append(
                    f"/api/health: raport anomalii vechi de {report_age // 3600}h"
                    f" (limita {max_report_age_hours}h)"
                )
        else:
            failures.append("/api/health: anomaly_report_age_s lipsa")
    except Exception as exc:  # aplicația căzută e cel mai mare incident
        failures.append(f"/api/health: {_public_reason(exc)}")

    for path in ENDPOINTS:
        try:
            data = _fetch_json(base_url, path, timeout)
        except Exception as exc:
            failures.append(f"{path}: {_public_reason(exc)}")
            continue
        for where in _stale_paths(data):
            suffix = "stale" if where == "." else f"stale la {where}"
            stale.append(f"{path}: {suffix}")
        if path == "/api/overview" and isinstance(data, dict):
            errors = data.get("errors")
            if isinstance(errors, dict):
                for name in sorted(errors):
                    failures.append(f"/api/overview: sursa {name}: {errors[name]}")

    state = "failed" if failures else ("stale" if stale else "fresh")
    evidence: dict[str, object] = {
        "state": state,
        "checkedAt": ob._iso(ob._utcnow()),
        "baseUrl": base_url,
        "endpointsChecked": len(ENDPOINTS) + 1,
        "staleSources": stale,
        "failures": failures,
    }
    if report_age is not None:
        evidence["reportAgeSeconds"] = report_age
    return evidence


def _clipped(problems: list[str]) -> tuple[list[str], int]:
    listed = [item[:MAX_PROBLEM_CHARS] for item in problems[:MAX_LISTED_PROBLEMS]]
    return listed, max(0, len(problems) - len(listed))


def _freshness_message(
    state: str, problems: list[str], checked_at: str, *, test: bool
) -> dict[str, str]:
    label = f"test: {state}" if test else state
    state_key = state.casefold()
    if state_key == "fresh":
        headline = "All monitored sources are fresh"
        status_label = "Fresh"
        accent = "#0f766e"
        accent_soft = "#ccfbf1"
    elif state_key == "stale":
        headline = "Some sources serve fallback snapshots"
        status_label = "Stale"
        accent = "#b45309"
        accent_soft = "#fef3c7"
    else:
        headline = "The source freshness check did not complete"
        status_label = state.replace("-", " ").strip().title() or "Failed"
        accent = "#b91c1c"
        accent_soft = "#fee2e2"

    listed, hidden = _clipped(problems)
    purpose = "Delivery test" if test else "Freshness incident"
    action = (
        "This operator-requested test did not raise an incident."
        if test
        else "Inspect the listed endpoints on dunarea.info and the upstream "
        "providers; stale means the app serves its last good snapshot."
    )
    text_lines = [
        f"Danube source freshness monitor: {label}.",
        f"Checked: {checked_at}.",
    ]
    text_lines += [f"- {item}" for item in listed]
    if hidden:
        text_lines.append(f"... and {hidden} more.")
    if not problems:
        text_lines.append("Every monitored endpoint reports fresh data.")
    text_lines.append(
        "This is the operator-requested delivery test."
        if test
        else "Treat non-fresh sources as a data incident."
    )

    if listed:
        rows = "".join(
            '<li style="margin:0 0 6px;font-family:Consolas,Menlo,monospace;'
            'font-size:12px;line-height:1.5;word-break:break-all;">'
            f"{html.escape(item)}</li>"
            for item in listed
        )
        if hidden:
            rows += (
                '<li style="margin:0;font-size:12px;color:#60736d;">'
                f"... and {hidden} more</li>"
            )
        problems_html = f'<ul style="margin:0;padding-left:18px;">{rows}</ul>'
    else:
        problems_html = (
            '<div style="font-size:13px;color:#17221f;">'
            "Every monitored endpoint reports fresh data.</div>"
        )

    escaped = {
        "action": html.escape(action),
        "checked_at": html.escape(checked_at),
        "headline": html.escape(headline),
        "purpose": html.escape(purpose.upper()),
        "status": html.escape(status_label.upper()),
    }
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped["headline"]}</title>
</head>
<body style="margin:0;background:#f3f5f4;color:#17221f;font-family:Arial,Helvetica,sans-serif;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;">
    {escaped["headline"]}.
  </div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f3f5f4;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:600px;background:#ffffff;border:1px solid #dce4e1;border-radius:18px;overflow:hidden;">
          <tr>
            <td style="padding:24px 28px;background:#102923;color:#ffffff;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                <tr>
                  <td style="font-size:21px;font-weight:700;letter-spacing:-0.3px;">Danube</td>
                  <td align="right" style="font-size:11px;font-weight:700;letter-spacing:1.4px;color:#b8d8cf;">SOURCE MONITOR</td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:32px 28px 14px;">
              <div style="font-size:11px;font-weight:700;letter-spacing:1.3px;color:#60736d;">{escaped["purpose"]}</div>
              <h1 style="margin:10px 0 14px;font-size:28px;line-height:1.2;letter-spacing:-0.7px;color:#17221f;">{escaped["headline"]}</h1>
              <span style="display:inline-block;padding:7px 12px;border-radius:999px;background:{accent_soft};color:{accent};font-size:12px;font-weight:700;letter-spacing:0.6px;">{escaped["status"]}</span>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 28px 10px;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:separate;border-spacing:0;background:#f7f9f8;border:1px solid #e1e8e5;border-radius:12px;">
                <tr>
                  <td style="width:118px;padding:15px 16px;border-bottom:1px solid #e1e8e5;color:#60736d;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">Sources</td>
                  <td style="padding:15px 16px;border-bottom:1px solid #e1e8e5;">{problems_html}</td>
                </tr>
                <tr>
                  <td style="padding:15px 16px;color:#60736d;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">Checked</td>
                  <td style="padding:15px 16px;color:#17221f;font-size:13px;">{escaped["checked_at"]}</td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 28px 30px;">
              <div style="padding:14px 16px;border-left:4px solid {accent};background:{accent_soft};color:#293b36;font-size:14px;line-height:1.55;">{escaped["action"]}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:18px 28px;background:#edf2f0;color:#60736d;font-size:11px;line-height:1.5;">
              Automated source-freshness evidence from Danube · 0x730
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
    return {
        "subject": f"[Danube] data sources {label}",
        "text": "\n".join(text_lines),
        "html": body,
    }


def _load_alert_config(path: str | Path) -> ob.AlertConfig:
    """Refolosește blocul de alertă din env-ul de backup; nu cere cheile S3."""
    values = ob._read_config_file(Path(path))
    if any(values.get(key) for key in ob.LEGACY_ALERT_KEYS):
        raise ob.BackupError("backup_alert_configuration_legacy")
    if not all(values.get(key) for key in ob.ALERT_KEYS):
        raise ob.BackupError("backup_alert_configuration_missing")
    account_id = values["DANUBE_BACKUP_CLOUDFLARE_ACCOUNT_ID"]
    if not re.fullmatch(r"[0-9a-f]{32}", account_id):
        raise ob.BackupError("backup_alert_account_id_invalid")
    return ob.AlertConfig(
        account_id=account_id,
        api_token=values["DANUBE_BACKUP_CLOUDFLARE_API_TOKEN"],
        sender=values["DANUBE_BACKUP_ALERT_FROM"],
        reply_to=values["DANUBE_BACKUP_ALERT_REPLY_TO"],
        recipient=values["DANUBE_BACKUP_ALERT_TO"],
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=ob.DEFAULT_CONFIG)
    parser.add_argument("--status-file", default=ob.DEFAULT_STATUS)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--max-report-age-hours", type=int, default=12)
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
    try:
        base_url = _validated_base_url(args.base_url)
        evidence = collect_evidence(
            base_url, args.timeout, args.max_report_age_hours
        )
        problems = list(evidence["failures"]) + list(evidence["staleSources"])
        if args.test_alert or (args.alert and evidence["state"] != "fresh"):
            message = _freshness_message(
                str(evidence["state"]),
                problems,
                str(evidence["checkedAt"]),
                test=args.test_alert,
            )
            ob._send_email(_load_alert_config(args.config), message)
            evidence["testAlertAccepted" if args.test_alert else "alertAccepted"] = True
        ob._write_status(args.status_file, "sourceFreshness", evidence)
        ob._emit("source_freshness", **evidence)
        return 0 if evidence["state"] == "fresh" else 1
    except (ob.BackupError, FreshnessError, InterruptedError) as exc:
        code = getattr(exc, "code", "freshness_interrupted")
        with contextlib.suppress(Exception):
            ob._write_status(
                args.status_file,
                "sourceFreshness",
                {"state": "failed", "at": ob._iso(ob._utcnow()), "reason": code},
            )
        ob._emit("source_freshness_failed", reason=code)
        return 1


if __name__ == "__main__":
    sys.exit(main())
