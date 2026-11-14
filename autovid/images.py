"""Image providers (placeholder / fal Flux / OpenAI) and optional image-to-video (fal Kling)."""
import base64
import hashlib
import pathlib
import random
import time

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


def make_image(prompt, out, cfg, style, seed=1):
    """Generate one scene image and normalise it to 9:16 at the configured size."""
    out = pathlib.Path(out)
    prov = cfg["images"]["provider"]
    suffix = cfg["images"]["style_suffix"].get(style, "")
    full = f"{prompt}. {suffix}"
    raw = out.with_suffix(".raw.png")
    if prov == "placeholder":
        placeholder(prompt, raw, style)
    elif prov == "fal":
        fal_image(full, raw, cfg, seed)
    elif prov == "openai":
        openai_image(full, raw, cfg)
    else:
        raise SystemExit(f"unknown images.provider {prov}")
    im = Image.open(raw).convert("RGB")
    tw, th = cfg["video"]["width"], cfg["video"]["height"]
    scale = max(tw / im.width, th / im.height)
    im = im.resize((int(im.width * scale) + 1, int(im.height * scale) + 1), Image.LANCZOS)
    l, t = (im.width - tw) // 2, (im.height - th) // 2
    im.crop((l, t, l + tw, t + th)).save(out)
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
