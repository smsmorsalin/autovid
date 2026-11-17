"""Telegram polling worker used by the scheduled GitHub Actions workflow."""
import hashlib
import pathlib

import requests

from . import publish
from .cli import add_script, pending_script_count
from .notify import _save, _state
from .util import env, load_config


def _api(token, method, **kwargs):
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}", timeout=120, **kwargs
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data["result"]


def _send(token, chat_id, text):
    _api(token, "sendMessage", data={"chat_id": chat_id, "text": text[:4096]})


def _download_video(token, file_id, destination):
    info = _api(token, "getFile", data={"file_id": file_id})
    response = requests.get(
        f"https://api.telegram.org/file/bot{token}/{info['file_path']}", timeout=600
    )
    response.raise_for_status()
    pathlib.Path(destination).write_bytes(response.content)


def _upload_pending(message, pending, token, chat_id, cfg, state, file_id):
    unique_id = (message.get("video") or message.get("document") or {}).get(
        "file_unique_id"
    ) or file_id
    if unique_id in state["uploaded"]:
        _send(token, chat_id, f"এই ভিডিও আগেই আপলোড হয়েছে:\n{state['uploaded'][unique_id]}")
        return
    path = pathlib.Path("telegram-review.mp4")
    _download_video(token, file_id, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest in state["uploaded"]:
        _send(token, chat_id, f"এই ভিডিও আগেই আপলোড হয়েছে:\n{state['uploaded'][digest]}")
        path.unlink(missing_ok=True)
        return
    try:
        video_id = publish.youtube(path, pending, cfg)
        url = f"https://youtube.com/shorts/{video_id}"
        state["uploaded"][unique_id] = url
        state["uploaded"][digest] = url
        state["pending"].pop(str(message["message_id"]), None)
        _save(state)
        _send(token, chat_id, f"সফলভাবে YouTube-এ আপলোড হয়েছে:\n{url}")
    except Exception as exc:
        _send(token, chat_id, f"YouTube আপলোড ব্যর্থ হয়েছে: {str(exc)[:700]}")
    finally:
        path.unlink(missing_ok=True)


def _handle_message(message, token, cfg, state):
    chat_id = str(message["chat"]["id"])
    allowed_chat = env("TELEGRAM_CHAT_ID", False)
    if allowed_chat and chat_id != str(allowed_chat):
        return
    text = (message.get("text") or "").strip()
    if text.startswith("/start") or text.startswith("/help"):
        _send(token, chat_id, "Reply 'upload' to a review video to publish it.\n"
             "You may also reply with a replacement video.\n"
             "/add <topic> - queue a script\n/remaining - show pending scripts")
        return
    if text.startswith("/remaining"):
        _send(token, chat_id, f"বাকি স্ক্রিপ্ট: {pending_script_count()}")
        return
    if text.startswith("/add"):
        value = text.partition(" ")[2].strip()
        if not value:
            _send(token, chat_id, "ব্যবহার: /add <বাংলা স্ক্রিপ্ট বা টপিক>")
        else:
            add_script(value)
            _send(token, chat_id, f"স্ক্রিপ্ট তালিকায় যোগ হয়েছে। বাকি: {pending_script_count()}")
        return

    reply = message.get("reply_to_message") or {}
    pending = state["pending"].get(str(reply.get("message_id")))
    if not pending:
        return
    video = message.get("video") or message.get("document")
    if not video and text.lower() == "upload":
        file_id = pending.get("video_file_id")
        if not file_id:
            _send(token, chat_id, "এই পুরনো রিভিউ মেসেজের ভিডিও পাওয়া যাচ্ছে না। নতুন ভিডিও জেনারেট করুন।")
            return
        _upload_pending(message, pending, token, chat_id, cfg, state, file_id)
        return
    if not video:
        return
    unique_id = video.get("file_unique_id") or video["file_id"]
    if unique_id in state["uploaded"]:
        _send(token, chat_id, f"এই ভিডিও আগেই আপলোড হয়েছে:\n{state['uploaded'][unique_id]}")
        return
    _upload_pending(reply, pending, token, chat_id, cfg, state, video["file_id"])


def poll(config_path=None):
    token = env("TELEGRAM_BOT_TOKEN")
    cfg = load_config(config_path)
    state = _state()
    updates = _api(token, "getUpdates", data={"offset": state["offset"] + 1, "timeout": 1})
    for update in updates:
        state["offset"] = update["update_id"]
        message = update.get("message")
        if message:
            _handle_message(message, token, cfg, state)
    _save(state)
