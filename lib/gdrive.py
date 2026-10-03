#!/usr/bin/env python3
# Public Drive file downloader. Confirmation pages are handled; quotas are not bypassed.

import http.cookiejar
from html.parser import HTMLParser
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlencode, urlparse

BASE = "https://drive.usercontent.google.com/download"
DRIVE_HOSTS = {'drive.google.com', 'docs.google.com', 'drive.usercontent.google.com'}


class DriveDownloadError(ValueError):
    pass


def is_drive_url(value):
    parsed = urlparse(value)
    return parsed.scheme in ('http', 'https') and parsed.hostname in DRIVE_HOSTS


def parse_reference(value):
    resource_key = None
    if is_drive_url(value):
        parsed = urlparse(value)
        query = parse_qs(parsed.query)
        match = re.search(r'/file/d/([A-Za-z0-9_-]+)(?:/|$)', parsed.path)
        file_id = match.group(1) if match else query.get('id', [''])[0]
        resource_key = query.get('resourcekey', [None])[0]
    else:
        file_id = value
    if not re.fullmatch(r'[A-Za-z0-9_-]+', file_id):
        raise DriveDownloadError('Expected a Drive file ID or public file share URL')
    return file_id, resource_key


def _opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "Mozilla/5.0"), ('Accept-Encoding', 'identity')]
    return op


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.params = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('name'):
            self.params[attrs['name']] = attrs.get('value', '')


def _form_params(html):
    parser = FormParser()
    parser.feed(html)
    return parser.params


def _page_error(html):
    page = html.lower()
    if any(text in page for text in ('quota exceeded', 'downloadquotaexceeded',
                                    'too many users have viewed or downloaded')):
        raise DriveDownloadError('Google Drive quota exceeded. Use an accessible private copy or mirror, '
                                 'or wait for the quota to reset; anonymous downloads cannot guarantee a bypass.')
    if any(text in page for text in ('accounts.google.com/servicelogin', 'you need access',
                                    'request access', 'access denied')):
        raise DriveDownloadError('Google Drive file requires permission or sign-in')


def _response(opener, file_id, resource_key):
    params = {'id': file_id, 'export': 'download'}
    if resource_key:
        params['resourcekey'] = resource_key
    for _ in range(3):
        try:
            response = opener.open(BASE + '?' + urlencode(params), timeout=30)
        except urllib.error.HTTPError as error:
            with error:
                _page_error(error.read(1 << 20).decode('utf-8', 'replace'))
            raise DriveDownloadError(f'Google Drive HTTP {error.code}; check file permissions or retry later') from error
        try:
            prefix = response.read(65536)
            content_type = response.headers.get('Content-Type', '').lower()
            if 'text/html' not in content_type and not prefix.lstrip().lower().startswith((b'<!doctype html', b'<html')):
                return response, prefix
            html = (prefix + response.read(1 << 20)).decode('utf-8', 'replace')
        except Exception:
            response.close()
            raise
        response.close()
        _page_error(html)
        form = _form_params(html)
        if not form.get('confirm') or form.get('id', file_id) != file_id:
            raise DriveDownloadError('Google Drive returned an HTML page instead of a file')
        for key in ('confirm', 'uuid', 'at'):
            if form.get(key):
                params[key] = form[key]
    raise DriveDownloadError('Google Drive confirmation did not produce a downloadable file')


def download(reference, out, log=print):
    file_id, resource_key = parse_reference(reference)
    resp, prefix = _response(_opener(), file_id, resource_key)
    output = Path(out)
    temporary = output.with_name(output.name + '.part')
    try:
        total = resp.headers.get('Content-Length')
        log('  downloading %s%s' % (file_id, ' (%s bytes)' % total if total else ''))
        output.parent.mkdir(parents=True, exist_ok=True)
        got = len(prefix)
        with temporary.open('xb') as stream:
            stream.write(prefix)
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                stream.write(chunk)
                got += len(chunk)
        if not got or (total is not None and got != int(total)):
            raise DriveDownloadError(f'Incomplete Drive download ({got} bytes)')
        temporary.replace(output)
    finally:
        resp.close()
    return out


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == '--is-drive-url':
        sys.exit(0 if is_drive_url(sys.argv[2]) else 1)
    if len(sys.argv) != 3:
        print('usage: gdrive.py <file_id_or_share_url> <output_path>', file=sys.stderr)
        sys.exit(2)
    try:
        download(sys.argv[1], sys.argv[2])
    except (ValueError, OSError) as error:
        print(f'Drive download error: {error}', file=sys.stderr)
        sys.exit(1)
