#!/usr/bin/env python3
"""Bounded authenticated download check; prints no source URL or credentials."""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import gdrive
import gdrive_api as api


def main():
    file_id, resource_key = gdrive.parse_reference(os.environ['DRIVE_CHECK_SOURCE'])
    info = api.credentials()
    if not info:
        raise api.DriveAPIError('No authenticated Drive download credential configured')
    client = api.Client(info, resource_key, file_id)
    with client.open(file_id, {'fields': 'size,md5Checksum,capabilities(canDownload)', 'supportsAllDrives': 'true'}) as r:
        meta = api._json_response(r)
    total = int(meta['size'])
    end = min(total, 32) - 1
    with client.open(file_id, {'alt': 'media', 'supportsAllDrives': 'true'}, {'Range': f'bytes=0-{end}'}) as r:
        if r.getcode() != 206 or r.headers.get('Content-Range') != f'bytes 0-{end}/{total}':
            raise api.DriveAPIError('Drive range check failed')
        sample = r.read(33)
        if len(sample) != end + 1:
            raise api.DriveAPIError('Drive range length check failed')
    print('Authenticated Drive metadata and HTTP 206 range: OK')
    if total <= 64 * 1024 * 1024:
        with tempfile.TemporaryDirectory() as tmp:
            api.download(file_id, resource_key, Path(tmp) / 'source.bin', info)
        print('Full small-file download with size/checksum validation: OK')
    else:
        print('Large source: full download skipped by bounded check')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print('Drive download check failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
