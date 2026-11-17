"""Send rendered videos to Telegram and record messages awaiting approval."""
import json
import pathlib

import requests

from .util import ROOT, env, log, read_json, write_json

STATE = ROOT / "state" / "telegram.json"


def _state():
    if STATE.exists():
        return read_json(STATE)
    return {"offset": 0, "pending": {}, "uploaded": {}}


def _save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    write_json(STATE, state)


def telegram(out_dir, text_extra=""):
    token, chat = env("TELEGRAM_BOT_TOKEN", False), env("TELEGRAM_CHAT_ID", False)
    if not token or not chat:
        log("Telegram not configured, skipping notification")
        return None
    out_dir = pathlib.Path(out_dir)
    meta = read_json(out_dir / "meta.json")
    caption = f"ভিডিও রিভিউ: {meta['title']}\n\n{text_extra}\n"
    caption += "আপলোড করতে এই মেসেজের reply হিসেবে upload লিখুন।\n"
    caption += "বিকল্প হিসেবে reply করে নতুন ভিডিওও পাঠাতে পারেন।\n"
    caption += "অথবা /remaining লিখে বাকি স্ক্রিপ্ট দেখুন।"
    video = out_dir / "final.mp4"
    with open(video, "rb") as video_file:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendVideo",
            data={"chat_id": chat, "caption": caption[:1024], "supports_streaming": "true"},
            files={"video": video_file}, timeout=600,
        )
    response.raise_for_status()
    message = response.json().get("result", {})
    state = _state()
    state["pending"][str(message["message_id"])] = {
        "title": meta["title"],
        "description": meta["description"],
        "tags": meta.get("tags", []),
        "topic": meta.get("topic", ""),
        "video_file_id": (message.get("video") or {}).get("file_id"),
    }
    _save(state)
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat, "text": f"বাকি স্ক্রিপ্ট: {remaining_scripts()}"},
        timeout=60,
    ).raise_for_status()
    log("Telegram notified")
    return message["message_id"]


def remaining_scripts():
    from .cli import pending_script_count
    return pending_script_count()
