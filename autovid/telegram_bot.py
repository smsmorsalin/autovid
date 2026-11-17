"""Telegram approval bot.

Two ways to run it:

    python -m autovid bot-poll            # one pass, for the GitHub Actions cron
    python -m autovid bot-poll --watch    # long-poll forever, for a local machine

Reply `upload` to a review video message and the bot publishes that exact video to
every platform enabled in config.yaml (YouTube by default), then reports each
result back to the chat.

State lives in state/telegram.json (see notify.py). The GitHub workflows commit
that file after every run, so the render job and the polling job share the same
pending/uploaded records.
"""
import hashlib
import pathlib
import tempfile
import time

import requests

from . import publish
from .cli import add_script, pending_script_count
from .notify import _save, _send, _state
from .util import env, load_config, log, read_json

HELP = (
    "ভিডিও রিভিউ মেসেজের reply হিসেবে upload লিখলে ভিডিওটি YouTube-এ আপলোড হবে "
    "(Facebook চালু থাকলে সেখানেও)।\n"
    "একটিই ভিডিও অপেক্ষা করলে শুধু upload লিখলেও হবে।\n"
    "You may also reply with a replacement video file.\n"
    "/add <topic> - queue a script\n"
    "/remaining - show pending scripts\n"
    "/help - show this message"
)


def _api(token, method, **kwargs):
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}", timeout=120, **kwargs
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data["result"]


def _download_video(token, file_id, destination):
    info = _api(token, "getFile", data={"file_id": file_id})
    with requests.get(
        f"https://api.telegram.org/file/bot{token}/{info['file_path']}",
        timeout=600, stream=True,
    ) as response:
        response.raise_for_status()
        with open(destination, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def _media(message):
    return message.get("video") or message.get("document") or {}


def _meta_from_reply(reply):
    """Recover usable metadata if state/telegram.json was lost or not committed yet."""
    caption = (reply.get("caption") or "").strip()
    title, description = "Bengali Short", caption
    first, _, rest = caption.partition("\n")
    if first.startswith("ভিডিও রিভিউ:"):
        title = first.split(":", 1)[1].strip() or title
        # Drop the bot's own instruction lines so they never become the description.
        keep = [line for line in rest.splitlines()
                if not line.startswith(("আপলোড করতে", "বিকল্প হিসেবে", "অথবা /remaining"))]
        description = "\n".join(keep).strip()
    return {
        "title": title[:100],
        "description": description,
        "tags": [],
        "topic": title,
    }


def _platforms(cfg):
    pub = cfg.get("publish", {})
    enabled = []
    if pub.get("youtube", True):
        enabled.append("youtube")
    if pub.get("facebook", False):
        enabled.append("facebook")
    if pub.get("instagram", False):
        enabled.append("instagram")
    return enabled


def _publish(token, chat_id, message, pending, file_id, cfg, state, unique_id):
    enabled = _platforms(cfg)
    if not enabled:
        _send(token, chat_id, "config.yaml-এ কোনো publish platform চালু নেই।")
        return

    record = state["uploaded"].get(unique_id) or {}
    if isinstance(record, str):                       # old schema: a YouTube URL
        record = {"youtube": record}
    todo = [name for name in enabled if not record.get(name)]
    if not todo:
        _send(token, chat_id, "এই ভিডিও আগেই আপলোড হয়েছে:\n" + _report(record, cfg))
        return

    tmp = pathlib.Path(tempfile.gettempdir()) / f"autovid-{unique_id}.mp4"
    try:
        _download_video(token, file_id, tmp)
    except (Exception, SystemExit) as exc:
        _send(token, chat_id, f"ভিডিও ডাউনলোড ব্যর্থ হয়েছে: {str(exc)[:700]}")
        return

    digest = hashlib.sha256()
    with open(tmp, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    digest = digest.hexdigest()
    digest_record = state["uploaded"].get(digest) or {}
    if isinstance(digest_record, str):
        digest_record = {"youtube": digest_record}
    for name, value in digest_record.items():
        record.setdefault(name, value)
    todo = [name for name in enabled if not record.get(name)]

    results, errors = dict(record), []
    try:
        for name in todo:
            try:
                if name == "youtube":
                    video_id = publish.youtube(tmp, pending, cfg)
                    results["youtube"] = f"https://youtube.com/shorts/{video_id}"
                elif name == "facebook":
                    video_id = publish.facebook_reel(tmp, pending, cfg)
                    results["facebook_video_id"] = video_id
                    results["facebook"] = f"https://www.facebook.com/reel/{video_id}"
                elif name == "instagram":
                    # Instagram requires a public HTTPS URL, which a Telegram upload does not have.
                    results["instagram"] = "skipped: needs a public video URL"
            except (Exception, SystemExit) as exc:
                log(f"{name} upload failed: {exc}")
                errors.append(f"{name}: {str(exc)[:700]}")
    finally:
        tmp.unlink(missing_ok=True)

    state["uploaded"][unique_id] = results
    state["uploaded"][digest] = results
    if not errors:
        for key, value in list(state["pending"].items()):
            if value is pending or (
                value.get("video_file_id")
                and value.get("video_file_id") == pending.get("video_file_id")
            ):
                state["pending"].pop(key, None)
    _save(state)

    lines = ["আপলোড রিপোর্ট:"]
    if results.get("youtube"):
        lines.append(f"✅ YouTube: {results['youtube']}")
    if results.get("facebook"):
        lines.append(f"✅ Facebook: {results['facebook']}")
    if results.get("instagram"):
        lines.append(f"ℹ️ Instagram: {results['instagram']}")
    for error in errors:
        lines.append(f"❌ {error}")
    if errors:
        lines.append("ব্যর্থ প্ল্যাটফর্মের জন্য আবার reply করে upload লিখুন।")
    _send(token, chat_id, "\n".join(lines))


def _report(record, cfg):
    lines = []
    if record.get("youtube"):
        lines.append(f"YouTube: {record['youtube']}")
    if record.get("facebook"):
        lines.append(f"Facebook: {record['facebook']}")
    return "\n".join(lines) or "(no URL recorded)"


def _handle_message(message, token, cfg, state):
    chat_id = str(message["chat"]["id"])
    allowed_chat = env("TELEGRAM_CHAT_ID", False)
    if allowed_chat and chat_id != str(allowed_chat):
        return

    text = (message.get("text") or "").strip()
    command, separator, argument = text.partition(" ")
    command = command.casefold().split("@", 1)[0]
    argument = argument.strip()

    if command in ("/start", "start", "/help", "help"):
        _send(token, chat_id, HELP)
        return
    if command in ("/remaining", "/remaning", "remaining", "remaning", "/queue", "queue"):
        _send(token, chat_id, f"বাকি স্ক্রিপ্ট: {pending_script_count()}")
        return
    if command in ("/add", "add"):
        value = argument if separator else ""
        limit = cfg.get("telegram", {}).get("max_script_length", 500)
        if not value:
            _send(token, chat_id, "ব্যবহার: /add <বাংলা স্ক্রিপ্ট বা টপিক>")
        elif len(value) > limit:
            _send(token, chat_id, f"স্ক্রিপ্ট {limit} অক্ষরের বেশি হতে পারবে না।")
        else:
            add_script(value)
            _send(token, chat_id, f"স্ক্রিপ্ট তালিকায় যোগ হয়েছে। বাকি: {pending_script_count()}")
        return

    reply = message.get("reply_to_message") or {}
    reply_id = str(reply.get("message_id") or "")
    pending = state["pending"].get(reply_id)
    attached = _media(message)
    reply_media = _media(reply)

    if command in ("upload", "/upload") and not state["pending"] and not reply_media:
        _send(
            token,
            chat_id,
            "কোনো ভিডিও আপলোডের জন্য অপেক্ষমাণ নেই।",
        )
        return

    # A replacement video sent as a reply to the review message.
    if attached and reply_media:
        pending = pending or _meta_from_reply(reply)
        state["pending"][reply_id] = pending
        file_id = attached.get("file_id")
        unique_id = attached.get("file_unique_id") or file_id
        _publish(token, chat_id, message, pending, file_id, cfg, state, unique_id)
        return

    if command not in ("upload", "/upload"):
        return

    # Be forgiving: a plain "upload" works when there is exactly one waiting video.
    if not reply_media and not pending and len(state["pending"]) == 1:
        reply_id, pending = next(iter(state["pending"].items()))

    if not reply_media and not pending:
        if state["pending"]:
            _send(token, chat_id, "একাধিক ভিডিও অপেক্ষা করছে। যে ভিডিওটি চান তার reply হিসেবে upload লিখুন।")
        else:
            _send(
                token,
                chat_id,
                "কোনো ভিডিও আপলোডের জন্য অপেক্ষমাণ নেই। আগে একটি ভিডিও রিভিউ মেসেজ তৈরি করুন।",
            )
        return

    pending = pending or _meta_from_reply(reply)
    state["pending"][reply_id] = pending
    # Prefer the file attached to the review message itself; fall back to the saved file id.
    file_id = reply_media.get("file_id") or pending.get("video_file_id")
    if not file_id:
        _send(token, chat_id, "এই রিভিউ মেসেজের ভিডিও পাওয়া যাচ্ছে না। নতুন ভিডিও জেনারেট করুন।")
        return
    unique_id = reply_media.get("file_unique_id") or file_id

    # Refresh metadata from the local render directory when it exists (local runs).
    out_dir = pending.get("dir")
    if out_dir and (pathlib.Path(out_dir) / "meta.json").exists():
        try:
            fresh = read_json(pathlib.Path(out_dir) / "meta.json")
            pending.update({k: fresh[k] for k in ("title", "description", "tags", "topic") if k in fresh})
        except Exception:
            pass

    _publish(token, chat_id, message, pending, file_id, cfg, state, unique_id)


def poll(config_path=None, watch=False):
    token = env("TELEGRAM_BOT_TOKEN")
    cfg = load_config(config_path)
    state = _state()
    log("Telegram bot started" + (" (watch mode)" if watch else " (single pass)"))
    while True:
        updates = _api(
            token,
            "getUpdates",
            data={
                "offset": state["offset"] + 1,
                "timeout": 50 if watch else 0,
                "allowed_updates": '["message"]',
            },
        )
        for update in updates:
            state["offset"] = max(state["offset"], update["update_id"])
            message = update.get("message")
            if not message:
                continue
            try:
                _handle_message(message, token, cfg, state)
            except (Exception, SystemExit) as exc:
                log(f"Telegram update failed: {exc}")
                chat_id = str((message.get("chat") or {}).get("id", ""))
                if chat_id:
                    _send(token, chat_id, f"কমান্ড প্রসেস ব্যর্থ: {str(exc)[:700]}")
            _save(state)
        _save(state)
        if not watch:
            break
        if not updates:
            time.sleep(1)


def process_update(update, config_path=None):
    """Process one Telegram update delivered by the webhook workflow."""
    token = env("TELEGRAM_BOT_TOKEN")
    cfg = load_config(config_path)
    state = _state()
    update_id = update.get("update_id")
    if update_id is not None:
        state["offset"] = max(state["offset"], update_id)
    message = update.get("message")
    if message:
        _handle_message(message, token, cfg, state)
    _save(state)
