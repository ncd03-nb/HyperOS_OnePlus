#!/usr/bin/env python3
"""Upload the finished ROM to Google Drive from GitHub Actions secrets."""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

DRIVE_SCOPE = ["https://www.googleapis.com/auth/drive"]
TOKEN_URI = "https://oauth2.googleapis.com/token"
RETRYABLE_HTTP = {429, 500, 502, 503, 504}


def _json_secret(name: str) -> dict[str, Any] | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None

    candidates = [raw]
    # Also accept a base64-encoded JSON secret; useful when multiline JSON has
    # been awkward to paste into a secret manager.
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        candidates.append(decoded)
    except Exception:
        pass

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            value: Any = json.loads(candidate)
            # Some users paste a JSON-escaped JSON string into Actions secrets.
            for _ in range(2):
                if isinstance(value, str):
                    value = json.loads(value)
                else:
                    break
            if isinstance(value, dict):
                return value
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"{name} is not valid JSON") from last_error


def _find_authorized_user(data: dict[str, Any]) -> dict[str, Any] | None:
    def usable(obj: Any) -> bool:
        return isinstance(obj, dict) and all(
            obj.get(key) for key in ("client_id", "client_secret", "refresh_token")
        )

    if usable(data):
        return dict(data)

    for key in ("authorized_user", "credentials", "oauth", "token"):
        obj = data.get(key)
        if usable(obj):
            return dict(obj)

    # Accept Google's downloaded OAuth client JSON only when a refresh token is
    # supplied alongside it (or via GOOGLE_OAUTH_REFRESH_TOKEN). Client JSON by
    # itself cannot authenticate a headless GitHub Actions runner.
    for key in ("installed", "web"):
        client = data.get(key)
        if not isinstance(client, dict):
            continue
        refresh_token = (
            data.get("refresh_token")
            or client.get("refresh_token")
            or os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN", "").strip()
        )
        if refresh_token and client.get("client_id") and client.get("client_secret"):
            return {
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "refresh_token": refresh_token,
                "token_uri": client.get("token_uri", TOKEN_URI),
            }
    return None


def get_credentials() -> Credentials:
    service_account_info = _json_secret("GOOGLE_SERVICE_ACCOUNT_JSON")
    if service_account_info:
        return service_account.Credentials.from_service_account_info(
            service_account_info, scopes=DRIVE_SCOPE
        )

    oauth_info = _json_secret("GOOGLE_OAUTH_CREDENTIALS_JSON")
    if oauth_info:
        authorized = _find_authorized_user(oauth_info)
        if not authorized:
            if isinstance(oauth_info.get("installed"), dict) or isinstance(
                oauth_info.get("web"), dict
            ):
                raise RuntimeError(
                    "GOOGLE_OAUTH_CREDENTIALS_JSON contains OAuth client credentials "
                    "but no refresh_token. Store an authorized-user JSON with "
                    "client_id, client_secret and refresh_token, or add the refresh "
                    "token as GOOGLE_OAUTH_REFRESH_TOKEN."
                )
            raise RuntimeError(
                "GOOGLE_OAUTH_CREDENTIALS_JSON must contain client_id, client_secret "
                "and refresh_token (directly or in an authorized_user object)."
            )

        credentials = Credentials(
            token=authorized.get("token"),
            refresh_token=authorized["refresh_token"],
            token_uri=authorized.get("token_uri") or TOKEN_URI,
            client_id=authorized["client_id"],
            client_secret=authorized["client_secret"],
            scopes=DRIVE_SCOPE,
        )
        # Always refresh on the runner. This avoids stale access tokens copied
        # into the secret and proves the refresh token is usable before a large
        # upload starts.
        credentials.refresh(Request())
        return credentials

    raise RuntimeError("Google Drive credentials are not configured.")


def _upload_with_retry(request):
    uploaded = None
    attempt = 0
    while uploaded is None:
        try:
            status, uploaded = request.next_chunk()
            if status is not None:
                pct = int(status.progress() * 100)
                print(f"Google Drive upload: {pct}%", file=sys.stderr)
            attempt = 0
        except HttpError as exc:
            status_code = getattr(exc.resp, "status", None)
            if status_code not in RETRYABLE_HTTP or attempt >= 7:
                raise
            delay = min(60, 2 ** attempt)
            attempt += 1
            print(
                f"Google Drive temporary HTTP {status_code}; retrying in {delay}s",
                file=sys.stderr,
            )
            time.sleep(delay)
    return uploaded


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: upload_gdrive.py <rom.zip>")
    rom = Path(sys.argv[1]).resolve()
    folder_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip()
    if not rom.is_file():
        raise FileNotFoundError(rom)
    if not folder_id:
        raise RuntimeError("GDRIVE_FOLDER_ID is not configured.")

    credentials = get_credentials()
    service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    media = MediaFileUpload(
        str(rom), mimetype="application/zip", resumable=True, chunksize=64 * 1024 * 1024
    )
    request = service.files().create(
        body={"name": rom.name, "parents": [folder_id]},
        media_body=media,
        fields="id,name,webViewLink",
        supportsAllDrives=True,
    )
    uploaded = _upload_with_retry(request)
    file_id = uploaded["id"]
    url = uploaded.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
    info_dir = Path("build_info")
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / "output_url.txt").write_text(url, encoding="utf-8")
    print(url)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Google Drive upload error: {exc}", file=sys.stderr)
        raise SystemExit(1)
