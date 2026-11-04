#!/usr/bin/env python3
"""Recreate the recorded SDK33 deltas from original/fixed extracted trees.

This authoring tool requires bsdiff4==1.4.1. The ROM builder needs only Python
stdlib and does not execute this script. No signing keys or ROM download needed.
"""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--fixed', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import bsdiff4
    from importlib.metadata import version
    if version('bsdiff4') != '1.4.1':
        raise ValueError('Reproducible authoring requires bsdiff4==1.4.1')
    profile = Path(__file__).resolve().parents[1] / 'devices/OnePlusAce3V/android-33'
    descriptor = json.loads((profile / 'profile.json').read_text())
    pending = []
    sha = lambda data: hashlib.sha256(data).hexdigest()
    for entry in descriptor['patches']:
        original = (args.original / entry['path']).read_bytes()
        fixed = (args.fixed / entry['path']).read_bytes()
        if sha(original) != entry['before_sha256'] or sha(fixed) != entry['after_sha256']:
            raise ValueError('Input hash mismatch: ' + entry['path'])
        patch = bsdiff4.diff(original, fixed)
        if sha(patch) != entry['patch_sha256']:
            raise ValueError('Non-reproducible delta: ' + entry['path'])
        pending.append((args.output / Path(entry['patch']).name, patch))
    args.output.mkdir(parents=True, exist_ok=True)
    for path, data in pending:
        path.write_bytes(data)
        print(path.name, sha(data))


if __name__ == '__main__':
    main()
