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
import datetime as dt
import html
import json
import re
import signal
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    from ops import offsite_backup as ob
except ModuleNotFoundError:  # invocare directă: python3 ops/source_freshness.py
    import offsite_backup as ob


DEFAULT_BASE_URL = "http://127.0.0.1:7300"
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_LISTED_PROBLEMS = 20
MAX_PROBLEM_CHARS = 160
# Un set neschimbat de probleme se repetă o dată pe săptămână, nu zilnic. O oră
# de toleranță: ștampila vine după colectare, iar o rulare cu câteva secunde mai
# rapidă peste o săptămână ar amâna altfel reamintirea cu încă o zi.
REMIND_AFTER_SECONDS = 7 * 24 * 60 * 60 - 60 * 60
ALERT_MEMORY_KEYS = ("alertedProblems", "incidentSince", "lastAlertAt", "lastRecoveryAt")

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
    "/api/danubehis/afluenti-romania",
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
        for name, task in (health.get("maintenance") or {}).items():
            if task.get("status") == "failed":
                failures.append(f"/api/health: maintenance {name}: failed")
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
        stale.extend(f"{path}: {problem}" for problem in _observation_problems(data))
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


def _observation_problems(node, path=""):
    """Read explicit observation policies; never infer age from arbitrary dates."""
    found = []
    if isinstance(node, dict):
        assessment = node.get("observation_freshness")
        if isinstance(assessment, dict):
            for item in assessment.get("problems", []):
                missing = f"; missing {item['missing']}" if item.get("missing") else ""
                found.append(
                    f"{path or '.'}: observation {item['station']}: {item['status']}"
                    f" ({item.get('observed_at')}; limit {assessment['max_age']} {assessment['unit']}"
                    f"{missing})"
                )
            if assessment.get("status") == "unknown" and not assessment.get("problems"):
                found.append(f"{path or '.'}: observation date unknown")
            # A collection summary already includes every problematic row.
            if "counts" in assessment:
                return found
        for key, value in node.items():
            if key != "observation_freshness":
                found.extend(_observation_problems(value, f"{path}.{key}" if path else key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_observation_problems(value, f"{path}[{i}]"))
    return found


def _problem_key(problem: str) -> str:
    """Condiția — ruta, stația și starea — fără detaliile care se schimbă la
    fiecare rulare: vârsta raportului și data unei observații întârziate.
    Linia completă rămâne în mesaj."""
    key = re.sub(r"raport anomalii vechi de \d+h", "raport anomalii vechi", problem)
    return re.sub(r"(: observation .+?: [a-z_]+) \(.*\)$", r"\1", key)


def _alert_time(value: object, now: dt.datetime) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed > now:
        return None
    return parsed


def decide_alert(
    state: str, problems: list[str], previous: dict, now: dt.datetime
) -> dict[str, object]:
    """Ce trimite `--alert` la această rulare, după ultima alertă reținută.

    Din 13.09.2026 același gol Gönyű a produs câte un e-mail pe zi, iar un
    incident nou ar fi sosit sub același subiect. Se trimite când apare o
    problemă nealertată, se repetă un set neschimbat după 7 zile și se trimite
    o singură revenire. Un set care se micșorează e reținut fără e-mail, ca o
    reapariție să fie din nou nouă. Memoria invalidă înseamnă alertă, nu tăcere.
    """
    alerted = previous.get("alertedProblems")
    if not isinstance(alerted, list) or not all(isinstance(p, str) for p in alerted):
        alerted = []
    memory = {key: previous[key] for key in ("lastAlertAt", "lastRecoveryAt")
              if isinstance(previous.get(key), str)}
    if state == "fresh":
        return {"send": "recovery" if alerted else None, "newProblems": [],
                "alertedProblems": [], **memory}

    # Începutul incidentului supraviețuiește reamintirilor și problemelor noi;
    # dispare la revenire. O valoare invalidă nu ajunge într-un subiect.
    if alerted and _alert_time(previous.get("incidentSince"), now):
        memory["incidentSince"] = previous["incidentSince"]
    keys = sorted({_problem_key(problem) for problem in problems})
    new = [key for key in keys if key not in alerted]
    last = _alert_time(previous.get("lastAlertAt"), now)
    if new:
        send = "incident"
    elif last is None or (now - last).total_seconds() >= REMIND_AFTER_SECONDS:
        send = "reminder"
    else:
        send = None
    return {"send": send, "newProblems": new, "alertedProblems": keys, **memory}


def _previous_alert_memory(status_file: str) -> dict:
    try:
        with open(status_file, encoding="utf-8") as handle:
            section = json.load(handle).get("sourceFreshness")
    except (OSError, ValueError, AttributeError):
        return {}
    return section if isinstance(section, dict) else {}


def _clipped(problems: list[str]) -> tuple[list[str], int]:
    listed = [item[:MAX_PROBLEM_CHARS] for item in problems[:MAX_LISTED_PROBLEMS]]
    return listed, max(0, len(problems) - len(listed))


def _freshness_message(
    state: str,
    problems: list[str],
    checked_at: str,
    *,
    test: bool,
    kind: str = "incident",
    new_problems: list[str] = (),
    since: str | None = None,
) -> dict[str, str]:
    """Mesajul e-mail. `kind`: incident (implicit), reminder sau recovery;
    `test` are prioritate și nu deschide sau închide nimic."""
    if test:
        kind = "test"
    label = f"test: {state}" if test else state
    if kind == "reminder":
        label = f"{state}, weekly reminder" + (f" (since {since})" if since else "")
    elif kind == "recovery":
        label = "recovered"
    # Când o parte persistă, problemele noi se văd din prima linie.
    new = set(new_problems)
    if new and any(_problem_key(item) not in new for item in problems):
        problems = [f"NEW · {item}" if _problem_key(item) in new else item
                    for item in problems]
    state_key = state.casefold()
    if state_key == "fresh":
        headline = ("All monitored sources are fresh again" if kind == "recovery"
                    else "All monitored sources are fresh")
        status_label = "Recovered" if kind == "recovery" else "Fresh"
        accent = "#0f766e"
        accent_soft = "#ccfbf1"
    elif state_key == "stale":
        headline = ("Some sources are still not fresh" if kind == "reminder" else
                    "Some sources have stale or undated observations or fallback snapshots")
        status_label = "Stale"
        accent = "#b45309"
        accent_soft = "#fef3c7"
    else:
        headline = ("The source freshness check is still failing" if kind == "reminder"
                    else "The source freshness check did not complete")
        status_label = state.replace("-", " ").strip().title() or "Failed"
        accent = "#b91c1c"
        accent_soft = "#fee2e2"

    listed, hidden = _clipped(problems)
    since_text = f" since {since}" if since else ""
    purpose, action, closing = {
        "test": (
            "Delivery test",
            "This operator-requested test did not raise an incident.",
            "This is the operator-requested delivery test.",
        ),
        "recovery": (
            "Recovery",
            "The freshness incident alerted earlier is closed. No action is needed.",
            "The earlier freshness incident is closed.",
        ),
        "reminder": (
            "Weekly reminder",
            f"These sources have not been fresh{since_text}. This reminder repeats"
            " weekly while no new problem appears; a new problem alerts at once.",
            f"Weekly reminder: these sources have not been fresh{since_text}.",
        ),
    }.get(kind, (
        "Freshness incident",
        "Inspect the listed endpoints on dunarea.info and the upstream "
        "providers; distinguish fallback delivery from old or undated observations.",
        "Treat non-fresh sources as a data incident.",
    ))
    text_lines = [
        f"Danube source freshness monitor: {label}.",
        f"Checked: {checked_at}.",
    ]
    text_lines += [f"- {item}" for item in listed]
    if hidden:
        text_lines.append(f"... and {hidden} more.")
    if not problems:
        text_lines.append("Every monitored endpoint reports fresh data.")
    text_lines.append(closing)

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
    previous = _previous_alert_memory(args.status_file)
    # Numai o rulare cu --alert decide și actualizează memoria alertelor;
    # testul, rulările manuale și un eșec o poartă mai departe neschimbată —
    # altfel un e-mail de revenire eșuat ar șterge incidentul și revenirea
    # nu ar mai fi trimisă niciodată.
    kept = {key: previous[key] for key in ALERT_MEMORY_KEYS if key in previous}
    try:
        base_url = _validated_base_url(args.base_url)
        evidence = collect_evidence(
            base_url, args.timeout, args.max_report_age_hours
        )
        problems = list(evidence["failures"]) + list(evidence["staleSources"])
        memory = dict(kept)
        if args.test_alert:
            message = _freshness_message(
                str(evidence["state"]), problems, str(evidence["checkedAt"]), test=True
            )
            ob._send_email(ob.load_alert_config(args.config), message)
            evidence["testAlertAccepted"] = True
        elif args.alert:
            now = ob._utcnow()
            decision = decide_alert(str(evidence["state"]), problems, previous, now)
            memory = {key: decision[key] for key in ALERT_MEMORY_KEYS if key in decision}
            if decision["send"]:
                message = _freshness_message(
                    str(evidence["state"]),
                    problems,
                    str(evidence["checkedAt"]),
                    test=False,
                    kind=decision["send"],
                    new_problems=decision["newProblems"],
                    since=memory.get("incidentSince"),
                )
                ob._send_email(ob.load_alert_config(args.config), message)
                evidence["alertAccepted"] = True
                if decision["send"] == "recovery":
                    memory["lastRecoveryAt"] = ob._iso(now)
                else:
                    memory["lastAlertAt"] = ob._iso(now)
                    memory.setdefault("incidentSince", ob._iso(now))
            evidence["alertDecision"] = decision["send"] or (
                "none" if evidence["state"] == "fresh" else "muted")
        evidence.update(memory)
        ob._write_status(args.status_file, "sourceFreshness", evidence)
        ob._emit("source_freshness", **evidence)
        return 0 if evidence["state"] == "fresh" else 1
    except (ob.BackupError, FreshnessError, InterruptedError) as exc:
        code = getattr(exc, "code", "freshness_interrupted")
        with contextlib.suppress(Exception):
            ob._write_status(
                args.status_file,
                "sourceFreshness",
                {"state": "failed", "at": ob._iso(ob._utcnow()), "reason": code, **kept},
            )
        ob._emit("source_freshness_failed", reason=code)
        return 1

if __name__ == "__main__":
    sys.exit(main())
