#!/usr/bin/env python3
"""Compatibility entry point: CLI and Actions now use the same port.sh flow."""
from pathlib import Path
import sys

path = Path(sys.argv[1] if len(sys.argv) > 1 else 'port.sh')
if 'lib/port_compat.py' not in path.read_text('utf-8'):
    raise SystemExit('Update port.sh: the shared compatibility flow is missing')
print(f'{path}: shared auto-profile, camera, panel and ADB flow already installed')
