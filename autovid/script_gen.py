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
            # Temporary service errors can affect one model or a regional
            # backend. _gem_post has already retried them; let model discovery
            # try another available Flash model before failing the run.
            if r.status_code not in (404, 429, 500, 502, 503, 504, 599):
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


# ---------------------------------------------------------------- YouTube metadata (title / description / tags)
META_SYSTEM = (
    "You are a YouTube Shorts editor for a Bengali educational channel. You write accurate, honest metadata "
    "that helps the right viewers find the video. No clickbait that the video does not deliver, no false claims, "
    "no ALL-CAPS shouting, no emoji spam. Reply with a single JSON object and nothing else."
)


def _clean_tag(t):
    t = re.sub(r"\s+", " ", str(t).replace("#", "")).strip(" ,.;:")
    return t


def sanitize_metadata(obj, script, cfg):
    """Enforce YouTube limits whatever the model returned."""
    mcfg = cfg.get("metadata", {})
    tmax = int(mcfg.get("title_max", 70))
    title = re.sub(r"\s+", " ", str(obj.get("title") or script.get("title") or "")).strip().strip("\"'“”")
    if len(title) > tmax:
        cut = title[:tmax].rsplit(" ", 1)[0].rstrip(" ,:;-")
        title = cut if len(cut) >= tmax // 2 else title[:tmax]
    tags, seen, total = [], set(), 0
    for t in obj.get("tags") or []:
        t = _clean_tag(t)
        k = t.lower()
        if not t or k in seen or len(t) > 40:
            continue
        cost = len(t) + (3 if " " in t else 1)           # YouTube counts quotes + comma for multi-word tags
        if total + cost > 450 or len(tags) >= 15:
            break
        seen.add(k)
        tags.append(t)
        total += cost
    hashtags = []
    for h in obj.get("hashtags") or []:
        h = "#" + re.sub(r"[\s#]+", "", str(h))
        if len(h) > 1 and h.lower() not in [x.lower() for x in hashtags]:
            hashtags.append(h)
    hashtags = hashtags[:5]
    desc = str(obj.get("description") or script.get("description") or "").strip()[:3000]
    return {"title": title, "description": desc, "tags": tags, "hashtags": hashtags}


def make_metadata(script, cfg):
    """Ask the LLM for title / description / tags / hashtags based on the FINAL script.
    Any failure falls back to what the script writer produced, so publishing is never blocked."""
    fallback = sanitize_metadata({
        "title": script.get("title", ""), "description": script.get("description", ""),
        "tags": [h.lstrip("#") for h in script.get("hashtags", [])] + [script.get("topic_short", "")],
        "hashtags": script.get("hashtags", [])}, script, cfg)
    if not cfg.get("metadata", {}).get("auto", True):
        return fallback
    narration = " ".join(sc["narration"] for sc in script["scenes"])
    user = (
        f"TOPIC: {script.get('topic', '')}\nSTYLE: {script.get('style', '')}\n"
        f"VIDEO NARRATION (Bengali):\n{narration}\n\n"
        "Write metadata for this vertical short video.\n"
        f"- title: Bengali, at most {cfg.get('metadata', {}).get('title_max', 70)} characters, specific, starts with the "
        "strongest keyword or a curiosity hook that the video really answers. Do not repeat the same words twice.\n"
        "- description: Bengali, 2-4 short lines. First line = what the viewer will learn (contains the main keywords). "
        "No links, no hashtags inside it.\n"
        "- tags: 10-14 search tags mixing Bengali and English (e.g. the English term people actually search), most "
        "important first, each under 30 characters, no # sign.\n"
        "- hashtags: 3-4 short hashtags (Bengali or English) including the main topic.\n"
        'JSON: {"title": "...", "description": "...", "tags": ["..."], "hashtags": ["#..."]}'
    )
    try:
        log("writing YouTube title/tags...")
        text = _llm(cfg, META_SYSTEM, user, search=False, max_tokens=1500)
        m = re.search(r"\{.*\}", text, re.S)
        meta = sanitize_metadata(json.loads(m.group(0)), script, cfg)
        if not meta["title"] or len(meta["tags"]) < 3:
            raise ValueError("metadata too thin")
        return meta
    except (Exception, SystemExit) as e:
        log("metadata step failed, using the script's own title/hashtags:", str(e)[:150])
        return fallback
