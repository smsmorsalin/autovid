"""Telegram notification (the 'approval' step): sends the finished video + a summary to your phone."""
import pathlib

import requests

import json

from .util import env, log, read_json


def telegram(out_dir, text_extra=""):
    token, chat = env("TELEGRAM_BOT_TOKEN", False), env("TELEGRAM_CHAT_ID", False)
    if not token or not chat:
        log("Telegram not configured, skipping notification")
        return
    out_dir = pathlib.Path(out_dir)
    meta = read_json(out_dir / "meta.json")
    warn = ""
    try:
        sc = json.loads((out_dir / "script.json").read_text(encoding="utf-8"))
        if sc.get("facts_verified") is False:
            warn = "WARNING: web search was unavailable, facts come from AI memory. Check every date and number!\n\n"
    except Exception:
        pass
    caption = (f"{warn}Ready for review: {meta['title']}\n\n{text_extra}\n"
               "To publish: run the 'Publish' workflow with this run's ID.")[:1000]
    video = out_dir / "final.mp4"
    with open(video, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendVideo",
                          data={"chat_id": chat, "caption": caption, "supports_streaming": "true"},
                          files={"video": f}, timeout=600)
    if r.status_code >= 400:
        # file too large for bots (50 MB) -> send text only
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": chat, "text": caption + "\n(video too large for Telegram: download the artifact)"},
                      timeout=60)
    log("Telegram notified")
