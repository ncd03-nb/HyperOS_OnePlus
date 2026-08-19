#!/usr/bin/env python3
"""Upload the finished ROM to Google Drive from GitHub Actions secrets."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

DRIVE_SCOPE = ["https://www.googleapis.com/auth/drive"]


def get_credentials() -> Credentials:
    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    oauth_json = os.environ.get("GOOGLE_OAUTH_CREDENTIALS_JSON", "").strip()
    if service_account_json:
        return service_account.Credentials.from_service_account_info(
            json.loads(service_account_json), scopes=DRIVE_SCOPE
        )
    if oauth_json:
        credentials = Credentials.from_authorized_user_info(json.loads(oauth_json), DRIVE_SCOPE)
        if not credentials.valid:
            credentials.refresh(Request())
        return credentials
    raise RuntimeError("Google Drive credentials are not configured.")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: upload_gdrive.py <rom.zip>")
    rom = Path(sys.argv[1]).resolve()
    folder_id = os.environ.get("GDRIVE_FOLDER_ID", "").strip()
    if not rom.is_file():
        raise FileNotFoundError(rom)
    if not folder_id:
        raise RuntimeError("GDRIVE_FOLDER_ID is not configured.")

    service = build("drive", "v3", credentials=get_credentials(), cache_discovery=False)
    media = MediaFileUpload(
        str(rom), mimetype="application/zip", resumable=True, chunksize=64 * 1024 * 1024
    )
    request = service.files().create(
        body={"name": rom.name, "parents": [folder_id]},
        media_body=media,
        fields="id,name,webViewLink",
        supportsAllDrives=True,
    )
    uploaded = None
    while uploaded is None:
        _, uploaded = request.next_chunk()
    file_id = uploaded["id"]
    print(uploaded.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
