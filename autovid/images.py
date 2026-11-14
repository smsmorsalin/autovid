"""Image providers (placeholder / fal Flux / OpenAI) and optional image-to-video (fal Kling)."""
import base64
import hashlib
import io
import pathlib
import random
import re
import time
import urllib.parse

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter

from .util import env, log

W, H = 1080, 1920


# ---------------------------------------------------------------- placeholder
def _gradient(top, bot, h=H, w=W):
    t = np.linspace(0, 1, h)[:, None, None]
    a = np.array(top, float)[None, None, :]
    b = np.array(bot, float)[None, None, :]
    arr = (a * (1 - t) + b * t) * np.ones((1, w, 1))
    return Image.fromarray(arr.astype("uint8"), "RGB")


def placeholder(prompt, out, style, w=W, h=H):
    """Procedural stand-in so the whole pipeline can be tested offline with no API keys."""
    rnd = random.Random(int(hashlib.md5(prompt.encode()).hexdigest()[:8], 16))
    if style == "timeline3d":
        img = _gradient((70, 130, 200), (200, 225, 245), h, w)
        horizon = int(h * rnd.uniform(0.50, 0.58))
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for _ in range(9):
            cx, cy = rnd.randint(0, w), rnd.randint(40, int(h * 0.30))
            rx, ry = rnd.randint(140, 320), rnd.randint(40, 90)
            d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=(255, 255, 255, 170))
        layer = layer.filter(ImageFilter.GaussianBlur(28))
        img.paste(layer, (0, 0), layer)
        water = _gradient((120, 125, 105), (70, 80, 85), h - horizon, w)
        img.paste(water, (0, horizon))
        d = ImageDraw.Draw(img, "RGBA")
        # bridge-like silhouette: deck + pillars
        deck_y = horizon - rnd.randint(40, 120)
        d.rectangle([0, deck_y, w, deck_y + 34], fill=(150, 70, 35, 255))
        for x in range(30, w, rnd.randint(150, 230)):
            d.rectangle([x, deck_y + 34, x + 26, horizon + 60], fill=(110, 52, 28, 255))
        for x in range(0, w, 60):
            d.line([x, deck_y, x + 30, deck_y - 60], fill=(150, 70, 35, 255), width=6)
    else:
        img = _gradient((10, 14, 28), (28, 20, 40), h, w)
        d = ImageDraw.Draw(img, "RGBA")
        for _ in range(60):
            x, y = rnd.randint(0, w), rnd.randint(0, int(h * 0.55))
            r = rnd.randint(2, 5)
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 220, 150, rnd.randint(70, 200)))
        d.ellipse([w * 0.68, 150, w * 0.68 + 120, 270], fill=(250, 240, 210, 235))
        floor = int(h * 0.62)
        d.rectangle([0, floor, w, h], fill=(70, 40, 25, 255))
        d.rectangle([120, floor - 260, w - 120, floor], fill=(110, 66, 36, 255))   # desk
        d.polygon([(700, floor - 260), (900, floor - 260), (820, floor - 560)], fill=(255, 190, 90, 70))
        d.ellipse([300, floor - 520, 460, floor - 360], fill=(235, 170, 120, 255))  # head
        d.rectangle([270, floor - 360, 490, floor - 260], fill=(120, 170, 230, 255))
    img.save(out)
    return out


# ---------------------------------------------------------------- fal
def _fal_headers():
    return {"Authorization": "Key " + env("FAL_KEY"), "Content-Type": "application/json"}


def _fal_run(model, payload, timeout=900):
    """Queue API: submit, poll, fetch."""
    base = "https://queue.fal.run/" + model
    r = requests.post(base, headers=_fal_headers(), json=payload, timeout=60)
    r.raise_for_status()
    job = r.json()
    status_url, response_url = job["status_url"], job["response_url"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = requests.get(status_url, headers=_fal_headers(), timeout=60).json()
        if s.get("status") == "COMPLETED":
            res = requests.get(response_url, headers=_fal_headers(), timeout=120)
            res.raise_for_status()
            return res.json()
        if s.get("status") in ("FAILED", "ERROR"):
            raise RuntimeError("fal job failed: " + str(s)[:300])
        time.sleep(3)
    raise TimeoutError("fal job timed out")


def _download(url, out):
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    pathlib.Path(out).write_bytes(r.content)


def fal_image(prompt, out, cfg, seed):
    res = _fal_run(cfg["images"]["fal_model"], {
        "prompt": prompt, "image_size": "portrait_16_9", "seed": seed,
        "num_images": 1, "enable_safety_checker": True,
    })
    _download(res["images"][0]["url"], out)
    return out


def openai_image(prompt, out, cfg):
    r = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": "Bearer " + env("OPENAI_API_KEY")},
        json={"model": cfg["images"]["openai_model"], "prompt": prompt, "size": "1024x1536", "n": 1},
        timeout=300,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"OpenAI image error {r.status_code}: {r.text[:300]}")
    item = r.json()["data"][0]
    if item.get("b64_json"):
        pathlib.Path(out).write_bytes(base64.b64decode(item["b64_json"]))
    else:
        _download(item["url"], out)
    return out


# ---------------------------------------------------------------- free providers
def cloudflare_image(prompt, out, cfg, seed):
    """Cloudflare Workers AI (free daily allowance). Needs CF_ACCOUNT_ID + CF_API_TOKEN.
    Settings the chosen model does not accept are dropped automatically and the call is retried."""
    c = cfg["images"].get("cloudflare", {})
    acct, tok = env("CF_ACCOUNT_ID"), env("CF_API_TOKEN")
    model = c.get("model", "@cf/black-forest-labs/flux-1-schnell")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
    payload = {"prompt": prompt[:2000]}
    if c.get("steps"):
        payload["steps"] = c["steps"]
    if c.get("width") and c.get("height"):
        payload.update(width=c["width"], height=c["height"])
    if c.get("use_seed"):
        payload["seed"] = seed
    r = None
    for attempt in range(8):
        r = requests.post(url, headers={"Authorization": "Bearer " + tok}, json=payload, timeout=300)
        if r.status_code == 429:
            time.sleep(15 * (attempt + 1))
            continue
        if r.status_code == 400:
            bad = re.findall(r"properties '/(\w+)'", r.text)
            bad = [k for k in bad if k in payload and k != "prompt"]
            if bad:
                for k in bad:
                    log(f"Cloudflare model does not accept '{k}', removing it")
                    payload.pop(k)
                continue
        break
    if r.status_code >= 400:
        raise RuntimeError(f"Cloudflare image error {r.status_code}: {r.text[:300]}")
    ct = r.headers.get("content-type", "")
    if ct.startswith("image/"):
        pathlib.Path(out).write_bytes(r.content)
    else:
        j = r.json()
        b64 = (j.get("result") or {}).get("image") or j.get("image")
        if not b64:
            raise RuntimeError("Cloudflare returned no image: " + str(j)[:300])
        pathlib.Path(out).write_bytes(base64.b64decode(b64))
    return out


_last_pollinations = [0.0]


def pollinations_image(prompt, out, cfg, seed):
    """Pollinations legacy endpoint: no key. The free limit is about 1 image / 45 s per IP (it changes), so
    the code waits as long as the server says. Slow: use as a backup only."""
    c = cfg["images"].get("pollinations", {})
    q = urllib.parse.urlencode({"width": 1080, "height": 1920, "model": c.get("model", "flux"),
                                "seed": seed, "nologo": "true", "private": "true"})
    url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:900]) + "?" + q
    r = None
    for attempt in range(4):
        wait = c.get("min_interval", 46) - (time.time() - _last_pollinations[0])
        if wait > 0:
            time.sleep(wait)
        _last_pollinations[0] = time.time()
        r = requests.get(url, timeout=300)
        if r.status_code in (402, 429):
            m = re.search(r"[Rr]etry after (\d+)", r.text)
            time.sleep((int(m.group(1)) if m else 45) + 2)
            continue
        break
    if r.status_code >= 400 or not r.headers.get("content-type", "").startswith("image/"):
        raise RuntimeError(f"Pollinations error {r.status_code}: {r.text[:200]}")
    pathlib.Path(out).write_bytes(r.content)
    return out


PROVIDERS = {
    "cloudflare": lambda prompt, out, cfg, seed, style: cloudflare_image(prompt, out, cfg, seed),
    "pollinations": lambda prompt, out, cfg, seed, style: pollinations_image(prompt, out, cfg, seed),
    "fal": lambda prompt, out, cfg, seed, style: fal_image(prompt, out, cfg, seed),
    "openai": lambda prompt, out, cfg, seed, style: openai_image(prompt, out, cfg),
    "placeholder": lambda prompt, out, cfg, seed, style: placeholder(prompt, out, style),
}


# ---------------------------------------------------------------- framing
def fit_canvas(im, tw, th, anchor="center"):
    """Make any picture a clean tw x th frame. Near-9:16 -> crop to fill. Otherwise (e.g. square)
    -> sharp picture at full width over a blurred copy of itself, with soft edges."""
    im = im.convert("RGB")
    r = im.width / im.height
    scale = max(tw / im.width, th / im.height)
    cover = im.resize((int(im.width * scale) + 1, int(im.height * scale) + 1), Image.LANCZOS)
    l, t = (cover.width - tw) // 2, (cover.height - th) // 2
    cover = cover.crop((l, t, l + tw, t + th))
    if abs(r - tw / th) < 0.06:
        return cover
    canvas = cover.filter(ImageFilter.GaussianBlur(45))
    canvas = Image.eval(canvas, lambda v: int(v * 0.65))
    fh = int(tw / r)
    fg = im.resize((tw, fh), Image.LANCZOS)
    y = 0 if anchor == "top" else (th - fh) // 2
    feather = min(120, fh // 4)
    a = np.full((fh, tw), 255, dtype="float32")
    ramp = np.linspace(0, 255, feather, dtype="float32")[:, None]
    if anchor != "top":
        a[:feather] = np.minimum(a[:feather], ramp)
    a[-feather:] = np.minimum(a[-feather:], ramp[::-1])
    mask = Image.fromarray(a.astype("uint8"), "L")
    canvas.paste(fg, (0, y), mask)
    return canvas


def make_image(prompt, out, cfg, style, seed=1):
    """Generate one scene image. images.provider may be one name or a list tried in order (fallback)."""
    out = pathlib.Path(out)
    provs = cfg["images"]["provider"]
    provs = [provs] if isinstance(provs, str) else list(provs)
    suffix = cfg["images"]["style_suffix"].get(style, "")
    full = f"{prompt}. {suffix}"
    raw = out.with_suffix(".raw.png")
    errors = []
    for name in provs:
        if name not in PROVIDERS:
            raise SystemExit(f"unknown images.provider {name}")
        for attempt in range(2):
            try:
                PROVIDERS[name](prompt if name == "placeholder" else full, raw, cfg, seed + attempt, style)
                Image.open(raw).verify()
                break
            except (Exception, SystemExit) as e:   # SystemExit = missing API key -> try the next provider
                errors.append(f"{name}: {e}")
                log(f"image provider {name} failed ({str(e)[:120]})")
                raw.unlink(missing_ok=True)
        else:
            continue
        break
    else:
        raise RuntimeError("all image providers failed:\n" + "\n".join(errors[-6:]))
    im = Image.open(raw)
    anchor = "top" if style == "explainer" else "center"
    fit_canvas(im, cfg["video"]["width"], cfg["video"]["height"], anchor).save(out)
    raw.unlink(missing_ok=True)
    return out


def image_to_video(image_path, prompt, out, cfg):
    """Optional: animate a still with fal Kling. Returns mp4 path, or None when disabled."""
    if cfg["i2v"]["provider"] != "fal_kling":
        return None
    b64 = base64.b64encode(pathlib.Path(image_path).read_bytes()).decode()
    log("image-to-video:", pathlib.Path(image_path).name)
    res = _fal_run(cfg["i2v"]["fal_model"], {
        "prompt": prompt + ", subtle natural camera motion, realistic",
        "image_url": "data:image/png;base64," + b64,
        "duration": str(cfg["i2v"].get("seconds", 5)),
        "aspect_ratio": "9:16",
    }, timeout=1500)
    _download(res["video"]["url"], out)
    return out
