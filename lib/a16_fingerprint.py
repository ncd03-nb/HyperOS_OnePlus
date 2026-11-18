"""Install the optical fingerprint bridge on the verified Ace 3V SDK36 port."""
import hashlib
import json

from binary_delta import apply_delta

COMPOSER = 'vendor/bin/hw/vendor.qti.hardware.display.composer-service'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def install(root, profile, port):
    descriptor = json.loads((profile / 'profile.json').read_text('utf-8'))
    product = port.properties(root / 'product/etc/build.prop')
    version = product.get('ro.mi.os.version.incremental', '').partition('|')[0].strip()
    if product.get('ro.mi.os.version.code') != '3' or version != descriptor['hyperos_version']:
        return {'status': 'skipped', 'reason': 'HyperOS build has no verified fingerprint profile'}

    observed = []
    mismatch = []
    for entry in descriptor['patches']:
        path = root / entry['path']
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            mismatch.append(entry['path'])
            continue
        data = path.read_bytes()
        digest = sha(data)
        if digest not in (entry['before_sha256'], entry['after_sha256']):
            mismatch.append(f'{entry["path"]} (sha256={digest})')
        observed.append((entry, path, data, digest))

    composer = root / COMPOSER
    if not composer.is_file() or sha(composer.read_bytes()) != descriptor['vendor_composer_sha256']:
        mismatch.append(COMPOSER)

    if mismatch:
        if any(digest == entry['after_sha256'] for entry, _, _, digest in observed):
            raise ValueError('Incompatible tree already contains Ace 3V fingerprint changes: '
                             + ', '.join(mismatch))
        return {'status': 'skipped', 'reason': 'Fingerprint inputs differ from the verified profile',
                'mismatched': mismatch}

    replacements = []
    for entry, path, original, digest in observed:
        if digest == entry['after_sha256']:
            continue
        delta = (profile / entry['patch']).read_bytes()
        if sha(delta) != entry['patch_sha256']:
            raise ValueError('Corrupt Ace 3V fingerprint delta: ' + entry['patch'])
        patched = apply_delta(original, delta, entry['after_bytes'])
        if sha(patched) != entry['after_sha256']:
            raise ValueError('Ace 3V fingerprint output mismatch: ' + entry['path'])
        replacements.append((path, patched))

    # Change all three matched components together, retaining their EROFS metadata.
    for path, patched in replacements:
        path.write_bytes(patched)
    vendor_props = root / 'vendor/build.prop'
    geometry = descriptor['geometry']
    before = port.properties(vendor_props)
    geometry_changed = any(before.get(key) != value for key, value in geometry.items())
    if geometry_changed:
        port.set_props(vendor_props, geometry)
    return {'status': 'applied' if replacements or geometry_changed else 'already_applied',
            'hyperos_version': version, 'geometry': geometry,
            'patched_files': [str(path.relative_to(root).as_posix()) for path, _ in replacements]}
