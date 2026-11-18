"""Send rendered videos to Telegram and record messages awaiting approval.

`state/telegram.json` is the bridge between the render job and the bot-poll job:

    {
      "offset": 123456789,          # last processed Telegram update_id
      "pending": {"<message_id>": {title, description, tags, topic, video_file_id, dir}},
      "uploaded": {"<file_unique_id>": {"youtube": "...", "facebook": "..."}}
    }

Both jobs must persist this file (the GitHub workflows commit it back to the repo).
Without that shared file the bot cannot match an "upload" reply to the review video.
"""
import os
import pathlib

import requests

from .util import ROOT, env, log, read_json, write_json

STATE = ROOT / "state" / "telegram.json"


def _state():
    state = read_json(STATE) if STATE.exists() else {}
    state.setdefault("offset", 0)
    state.setdefault("pending", {})
    state.setdefault("uploaded", {})
    # Older versions stored uploaded[<id>] as a plain YouTube URL string.
    for key, value in list(state["uploaded"].items()):
        if isinstance(value, str):
            state["uploaded"][key] = {"youtube": value}
    return state


def _save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    write_json(STATE, state)


def _send(token, chat_id, text):
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text[:4096]},
        timeout=60,
    )
    response.raise_for_status()
    return response.json().get("result", {})


def telegram_message(text):
    """Send a plain status or error update to the configured Telegram chat."""
    token, chat = env("TELEGRAM_BOT_TOKEN", False), env("TELEGRAM_CHAT_ID", False)
    if not token or not chat:
        log("Telegram not configured, skipping status notification")
        return None
    safe_text = str(text)
    for key, value in os.environ.items():
        if value and len(value) >= 8 and any(
            marker in key.upper() for marker in ("TOKEN", "SECRET", "API_KEY", "CLIENT_SECRET", "REFRESH")
        ):
            safe_text = safe_text.replace(value, "[redacted]")
    return _send(token, chat, safe_text)


def telegram(out_dir, text_extra="", remaining=None):
    token, chat = env("TELEGRAM_BOT_TOKEN", False), env("TELEGRAM_CHAT_ID", False)
    if not token or not chat:
        log("Telegram not configured, skipping notification")
        return None
    out_dir = pathlib.Path(out_dir).resolve()
    meta = read_json(out_dir / "meta.json")
    caption = f"ভিডিও রিভিউ: {meta.get('title', '')}\n\n{text_extra}\n"
    caption += "আপলোড করতে এই ভিডিও মেসেজের reply হিসেবে upload লিখুন।\n"
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
        "title": meta.get("title", ""),
        "description": meta.get("description", ""),
        "tags": meta.get("tags", []),
        "topic": meta.get("topic", ""),
        "dir": str(out_dir),
        "video_file_id": (message.get("video") or {}).get("file_id"),
    }
    _save(state)
    count = remaining_scripts() if remaining is None else remaining
    _send(token, chat, f"বাকি স্ক্রিপ্ট: {count}")
    log("Telegram notified")
    return message["message_id"]


def remaining_scripts():
    from .cli import pending_script_count
    return pending_script_count()
