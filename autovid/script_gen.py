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
NO_SEARCH_NOTE = "[NO WEB SEARCH: these facts come from the AI's own memory and are NOT verified]"


def _gemini_candidates(key):
    """Models this key can call, newest plain Flash first, then Flash-Lite."""
    r = requests.get(f"{_GEM}/models", params={"key": key, "pageSize": 200}, timeout=60)
    r.raise_for_status()
    found = []
    for m in r.json().get("models", []):
        name = m["name"].split("/")[-1]
        if "flash" not in name or any(x in name for x in ("image", "tts", "live", "audio", "thinking", "embed", "native")):
            continue
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        ver = tuple(int(x) for x in re.findall(r"\d+", name)) or (0,)
        found.append(("lite" in name, tuple(-v for v in ver), name))
    return [n for _, _, n in sorted(found)]


def _hard_quota(text):
    t = text.lower()
    return "limit: 0" in t or "perday" in t or "per day" in t or "billing" in t


def _retry_delay(text):
    m = re.search(r'retrydelay"?\s*[:=]\s*"?(\d+)', text.lower())
    return int(m.group(1)) if m else None


class _Fail:
    """Stand-in response for network errors / timeouts so the caller can fall back."""
    def __init__(self, text):
        self.status_code, self.text = 599, text

    def json(self):
        return {}


def _gem_post(model, body, key):
    """One call. Short per-minute rate limits are waited out; hard quota errors and timeouts return at once
    (the caller then tries the next option). Rejected optional settings (thinking) are dropped and retried."""
    timeout = (20, 180)
    r = None
    for attempt in range(4):
        try:
            r = requests.post(f"{_GEM}/models/{model}:generateContent", params={"key": key}, json=body, timeout=timeout)
        except requests.exceptions.RequestException as e:
            log(f"Gemini {model}: {type(e).__name__} (no answer within {timeout[1]}s)")
            if attempt == 0:
                continue                                  # one more try, then give up on this model
            return _Fail(f"{type(e).__name__}: {str(e)[:150]}")
        if r.status_code == 400 and "thinking" in r.text.lower() and "thinkingConfig" in body["generationConfig"]:
            log("model rejects thinkingConfig, removing it")
            body["generationConfig"].pop("thinkingConfig")
            continue
        if r.status_code in (500, 503):
            time.sleep(15 * (attempt + 1))
            continue
        if r.status_code == 429:
            wait = _retry_delay(r.text) or 30
            if _hard_quota(r.text) or wait > 90:
                return r
            log(f"Gemini rate limit on {model}, waiting {wait + 2}s")
            time.sleep(wait + 2)
            continue
        return r
    return r


def _gemini(cfg, system, user, search, max_tokens):
    """Google AI Studio (free key). Research uses Google Search grounding; the script call asks for JSON.
    Falls back: without search -> other available Flash models. Never loops on a hard quota error."""
    key = env("GEMINI_API_KEY")
    models = [cfg["llm"]["model"]] if cfg["llm"]["model"] != "auto" else []
    discovered = not models
    if discovered:
        models = _gemini_candidates(key)
    errors, i = [], 0
    while i < len(models):
        model = models[i]
        for use_search in ([True, False] if search and not cfg["llm"].get("_no_search") else [False]):
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7,
                                     "thinkingConfig": {"thinkingBudget": 0}},
            }
            if use_search:
                body["tools"] = [{"google_search": {}}]
            elif not search:
                body["generationConfig"]["responseMimeType"] = "application/json"
            r = _gem_post(model, body, key)
            if r.status_code == 200:
                cands = r.json().get("candidates") or []
                if not cands:
                    errors.append(f"{model}: empty answer")
                    continue
                parts = cands[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                if search and use_search:
                    chunks = (cands[0].get("groundingMetadata") or {}).get("groundingChunks") or []
                    links = [c["web"]["uri"] for c in chunks if c.get("web", {}).get("uri")]
                    if links:
                        text += "\n\nSOURCE LINKS found by search:\n" + "\n".join(dict.fromkeys(links))
                if search and not use_search:
                    cfg["llm"]["_no_search"] = True        # search has no quota: skip it from now on
                    text = NO_SEARCH_NOTE + "\n" + text
                    log("WARNING: Google Search quota unavailable; facts are NOT web-verified")
                if cfg["llm"]["model"] != model:
                    log("Gemini model in use:", model)
                    cfg["llm"]["model"] = model
                return text
            errors.append(f"{model}{' +search' if use_search else ''}: {r.status_code} {r.text[:160]}")
            log(f"Gemini {model}{' with search' if use_search else ''} -> {r.status_code}")
            if r.status_code not in (404, 429, 599):
                raise RuntimeError(f"Gemini API error {r.status_code}: {r.text[:500]}")
            if r.status_code == 404:
                break                                    # model gone: next model, no point retrying without search
        i += 1
        if i == len(models) and not discovered:          # configured model failed: look at what the key can use
            discovered = True
            models += [m for m in _gemini_candidates(key)[:4] if m not in models]
    raise RuntimeError(
        "All Gemini models failed. Open https://aistudio.google.com/rate-limit to see which models have free "
        "quota for your key, or create a new key in a new project.\n" + "\n".join(errors[-6:]))


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
    script["facts_verified"] = not facts.startswith("[NO WEB SEARCH")
    script["topic"] = topic
    return script
