"""Observation-age policy, independent of cache delivery and provider uptime.

Thresholds are monitor tolerances, not guarantees made by the providers.
Daily dates retain their source calendar day; timestamps retain their offset.
"""

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Bucharest")
# collection, observation field, maximum age, unit
POLICIES = {
    "afdj": ("statii", "actualizat", 2, "days"),
    "hydroinfo": ("statii", "data", 2, "days"),
    "danubehis": ("statii", "data", 2, "days"),
    "hidmet": (None, "data", 2, "days"),
    "inhga": (None, "data_buletin", 1, "days"),
    "glofas": (None, "date", 2, "days"),
    "danubehis_afluenti_romania": ("sections", "latest.date", 3, "days"),
    "sen": (None, "actualizat", 30 * 60, "seconds"),
    "danubeportal": ("mire", "masurat_utc", 48 * 3600, "seconds"),
    "pegelonline": ("stations", "ts", 6 * 3600, "seconds"),
}


def _value(row, field):
    for part in field.split("."):
        row = row.get(part) if isinstance(row, dict) else None
    return row


def observation(value, limit, unit, now, *, source):
    result = {"observed_at": value, "max_age": limit, "unit": unit,
              "status": "unknown", "age": None}
    try:
        if unit == "days":
            observed = datetime.fromisoformat(value).date()
            age = (now.astimezone(TZ).date() - observed).days
        else:
            if source == "sen":
                observed = datetime.strptime(value, "%y/%m/%d %H:%M:%S").replace(tzinfo=TZ)
            else:
                observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if observed.tzinfo is None:
                    # DanubeSTREAM explicitly names this field measure_date_utc.
                    if source != "danubeportal":
                        return result
                    observed = observed.replace(tzinfo=ZoneInfo("UTC"))
            age = int((now - observed).total_seconds())
        result["age"] = age
        if age >= 0:
            result["status"] = "stale" if age > limit else "fresh"
    except (ValueError, TypeError, AttributeError, OverflowError):
        pass
    return result


def annotate(source, payload, *, now=None):
    """Copy before annotation: never modify cached data or historical archives."""
    if source not in POLICIES:
        return payload
    now = now or datetime.now(TZ)
    out = deepcopy(payload)
    collection, field, limit, unit = POLICIES[source]
    rows = out.get(collection, []) if collection else [out]
    if source == "pegelonline":
        measurements = []
        for station in rows:
            for parameter in ("q", "w"):
                if isinstance(station.get(parameter), dict):
                    measurements.append((station[parameter], f"{station.get('name', '?')} {parameter.upper()}"))
        rows = measurements
    else:
        rows = [(row, None) for row in rows]
    items = []
    for row, label in rows:
        if not isinstance(row, dict):
            continue
        assessment = observation(_value(row, field), limit, unit, now, source=source)
        if collection:
            row["observation_freshness"] = assessment
        items.append({"station": label or row.get("statie") or row.get("station") or row.get("id") or source,
                      **assessment})
    counts = {s: sum(item["status"] == s for item in items)
              for s in ("fresh", "stale", "unknown")}
    state = ("partial_stale" if counts["stale"] and counts["fresh"] else
             "stale" if counts["stale"] else
             "unknown" if counts["unknown"] or not items else "fresh")
    out["observation_freshness"] = {
        "status": state, "checked_at": now.isoformat(timespec="seconds"),
        "max_age": limit, "unit": unit, "counts": counts,
        "problems": [item for item in items if item["status"] != "fresh"],
    }
    return out
