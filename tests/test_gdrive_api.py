import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import gdrive
import gdrive_api as api

INFO = {'client_id': 'test-client', 'client_secret': 'test-secret', 'refresh_token': 'test-refresh'}


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}

    def getcode(self):
        return self.status


def error(status, reason):
    return urllib.error.HTTPError('https://www.googleapis.com/', status, 'failed', {},
        io.BytesIO(json.dumps({'error': {'errors': [{'reason': reason}]}}).encode()))


class DriveAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / 'rom.zip'
        self.binary = b'PK\x03\x04' + b'valid ROM data' * 1024
        self.meta = {'size': str(len(self.binary)), 'md5Checksum': hashlib.md5(self.binary).hexdigest(),
                     'mimeType': 'application/zip', 'capabilities': {'canDownload': True}}
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {name: '' for name in ('GOOGLE_DRIVE_DOWNLOAD_CREDENTIALS_JSON',
            'GOOGLE_TOKEN_B64', 'GOOGLE_OAUTH_CREDENTIALS_JSON', 'GOOGLE_SERVICE_ACCOUNT_JSON',
            'GOOGLE_OAUTH_REFRESH_TOKEN')}).start()
        patch.object(api.time, 'sleep').start()

    def test_api_selected_before_anonymous_download(self):
        os.environ['GOOGLE_DRIVE_DOWNLOAD_CREDENTIALS_JSON'] = json.dumps(INFO)
        with patch.object(api, 'download', return_value=self.out) as download, patch.object(gdrive, '_opener') as anonymous:
            gdrive.download('https://drive.google.com/file/d/file-id/view?resourcekey=key', self.out)
        self.assertEqual(download.call_args.args[:4], ('file-id', 'key', self.out, INFO))
        anonymous.assert_not_called()

    def test_bad_credentials_do_not_silently_fall_back_to_anonymous(self):
        os.environ['GOOGLE_DRIVE_DOWNLOAD_CREDENTIALS_JSON'] = 'not JSON'
        with patch.object(gdrive, '_opener') as anonymous:
            with self.assertRaisesRegex(ValueError, 'credential JSON'):
                gdrive.download('file-id', self.out)
        anonymous.assert_not_called()

    def test_separate_readonly_identity_has_priority(self):
        os.environ['GOOGLE_DRIVE_DOWNLOAD_CREDENTIALS_JSON'] = json.dumps(INFO)
        os.environ['GOOGLE_OAUTH_CREDENTIALS_JSON'] = json.dumps({'refresh_token': 'upload'})
        self.assertEqual(api.credentials(), INFO)

    def test_existing_upload_oauth_can_be_used_for_download(self):
        os.environ['GOOGLE_OAUTH_CREDENTIALS_JSON'] = json.dumps({'authorized_user': INFO})
        self.assertEqual(api.credentials(), INFO)

    def test_full_file_validated_then_published(self):
        responses = [Response(json.dumps(self.meta).encode()), Response(self.binary)]
        with patch.object(api.Client, 'open', side_effect=responses):
            api.download('file-id', 'key', self.out, INFO, log=lambda _: None)
        self.assertEqual(self.out.read_bytes(), self.binary)
        self.assertFalse(self.out.with_suffix('.zip.part').exists())

    def test_short_stream_is_resumed_without_duplicate_bytes(self):
        prefix = self.binary[:100]
        responses = [Response(json.dumps(self.meta).encode()), Response(prefix),
            Response(self.binary[100:], 206, {'Content-Range': f'bytes 100-{len(self.binary)-1}/{len(self.binary)}'})]
        with patch.object(api.Client, 'open', side_effect=responses) as opened:
            api.download('file-id', None, self.out, INFO, log=lambda _: None)
        self.assertEqual(self.out.read_bytes(), self.binary)
        self.assertEqual(opened.call_args_list[2].args[2]['Range'], 'bytes=100-')

    def test_server_ignoring_resumed_range_does_not_publish(self):
        responses = [Response(json.dumps(self.meta).encode()), Response(self.binary[:100]), Response(self.binary)]
        with patch.object(api.Client, 'open', side_effect=responses):
            with self.assertRaisesRegex(ValueError, 'resumed byte range'):
                api.download('file-id', None, self.out, INFO, log=lambda _: None)
        self.assertFalse(self.out.exists())

    def test_bad_checksum_keeps_existing_rom_intact(self):
        self.out.write_bytes(b'existing ROM')
        responses = [Response(json.dumps(self.meta).encode()), Response(b'X' * len(self.binary))]
        with patch.object(api.Client, 'open', side_effect=responses):
            with self.assertRaisesRegex(ValueError, 'MD5'):
                api.download('file-id', None, self.out, INFO, log=lambda _: None)
        self.assertEqual(self.out.read_bytes(), b'existing ROM')

    def test_html_never_published(self):
        responses = [Response(json.dumps(self.meta).encode()), Response(b'<html>quota exceeded</html>')]
        with patch.object(api.Client, 'open', side_effect=responses):
            with self.assertRaisesRegex(ValueError, 'HTML'):
                api.download('file-id', None, self.out, INFO, log=lambda _: None)
        self.assertFalse(self.out.exists())

    def test_quota_reported_once_without_retry_storm(self):
        client = api.Client(INFO)
        client.access = 'test-access'
        with patch.object(client.opener, 'open', side_effect=error(403, 'downloadQuotaExceeded')) as opened:
            with self.assertRaisesRegex(ValueError, 'API download quota'):
                client.open('file-id', {'alt': 'media'})
        self.assertEqual(opened.call_count, 1)

    def test_rate_limit_retry_and_resource_key(self):
        client = api.Client(INFO, 'resource-key', 'file-id')
        client.access = 'test-access'
        with patch.object(client.opener, 'open', side_effect=[error(403, 'userRateLimitExceeded'), Response(b'ok')]) as opened:
            with client.open('file-id', {'alt': 'media'}) as r:
                self.assertEqual(r.read(), b'ok')
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-access')
        self.assertEqual(request.get_header('X-goog-drive-resource-keys'), 'file-id/resource-key')
        self.assertEqual(opened.call_count, 2)

    def test_expired_access_token_refreshes_then_retries(self):
        client = api.Client(INFO)
        client.access = 'expired'
        with patch.object(client, 'refresh') as refresh, patch.object(client.opener, 'open', side_effect=[error(401, 'authError'), Response(b'ok')]):
            with client.open('file-id', {'alt': 'media'}):
                pass
        refresh.assert_called_once()

    def test_redirect_strips_bearer_and_refuses_other_hosts(self):
        handler = api._Redirect()
        req = urllib.request.Request(api.API + 'file-id', headers={'Authorization': 'Bearer secret',
            'X-Goog-Drive-Resource-Keys': 'file-id/key', 'Range': 'bytes=0-'})
        redirected = handler.redirect_request(req, None, 302, 'Found', {}, 'https://download.googleusercontent.com/file')
        self.assertIsNone(redirected.get_header('Authorization'))
        self.assertIsNone(redirected.get_header('X-goog-drive-resource-keys'))
        self.assertEqual(redirected.get_header('Range'), 'bytes=0-')
        for url in ('https://evil.test/file', 'https://www.googleapis.com.evil.test/', 'http://download.googleusercontent.com/file'):
            with self.assertRaisesRegex(ValueError, 'redirect'):
                handler.redirect_request(req, None, 302, 'Found', {}, url)


if __name__ == '__main__':
    unittest.main()
