"""Telegram notification (the 'approval' step): sends the finished video + a summary to your phone."""
import pathlib

import requests

from .util import env, log, read_json


def telegram(out_dir, text_extra=""):
    token, chat = env("TELEGRAM_BOT_TOKEN", False), env("TELEGRAM_CHAT_ID", False)
    if not token or not chat:
        log("Telegram not configured, skipping notification")
        return
    out_dir = pathlib.Path(out_dir)
    meta = read_json(out_dir / "meta.json")
    caption = (f"Ready for review: {meta['title']}\n\n{text_extra}\n"
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
