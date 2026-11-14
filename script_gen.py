"""Topic -> researched facts -> structured scene script (JSON), via the Anthropic API."""
import json
import re
import time

import requests

from .util import env, log

API = "https://api.anthropic.com/v1/messages"

KINDS = {
    "explainer": ["image", "table", "compare", "code", "stat"],
    "timeline3d": ["image"],
}
GRADES = ["none", "sepia", "warm", "cool", "dusk"]
TONES = ["year", "warn", "good", "neutral"]


def _anthropic(cfg, system, user, search, max_tokens):
    body = {
        "model": cfg["llm"]["model"],
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if search:
        body["tools"] = [{
            "type": cfg["llm"].get("web_search_tool", "web_search_20250305"),
            "name": "web_search",
            "max_uses": cfg["llm"].get("max_search_uses", 6),
        }]
    headers = {
        "x-api-key": env("ANTHROPIC_API_KEY"),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    r = requests.post(API, headers=headers, json=body, timeout=600)
    if r.status_code >= 400:
        raise RuntimeError(f"Anthropic API error {r.status_code}: {r.text[:500]}")
    return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")


_GEM = "https://generativelanguage.googleapis.com/v1beta"


def _gemini_pick_model(key):
    """Ask Google which models this key can use; pick the newest plain 'flash' that can generateContent."""
    r = requests.get(f"{_GEM}/models", params={"key": key, "pageSize": 200}, timeout=60)
    r.raise_for_status()
    best, best_ver = None, (-1,)
    for m in r.json().get("models", []):
        name = m["name"].split("/")[-1]
        if "flash" not in name or any(x in name for x in ("lite", "image", "tts", "live", "audio", "thinking", "embed", "native")):
            continue
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        ver = tuple(int(x) for x in re.findall(r"\d+", name)) or (0,)
        if ver > best_ver:
            best, best_ver = name, ver
    if not best:
        raise RuntimeError("no usable Gemini flash model found for this key")
    return best


def _gemini(cfg, system, user, search, max_tokens):
    """Google AI Studio (free key). Research uses Google Search grounding; the script call asks for JSON.
    If the configured model is retired (404), the newest available Flash model is picked automatically."""
    key = env("GEMINI_API_KEY")
    model = cfg["llm"]["model"]
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7},
    }
    if search:
        body["tools"] = [{"google_search": {}}]
    else:
        body["generationConfig"]["responseMimeType"] = "application/json"
    switched = False
    for attempt in range(6):
        if model == "auto":
            model = _gemini_pick_model(key)
            log("Gemini model:", model)
        r = requests.post(f"{_GEM}/models/{model}:generateContent", params={"key": key}, json=body, timeout=600)
        if r.status_code in (429, 500, 503):          # free tier: rate limited / busy -> wait and retry
            wait = 20 * (attempt + 1)
            log(f"Gemini {r.status_code}, retrying in {wait}s")
            time.sleep(wait)
            continue
        if r.status_code == 404 and not switched:      # model retired / unavailable -> auto-pick a current one
            log(f"Gemini model {model} unavailable, looking for a current Flash model")
            model, switched = _gemini_pick_model(key), True
            log("Gemini model:", model)
            cfg["llm"]["model"] = model                # remember for the following calls in this run
            continue
        break
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini API error {r.status_code}: {r.text[:500]}")
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError("Gemini returned no candidates: " + str(data)[:400])
    parts = cands[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts)
    if search:  # append the grounding source links so they end up in the description
        chunks = (cands[0].get("groundingMetadata") or {}).get("groundingChunks") or []
        links = [c["web"]["uri"] for c in chunks if c.get("web", {}).get("uri")]
        if links:
            text += "\n\nSOURCE LINKS found by search:\n" + "\n".join(dict.fromkeys(links))
    return text


def _llm(cfg, system, user, search=False, max_tokens=8000):
    prov = cfg["llm"].get("provider", "gemini")
    if prov == "gemini":
        return _gemini(cfg, system, user, search, max_tokens)
    if prov == "anthropic":
        return _anthropic(cfg, system, user, search, max_tokens)
    raise SystemExit(f"unknown llm.provider {prov}")


def research(topic, cfg):
    """Gather verifiable, sourced facts. Returns plain text (bullets with source URLs)."""
    search = bool(cfg["llm"].get("research", True))
    system = (
        "You are a careful fact researcher for short educational videos. "
        "Only include facts confirmed by at least one reliable source. "
        "If sources disagree on a number or date, say so explicitly."
    )
    user = (
        f"Topic: {topic}\n\n"
        "Return 12-20 bullet points of verifiable facts (dates, numbers, names, cause and effect). "
        "End every bullet with the source URL in parentheses when you have one. No opinions, no filler."
    )
    log("researching facts...")
    return _llm(cfg, system, user, search=search, max_tokens=4000)


SCHEMA_EXAMPLE = """{
  "title": "video title in Bengali (max 70 chars)",
  "title_en": "same title in English",
  "description": "2-3 sentence description in Bengali",
  "hashtags": ["#tag1", "#tag2", "#tag3"],
  "topic_short": "2-4 word topic name in Bengali, used on the end card and header pill",
  "visual_bible": "ONE English sentence describing the recurring look/setting/characters so every image stays consistent",
  "sources": [{"title": "...", "url": "https://..."}],
  "scenes": [
    {
      "narration": "Bengali narration, 6-14 words, spoken aloud",
      "kind": "image | table | compare | code | stat",
      "heading": "ONLY for timeline3d: short top text (a year or key fact, max 5 words)",
      "heading_tone": "year | warn | good | neutral",
      "image_prompt": "English prompt for kind=image (no text in the image)",
      "grade": "none | sepia | warm | cool | dusk",
      "motion": "zoom_in | zoom_out | pan_left | pan_right",
      "data": {}
    }
  ]
}"""

DATA_HELP = """data formats (explainer style only):
- table:   {"title":"users","columns":["id","name"],"rows":[["1","..."],["2","..."]],"highlight_row":2}   (max 8 rows, highlight_row is 0-based or omit)
- compare: {"left":{"label":"...","value":"...","tone":"bad"},"right":{"label":"...","value":"...","tone":"good"}}
- code:    {"lines":["SELECT name FROM users","WHERE id = 7421;"]}
- stat:    {"value":"২০ ধাপ","label":"..."}
- image:   {} (uses image_prompt)"""


def _style_guide(style):
    if style == "timeline3d":
        return (
            "STYLE = timeline3d: a chronological explainer about a place, object or event. "
            "Open with a hook. Then go in time order; each dated scene has heading = the year plus a few words. "
            "Every scene is kind=image (a 3D-render look). Use grade=sepia for old eras, warm/dusk for the ending, "
            "none/cool for modern. heading_tone: year=dates, warn=problems/risks, good=solutions, neutral=other. "
            "Close with a one-line takeaway."
        )
    return (
        "STYLE = explainer: teach ONE technical or conceptual idea through a relatable everyday analogy. "
        "Open with a hook question or a tiny story. Mix kinds: image scenes for the story, "
        "table/compare/code/stat scenes to show the mechanism with concrete numbers. "
        "Finish with a question that invites comments. Do not use heading."
    )


def write_script(topic, facts, style, cfg):
    target = cfg.get("target_seconds", 75)
    n_scenes = max(10, int(target / 4))
    system = (
        "You write scripts for faceless vertical short videos in Bengali (Bangla script). "
        "Use simple spoken Bengali, short sentences. Use Bengali digits (০-৯) in narration. "
        "Use ONLY facts from the FACTS section. Never invent numbers, dates or quotes. "
        "Do not depict real people's faces; avoid logos and on-image text in image prompts. "
        "Reply with a single JSON object and nothing else."
    )
    user = (
        f"TOPIC: {topic}\n\nFACTS (researched):\n{facts}\n\n"
        f"{_style_guide(style)}\n\n"
        f"Target length about {target} seconds, roughly {n_scenes} scenes, one idea per scene.\n"
        f"Allowed kinds for this style: {KINDS[style]}.\n{DATA_HELP}\n\n"
        f"JSON shape:\n{SCHEMA_EXAMPLE}\n"
        "sources must list the URLs you actually relied on from FACTS."
    )
    log("writing script...")
    text = _llm(cfg, system, user, search=False, max_tokens=8000)
    return parse_script(text, style)


def parse_script(text, style):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("LLM did not return JSON:\n" + text[:500])
    obj = json.loads(m.group(0))
    return normalize(obj, style)


def normalize(obj, style):
    obj["style"] = style
    obj.setdefault("title", "")
    obj.setdefault("title_en", obj["title"])
    obj.setdefault("description", "")
    obj.setdefault("hashtags", [])
    obj.setdefault("topic_short", obj["title"][:20])
    obj.setdefault("visual_bible", "")
    obj.setdefault("sources", [])
    scenes = obj.get("scenes") or []
    if not scenes:
        raise ValueError("script has no scenes")
    allowed = KINDS[style]
    for s in scenes:
        if not s.get("narration", "").strip():
            raise ValueError("scene without narration")
        if s.get("kind") not in allowed:
            s["kind"] = "image"
        if s["kind"] == "image" and not s.get("image_prompt"):
            s["image_prompt"] = "an atmospheric establishing shot related to: " + s["narration"][:80]
        if s.get("grade") not in GRADES:
            s["grade"] = "none"
        if s.get("heading_tone") not in TONES:
            s["heading_tone"] = "neutral"
        if s.get("motion") not in ("zoom_in", "zoom_out", "pan_left", "pan_right"):
            s["motion"] = "zoom_in"
        s.setdefault("data", {})
        s.setdefault("heading", "")
    return obj


def generate(topic, style, cfg):
    facts = research(topic, cfg)
    script = write_script(topic, facts, style, cfg)
    script["facts"] = facts
    script["topic"] = topic
    return script
