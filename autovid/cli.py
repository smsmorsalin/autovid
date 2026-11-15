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


def next_topic(topics_file):
    """First line of topics.txt (format 'style | topic') that has not been produced yet."""
    done = {d["topic"] for d in _done()}
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

    n = sub.add_parser("next", help="take the next topic from topics.txt, make it, notify/publish")
    n.add_argument("--topics", default=str(ROOT / "topics.txt"))
    n.add_argument("--out-root", default="out")

    p = sub.add_parser("publish", help="publish an already-rendered video dir")
    p.add_argument("--dir", required=True)
    p.add_argument("--public-url")

    a = ap.parse_args()
    cfg = load_config(a.config)

    if a.cmd == "make":
        from . import pipeline   # heavy imports (Pillow, numpy, Playwright) only when making a video
        ep = a.episode or cfg["series"].get("episode_start", 1)
        pipeline.make_video(cfg, a.out, a.topic, a.style, a.script, ep)
        if a.notify:
            notify.telegram(a.out)

    elif a.cmd == "next":
        style, topic = next_topic(a.topics)
        if not topic:
            log("no pending topics in", a.topics)
            return
        from . import pipeline
        ep = cfg["series"].get("episode_start", 1) + len(_done())
        out = pathlib.Path(a.out_root) / f"{datetime.date.today().isoformat()}-{_slug(topic)}"
        pipeline.make_video(cfg, out, topic, style, None, ep)
        mark_done(topic, style, out)
        notify.telegram(out)
        if not cfg["publish"].get("require_approval", True):
            publish.publish_all(out, cfg)

    elif a.cmd == "publish":
        publish.publish_all(a.dir, cfg, a.public_url)


if __name__ == "__main__":
    main()
