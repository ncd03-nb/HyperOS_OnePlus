#!/usr/bin/env python3
"""Reassemble a toolbuild GitHub download manifest into the original ROM ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.request
from urllib.parse import urlparse

BUFFER = 1 << 20


def open_url(url):
    if urlparse(url).scheme != 'https':
        raise ValueError('Release manifest and asset URLs must use HTTPS')
    request = urllib.request.Request(url, headers={'User-Agent': 'HyperOS-OnePlus', 'Accept-Encoding': 'identity'})
    return urllib.request.urlopen(request, timeout=60)


def validate(manifest):
    if not isinstance(manifest, dict) or manifest.get('format') != 'toolbuild-zip-parts-v1':
        raise ValueError('Unsupported ROM download manifest')
    assets = manifest.get('assets')
    if not isinstance(assets, list) or not 1 <= len(assets) <= 1000:
        raise ValueError('Invalid manifest asset count')
    names = set()
    for item in [manifest, *assets]:
        if not isinstance(item, dict) or type(item.get('size_bytes')) is not int or item['size_bytes'] <= 0:
            raise ValueError('Invalid manifest size')
        if not isinstance(item.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', item['sha256']):
            raise ValueError('Invalid manifest SHA256')
    for item in assets:
        name = item.get('name')
        if not isinstance(name, str) or not name or name in names or '/' in name or '\\' in name:
            raise ValueError('Invalid or duplicate asset name')
        names.add(name)
        if not isinstance(item.get('url'), str) or urlparse(item['url']).scheme != 'https':
            raise ValueError('Invalid asset download URL')
    if sum(item['size_bytes'] for item in assets) != manifest['size_bytes']:
        raise ValueError('Manifest asset sizes do not match original ZIP')
    return assets


def download(url, output):
    with open_url(url) as response:
        encoded = response.read((2 << 20) + 1)
    if len(encoded) > 2 << 20:
        raise ValueError('ROM download manifest is too large')
    manifest = json.loads(encoded)
    assets = validate(manifest)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.part')
    digest = hashlib.sha256()
    with temporary.open('xb') as stream:
        for item in assets:
            print(f"Downloading release asset {item['name']}", flush=True)
            part_digest, count = hashlib.sha256(), 0
            with open_url(item['url']) as response:
                while chunk := response.read(BUFFER):
                    count += len(chunk)
                    if count > item['size_bytes']:
                        raise ValueError(f"Asset too large: {item['name']}")
                    stream.write(chunk)
                    digest.update(chunk)
                    part_digest.update(chunk)
            if count != item['size_bytes'] or part_digest.hexdigest() != item['sha256']:
                raise ValueError(f"Asset size/SHA256 mismatch: {item['name']}")
    if digest.hexdigest() != manifest['sha256']:
        raise ValueError('Reassembled ZIP SHA256 mismatch')
    temporary.replace(output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('output')
    args = parser.parse_args()
    try:
        download(args.url, args.output)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Release download error: {error}\n')
