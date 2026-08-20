#!/usr/bin/env python3
"""Telegram build progress for the HyperOS porter.

The workflow calls this script at the same milestones as the NothingsVN OPlus
builder.  It edits one progress message and sends a fresh final alert so the
user receives a Telegram notification when the build ends.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

INFO_DIR = Path("build_info")
FINAL = {"success", "fail", "cancelled"}
ALIASES = {"failure": "fail", "failed": "fail", "canceled": "cancelled"}
STAGES = [
    ("start", "Khởi tạo"),
    ("sync", "Đồng bộ"),
    ("download", "Tải ROM"),
    ("unpack", "Giải nén"),
    ("build", "Build"),
    ("pack", "Đóng gói"),
    ("upload", "Tải lên"),
    ("success", "Hoàn tất"),
]
STATUS = {
    "start": ("BẮT ĐẦU", "KHỞI TẠO MÔI TRƯỜNG BUILD"),
    "sync": ("ĐỒNG BỘ", "ĐỒNG BỘ DỮ LIỆU"),
    "download": ("TẢI ROM", "TẢI SOURCE ROM"),
    "unpack": ("GIẢI NÉN", "GIẢI NÉN PHÂN VÙNG"),
    "build": ("BUILD", "BUILD VÀ PATCH ROM"),
    "pack": ("ĐÓNG GÓI", "ĐÓNG GÓI ROM ZIP"),
    "upload": ("TẢI LÊN", "TẢI LÊN GOOGLE DRIVE"),
    "success": ("HOÀN TẤT", "BUILD HOÀN TẤT"),
    "fail": ("THẤT BẠI", "BUILD GẶP LỖI"),
    "cancelled": ("ĐÃ HỦY", "BUILD ĐÃ BỊ HỦY"),
}


def read(name: str) -> str:
    try:
        return (INFO_DIR / name).read_text("utf-8", errors="replace").strip()
    except Exception:
        return ""


def write(name: str, value: str) -> None:
    INFO_DIR.mkdir(parents=True, exist_ok=True)
    (INFO_DIR / name).write_text(str(value), "utf-8")


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def one_line(value: str, limit: int = 240) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def progress(status: str, previous: str) -> str:
    keys = [key for key, _ in STAGES]
    marker = previous if status in {"fail", "cancelled"} and previous else status
    current = keys.index(marker) if marker in keys else -1
    result = []
    for index, (key, label) in enumerate(STAGES):
        if index < current or status == "success":
            mark = "✓"
        elif index == current:
            mark = "!" if status in {"fail", "cancelled"} else "●"
        else:
            mark = "○"
        result.append(f"{mark} {label}")
    if status == "fail":
        result.append("! Dừng do lỗi")
    elif status == "cancelled":
        result.append("! Đã hủy")
    return " → ".join(result)


def actions_url() -> str:
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    return f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""


def reason_from_log() -> str:
    explicit = os.getenv("NOTIFY_ERROR_REASON", "").strip()
    if explicit:
        return one_line(explicit)
    path = Path(os.getenv("NOTIFY_ERROR_LOG", "build_action.log"))
    try:
        lines = path.read_text("utf-8", errors="replace").splitlines()
    except Exception:
        return "Mở GitHub Actions để xem log lỗi đầy đủ."
    needles = ("error", "failed", "fatal", "exception", "traceback", "no space left")
    for line in reversed(lines):
        if any(word in line.lower() for word in needles):
            return one_line(line)
    return one_line(lines[-1]) if lines else "Mở GitHub Actions để xem log lỗi đầy đủ."


def message(status: str) -> str:
    previous = read("last_status.txt")
    if status not in FINAL:
        write("last_status.txt", status)
    marker, title = STATUS.get(status, (status.upper(), "CẬP NHẬT BUILD"))
    build_id = os.getenv("TELEGRAM_BUILD_ID", "manual-build")
    run_bits = [
        f"#{os.getenv('GITHUB_RUN_NUMBER')}" if os.getenv("GITHUB_RUN_NUMBER") else "",
        f"lần thử {os.getenv('GITHUB_RUN_ATTEMPT')}" if os.getenv("GITHUB_RUN_ATTEMPT") else "",
        f"ID {os.getenv('GITHUB_RUN_ID')}" if os.getenv("GITHUB_RUN_ID") else "",
    ]
    lines = [
        "<b>Build HyperOS 4</b>",
        f"• <b>Trạng thái:</b> <code>{esc(marker)}</code> — <b>{esc(title)}</b>",
        f"• <b>Mã build:</b> <code>{esc(build_id)}</code>",
        f"• <b>Tiến trình:</b> {esc(progress(status, previous))}",
    ]
    fields = [
        ("Người build", os.getenv("BUILDER_NAME", ""), False),
        ("Thiết bị", read("device_name.txt"), True),
        ("Mã thiết bị", read("device_model.txt") or read("device_code.txt"), True),
        ("ROM nền", read("rom_version.txt"), True),
        ("File output", read("output_zip.txt"), True),
        ("Kho lưu trữ", os.getenv("GITHUB_REPOSITORY", ""), True),
        ("Lượt chạy", " / ".join(x for x in run_bits if x), True),
    ]
    for label, value, code in fields:
        if value:
            rendered = f"<code>{esc(one_line(value))}</code>" if code else esc(one_line(value))
            lines.append(f"• <b>{esc(label)}:</b> {rendered}")
    if actions_url():
        lines.append(f'• <b>Tiến độ build trong Actions:</b> <a href="{esc(actions_url())}">Xem tại đây</a>')
    base_url = os.getenv("BASE_ROM_URL", "")
    port_url = os.getenv("PORT_ROM_URL", "")
    if base_url:
        lines.append(f'• <b>Base ROM:</b> <a href="{esc(base_url)}">Source</a>')
    if port_url:
        lines.append(f'• <b>Port ROM:</b> <a href="{esc(port_url)}">Source</a>')
    output_url = read("output_url.txt")
    if output_url:
        lines.append(f'• <b>Tải ROM:</b> <a href="{esc(output_url)}">Google Drive</a>')
    if status == "fail":
        lines.append(f"• <b>Lý do:</b> <code>{esc(reason_from_log())}</code>")
        lines.append("• <b>Log lỗi:</b> xem artifact Build-log hoặc GitHub Actions.")
    return "\n".join(lines)[:4000]


def api(method: str, payload: dict[str, object]) -> dict:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    body = urllib.parse.urlencode(payload).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}", data=body
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def save_github_env(name: str, value: str) -> None:
    env_file = os.getenv("GITHUB_ENV", "")
    if env_file and value:
        with open(env_file, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")


def final_alert(status: str) -> str:
    titles = {
        "success": "✅ BUILD HYPEROS 4 HOÀN TẤT",
        "fail": "❌ BUILD HYPEROS 4 THẤT BẠI",
        "cancelled": "⛔ BUILD HYPEROS 4 ĐÃ HỦY",
    }
    lines = [
        f"<b>{titles[status]}</b>",
        f"• <b>Mã build:</b> <code>{esc(os.getenv('TELEGRAM_BUILD_ID', 'manual-build'))}</code>",
    ]
    device = read("device_model.txt") or read("device_code.txt") or read("device_name.txt")
    if device:
        lines.append(f"• <b>Thiết bị:</b> <code>{esc(device)}</code>")
    output = read("output_zip.txt")
    if output:
        lines.append(f"• <b>File output:</b> <code>{esc(output)}</code>")
    url = read("output_url.txt")
    if url:
        lines.append(f'• <b>Tải ROM:</b> <a href="{esc(url)}">Google Drive</a>')
    return "\n".join(lines)


def main() -> int:
    status = ALIASES.get((sys.argv[1] if len(sys.argv) > 1 else "start").lower(), (sys.argv[1] if len(sys.argv) > 1 else "start").lower())
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("BUILDER_ID") or os.getenv("TELEGRAM_CHANNEL_ID", "")
    message_id = os.getenv("TELEGRAM_MSG_ID", "")
    if not token or not chat_id:
        print("Telegram notification skipped: token/chat id is not configured.")
        return 0
    payload: dict[str, object] = {
        "chat_id": chat_id,
        "text": message(status),
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    try:
        if message_id:
            try:
                api("editMessageText", {**payload, "message_id": message_id})
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if "message is not modified" not in detail.lower():
                    result = api("sendMessage", payload)
                    message_id = str(result.get("result", {}).get("message_id", ""))
                    save_github_env("TELEGRAM_MSG_ID", message_id)
        else:
            result = api("sendMessage", payload)
            message_id = str(result.get("result", {}).get("message_id", ""))
            save_github_env("TELEGRAM_MSG_ID", message_id)
        if status in FINAL:
            api("sendMessage", {**payload, "text": final_alert(status)})
    except Exception as exc:
        print(f"Telegram notification failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
