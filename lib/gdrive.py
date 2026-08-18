#!/usr/bin/env python3
# Dependency-free Google Drive downloader for large files.
#
# Big Drive files can't be served directly: the first request returns an HTML
# "can't scan for viruses" interstitial carrying a confirm token, and only the
# second request (with that token) returns the file. This handles both, using
# the file id so it keeps working after any pre-signed link expires.
#
#   gdrive.py <file_id> <output_path>

import http.cookiejar
import re
import sys
import urllib.request
from urllib.parse import urlencode

BASE = "https://drive.usercontent.google.com/download"


def _opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "Mozilla/5.0")]
    return op


def _form_params(html):
    params = {}
    for tag in re.findall(r"<input[^>]+>", html):
        n = re.search(r'name="([^"]+)"', tag)
        v = re.search(r'value="([^"]*)"', tag)
        if n:
            params[n.group(1)] = v.group(1) if v else ""
    return params


def download(file_id, out, log=print):
    op = _opener()
    resp = op.open(BASE + "?" + urlencode({"id": file_id, "export": "download"}))
    ctype = resp.headers.get("Content-Type", "")
    if "text/html" in ctype.lower():
        html = resp.read().decode("utf-8", "replace")
        p = _form_params(html)
        q = {"id": p.get("id") or file_id,
             "export": p.get("export") or "download",
             "confirm": p.get("confirm") or "t"}
        for k in ("uuid", "at"):
            if p.get(k):
                q[k] = p[k]
        resp = op.open(BASE + "?" + urlencode(q))
    total = resp.headers.get("Content-Length")
    log("  downloading %s%s" % (file_id, " (%s bytes)" % total if total else ""))
    got = 0
    with open(out, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
    if got < 1024:
        raise ValueError("download too small (%d bytes) - link/id may be wrong" % got)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: gdrive.py <file_id> <output_path>", file=sys.stderr)
        sys.exit(2)
    download(sys.argv[1], sys.argv[2])
