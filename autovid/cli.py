import argparse
import datetime
import json
import pathlib
import re

from . import notify, publish
from .util import ROOT, load_config, log

STATE = ROOT / "state" / "done.json"


def _slug(s):
    s = re.sub(r"[^\wঀ-৿]+", "-", s, flags=re.U).strip("-").lower()
    return (s[:40] or "video")


def _done():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else []


def _scripts_file():
    return ROOT / "scripts.json"


def _queued_scripts():
    path = _scripts_file()
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def add_script(value, style="explainer"):
    """Append a Telegram-provided topic/script to the persistent queue."""
    parsed_style, separator, topic = value.partition("|")
    if separator and parsed_style.strip() in ("explainer", "timeline3d"):
        style, value = parsed_style.strip(), topic.strip()
    queue = _queued_scripts()
    queue.append({"topic": value.strip(), "style": style, "done": False})
    _scripts_file().write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


def pending_script_count(topics_file=ROOT / "topics.txt"):
    done = {item["topic"] for item in _done()}
    queued = sum(1 for item in _queued_scripts() if not item.get("done"))
    topics = 0
    for line in pathlib.Path(topics_file).read_text(encoding="utf-8").splitlines():
        style, _, topic = line.partition("|")
        topic = topic.strip() if style.strip() in ("explainer", "timeline3d") else line.strip()
        if topic and not line.lstrip().startswith("#") and topic not in done:
            topics += 1
    return queued + topics


def _is_pending_topic(topic, topics_file=ROOT / "topics.txt"):
    if topic in {item["topic"] for item in _done()}:
        return False
    if any(not item.get("done") and item.get("topic") == topic for item in _queued_scripts()):
        return True
    for line in pathlib.Path(topics_file).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        style, _, candidate = line.partition("|")
        candidate = candidate.strip() if style.strip() in ("explainer", "timeline3d") else line.strip()
        if candidate == topic:
            return True
    return False


def next_topic(topics_file):
    """First line of topics.txt (format 'style | topic') that has not been produced yet."""
    done = {d["topic"] for d in _done()}
    for item in _queued_scripts():
        if not item.get("done") and item["topic"] not in done:
            return item.get("style", "explainer"), item["topic"]
    for line in pathlib.Path(topics_file).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        style, _, topic = line.partition("|")
        style, topic = style.strip(), topic.strip()
        if style not in ("explainer", "timeline3d"):
            style, topic = "explainer", line
        if topic not in done:
            return style, topic
    return None, None


def mark_done(topic, style, out_dir):
    d = _done()
    d.append({"topic": topic, "style": style, "dir": str(out_dir),
              "date": datetime.date.today().isoformat()})
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    queue = _queued_scripts()
    changed = False
    for item in queue:
        if not item.get("done") and item["topic"] == topic:
            item["done"] = True
            changed = True
            break
    if changed:
        _scripts_file().write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


def _notify_publish_result(result):
    lines = ["Publish report:"]
    for platform, url in result.get("results", {}).items():
        lines.append(f"✅ {platform}: {url}")
    for platform, error in result.get("errors", {}).items():
        lines.append(f"❌ {platform}: {str(error)[:700]}")
    notify.telegram_message("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(prog="autovid")
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make", help="make one video")
    m.add_argument("--topic")
    m.add_argument("--style", default="explainer", choices=["explainer", "timeline3d"])
    m.add_argument("--script", help="skip the LLM: use this script.json")
    m.add_argument("--out", default="out/video")
    m.add_argument("--episode", type=int)
    m.add_argument("--notify", action="store_true", help="send the result to Telegram")

    c = sub.add_parser("create", help="create one requested topic and send it to Telegram for review")
    c.add_argument("--topic", required=True)
    c.add_argument("--style", default="explainer", choices=["explainer", "timeline3d"])
    c.add_argument("--out-root", default="out")

    n = sub.add_parser("next", help="take the next topic from topics.txt, make it, notify/publish")
    n.add_argument("--topics", default=str(ROOT / "topics.txt"))
    n.add_argument("--out-root", default="out")

    p = sub.add_parser("publish", help="publish an already-rendered video dir")
    p.add_argument("--dir", required=True)
    p.add_argument("--public-url")

    b = sub.add_parser("bot-poll", help="process Telegram commands and video approvals")
    b.add_argument("--watch", action="store_true",
                   help="keep long-polling (local use); default is a single pass for GitHub Actions")

    u = sub.add_parser("bot-update", help="process one Telegram webhook update")
    u.add_argument("--update-json", required=True)

    v = sub.add_parser("voice-check", help="test the ElevenLabs key, plan, and voice ID")
    v.add_argument("--test", action="store_true",
                   help="also synthesize a short Bangla sample (uses a few credits)")

    f = sub.add_parser("facebook-check", help="test the Facebook Page ID and Page access token")

    y = sub.add_parser("youtube-check", help="test the YouTube OAuth refresh token")
    ya = sub.add_parser("youtube-auth", help="create a YouTube refresh token via browser authorization")
    ya.add_argument("--port", type=int, default=8085)

    a = ap.parse_args()
    cfg = load_config(a.config)

    if a.cmd == "make":
        from . import pipeline   # heavy imports (Pillow, numpy, Playwright) only when making a video
        ep = a.episode or cfg["series"].get("episode_start", 1)
        pipeline.make_video(cfg, a.out, a.topic, a.style, a.script, ep)
        if a.notify:
            notify.telegram(a.out)

    elif a.cmd == "create":
        from . import pipeline
        topic = a.topic.strip()
        if not topic:
            raise ValueError("A topic is required to create a video")
        was_pending = _is_pending_topic(topic)
        remaining = max(0, pending_script_count() - int(was_pending))
        ep = cfg["series"].get("episode_start", 1) + len(_done())
        out = pathlib.Path(a.out_root) / f"{datetime.datetime.now():%Y-%m-%d-%H%M%S}-{_slug(topic)}"
        pipeline.make_video(cfg, out, topic, a.style, None, ep)
        message_id = notify.telegram(out, remaining=remaining)
        if not message_id:
            raise RuntimeError("Video was created but could not be sent to Telegram for review")
        mark_done(topic, a.style, out)

    elif a.cmd == "next":
        style, topic = next_topic(a.topics)
        if not topic:
            log("no pending topics in", a.topics)
            notify.telegram_message("আজকের জন্য কোনো নতুন ভিডিও টপিক বাকি নেই। নতুন টপিক যোগ হলে পরের নির্ধারিত সময়ে তৈরি হবে।")
            return
        from . import pipeline
        ep = cfg["series"].get("episode_start", 1) + len(_done())
        out = pathlib.Path(a.out_root) / f"{datetime.date.today().isoformat()}-{_slug(topic)}"
        pipeline.make_video(cfg, out, topic, style, None, ep)
        # Send the review video first: if Telegram fails the topic is not marked done,
        # so the next run retries it instead of silently skipping the video. The count
        # is reduced by one because this topic is about to be marked done.
        message_id = notify.telegram(out, remaining=max(0, pending_script_count() - 1))
        mark_done(topic, style, out)
        if message_id and not cfg["publish"].get("require_approval", True):
            _notify_publish_result(publish.publish_all(out, cfg))

    elif a.cmd == "publish":
        result = publish.publish_all(a.dir, cfg, a.public_url)
        if result.get("errors"):
            errors = "; ".join(
                f"{platform}: {error}" for platform, error in result["errors"].items()
            )
            raise RuntimeError(f"Publish failed: {errors}")
        _notify_publish_result(result)

    elif a.cmd == "bot-poll":
        from .telegram_bot import poll
        poll(a.config, watch=a.watch)

    elif a.cmd == "bot-update":
        from .telegram_bot import process_update
        process_update(json.loads(a.update_json), a.config)

    elif a.cmd == "voice-check":
        from .tts import voice_check
        raise SystemExit(0 if voice_check(cfg, test=a.test) else 1)

    elif a.cmd == "facebook-check":
        from .publish import facebook_check
        raise SystemExit(0 if facebook_check() else 1)

    elif a.cmd == "youtube-check":
        from .publish import youtube_check
        raise SystemExit(0 if youtube_check() else 1)

    elif a.cmd == "youtube-auth":
        from .publish import youtube_auth
        raise SystemExit(0 if youtube_auth(port=a.port) else 1)


if __name__ == "__main__":
    main()
