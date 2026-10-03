import hashlib
import io
import json
from pathlib import Path
import struct
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import gdrive
import rom_input
import release_download
import super_extractor as lp


def super_fixture():
    """AOSP LP v1.2: populated system_a, empty system_b, linear + zero extents."""
    image = bytearray(65536)
    geometry = bytearray(struct.pack('<2I32s3I', lp.GEOMETRY_MAGIC, 52, bytes(32), 4096, 2, 4096))
    geometry[8:40] = hashlib.sha256(geometry).digest()
    image[4096:4148] = image[8192:8244] = geometry
    partitions = struct.pack('<36s4I', b'system_a', 1, 0, 2, 0) + struct.pack('<36s4I', b'system_b', 1, 2, 0, 0)
    extents = struct.pack('<QIQI', 8, 0, 64, 0) + struct.pack('<QIQI', 8, 1, 0, 0)
    groups = struct.pack('<36sIQ', b'default', 0, 0)
    devices = struct.pack('<Q2IQ36sI', 64, 4096, 0, len(image), b'super', 0)
    tables = partitions + extents + groups + devices
    header = bytearray(256)
    struct.pack_into('<I2HI', header, 0, lp.HEADER_MAGIC, 10, 2, len(header))
    struct.pack_into('<I', header, 44, len(tables))
    header[48:80] = hashlib.sha256(tables).digest()
    start = 0
    for offset, table, stride in ((80, partitions, 52), (92, extents, 24), (104, groups, 48), (116, devices, 64)):
        struct.pack_into('<3I', header, offset, start, len(table) // stride, stride)
        start += len(table)
    header[12:44] = hashlib.sha256(header).digest()
    metadata = header + tables
    image[12288:12288 + len(metadata)] = metadata
    image[20480:20480 + len(metadata)] = metadata
    image[32768:36864] = b'abcd' * 1024
    return bytes(image), b'abcd' * 1024 + bytes(4096)


def sparse_fixture(image):
    chunks = []
    for start in range(0, len(image), 4096):
        block = image[start:start + 4096]
        if not any(block):
            kind, data = 0xcac3, b''
        elif block == block[:4] * 1024:
            kind, data = 0xcac2, block[:4]
        else:
            kind, data = 0xcac1, block
        chunks.append(struct.pack('<2H2I', kind, 0, 1, 12 + len(data)) + data)
    return struct.pack('<I4H4I', lp.SPARSE_MAGIC, 1, 0, 28, 12, 4096, len(chunks), len(chunks), 0) + b''.join(chunks)


class ROMInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw, self.expected = super_fixture()

    def run_image(self, image):
        path = self.root / 'super.img'
        path.write_bytes(image)
        report = rom_input.prepare(path, self.root / 'out', ['system'], 'must-not-run')
        self.assertEqual((self.root / 'out/system.img').read_bytes(), self.expected)
        self.assertEqual(report['partitions']['system']['logical_partition'], 'system_a')
        self.assertEqual(report['partitions']['system']['sha256'], hashlib.sha256(self.expected).hexdigest())

    def test_raw_super(self):
        self.run_image(self.raw)

    def test_sparse_raw_fill_holes(self):
        self.run_image(sparse_fixture(self.raw))

    def test_backup_metadata_and_geometry(self):
        image = bytearray(self.raw)
        image[4104] ^= 1
        image[12300] ^= 1
        self.run_image(image)

    def test_bad_metadata_checksums_rejected(self):
        image = bytearray(self.raw)
        image[12300] ^= 1
        image[20492] ^= 1
        path = self.root / 'super.img'
        path.write_bytes(image)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            lp.extract(path, self.root / 'out', ['system'])
        self.assertFalse((self.root / 'out/system.img').exists())

    def test_truncated_sparse_rejected(self):
        path = self.root / 'super.img'
        path.write_bytes(sparse_fixture(self.raw)[:-1])
        with self.assertRaises(ValueError):
            lp.extract(path, self.root / 'out', ['system'])

    def test_missing_partition_rejected_before_writing(self):
        path = self.root / 'super.img'
        path.write_bytes(self.raw)
        with self.assertRaisesRegex(ValueError, 'product'):
            lp.extract(path, self.root / 'out', ['system', 'product'])
        self.assertFalse((self.root / 'out/system.img').exists())

    def test_toolbuild_nested_zip_skips_firmware_and_scripts(self):
        path = self.root / 'rom.zip'
        with zipfile.ZipFile(path, 'w') as bundle:
            bundle.writestr('super/super.img', sparse_fixture(self.raw))
            bundle.writestr('images/boot.img', b'firmware')
            bundle.writestr('install.bat', b'never execute')
        report = rom_input.prepare(path, self.root / 'out', ['system'], 'must-not-run')
        self.assertEqual(report['source_type'], 'super_zip')
        self.assertEqual((self.root / 'out/system.img').read_bytes(), self.expected)
        self.assertEqual({p.name for p in (self.root / 'out').iterdir()}, {'system.img', 'input_manifest.json'})

    def test_nested_raw_images_normalized(self):
        path = self.root / 'rom.zip'
        with zipfile.ZipFile(path, 'w') as bundle:
            bundle.writestr('images/system_a.img', self.expected)
            bundle.writestr('images/system_b.img', b'')
        report = rom_input.prepare(path, self.root / 'out', ['system'], 'must-not-run')
        self.assertEqual(report['source_type'], 'raw_image_zip')
        self.assertEqual((self.root / 'out/system.img').read_bytes(), self.expected)

    def test_directory_empty_a_falls_back_to_b(self):
        source = self.root / 'input'
        source.mkdir()
        (source / 'system_a.img').touch()
        (source / 'system_b.img').write_bytes(self.expected)
        rom_input.prepare(source, self.root / 'out', ['system'])
        self.assertEqual((self.root / 'out/system.img').read_bytes(), self.expected)

    def test_duplicate_zip_images_rejected(self):
        path = self.root / 'rom.zip'
        with zipfile.ZipFile(path, 'w') as bundle:
            bundle.writestr('one/super.img', self.raw)
            bundle.writestr('two/super.img', self.raw)
        with self.assertRaisesRegex(ValueError, 'found 2'):
            rom_input.prepare(path, self.root / 'out', ['system'])

    def test_payload_tool_used_only_for_payload(self):
        path = self.root / 'rom.zip'
        with zipfile.ZipFile(path, 'w') as bundle:
            bundle.writestr('payload.bin', b'CrAUfixture')
        def run(command, check):
            self.assertEqual(command[0], 'payload-tool')
            (self.root / 'out/system.img').write_bytes(self.expected)
        with patch.object(rom_input.subprocess, 'run', side_effect=run):
            report = rom_input.prepare(path, self.root / 'out', ['system'], 'payload-tool')
        self.assertEqual(report['source_type'], 'payload_zip')


@unittest.skipUnless(os.name == 'posix', 'Linux shell integration')
class ShellInputTests(unittest.TestCase):
    def test_shell_selects_super_even_with_payload_dumper_and_propagates_failure(self):
        repo = Path(__file__).resolve().parents[1]
        source = (repo / 'port.sh').read_text()
        functions = source[source.index('find_dumper() {'):source.index('unpack_erofs() {')]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw, expected = super_fixture()
            path = root / 'rom.zip'
            with zipfile.ZipFile(path, 'w') as bundle:
                bundle.writestr('super/super.img', sparse_fixture(raw))
            prefix = ('set -euo pipefail\n'
                      f'HERE={shlex.quote(str(repo))}\nPY={shlex.quote(sys.executable)}\n'
                      'log() { echo "$*" >&2; }\n'
                      'quiet_run() { "$@"; }\n' + functions +
                      '\nfind_dumper() { echo must-not-run; }\n')
            call = f'get_images {shlex.quote(str(path))} {shlex.quote(str(root / "out"))} donor system\n'
            result = subprocess.run(['bash', '-c', prefix + call + 'test -f "$IMG_system"'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'out/system.img').read_bytes(), expected)
            call = f'get_images {shlex.quote(str(path))} {shlex.quote(str(root / "bad-out"))} donor product\n'
            result = subprocess.run(['bash', '-c', prefix + call + 'echo should-not-reach'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('should-not-reach', result.stdout)
            self.assertIn('no populated partition', result.stderr)


class Response(io.BytesIO):
    def __init__(self, data, content_type='text/html', length=None):
        super().__init__(data)
        self.headers = {'Content-Type': content_type, 'Content-Length': str(len(data) if length is None else length)}


class DriveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'rom.zip'

    def test_share_url_and_host_validation(self):
        self.assertEqual(gdrive.parse_reference('https://drive.google.com/file/d/1Ct-b4gtmCPn0afUve48bc0_S4_RvwWFy/view?resourcekey=abc'),
                         ('1Ct-b4gtmCPn0afUve48bc0_S4_RvwWFy', 'abc'))
        self.assertFalse(gdrive.is_drive_url('https://drive.google.com.evil.example/file/d/id/view'))
        with self.assertRaises(ValueError):
            gdrive.parse_reference('https://drive.google.com/drive/folders/abc')

    def test_confirmation_and_resource_key(self):
        form = b"<html><input name='id' value='file-id'><input name='confirm' value='t'><input name='uuid' value='x&amp;y'></html>"
        binary = b'PK\x03\x04' + bytes(2000)
        with patch.object(gdrive, '_opener') as make:
            make.return_value.open.side_effect = [Response(form), Response(binary, 'application/zip')]
            gdrive.download('https://drive.google.com/file/d/file-id/view?resourcekey=secret', self.output, log=lambda _: None)
            second_url = make.return_value.open.call_args_list[1].args[0]
        self.assertIn('resourcekey=secret', second_url)
        self.assertIn('uuid=x%26y', second_url)
        self.assertEqual(self.output.read_bytes(), binary)

    def test_quota_after_confirm_does_not_replace_existing_zip(self):
        self.output.write_bytes(b'previous ROM')
        form = b'<html><input name="confirm" value="t"></html>'
        quota = b'<html>Google Drive - Quota exceeded' + bytes(2000) + b'</html>'
        with patch.object(gdrive, '_opener') as make:
            make.return_value.open.side_effect = [Response(form), Response(quota)]
            with self.assertRaisesRegex(ValueError, 'quota exceeded'):
                gdrive.download('file-id', self.output)
        self.assertEqual(self.output.read_bytes(), b'previous ROM')
        self.assertFalse(self.output.with_name('rom.zip.part').exists())

    def test_unexpected_html_without_header_rejected(self):
        with patch.object(gdrive, '_opener') as make:
            make.return_value.open.return_value = Response(b'<!doctype html><html>failed</html>', 'application/octet-stream')
            with self.assertRaisesRegex(ValueError, 'HTML'):
                gdrive.download('file-id', self.output)
        self.assertFalse(self.output.exists())

    def test_truncated_download_not_published(self):
        with patch.object(gdrive, '_opener') as make:
            make.return_value.open.return_value = Response(b'PKshort', 'application/zip', 2000)
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                gdrive.download('file-id', self.output, log=lambda _: None)
        self.assertFalse(self.output.exists())


class ReleaseDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'rom.zip'
        self.original = b'PK\x03\x04' + bytes(2000)
        self.parts = [self.original[:1000], self.original[1000:]]
        self.manifest = {'format': 'toolbuild-zip-parts-v1', 'filename': 'rom.zip',
                         'size_bytes': len(self.original), 'sha256': hashlib.sha256(self.original).hexdigest(),
                         'assets': [{'name': f'rom.zip.part{index + 1:03d}', 'size_bytes': len(data),
                                     'sha256': hashlib.sha256(data).hexdigest(), 'url': f'https://example.test/{index}'}
                                    for index, data in enumerate(self.parts)]}

    def test_parts_reassembled_byte_exactly(self):
        with patch.object(release_download, 'open_url') as opened:
            opened.side_effect = [io.BytesIO(json.dumps(self.manifest).encode()), *[io.BytesIO(p) for p in self.parts]]
            release_download.download('https://example.test/rom.zip.download.json', self.output)
        self.assertEqual(self.output.read_bytes(), self.original)

    def test_corrupt_part_does_not_publish_or_replace_rom(self):
        self.output.write_bytes(b'previous ROM')
        with patch.object(release_download, 'open_url') as opened:
            opened.side_effect = [io.BytesIO(json.dumps(self.manifest).encode()), io.BytesIO(b'x' * 1000)]
            with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
                release_download.download('https://example.test/rom.zip.download.json', self.output)
        self.assertEqual(self.output.read_bytes(), b'previous ROM')

    def test_whole_zip_checksum_required(self):
        self.manifest['sha256'] = '0' * 64
        with patch.object(release_download, 'open_url') as opened:
            opened.side_effect = [io.BytesIO(json.dumps(self.manifest).encode()), *[io.BytesIO(p) for p in self.parts]]
            with self.assertRaisesRegex(ValueError, 'Reassembled ZIP'):
                release_download.download('https://example.test/rom.zip.download.json', self.output)
        self.assertFalse(self.output.exists())

    def test_invalid_manifest_rejected_before_creating_download(self):
        self.manifest['assets'][0]['url'] = 'file:///etc/passwd'
        with patch.object(release_download, 'open_url', return_value=io.BytesIO(json.dumps(self.manifest).encode())):
            with self.assertRaisesRegex(ValueError, 'URL'):
                release_download.download('https://example.test/rom.zip.download.json', self.output)
        self.assertFalse(self.output.with_name('rom.zip.part').exists())


if __name__ == '__main__':
    unittest.main()
