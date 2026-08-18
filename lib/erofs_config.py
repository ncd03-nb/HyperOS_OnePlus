#!/usr/bin/env python3
# Shared SELinux config synthesis for the porter (used by both port.py and
# port.sh). erofs fs_config / file_contexts entries have to be generated for
# every file that gets moved in or overlaid, and the SELinux label is inherited
# from the nearest parent directory that already has a context. Doing that
# gap-fill with nested dicts is the one part that is genuinely nicer in Python
# than in awk, so the Bash driver shells out to this for it.
#
# CLI:
#   erofs_config.py sync <work> <part>
#   erofs_config.py set  <work> <part> <rel> <label> [mode]

import os
import sys

# vendor/odm partition roots are root:shell(2000); the rest are root:root
PART_ROOT_GID = {"vendor": 2000, "odm": 2000}
PART_DEFAULT_LABEL = {
    "system": "system_file",
    "product": "system_file",
    "system_ext": "system_file",
    "vendor": "vendor_file",
    "odm": "vendor_file",
}

_FC_ESCAPE = ".+[](){}^$?*|\\"


def log(msg):
    print(msg, flush=True)


def fc_escape(path):
    return "".join("\\" + c if c in _FC_ESCAPE else c for c in path)


def load_fs_config(path):
    entries, order = {}, []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                parts = line.split(" ")
                entries[parts[0]] = parts[1:]
                order.append(parts[0])
    return entries, order


def load_file_contexts(path):
    lines, by_norm = [], {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.rstrip("\n")
                if not s.strip():
                    continue
                lines.append(s)
                by_norm[s.split(" ")[0].replace("\\", "")] = s
    return lines, by_norm


def nearest_context(norm_path, by_norm):
    p = norm_path
    while p:
        if p in by_norm:
            return by_norm[p].split(" ", 1)[1]
        if "/" not in p.rstrip("/"):
            break
        p = p.rsplit("/", 1)[0]
    return None


def _cfg_paths(work, part):
    cfg = os.path.join(work, "config")
    os.makedirs(cfg, exist_ok=True)
    return (os.path.join(cfg, part + "_fs_config"),
            os.path.join(cfg, part + "_file_contexts"))


def sync_config(work, part):
    part_dir = os.path.join(work, part)
    fsc_path, fc_path = _cfg_paths(work, part)
    fs_entries, fs_order = load_fs_config(fsc_path)
    fc_lines, fc_by_norm = load_file_contexts(fc_path)

    root_gid = PART_ROOT_GID.get(part, 0)
    default_label = "u:object_r:%s:s0" % PART_DEFAULT_LABEL.get(part, "system_file")
    added_fs = added_fc = 0

    def ensure(rel, is_dir):
        nonlocal added_fs, added_fc
        fs_key = "%s/%s" % (part, rel) if rel else part + "/"
        if fs_key not in fs_entries:
            mode = "0755" if is_dir else "0644"
            fs_entries[fs_key] = ["0", str(root_gid), mode]
            fs_order.append(fs_key)
            added_fs += 1
        norm = "/%s/%s" % (part, rel) if rel else "/" + part
        if norm not in fc_by_norm:
            label = nearest_context(norm, fc_by_norm) or default_label
            line = "%s %s" % (fc_escape(norm), label)
            fc_lines.append(line)
            fc_by_norm[norm] = line
            added_fc += 1

    ensure("", True)
    for root, dirs, files in os.walk(part_dir):
        relbase = os.path.relpath(root, part_dir)
        relbase = "" if relbase == "." else relbase
        for d in sorted(dirs):
            ensure((relbase + "/" + d) if relbase else d, True)
        for fname in sorted(files):
            ensure((relbase + "/" + fname) if relbase else fname, False)

    if added_fs or added_fc:
        with open(fsc_path, "w", encoding="utf-8") as f:
            for k in fs_order:
                f.write(" ".join([k] + fs_entries[k]) + "\n")
        with open(fc_path, "w", encoding="utf-8") as f:
            for line in fc_lines:
                f.write(line + "\n")
    log("  %s: +%d fs_config, +%d file_contexts entries" % (part, added_fs, added_fc))


def set_context(work, part, rel, label, mode="0644"):
    fsc_path, fc_path = _cfg_paths(work, part)
    root_gid = PART_ROOT_GID.get(part, 0)
    fs_entries, fs_order = load_fs_config(fsc_path)
    fc_lines, fc_by_norm = load_file_contexts(fc_path)

    fs_key = "%s/%s" % (part, rel)
    if fs_key not in fs_entries:
        fs_order.append(fs_key)
    fs_entries[fs_key] = ["0", str(root_gid), mode]

    norm = "/%s/%s" % (part, rel)
    newline = "%s u:object_r:%s:s0" % (fc_escape(norm), label)
    for i, line in enumerate(fc_lines):
        if line.split(" ")[0].replace("\\", "") == norm:
            fc_lines[i] = newline
            break
    else:
        fc_lines.append(newline)

    with open(fsc_path, "w", encoding="utf-8") as f:
        for k in fs_order:
            f.write(" ".join([k] + fs_entries[k]) + "\n")
    with open(fc_path, "w", encoding="utf-8") as f:
        for line in fc_lines:
            f.write(line + "\n")


def _main(argv):
    if not argv:
        print(__doc__ or "usage: erofs_config.py sync|set ...", file=sys.stderr)
        return 2
    cmd = argv[0]
    if cmd == "sync":
        sync_config(argv[1], argv[2])
    elif cmd == "set":
        rel = argv[3]
        label = argv[4]
        mode = argv[5] if len(argv) > 5 else "0644"
        set_context(argv[1], argv[2], rel, label, mode)
    else:
        print("unknown command: %s" % cmd, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
