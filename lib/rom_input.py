#!/usr/bin/env python3
"""Resolve payload, nested raw images, and toolbuild ZIP -> super/super.img."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import zipfile

from super_extractor import extract as extract_super


def choose(candidates, description):
    if len(candidates) != 1:
        raise ValueError(f'{description}: expected one image, found {len(candidates)}')
    return candidates[0]


def select_raw(entries, part, basename):
    for name in (part + '.img', part + '_a.img', part + '_b.img'):
        candidates = [item for item in entries if basename(item) == name and
                      (item.file_size if isinstance(item, zipfile.ZipInfo) else item.stat().st_size) != 0]
        if candidates:
            return choose(candidates, name)


def copy_stream(source, target):
    if target.exists():
        raise ValueError(f'Refusing to overwrite {target.name}; choose a fresh input directory')
    temporary = target.with_name(target.name + '.part')
    with temporary.open('xb') as stream:
        shutil.copyfileobj(source, stream, 1 << 20)
    temporary.rename(target)


def prepare(source, output, parts, payload_tool=None):
    source, output = Path(source), Path(output)
    if not parts or any(not re.fullmatch(r'[A-Za-z0-9_]+', part) for part in parts):
        raise ValueError('Invalid requested partitions')
    output.mkdir(parents=True, exist_ok=True)
    report = {'source_type': None, 'partitions': {}}
    payload = None
    super_image = None
    if source.is_dir():
        entries = [path for path in source.rglob('*.img') if path.is_file()]
        for part in parts:
            image = select_raw(entries, part, lambda path: path.name)
            if image:
                with image.open('rb') as stream:
                    copy_stream(stream, output / (part + '.img'))
                report['partitions'][part] = {'source': str(image)}
        if len(report['partitions']) != len(parts):
            super_image = choose([path for path in entries if path.name == 'super.img'], 'super.img')
        report['source_type'] = 'image_directory'
    elif zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as bundle:
            entries = [item for item in bundle.infolist() if not item.is_dir()]
            payloads = [item for item in entries if PurePosixPath(item.filename).name == 'payload.bin']
            if payloads:
                item = choose(payloads, 'payload.bin')
                payload = output / '.input_payload.bin'
                with bundle.open(item) as stream:
                    copy_stream(stream, payload)
                report['source_type'] = 'payload_zip'
            else:
                for part in parts:
                    image = select_raw(entries, part, lambda item: PurePosixPath(item.filename).name)
                    if image:
                        with bundle.open(image) as stream:
                            copy_stream(stream, output / (part + '.img'))
                        report['partitions'][part] = {'zip_member': image.filename}
                if len(report['partitions']) != len(parts):
                    image = choose([item for item in entries if PurePosixPath(item.filename).name == 'super.img'], 'super.img')
                    super_image = output / '.input_super.img'
                    with bundle.open(image) as stream:
                        copy_stream(stream, super_image)
                    report['super_member'] = image.filename
                report['source_type'] = 'super_zip' if super_image else 'raw_image_zip'
    else:
        with source.open('rb') as stream:
            magic = stream.read(4)
        if magic == b'CrAU':
            payload = source
            report['source_type'] = 'payload'
        else:
            super_image = source
            report['source_type'] = 'super'
    if payload:
        command = ([payload_tool, str(payload), '-o', str(output), '-i', ','.join(parts)] if payload_tool else
                   [sys.executable, str(Path(__file__).with_name('payload_extractor.py')), '-o', str(output), '-p', ','.join(parts), str(payload)])
        subprocess.run(command, check=True)
        report['partitions'] = {part: {'payload': True} for part in parts}
    if super_image:
        missing = [part for part in parts if part not in report['partitions']]
        report['partitions'].update(extract_super(super_image, output, missing))
    for part in parts:
        if not (output / (part + '.img')).is_file() or not (output / (part + '.img')).stat().st_size:
            raise ValueError(f'{part}.img was not produced')
    for temporary in (payload, super_image):
        if temporary and temporary.parent == output and temporary.name.startswith('.input_'):
            temporary.unlink()
    (output / 'input_manifest.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('-o', '--output', required=True)
    parser.add_argument('-p', '--parts', required=True)
    parser.add_argument('--payload-tool')
    args = parser.parse_args()
    try:
        prepare(args.source, args.output, args.parts.split(','), args.payload_tool)
    except (ValueError, OSError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        parser.exit(1, f'ROM input error: {error}\n')


if __name__ == '__main__':
    main()
