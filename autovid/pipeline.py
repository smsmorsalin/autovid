"""End-to-end: topic (or ready script.json) -> final.mp4 + meta.json. Resumable: finished steps are cached in the work dir."""
import hashlib
import pathlib

from . import images, script_gen, tts
from .render import render_video
from .util import log, read_json, write_json


def make_video(cfg, out_dir, topic=None, style="explainer", script_path=None, episode=1):
    out_dir = pathlib.Path(out_dir).resolve()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    sp = out_dir / "script.json"
    if script_path:
        script = script_gen.normalize(read_json(script_path), style)
        script.setdefault("topic", topic or script.get("title", ""))
        write_json(sp, script)
    elif sp.exists():
        script = read_json(sp)
        log("using cached script.json")
    else:
        script = script_gen.generate(topic, style, cfg)
        write_json(sp, script)
    style = script["style"]
    bible = script.get("visual_bible", "")

    assets = []
    for i, sc in enumerate(script["scenes"]):
        a = {}
        # voice
        audio = work / f"s{i:02d}.mp3"
        a["audio"], a["dur"], a["words"] = None, None, None
        wj = work / f"s{i:02d}_words.json"
        if audio.exists() and wj.exists():
            from .util import media_duration
            a["audio"], a["dur"] = str(audio), media_duration(audio)
            w = read_json(wj)
            a["words"] = [tuple(x) for x in w] if w else None
        else:
            log(f"voice {i + 1}/{len(script['scenes'])}")
            p, dur, words = tts.synthesize(sc["narration"], audio, cfg)
            a["audio"], a["dur"], a["words"] = str(p), dur, words
            write_json(wj, [list(x) for x in words] if words else [])
        # visual
        if sc["kind"] == "image":
            img = work / f"s{i:02d}_img.png"
            if not img.exists():
                log(f"image {i + 1}/{len(script['scenes'])}")
                seed = int(hashlib.md5((script["title"] + str(i)).encode()).hexdigest()[:6], 16)
                prompt = (bible + ". " if bible else "") + sc["image_prompt"]
                images.make_image(prompt, img, cfg, style, seed)
            a["image"] = str(img)
            if style == "timeline3d":
                clip = work / f"s{i:02d}_clip.mp4"
                if not clip.exists():
                    images.image_to_video(img, sc["image_prompt"], clip, cfg)
                if clip.exists():
                    a["clip"] = str(clip)
        assets.append(a)

    final = out_dir / "final.mp4"
    render_video(script, assets, cfg, work, episode, final)

    md = script_gen.make_metadata(script, cfg)
    ex = [h for h in cfg["publish"].get("extra_hashtags", []) if h.lower() not in [x.lower() for x in md["hashtags"]]]
    parts = [md["description"], " ".join(md["hashtags"] + ex)]
    note = cfg.get("metadata", {}).get("ai_note", "")
    if note:
        parts.append(note)
    src = "\n".join(f"- {s.get('title', '')} {s.get('url', '')}".strip() for s in script.get("sources", []) if s.get("url"))
    if src:
        parts.append("Sources:\n" + src)
    meta = {"title": md["title"], "description": "\n\n".join(x for x in parts if x), "tags": md["tags"],
            "style": style, "topic": script.get("topic", "")}
    write_json(out_dir / "meta.json", meta)
    log("done ->", final)
    return final
