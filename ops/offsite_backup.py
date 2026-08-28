#!/usr/bin/env python3
"""Backup SQLite Danube verificat, criptat local și păstrat off-box.

Fluxul de scriere compune obligatoriu cu ``ops/backup.py``:

1. ``backup.Connection.backup()`` produce copia WAL-aware de pe disc;
2. aceeași copie este verificată din nou și trebuie să conțină arhivă permanentă;
3. OpenSSL o criptează AES-256-CTR, iar un HMAC-SHA-256 independent autentifică
   antetul, contextul obiectului și fiecare byte criptat;
4. obiectul privat este încărcat în Spaces sub prefixul fix ``database/danube/``;
5. un HEAD și un GET autentificate verifică mărimea și SHA-256, iar un GET
   neautentificat dovedește că obiectul nu este public;
6. abia apoi retenția poate șterge obiecte Danube vechi, strict parseabile.

Nici restaurarea de probă nu atinge producția: cel mai nou obiect este
descărcat și decriptat numai într-un director temporar 0700, copia SQLite 0600
este verificată, apoi întregul director este șters și absența lui este probată.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from ops import backup as sqlite_backup
except ModuleNotFoundError:  # invocare directă: python3 ops/offsite_backup.py
    import backup as sqlite_backup


OBJECT_PREFIX = "database/danube/"
OBJECT_RE = re.compile(
    r"^database/danube/(?P<year>\d{4})/(?P<month>\d{2})/"
    r"danube-(?P<stamp>\d{8}T\d{6}Z)\.sqlite3\.enc$"
)
DEFAULT_CONFIG = "/home/dunarea/dunarea.info/data/keys/offsite-backup.env"
DEFAULT_STATUS = "/home/dunarea/dunarea.info/backup-status.json"
DEFAULT_STAGING = "/home/dunarea/dunarea.info/backup-staging"
DEFAULT_BACKUPS = "/home/dunarea/dunarea.info/backups"
DEFAULT_SOURCE = "/home/dunarea/dunarea.info/cache.db"
MAX_LIST_PAGES = 100
ENVELOPE_MAGIC = b"DANUBEBK"
ENVELOPE_VERSION = 1
ENVELOPE_HEADER_BYTES = len(ENVELOPE_MAGIC) + 1 + 16 + 32
ENVELOPE_TAG_BYTES = 32

STORAGE_KEYS = (
    "DANUBE_BACKUP_S3_ENDPOINT",
    "DANUBE_BACKUP_S3_REGION",
    "DANUBE_BACKUP_S3_BUCKET",
    "DANUBE_BACKUP_S3_ACCESS_KEY_ID",
    "DANUBE_BACKUP_S3_SECRET_ACCESS_KEY",
    "DANUBE_BACKUP_ENCRYPTION_KEY_FILE",
)
ALERT_KEYS = (
    "DANUBE_BACKUP_CLOUDFLARE_ACCOUNT_ID",
    "DANUBE_BACKUP_CLOUDFLARE_API_TOKEN",
    "DANUBE_BACKUP_ALERT_FROM",
    "DANUBE_BACKUP_ALERT_REPLY_TO",
    "DANUBE_BACKUP_ALERT_TO",
)
LEGACY_ALERT_KEYS = (
    "DANUBE_BACKUP_TEM_SECRET_KEY",
    "DANUBE_BACKUP_TEM_PROJECT_ID",
    "DANUBE_BACKUP_TEM_REGION",
)


class BackupError(RuntimeError):
    """Eroare publicabilă numai printr-un reason code stabil."""

    def __init__(self, code: str, *, alert_accepted: bool = False):
        super().__init__(code)
        self.code = code
        self.alert_accepted = alert_accepted


@dataclasses.dataclass(frozen=True)
class AlertConfig:
    account_id: str
    api_token: str
    sender: str
    reply_to: str
    recipient: str


@dataclasses.dataclass(frozen=True)
class Config:
    endpoint: str
    region: str
    bucket: str
    access_key: str
    secret_key: str
    encryption_key_file: Path
    alert: AlertConfig | None


@dataclasses.dataclass(frozen=True)
class RemoteObject:
    key: str
    timestamp: dt.datetime
    size: int | None = None


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _emit(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True), flush=True)


def _secure_regular_file(path: Path, code: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise BackupError(code) from exc
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
        raise BackupError(code)
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise BackupError(code)


def _read_config_file(path: Path) -> dict[str, str]:
    _secure_regular_file(path, "backup_config_insecure_or_missing")
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise BackupError("backup_config_unreadable") from exc
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise BackupError("backup_config_invalid")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise BackupError("backup_config_invalid")
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if not value or any(char in value for char in "\r\n\x00"):
            raise BackupError("backup_config_invalid")
        values[key] = value
    return values


def load_config(path: str | Path) -> Config:
    values = _read_config_file(Path(path))
    if any(not values.get(key) for key in STORAGE_KEYS):
        raise BackupError("backup_storage_configuration_missing")

    endpoint = urllib.parse.urlsplit(values["DANUBE_BACKUP_S3_ENDPOINT"])
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
        or endpoint.path not in ("", "/")
    ):
        raise BackupError("backup_s3_endpoint_invalid")
    region = values["DANUBE_BACKUP_S3_REGION"]
    bucket = values["DANUBE_BACKUP_S3_BUCKET"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,61}[a-z0-9]", bucket):
        raise BackupError("backup_s3_bucket_invalid")
    if not re.fullmatch(r"[a-z0-9-]{2,32}", region):
        raise BackupError("backup_s3_region_invalid")

    key_file = Path(values["DANUBE_BACKUP_ENCRYPTION_KEY_FILE"])
    if not key_file.is_absolute():
        raise BackupError("backup_encryption_key_path_invalid")
    _secure_regular_file(key_file, "backup_encryption_key_insecure_or_missing")
    try:
        encryption_secret = key_file.read_bytes().strip()
    except OSError as exc:
        raise BackupError("backup_encryption_key_unreadable") from exc
    if len(encryption_secret) < 32 or b"\x00" in encryption_secret:
        raise BackupError("backup_encryption_key_invalid")
    if encryption_secret.decode("utf-8", "ignore") in {
        values["DANUBE_BACKUP_S3_ACCESS_KEY_ID"],
        values["DANUBE_BACKUP_S3_SECRET_ACCESS_KEY"],
    }:
        raise BackupError("backup_encryption_key_reused")

    if any(values.get(key) for key in LEGACY_ALERT_KEYS):
        raise BackupError("backup_alert_configuration_legacy")

    configured_alert_values = [bool(values.get(key)) for key in ALERT_KEYS]
    if any(configured_alert_values) and not all(configured_alert_values):
        raise BackupError("backup_alert_configuration_partial")
    alert = None
    if all(configured_alert_values):
        account_id = values["DANUBE_BACKUP_CLOUDFLARE_ACCOUNT_ID"]
        if not re.fullmatch(r"[0-9a-f]{32}", account_id):
            raise BackupError("backup_alert_account_id_invalid")
        alert = AlertConfig(
            account_id=account_id,
            api_token=values["DANUBE_BACKUP_CLOUDFLARE_API_TOKEN"],
            sender=values["DANUBE_BACKUP_ALERT_FROM"],
            reply_to=values["DANUBE_BACKUP_ALERT_REPLY_TO"],
            recipient=values["DANUBE_BACKUP_ALERT_TO"],
        )

    return Config(
        endpoint=f"https://{endpoint.netloc}",
        region=region,
        bucket=bucket,
        access_key=values["DANUBE_BACKUP_S3_ACCESS_KEY_ID"],
        secret_key=values["DANUBE_BACKUP_S3_SECRET_ACCESS_KEY"],
        encryption_key_file=key_file,
        alert=alert,
    )


def parse_object_key(key: str) -> RemoteObject | None:
    match = OBJECT_RE.fullmatch(key)
    if not match:
        return None
    try:
        stamp = dt.datetime.strptime(match.group("stamp"), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        return None
    if match.group("year") != stamp.strftime("%Y") or match.group("month") != stamp.strftime(
        "%m"
    ):
        return None
    return RemoteObject(key=key, timestamp=stamp)


def object_key(now: dt.datetime) -> str:
    value = now.astimezone(dt.timezone.utc)
    return (
        f"{OBJECT_PREFIX}{value:%Y/%m}/"
        f"danube-{value:%Y%m%dT%H%M%SZ}.sqlite3.enc"
    )


def retention_candidates(
    objects: list[RemoteObject], now: dt.datetime, keep_days: int
) -> list[RemoteObject]:
    if keep_days < 1:
        raise BackupError("backup_retention_invalid")
    now_utc = now.astimezone(dt.timezone.utc)
    valid = sorted(
        (
            item
            for item in objects
            if parse_object_key(item.key) is not None
            and item.timestamp <= now_utc + dt.timedelta(minutes=5)
        ),
        key=lambda item: item.timestamp,
        reverse=True,
    )
    if not valid:
        return []
    cutoff = now_utc - dt.timedelta(days=keep_days)
    # Cel mai nou obiect nu se șterge nici dacă ceasul sau jobul au derapat.
    return [item for item in valid[1:] if item.timestamp < cutoff]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_owner_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise BackupError("backup_staging_directory_unsafe")
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)


@contextlib.contextmanager
def staging_workspace(root: str | Path):
    root_path = Path(root)
    _assert_owner_directory(root_path)
    workspace = Path(tempfile.mkdtemp(prefix=".danube-offsite-", dir=root_path))
    os.chmod(workspace, 0o700)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=False)
        # Verificarea stă în finally ca să ruleze și când criptarea, uploadul
        # sau restaurarea aruncă o excepție.
        if workspace.exists():
            raise BackupError("backup_staging_cleanup_failed")


def _require_command(name: str) -> str:
    command = shutil.which(name)
    if not command:
        raise BackupError(f"backup_{name}_missing")
    return command


def _run_crypto(arguments: list[str], code: str, timeout: int = 600) -> None:
    try:
        result = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupError(code) from exc
    if result.returncode != 0:
        raise BackupError(code)


def _envelope_auth_key(config: Config, salt: bytes) -> bytes:
    try:
        secret = config.encryption_key_file.read_bytes().strip()
        return hashlib.scrypt(
            secret,
            salt=b"danube.sqlite-backup.auth.v1\0" + salt,
            n=2**15,
            r=8,
            p=1,
            dklen=32,
            maxmem=64 * 1024 * 1024,
        )
    except (OSError, ValueError) as exc:
        raise BackupError("backup_encryption_key_unreadable") from exc


def _context_digest(context: str) -> bytes:
    if parse_object_key(context) is None:
        raise BackupError("backup_object_key_invalid")
    return hashlib.sha256(context.encode("utf-8")).digest()


def encrypt_file(
    source: Path, target: Path, config: Config, workspace: Path, context: str
) -> None:
    payload = workspace / "openssl-payload.enc"
    command = [
        _require_command("openssl"),
        "enc",
        "-aes-256-ctr",
        "-pbkdf2",
        "-iter",
        "600000",
        "-md",
        "sha512",
        "-salt",
        "-pass",
        f"file:{config.encryption_key_file}",
        "-out",
        str(payload),
        "-in",
        str(source),
    ]
    _run_crypto(command, "backup_encryption_failed")
    if not payload.is_file() or payload.stat().st_size <= 0:
        raise BackupError("backup_encryption_empty")
    salt = os.urandom(16)
    header = ENVELOPE_MAGIC + bytes([ENVELOPE_VERSION]) + salt + _context_digest(context)
    signer = hmac.new(_envelope_auth_key(config, salt), digestmod=hashlib.sha256)
    signer.update(header)
    with target.open("wb") as output, payload.open("rb") as encrypted:
        output.write(header)
        for chunk in iter(lambda: encrypted.read(1024 * 1024), b""):
            signer.update(chunk)
            output.write(chunk)
        output.write(signer.digest())
    payload.unlink()
    os.chmod(target, 0o600)


def decrypt_file(
    source: Path, target: Path, config: Config, workspace: Path, context: str
) -> None:
    payload = workspace / "openssl-payload-restore.enc"
    try:
        total_size = source.stat().st_size
        if total_size <= ENVELOPE_HEADER_BYTES + ENVELOPE_TAG_BYTES:
            raise BackupError("backup_decryption_failed")
        with source.open("rb") as encrypted:
            header = encrypted.read(ENVELOPE_HEADER_BYTES)
            if (
                header[: len(ENVELOPE_MAGIC)] != ENVELOPE_MAGIC
                or header[len(ENVELOPE_MAGIC)] != ENVELOPE_VERSION
                or header[-32:] != _context_digest(context)
            ):
                raise BackupError("backup_decryption_failed")
            salt_start = len(ENVELOPE_MAGIC) + 1
            salt = header[salt_start : salt_start + 16]
            signer = hmac.new(
                _envelope_auth_key(config, salt), digestmod=hashlib.sha256
            )
            signer.update(header)
            remaining = total_size - ENVELOPE_HEADER_BYTES - ENVELOPE_TAG_BYTES
            with payload.open("wb") as output:
                while remaining:
                    chunk = encrypted.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise BackupError("backup_decryption_failed")
                    signer.update(chunk)
                    output.write(chunk)
                    remaining -= len(chunk)
            tag = encrypted.read(ENVELOPE_TAG_BYTES)
            if len(tag) != ENVELOPE_TAG_BYTES or encrypted.read(1):
                raise BackupError("backup_decryption_failed")
            if not hmac.compare_digest(signer.digest(), tag):
                raise BackupError("backup_decryption_failed")
    except OSError as exc:
        raise BackupError("backup_decryption_failed") from exc
    os.chmod(payload, 0o600)
    command = [
        _require_command("openssl"),
        "enc",
        "-d",
        "-aes-256-ctr",
        "-pbkdf2",
        "-iter",
        "600000",
        "-md",
        "sha512",
        "-pass",
        f"file:{config.encryption_key_file}",
        "-out",
        str(target),
        "-in",
        str(payload),
    ]
    _run_crypto(command, "backup_decryption_failed")
    payload.unlink()
    if not target.is_file() or target.stat().st_size <= 0:
        raise BackupError("backup_decryption_empty")
    os.chmod(target, 0o600)


def _curl_escape(value: str) -> str:
    if any(char in value for char in "\r\n\x00"):
        raise BackupError("backup_storage_credential_invalid")
    return value.replace("\\", "\\\\").replace('"', '\\"')


class SpacesClient:
    def __init__(self, config: Config, workspace: Path):
        self.config = config
        self.curl = _require_command("curl")
        self.curl_config = workspace / "curl-auth.conf"
        self.curl_config.write_text(
            'user = "'
            + _curl_escape(f"{config.access_key}:{config.secret_key}")
            + '"\n',
            encoding="utf-8",
        )
        os.chmod(self.curl_config, 0o600)

    def _url(self, key: str = "", query: dict[str, str] | None = None) -> str:
        endpoint = urllib.parse.urlsplit(self.config.endpoint)
        host = f"{self.config.bucket}.{endpoint.hostname}"
        if endpoint.port:
            host += f":{endpoint.port}"
        path = "/" + urllib.parse.quote(key, safe="/") if key else "/"
        encoded = urllib.parse.urlencode(query or {})
        return urllib.parse.urlunsplit(("https", host, path, encoded, ""))

    def _request(
        self,
        extra: list[str],
        *,
        timeout: int = 300,
        code: str,
        authenticated: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        command = [self.curl, "--silent", "--show-error", "--connect-timeout", "15"]
        if authenticated:
            command += [
                "--config",
                str(self.curl_config),
                "--aws-sigv4",
                f"aws:amz:{self.config.region}:s3",
            ]
        command += ["--max-time", str(timeout), *extra]
        try:
            result = subprocess.run(command, capture_output=True, timeout=timeout + 10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackupError(code) from exc
        if result.returncode != 0:
            raise BackupError(code)
        return result

    def upload(self, key: str, path: Path, sha256: str) -> None:
        if parse_object_key(key) is None:
            raise BackupError("backup_object_key_invalid")
        result = self._request(
            [
                "--request",
                "PUT",
                "--upload-file",
                str(path),
                "--header",
                "Content-Type: application/octet-stream",
                "--header",
                "x-amz-acl: private",
                "--header",
                f"x-amz-meta-sha256: {sha256}",
                "--output",
                os.devnull,
                "--write-out",
                "%{http_code}",
                self._url(key),
            ],
            timeout=600,
            code="backup_upload_failed",
        )
        if result.stdout.decode("ascii", "ignore") not in {"200", "201"}:
            raise BackupError("backup_upload_failed")

    def head(self, key: str) -> dict[str, str]:
        result = self._request(
            ["--head", self._url(key)], code="backup_head_failed"
        )
        headers: dict[str, str] = {}
        for raw in result.stdout.decode("iso-8859-1", "replace").splitlines():
            if ":" in raw:
                name, value = raw.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        return headers

    def download(self, key: str, target: Path) -> None:
        self._request(
            ["--output", str(target), self._url(key)],
            timeout=600,
            code="backup_download_failed",
        )
        if not target.is_file():
            raise BackupError("backup_download_failed")
        os.chmod(target, 0o600)

    def private_access_proof(self, key: str) -> bool:
        result = self._request(
            [
                "--range",
                "0-0",
                "--output",
                os.devnull,
                "--write-out",
                "%{http_code}",
                self._url(key),
            ],
            timeout=30,
            code="backup_private_access_probe_failed",
            authenticated=False,
        )
        return result.stdout.decode("ascii", "ignore") in {"403", "404"}

    def list_objects(self) -> list[RemoteObject]:
        objects: list[RemoteObject] = []
        token: str | None = None
        for _page in range(MAX_LIST_PAGES):
            query = {"list-type": "2", "prefix": OBJECT_PREFIX}
            if token:
                query["continuation-token"] = token
            result = self._request(
                [self._url(query=query)], code="backup_list_failed"
            )
            try:
                root = ET.fromstring(result.stdout)
            except ET.ParseError as exc:
                raise BackupError("backup_list_unreadable") from exc

            def values(name: str) -> list[str]:
                return [
                    (node.text or "")
                    for node in root.iter()
                    if node.tag.rsplit("}", 1)[-1] == name
                ]

            content_nodes = [
                node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Contents"
            ]
            for content in content_nodes:
                key = ""
                size: int | None = None
                for child in content:
                    local = child.tag.rsplit("}", 1)[-1]
                    if local == "Key":
                        key = child.text or ""
                    elif local == "Size":
                        try:
                            size = int(child.text or "")
                        except ValueError:
                            size = None
                parsed = parse_object_key(key)
                if parsed:
                    objects.append(dataclasses.replace(parsed, size=size))

            truncated = (values("IsTruncated") or ["false"])[0].lower() == "true"
            if not truncated:
                return objects
            token = (values("NextContinuationToken") or [""])[0]
            if not token:
                raise BackupError("backup_list_missing_continuation_token")
        raise BackupError("backup_list_page_limit")

    def delete(self, key: str) -> None:
        if parse_object_key(key) is None:
            raise BackupError("backup_retention_scope_refused")
        result = self._request(
            [
                "--request",
                "DELETE",
                "--output",
                os.devnull,
                "--write-out",
                "%{http_code}",
                self._url(key),
            ],
            code="backup_delete_failed",
        )
        if result.stdout.decode("ascii", "ignore") not in {"200", "204"}:
            raise BackupError("backup_delete_failed")


def _parse_mailbox(value: str) -> dict[str, str]:
    named = re.fullmatch(r"\s*([^<>]+?)\s*<([^<>]+)>\s*", value)
    if named:
        return {"name": named.group(1).strip(), "address": named.group(2).strip()}
    return {"address": value.strip()}


def deliver_alert(
    alert: AlertConfig | None,
    state: str,
    *,
    object_name: str | None = None,
    age_hours: int | None = None,
    test: bool = False,
) -> None:
    if alert is None:
        raise BackupError("backup_alert_configuration_missing")
    label = f"test: {state}" if test else state
    lines = [
        f"Danube encrypted SQLite backup monitor: {label}.",
        f"Latest object: {object_name}." if object_name else "No valid Danube object exists.",
        f"Age: {age_hours}h (limit 30h)." if age_hours is not None else None,
        "This is the operator-requested delivery test."
        if test
        else "Treat this as an application recovery incident.",
    ]
    payload = {
        "from": _parse_mailbox(alert.sender),
        "to": alert.recipient,
        "reply_to": _parse_mailbox(alert.reply_to),
        "subject": f"[Danube] SQLite backup {label}",
        "text": "\n".join(line for line in lines if line),
    }
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        + urllib.parse.quote(alert.account_id, safe="")
        + "/email/sending/send"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {alert.api_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status < 200 or response.status >= 300:
                raise BackupError("backup_alert_delivery_failed")
            response_body = response.read(65537)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise BackupError("backup_alert_delivery_failed") from exc
    if len(response_body) > 65536:
        raise BackupError("backup_alert_delivery_failed")
    try:
        accepted = json.loads(response_body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError("backup_alert_delivery_failed") from exc
    if not isinstance(accepted, dict) or accepted.get("success") is not True:
        raise BackupError("backup_alert_delivery_failed")
    result = accepted.get("result")
    if not isinstance(result, dict):
        raise BackupError("backup_alert_delivery_failed")
    for field in ("delivered", "queued", "permanent_bounces"):
        if not isinstance(result.get(field), list):
            raise BackupError("backup_alert_delivery_failed")
    recipient = alert.recipient.casefold()
    delivered = {
        str(address).casefold() for address in result.get("delivered", [])
    }
    queued = {str(address).casefold() for address in result.get("queued", [])}
    bounced = {
        str(address).casefold() for address in result.get("permanent_bounces", [])
    }
    if recipient in bounced or recipient not in delivered | queued:
        raise BackupError("backup_alert_delivery_failed")


def _write_status(path: str | Path, section: str, value: dict[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    current: dict[str, object] = {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            current = loaded
    except (OSError, json.JSONDecodeError):
        pass
    current[section] = value
    partial = target.with_name(target.name + ".partial")
    try:
        partial.write_text(json.dumps(current, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(partial, 0o600)
        os.replace(partial, target)
        os.chmod(target, 0o600)
    finally:
        if partial.exists():
            partial.unlink()


def _verified_local_backup(source: Path, dest: Path, keep_days: int) -> tuple[Path, dict]:
    if not sqlite_backup.backup(str(source), str(dest), keep_days):
        raise BackupError("backup_sqlite_copy_failed")
    path = dest / f"cache-{_utcnow().date().isoformat()}.db"
    if not sqlite_backup.verify(str(path)):
        raise BackupError("backup_sqlite_verify_failed")
    try:
        stats = sqlite_backup._stats(str(path))
    except Exception as exc:
        raise BackupError("backup_sqlite_stats_failed") from exc
    if stats.get("permanent", 0) <= 0:
        raise BackupError("backup_permanent_archive_missing")
    return path, stats


def perform_backup(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.config)
    now = _utcnow()
    source = Path(args.source)
    dest = Path(args.dest)
    local_copy, stats = _verified_local_backup(source, dest, args.keep_days)
    key = object_key(now)
    workspace_path: Path | None = None
    result: dict[str, object]
    with staging_workspace(args.staging) as workspace:
        workspace_path = workspace
        encrypted = workspace / "backup.sqlite3.enc"
        readback = workspace / "readback.sqlite3.enc"
        encrypt_file(local_copy, encrypted, config, workspace, key)
        encrypted_size = encrypted.stat().st_size
        encrypted_sha = _sha256(encrypted)
        spaces = SpacesClient(config, workspace)
        spaces.upload(key, encrypted, encrypted_sha)
        headers = spaces.head(key)
        if headers.get("content-length") != str(encrypted_size):
            raise BackupError("backup_head_size_mismatch")
        if headers.get("x-amz-meta-sha256", "").lower() != encrypted_sha:
            raise BackupError("backup_head_checksum_mismatch")
        spaces.download(key, readback)
        if readback.stat().st_size != encrypted_size or _sha256(readback) != encrypted_sha:
            raise BackupError("backup_authenticated_readback_mismatch")
        if not spaces.private_access_proof(key):
            raise BackupError("backup_object_public")
        objects = spaces.list_objects()
        deletions = retention_candidates(objects, now, args.offsite_keep_days)
        for candidate in deletions:
            spaces.delete(candidate.key)
        result = {
            "state": "ok",
            "at": _iso(now),
            "objectKey": key,
            "encryptedBytes": encrypted_size,
            "encryptedSha256": encrypted_sha,
            "rows": stats["rows"],
            "permanentRows": stats["permanent"],
            "authenticatedReadback": True,
            "privateAccess": True,
            "retentionDeleted": len(deletions),
        }
    if workspace_path is None or workspace_path.exists():
        raise BackupError("backup_staging_cleanup_failed")
    result["cleanupVerified"] = True
    _write_status(args.status_file, "backup", result)
    _emit("backup_offsite_ok", **result)
    return result


def _freshness(
    config: Config, workspace: Path, max_age_hours: int
) -> tuple[dict[str, object], RemoteObject | None]:
    spaces = SpacesClient(config, workspace)
    now = _utcnow()
    objects = sorted(
        (
            item
            for item in spaces.list_objects()
            if item.timestamp <= now + dt.timedelta(minutes=5)
        ),
        key=lambda item: item.timestamp,
        reverse=True,
    )
    if not objects:
        return {"state": "missing", "checkedAt": _iso(now)}, None
    newest = objects[0]
    headers = spaces.head(newest.key)
    try:
        size = int(headers.get("content-length", ""))
    except ValueError:
        size = 0
    checksum = headers.get("x-amz-meta-sha256", "")
    if size <= 0 or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise BackupError("backup_freshness_metadata_invalid")
    age_seconds = max(0, int((now - newest.timestamp).total_seconds()))
    state = "fresh" if age_seconds <= max_age_hours * 3600 else "stale"
    return {
        "state": state,
        "checkedAt": _iso(now),
        "objectKey": newest.key,
        "encryptedBytes": size,
        "ageSeconds": age_seconds,
        "limitHours": max_age_hours,
        "authenticatedReadback": True,
    }, newest


def perform_monitor(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.config)
    workspace_path: Path | None = None
    with staging_workspace(args.staging) as workspace:
        workspace_path = workspace
        evidence, newest = _freshness(config, workspace, args.max_age_hours)
        age_hours = (
            int(evidence["ageSeconds"]) // 3600 if "ageSeconds" in evidence else None
        )
        if args.test_alert:
            deliver_alert(
                config.alert,
                str(evidence["state"]),
                object_name=newest.key if newest else None,
                age_hours=age_hours,
                test=True,
            )
            evidence["testAlertAccepted"] = True
        elif args.alert and evidence["state"] != "fresh":
            deliver_alert(
                config.alert,
                str(evidence["state"]),
                object_name=newest.key if newest else None,
                age_hours=age_hours,
            )
            evidence["alertAccepted"] = True
    if workspace_path is None or workspace_path.exists():
        raise BackupError("backup_staging_cleanup_failed")
    evidence["cleanupVerified"] = True
    _write_status(args.status_file, "monitor", evidence)
    _emit("backup_monitor", **evidence)
    if evidence["state"] != "fresh":
        raise BackupError(
            f"backup_{evidence['state']}",
            alert_accepted=bool(
                evidence.get("alertAccepted") or evidence.get("testAlertAccepted")
            ),
        )
    return evidence


def perform_restore_drill(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.config)
    started = time.monotonic()
    started_at = _utcnow()
    workspace_path: Path | None = None
    with staging_workspace(args.staging) as workspace:
        workspace_path = workspace
        spaces = SpacesClient(config, workspace)
        objects = sorted(
            (
                item
                for item in spaces.list_objects()
                if item.timestamp <= started_at + dt.timedelta(minutes=5)
            ),
            key=lambda item: item.timestamp,
            reverse=True,
        )
        if not objects:
            raise BackupError("restore_backup_missing")
        newest = objects[0]
        encrypted = workspace / "source.sqlite3.enc"
        restored = workspace / "restored.sqlite3"
        spaces.download(newest.key, encrypted)
        headers = spaces.head(newest.key)
        try:
            expected_size = int(headers.get("content-length", ""))
        except ValueError as exc:
            raise BackupError("restore_object_metadata_invalid") from exc
        expected_sha = headers.get("x-amz-meta-sha256", "")
        if (
            expected_size <= 0
            or encrypted.stat().st_size != expected_size
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
            or _sha256(encrypted) != expected_sha
        ):
            raise BackupError("restore_encrypted_readback_mismatch")
        decrypt_file(encrypted, restored, config, workspace, newest.key)
        if not sqlite_backup.verify(str(restored)):
            raise BackupError("restore_sqlite_verify_failed")
        try:
            stats = sqlite_backup._stats(str(restored))
        except Exception as exc:
            raise BackupError("restore_sqlite_stats_failed") from exc
        if stats.get("permanent", 0) <= 0:
            raise BackupError("restore_permanent_archive_missing")
        rto_seconds = round(time.monotonic() - started, 3)
        rpo_seconds = max(0, int((started_at - newest.timestamp).total_seconds()))
        result: dict[str, object] = {
            "state": "ok",
            "at": _iso(started_at),
            "objectKey": newest.key,
            "encryptedBytes": expected_size,
            "rows": stats["rows"],
            "permanentRows": stats["permanent"],
            "rpoSeconds": rpo_seconds,
            "rtoSeconds": rto_seconds,
            "productionOverwritten": False,
        }
    if workspace_path is None or workspace_path.exists():
        raise BackupError("backup_staging_cleanup_failed")
    result["decryptedBytesRemoved"] = True
    result["temporaryArtifactsRemoved"] = True
    result["cleanupVerified"] = True
    _write_status(args.status_file, "restoreDrill", result)
    _emit("backup_restore_drill_ok", **result)
    return result


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--staging", default=DEFAULT_STAGING)
    parser.add_argument("--status-file", default=DEFAULT_STATUS)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser("backup", help="copie locală + criptare + off-box")
    _common(write)
    write.add_argument("--source", default=DEFAULT_SOURCE)
    write.add_argument("--dest", default=DEFAULT_BACKUPS)
    write.add_argument("--keep-days", type=int, default=14)
    write.add_argument("--offsite-keep-days", type=int, default=30)
    write.add_argument("--alert-on-failure", action="store_true")

    monitor = commands.add_parser("monitor", help="freshness read-only + alert opțional")
    _common(monitor)
    monitor.add_argument("--max-age-hours", type=int, default=30)
    monitor.add_argument("--alert", action="store_true")
    monitor.add_argument("--test-alert", action="store_true")

    restore = commands.add_parser("restore-drill", help="restorește numai o copie temporară")
    _common(restore)
    return parser


def _signal_handler(_signum, _frame):
    raise InterruptedError


def main(argv: list[str] | None = None) -> int:
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _signal_handler)
    args = _parser().parse_args(argv)
    config: Config | None = None
    try:
        if args.command == "backup":
            try:
                config = load_config(args.config)
            except BackupError:
                # perform_backup va păstra același reason code; aici memorăm
                # numai dacă există o configurație validă pentru alerta de eșec.
                config = None
            perform_backup(args)
        elif args.command == "monitor":
            perform_monitor(args)
        else:
            perform_restore_drill(args)
        return 0
    except (BackupError, InterruptedError) as exc:
        code = exc.code if isinstance(exc, BackupError) else "backup_interrupted"
        section = "backup" if args.command == "backup" else args.command
        with contextlib.suppress(Exception):
            _write_status(
                args.status_file,
                section,
                {"state": "failed", "at": _iso(_utcnow()), "reason": code},
            )
        alert_already_accepted = isinstance(exc, BackupError) and exc.alert_accepted
        alert_state = "accepted" if alert_already_accepted else "not-requested"
        alert_requested = (
            args.command == "backup" and args.alert_on_failure
        ) or (
            args.command == "monitor" and (args.alert or args.test_alert)
        )
        alert_requested = alert_requested and not alert_already_accepted
        if alert_requested:
            if config is None:
                with contextlib.suppress(BackupError):
                    config = load_config(args.config)
            try:
                deliver_alert(
                    config.alert if config else None,
                    "provider-check-failed" if args.command == "monitor" else "failed",
                    test=bool(args.command == "monitor" and args.test_alert),
                )
                alert_state = "accepted"
            except BackupError as alert_exc:
                alert_state = alert_exc.code
        _emit("backup_failed", reason=code, alert=alert_state)
        return 1


if __name__ == "__main__":
    sys.exit(main())
