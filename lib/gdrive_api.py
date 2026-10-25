"""Authenticated Drive downloads for headless ROM builds; never logs credentials."""

import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

API = 'https://www.googleapis.com/drive/v3/files/'
TOKEN = 'https://oauth2.googleapis.com/token'
READ_SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
RETRIES = 5


class DriveAPIError(ValueError):
    pass


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        host = target.hostname or ''
        if (target.scheme != 'https' or target.username or target.password
                or target.port not in (None, 443)
                or not (host == 'www.googleapis.com' or host.endswith('.googleusercontent.com'))):
            raise DriveAPIError('Drive returned an unexpected download redirect')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if host != 'www.googleapis.com':
            for name in ('Authorization', 'X-goog-drive-resource-keys'):
                redirected.remove_header(name)
        return redirected


def _secret(name):
    raw = os.environ.get(name, '').strip()
    if not raw:
        return None
    try:
        if not raw.startswith(('{', '"')):
            raw = base64.b64decode(raw, validate=True).decode('utf-8')
        obj = json.loads(raw)
        for _ in range(2):
            if isinstance(obj, str):
                obj = json.loads(obj)
        if not isinstance(obj, dict):
            raise ValueError()
        return obj
    except (ValueError, UnicodeError):
        raise DriveAPIError(name + ' is not a valid credential JSON') from None


def credentials():
    # A separate read-only credential leaves the upload identity unchanged.
    for name in ('GOOGLE_DRIVE_DOWNLOAD_CREDENTIALS_JSON', 'GOOGLE_TOKEN_B64',
                 'GOOGLE_OAUTH_CREDENTIALS_JSON', 'GOOGLE_SERVICE_ACCOUNT_JSON'):
        obj = _secret(name)
        if obj is None:
            continue
        for key in ('authorized_user', 'credentials', 'oauth', 'token'):
            if isinstance(obj.get(key), dict):
                obj = obj[key]
                break
        for key in ('installed', 'web'):
            if isinstance(obj.get(key), dict):
                client = obj[key]
                obj = {**client, 'refresh_token': obj.get('refresh_token') or
                       client.get('refresh_token') or os.environ.get('GOOGLE_OAUTH_REFRESH_TOKEN', '')}
                break
        if obj.get('type') == 'service_account' or all(obj.get(k) for k in
                ('client_id', 'client_secret', 'refresh_token')):
            return obj
        raise DriveAPIError(name + ' needs an authorized-user refresh_token, not only an OAuth client')
    return None


def _json_response(response):
    raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise DriveAPIError('Drive API response is too large')
    try:
        obj = json.loads(raw)
    except ValueError:
        raise DriveAPIError('Drive API returned an invalid response') from None
    if not isinstance(obj, dict):
        raise DriveAPIError('Drive API returned an invalid response')
    return obj


def _reasons(error):
    try:
        return {item.get('reason') for item in _json_response(error).get('error', {}).get('errors', [])}
    except (ValueError, AttributeError, TypeError):
        return set()


class Client:
    def __init__(self, info, resource_key=None, file_id=None):
        self.info = info
        self.resource = file_id + '/' + resource_key if resource_key else None
        if self.resource and ('\r' in self.resource or '\n' in self.resource):
            raise DriveAPIError('Invalid Drive resource key')
        self.opener = urllib.request.build_opener(_Redirect())
        self.token_opener = urllib.request.build_opener(_NoRedirect())
        self.access = None

    def refresh(self):
        if self.info.get('type') == 'service_account':
            try:
                from google.auth.transport.requests import Request
                from google.oauth2.service_account import Credentials
                info = dict(self.info, token_uri=TOKEN)
                cred = Credentials.from_service_account_info(info, scopes=[READ_SCOPE])
                cred.refresh(Request())
                self.access = cred.token
                return
            except Exception:
                raise DriveAPIError('Drive service-account login failed; check credentials and file sharing') from None
        data = urlencode({k: self.info[k] for k in ('client_id', 'client_secret', 'refresh_token')} |
                         {'grant_type': 'refresh_token'}).encode()
        try:
            with self.token_opener.open(urllib.request.Request(TOKEN, data=data), timeout=30) as r:
                obj = _json_response(r)
        except (OSError, ValueError):
            raise DriveAPIError('Google OAuth refresh failed. Reconnect Google with the app In production and update the download secret') from None
        self.access = obj.get('access_token')
        if not self.access:
            raise DriveAPIError('Google did not return a Drive access token')

    def open(self, file_id, params, extra=None):
        if not self.access:
            self.refresh()
        for attempt in range(RETRIES + 1):
            headers = {'Authorization': 'Bearer ' + self.access, 'Accept-Encoding': 'identity'}
            if self.resource:
                headers['X-Goog-Drive-Resource-Keys'] = self.resource
            headers.update(extra or {})
            try:
                return self.opener.open(urllib.request.Request(API + file_id + '?' + urlencode(params), headers=headers), timeout=60)
            except urllib.error.HTTPError as e:
                status = e.code
                with e:
                    reasons = _reasons(e)
                if status == 401 and attempt == 0:
                    self.refresh()
                    continue
                retryable = status in (429, 500, 502, 503, 504) or bool(reasons & {'rateLimitExceeded', 'userRateLimitExceeded'})
                if retryable and attempt < RETRIES:
                    time.sleep(min(30, 2 ** attempt))
                    continue
                if 'downloadQuotaExceeded' in reasons:
                    raise DriveAPIError('Google Drive API download quota exceeded. Use an accessible mirror or wait for the quota to reset') from None
                if status in (403, 404):
                    raise DriveAPIError(f'Drive API HTTP {status}: file is unavailable to the configured Google account or download is restricted; share the file with that account or use a mirror') from None
                raise DriveAPIError(f'Drive API HTTP {status}; download stopped') from None
            except urllib.error.URLError:
                if attempt >= RETRIES:
                    raise DriveAPIError('Cannot connect to Google Drive API') from None
                time.sleep(min(30, 2 ** attempt))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def download(file_id, resource_key, out, info, log=print):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', file_id):
        raise DriveAPIError('Invalid Drive file ID')
    client = Client(info, resource_key, file_id)
    with client.open(file_id, {'fields': 'size,md5Checksum,mimeType,capabilities(canDownload)',
                               'supportsAllDrives': 'true'}) as r:
        meta = _json_response(r)
    if ('size' not in meta or meta.get('mimeType', '').startswith('application/vnd.google-apps.')
            or not meta.get('capabilities', {}).get('canDownload', True)):
        raise DriveAPIError('Drive source must be a downloadable binary file, not a folder, shortcut or Google document')
    total = int(meta['size'])
    if total <= 0:
        raise DriveAPIError('Drive source is empty')
    output = Path(out)
    temporary = output.with_name(output.name + '.part')
    output.parent.mkdir(parents=True, exist_ok=True)
    log(f'  downloading via authenticated Drive API ({total} bytes)')
    digest = hashlib.md5()
    got = 0
    failures = 0
    started = time.monotonic()
    last_log = started
    etag = None
    # Never append a stale .part left by a different run.
    with temporary.open('xb') as stream:
        while got < total:
            headers = {'Range': f'bytes={got}-'}
            if etag:
                headers['If-Match'] = etag
            offset = got
            try:
                with client.open(file_id, {'alt': 'media', 'supportsAllDrives': 'true'}, headers) as response:
                    status = response.getcode()
                    if status == 206:
                        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                        if not match or tuple(map(int, match.groups())) != (got, total - 1, total):
                            raise DriveAPIError('Drive returned an inconsistent byte range')
                    elif status != 200 or got:
                        raise DriveAPIError('Drive did not honor the resumed byte range')
                    current_etag = response.headers.get('ETag')
                    if etag and current_etag and etag != current_etag:
                        raise DriveAPIError('Drive file changed while downloading')
                    etag = etag or current_etag
                    if 'text/html' in response.headers.get('Content-Type', '').lower():
                        raise DriveAPIError('Drive returned HTML instead of a ROM')
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        if not got and chunk.lstrip().lower().startswith((b'<!doctype html', b'<html')):
                            raise DriveAPIError('Drive returned HTML instead of a ROM')
                        if got + len(chunk) > total:
                            raise DriveAPIError('Drive returned more bytes than expected')
                        stream.write(chunk)
                        digest.update(chunk)
                        got += len(chunk)
                        now = time.monotonic()
                        if now - last_log >= 10:
                            log(f'  Drive download: {got / total:.1%}, {got / (1024 * 1024) / max(now - started, .001):.1f} MiB/s')
                            last_log = now
                if got < total:
                    raise OSError('Short Drive response')
            except (OSError, http.client.HTTPException):
                failures = 0 if got > offset else failures
                failures += 1
                if failures > RETRIES:
                    raise DriveAPIError('Drive connection interrupted repeatedly; incomplete ROM was not published') from None
                log(f'  Drive connection interrupted; resuming at {got}/{total} bytes')
                time.sleep(min(30, 2 ** (failures - 1)))
    if meta.get('md5Checksum') and digest.hexdigest() != meta['md5Checksum'].lower():
        raise DriveAPIError('Drive MD5 checksum mismatch; incomplete or changed ROM was not published')
    temporary.replace(output)
    log('  Drive download complete; size' + (' and MD5 verified' if meta.get('md5Checksum') else ' verified'))
    return out
