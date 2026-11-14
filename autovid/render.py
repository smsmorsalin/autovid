"""Scene rendering: HTML/CSS (via Playwright) for text + cards, ffmpeg for motion, grade, audio, concat."""
import base64
import html as _html
import pathlib
import re

from playwright.sync_api import sync_playwright

from .util import log, run

DW, DH = 1080, 1920            # design size in CSS px (output is scaled to cfg width/height)
CAP_Y, CAP_H = 1360, 380       # caption band (design px)

FONT = "'Noto Sans Bengali','Hind Siliguri','Anek Bangla','FreeSans','DejaVu Sans',sans-serif"
MONO = "'JetBrains Mono','DejaVu Sans Mono','Liberation Mono',monospace"

TONE = {"year": "#ffd23f", "warn": "#ff5a5a", "good": "#4ade80", "neutral": "#7ee7ff"}

GRADE = {
    "none": "",
    "sepia": "colorchannelmixer=rr=.393:rg=.769:rb=.189:gr=.349:gg=.686:gb=.168:br=.272:bg=.534:bb=.131,",
    "warm": "colorbalance=rs=.08:gs=.02:bs=-.08:rm=.06:bm=-.05,",
    "cool": "colorbalance=rs=-.06:bs=.08:bm=.04,",
    "dusk": "eq=saturation=1.15:contrast=1.05,colorbalance=rs=.12:gs=-.02:bs=-.10,",
}

esc = _html.escape


def data_uri(path):
    b = pathlib.Path(path).read_bytes()
    ext = pathlib.Path(path).suffix.lower().lstrip(".") or "png"
    ext = "jpeg" if ext in ("jpg", "jpeg") else ext
    return f"data:image/{ext};base64," + base64.b64encode(b).decode()


BASE_CSS = f"""
html,body{{margin:0;padding:0;width:{DW}px;height:{DH}px;overflow:hidden;background:transparent}}
*{{box-sizing:border-box;font-family:{FONT}}}
.abs{{position:absolute}}
"""

# ------------------------------------------------------------------ browser
class Browser:
    def __init__(self, cfg):
        self.scale = cfg["video"]["width"] / DW

    def __enter__(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch()
        ctx = self.browser.new_context(viewport={"width": DW, "height": DH},
                                       device_scale_factor=self.scale)
        self.page = ctx.new_page()
        return self

    def __exit__(self, *a):
        self.browser.close()
        self.pw.stop()

    def shot(self, html_str, out, transparent=True, clip=None):
        self.page.set_content(html_str, wait_until="load")
        self.page.evaluate("document.fonts.ready")
        kw = {"path": str(out), "omit_background": transparent}
        if clip:
            kw["clip"] = clip
        self.page.screenshot(**kw)


# ------------------------------------------------------------------ card HTML
CARD_CSS = """
.card{position:absolute;left:60px;right:60px;background:#0e1424;border:3px solid #2f6fe0;
 border-radius:30px;padding:38px;box-shadow:0 0 70px rgba(47,111,224,.35)}
.chip{display:inline-block;color:#8fb6ff;font-size:34px;font-weight:700;margin-bottom:22px;letter-spacing:1px}
table{width:100%;border-collapse:separate;border-spacing:0 6px}
th{color:#6f86b8;font-size:32px;text-align:left;padding:6px 16px;font-weight:600}
td{color:#d5ddf2;font-size:40px;padding:16px;background:#121a2e}
td:first-child{border-radius:14px 0 0 14px}td:last-child{border-radius:0 14px 14px 0}
tr.hl td{background:rgba(251,146,60,.20);color:#ffd9b0;border-top:2px solid #fb923c;border-bottom:2px solid #fb923c}
.cmp{position:absolute;left:60px;right:60px;height:360px;border-radius:34px;padding:34px 44px;
 display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center}
.cmp.bad{background:rgba(239,68,68,.10);border:4px solid #ef4444;box-shadow:0 0 60px rgba(239,68,68,.3)}
.cmp.good{background:rgba(34,197,94,.10);border:4px solid #22c55e;box-shadow:0 0 60px rgba(34,197,94,.3)}
.cmp .lab{font-size:42px;font-weight:600;color:#cdd6ee;margin-bottom:10px}
.cmp.bad .val{color:#ff6b6b}.cmp.good .val{color:#4ade80}
.val{font-weight:800;line-height:1.15}
.vs{position:absolute;left:50%;transform:translateX(-50%);top:750px;background:#1a2340;color:#9fb0dc;
 font-size:34px;font-weight:700;padding:8px 30px;border-radius:30px;border:2px solid #2c3a66}
.code{position:absolute;left:60px;right:60px;background:#0d1117;border:3px solid #30394d;border-radius:28px;
 padding:34px 36px;box-shadow:0 0 60px rgba(0,0,0,.6)}
.code .dots{margin-bottom:22px}.code .dots i{display:inline-block;width:20px;height:20px;border-radius:50%;margin-right:12px}
.code .ln{font-family:__MONO__;font-size:44px;line-height:1.7;color:#c9d1d9;white-space:pre-wrap}
.code .n{color:#4b5568;margin-right:26px}.kw{color:#ff9e64;font-weight:700}.st{color:#9ece6a}.nm{color:#7dcfff}
.stat{position:absolute;left:40px;right:40px;top:560px;text-align:center}
.stat .v{font-weight:800;color:#4ade80;text-shadow:0 0 60px rgba(74,222,128,.45);line-height:1.1}
.stat .l{font-size:56px;color:#b4bfdc;margin-top:30px;font-weight:600}
""".replace("__MONO__", MONO)

KW = re.compile(r"\b(SELECT|FROM|WHERE|CREATE|INDEX|ON|AND|OR|ORDER|BY|LIMIT|INSERT|INTO|VALUES|UPDATE|SET|DELETE|JOIN|GROUP|def|return|for|in|if|else|import|class)\b")


def _fit(text, max_px, ratio=0.62, lo=64, hi=190):
    return int(max(lo, min(hi, max_px / max(1, len(text)) / ratio)))


def card_html(scene):
    k, d = scene["kind"], scene.get("data") or {}
    if k == "table":
        cols, rows = d.get("columns", []), d.get("rows", [])[:8]
        hl = d.get("highlight_row")
        th = "".join(f"<th>{esc(str(c))}</th>" for c in cols)
        trs = "".join(
            f"<tr class='{'hl' if hl == i else ''}'>" + "".join(f"<td>{esc(str(c))}</td>" for c in r) + "</tr>"
            for i, r in enumerate(rows))
        top = 360
        return (f"<div class='card' style='top:{top}px'><div class='chip'>&#9632; {esc(str(d.get('title', '')))}</div>"
                f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>")
    if k == "compare":
        def box(side, top):
            s = d.get(side, {})
            v = str(s.get("value", ""))
            tone = s.get("tone", "bad" if side == "left" else "good")
            return (f"<div class='cmp {tone}' style='top:{top}px'><div class='lab'>{esc(str(s.get('label', '')))}</div>"
                    f"<div class='val' style='font-size:{_fit(v, 880)}px'>{esc(v)}</div></div>")
        return box("left", 360) + "<div class='vs'>VS</div>" + box("right", 830)
    if k == "code":
        lines = d.get("lines", [])[:8]
        out = []
        for i, ln in enumerate(lines, 1):
            t = esc(str(ln), quote=False)
            t = re.sub(r"('[^']*')", r"<span class='st'>\1</span>", t)
            t = KW.sub(r"<span class='kw'>\1</span>", t)
            out.append(f"<div class='ln'><span class='n'>{i}</span>{t}</div>")
        return ("<div class='code' style='top:420px'><div class='dots'><i style='background:#ff5f56'></i>"
                "<i style='background:#ffbd2e'></i><i style='background:#27c93f'></i></div>" + "".join(out) + "</div>")
    if k == "stat":
        v = str(d.get("value", ""))
        return (f"<div class='stat'><div class='v' style='font-size:{_fit(v, 960, lo=90, hi=260)}px'>{esc(v)}</div>"
                f"<div class='l'>{esc(str(d.get('label', '')))}</div></div>")
    return ""


# ------------------------------------------------------------------ scene layers
def bg_html(scene, style, image_path):
    """Opaque background layer (this is the layer that gets the camera motion)."""
    k = scene["kind"]
    if style == "timeline3d":
        return (f"<html><head><style>{BASE_CSS}</style></head><body style='background:#000'>"
                f"<div class='abs' style='inset:0;background:url({data_uri(image_path)}) center/cover'></div>"
                f"<div class='abs' style='left:0;right:0;top:0;height:520px;background:linear-gradient(rgba(0,0,0,.55),transparent)'></div>"
                f"<div class='abs' style='left:0;right:0;bottom:0;height:760px;background:linear-gradient(transparent,rgba(0,0,0,.70))'></div>"
                f"</body></html>")
    base = "<html><head><style>" + BASE_CSS + CARD_CSS + "</style></head><body style='background:#0a0c13'>"
    if k == "image":
        base += (f"<div class='abs' style='left:0;right:0;top:0;height:1360px;background:url({data_uri(image_path)}) center/cover;"
                 f"-webkit-mask-image:linear-gradient(#000 72%,transparent);mask-image:linear-gradient(#000 72%,transparent)'></div>")
    else:
        base += ("<div class='abs' style='inset:0;background:radial-gradient(900px 700px at 50% 35%,#131c36,#0a0c13)'></div>"
                 + card_html(scene))
    return base + "</body></html>"


def fixed_html(scene, style, cfg, topic_short, episode):
    """Transparent overlay that stays still: header (explainer) or top heading (timeline3d)."""
    css = BASE_CSS
    body = ""
    if style == "timeline3d":
        h = scene.get("heading", "")
        col = TONE.get(scene.get("heading_tone", "neutral"), "#7ee7ff")
        fs = 84 if len(h) <= 16 else (72 if len(h) <= 26 else 60)
        sh = ",".join(f"{x}px {y}px 0 #000" for x in (-4, 0, 4) for y in (-4, 0, 4) if (x, y) != (0, 0))
        body += (f"<div class='abs' style='left:50px;right:50px;top:150px;text-align:center;font-weight:800;"
                 f"font-size:{fs}px;line-height:1.25;color:{col};text-shadow:{sh},0 6px 24px rgba(0,0,0,.7)'>{esc(h)}</div>")
        if cfg["images"].get("label_ai_visuals", True) and scene["kind"] == "image":
            body += (f"<div class='abs' style='left:40px;top:1795px;font-size:30px;color:rgba(255,255,255,.88);"
                     f"text-shadow:0 2px 8px #000'>{esc(cfg['images'].get('label_text', ''))}</div>")
    else:
        body += (f"<div class='abs' style='left:60px;right:60px;top:90px;display:flex;justify-content:space-between;"
                 f"font-size:34px;font-weight:700;letter-spacing:3px;color:#e8ecf8;text-shadow:0 2px 10px #000'>"
                 f"<span>{esc(cfg['series']['name'])}</span><span style='color:#9fb0dc'>EP {episode:02d}</span></div>"
                 f"<div class='abs' style='left:60px;top:176px;font-size:30px;font-weight:700;color:#fb923c;"
                 f"background:rgba(251,146,60,.18);border:2px solid #fb923c;border-radius:40px;padding:6px 26px'>"
                 f"&#9679; {esc(topic_short)}</div>")
    return f"<html><head><style>{css}</style></head><body>{body}</body></html>"


def end_card_html(cfg, topic_short):
    s = cfg["series"]
    return (f"<html><head><style>{BASE_CSS}</style></head><body style='background:#0a0c13'>"
            f"<div class='abs' style='inset:0;background:radial-gradient(900px 800px at 50% 45%,#16203f,#0a0c13)'></div>"
            f"<div class='abs' style='left:40px;right:40px;top:640px;text-align:center'>"
            f"<div style='font-size:46px;letter-spacing:6px;color:#e8ecf8;font-weight:700'>{esc(s['name'])}</div>"
            f"<div style='font-size:52px;color:#cdd6ee;margin-top:60px'>{esc(s.get('end_line1', ''))}</div>"
            f"<div style='font-size:96px;font-weight:800;color:#fb923c;line-height:1.25;margin-top:10px'>{esc(topic_short)}</div>"
            f"<div style='font-size:52px;color:#cdd6ee;margin-top:6px'>{esc(s.get('end_line2', ''))}</div>"
            f"<div style='display:inline-block;margin-top:80px;background:linear-gradient(90deg,#f97316,#ef4444);"
            f"color:#fff;font-size:46px;font-weight:800;padding:26px 70px;border-radius:24px'>"
            f"{esc(s.get('cta', ''))} {esc(s.get('handle', ''))}</div></div></body></html>")


# ------------------------------------------------------------------ captions
def split_groups(words, max_words=4):
    groups, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        if len(cur) >= max_words or re.search(r"[।,?!.]$", w):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    # never show a lone word if it can join the previous caption
    merged = []
    for g in groups:
        if merged and len(g) == 1 and len(merged[-1]) < 5:
            merged[-1] = merged[-1] + g
        else:
            merged.append(g)
    return merged


def word_times(words, tts_words, speech_dur, lead=0.12):
    """Start time of each word. Real timings when counts match, else proportional to length."""
    if tts_words and len(tts_words) == len(words):
        return [t[1] for t in tts_words]
    total = max(0.5, speech_dur - lead - 0.05)
    weights = [len(w) + 1 for w in words]
    s = sum(weights)
    t, out = lead, []
    for wt in weights:
        out.append(t)
        t += total * wt / s
    return out


def render_captions(br, words, starts, D, style, workdir, tag):
    """One transparent PNG per highlight state; returns concat-demuxer segment list [(png, seconds)]."""
    workdir = pathlib.Path(workdir)
    t3 = style == "timeline3d"
    css = BASE_CSS + (
        f".cap{{position:absolute;left:50px;right:50px;top:{CAP_Y}px;height:{CAP_H}px;padding-top:20px;display:flex;"
        f"flex-wrap:wrap;justify-content:center;align-content:flex-start;gap:6px 20px;font-size:68px;font-weight:700;line-height:1.35}}"
    )
    if t3:
        sh = ",".join(f"{x}px {y}px 0 #000" for x in (-3, 0, 3) for y in (-3, 0, 3) if (x, y) != (0, 0))
        css += (f".cap span{{color:#fff;text-shadow:{sh},0 4px 18px rgba(0,0,0,.7)}}"
                f".cap span.on{{color:#ffe066}}")
    else:
        css += (".cap span{color:rgba(255,255,255,.34);text-shadow:0 2px 12px #000}"
                ".cap span.past{color:#fff}.cap span.on{color:#ffb15c;font-weight:800}")
    clip = {"x": 0, "y": CAP_Y, "width": DW, "height": CAP_H}
    blank = workdir / "blank_cap.png"
    if not blank.exists():
        br.shot(f"<html><head><style>{BASE_CSS}</style></head><body></body></html>", blank, True, clip)

    ends = starts[1:] + [D]
    segs = [(blank, max(0.04, starts[0]))]
    for gi, g in enumerate(split_groups(words)):
        spans = "".join(f"<span>{esc(words[i])}</span>" for i in g)
        br.page.set_content(f"<html><head><style>{css}</style></head><body><div class='cap'>{spans}</div></body></html>",
                            wait_until="load")
        br.page.evaluate("document.fonts.ready")
        for k, wi in enumerate(g):
            br.page.evaluate(
                "k=>document.querySelectorAll('.cap span').forEach((s,i)=>{s.className=i===k?'on':(i<k?'past':'')})", k)
            png = workdir / f"{tag}_g{gi}_{k}.png"
            br.page.screenshot(path=str(png), omit_background=True, clip=clip)
            segs.append((png, max(0.04, ends[wi] - starts[wi])))
    return segs


# ------------------------------------------------------------------ ffmpeg
def _motion(motion, N, amt=0.12):
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "zoom_out":
        return f"{1 + amt}-{amt}*on/{N}", cx, cy
    if motion == "pan_left":
        return f"{1 + amt}", f"(iw-iw/zoom)*(1-on/{N})", cy
    if motion == "pan_right":
        return f"{1 + amt}", f"(iw-iw/zoom)*on/{N}", cy
    return f"1+{amt}*on/{N}", cx, cy


def compose_scene(cfg, workdir, tag, D, bg, bg_is_video, fixed, segs, audio, motion, grade, out):
    W, H, fps = cfg["video"]["width"], cfg["video"]["height"], cfg["video"]["fps"]
    N = max(1, int(round(D * fps)))
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if bg_is_video:
        cmd += ["-stream_loop", "-1", "-i", bg]
    else:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{D:.3f}", "-i", bg]
    n = 1
    fixed_i = cap_i = None
    if fixed:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{D:.3f}", "-i", fixed]
        fixed_i, n = n, n + 1
    if segs:
        lst = pathlib.Path(workdir) / f"{tag}_caps.txt"
        lines = []
        for png, dur in segs:
            lines += [f"file '{png}'", f"duration {dur:.3f}"]
        lines.append(f"file '{segs[-1][0]}'")
        lst.write_text("\n".join(lines), encoding="utf-8")
        cmd += ["-f", "concat", "-safe", "0", "-i", str(lst)]
        cap_i, n = n, n + 1
    if audio:
        cmd += ["-i", str(audio)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    audio_i = n

    g = GRADE.get(grade, "")
    fg = []
    if bg_is_video:
        fg.append(f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},{g}format=yuv420p[bg]")
    else:
        z, x, y = _motion(motion, N)
        fg.append(f"[0:v]scale={int(W * 1.5)}:{int(H * 1.5)},zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={fps},{g}format=yuv420p[bg]")
    cur = "[bg]"
    if fixed_i:
        fg.append(f"{cur}[{fixed_i}:v]overlay=0:0:format=auto[v1]")
        cur = "[v1]"
    if cap_i:
        fg.append(f"[{cap_i}:v]fps={fps},format=rgba[caps]")
        fg.append(f"{cur}[caps]overlay=0:{int(CAP_Y * W / DW)}:format=auto[v2]")
        cur = "[v2]"
    fg.append(f"{cur}format=yuv420p[vout]")
    fg.append(f"[{audio_i}:a]aresample=44100,aformat=channel_layouts=stereo,apad=whole_dur={D:.3f},atrim=end={D:.3f}[aout]")
    cmd += ["-filter_complex", ";".join(fg), "-map", "[vout]", "-map", "[aout]", "-t", f"{D:.3f}",
            "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", str(out)]
    run(cmd)
    return out


def concat_scenes(paths, out, workdir):
    lst = pathlib.Path(workdir) / "scenes.txt"
    lst.write_text("\n".join(f"file '{p}'" for p in paths), encoding="utf-8")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])
    return out


def add_music(video, music, volume, out):
    run(["ffmpeg", "-y", "-v", "error", "-i", video, "-stream_loop", "-1", "-i", music,
         "-filter_complex",
         f"[1:a]volume={volume}[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", out])
    return out


# ------------------------------------------------------------------ top level
def render_video(script, assets, cfg, workdir, episode, out):
    """assets[i] = {audio, dur, words(tts timings|None), image, clip(optional)}"""
    workdir = pathlib.Path(workdir)
    style = script["style"]
    pad = cfg["video"].get("scene_padding", 0.35)
    scene_files = []
    with Browser(cfg) as br:
        for i, (sc, a) in enumerate(zip(script["scenes"], assets)):
            tag = f"s{i:02d}"
            sp = workdir / f"{tag}.mp4"
            if sp.exists():
                scene_files.append(sp)
                continue
            log(f"rendering scene {i + 1}/{len(assets)} ({sc['kind']})")
            D = a["dur"] + pad
            bg_png = workdir / f"{tag}_bg.png"
            br.shot(bg_html(sc, style, a.get("image")), bg_png, transparent=False)
            fx = workdir / f"{tag}_fixed.png"
            br.shot(fixed_html(sc, style, cfg, script["topic_short"], episode), fx)
            words = sc["narration"].split()
            starts = word_times(words, a.get("words"), a["dur"])
            segs = render_captions(br, words, starts, D, style, workdir, tag)
            bg, is_vid = (a["clip"], True) if a.get("clip") else (bg_png, False)
            compose_scene(cfg, workdir, tag, D, bg, is_vid, fx, segs, a["audio"],
                          sc.get("motion", "zoom_in"), sc.get("grade", "none"), sp)
            scene_files.append(sp)

        # end card
        ep = workdir / "s_end.mp4"
        if not ep.exists():
            log("rendering end card")
            png = workdir / "end_bg.png"
            br.shot(end_card_html(cfg, script["topic_short"]), png, transparent=False)
            compose_scene(cfg, workdir, "end", cfg["video"].get("end_card_seconds", 2.5), png, False,
                          None, None, None, "zoom_in", "none", ep)
        scene_files.append(ep)

    joined = workdir / "joined.mp4"
    concat_scenes(scene_files, joined, workdir)
    music = cfg["video"].get("music")
    if music and pathlib.Path(music).exists():
        add_music(joined, music, cfg["video"].get("music_volume", 0.08), out)
    else:
        pathlib.Path(out).write_bytes(joined.read_bytes())
    return out
