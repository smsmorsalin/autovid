"""Text-to-speech providers. Each returns (audio_path, duration_seconds, word_timings|None).

word_timings = [(word, start_s, end_s), ...] in speaking order, used for caption highlighting.
When a provider cannot give timings, captions fall back to character-proportional timing.
"""
import asyncio
import base64
import pathlib

import requests

from .util import env, media_duration, run


def _silent(text, out, cfg):
    words = max(1, len(text.split()))
    secs = max(1.5, words * 0.42)
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-t", f"{secs:.2f}", "-c:a", "libmp3lame", "-q:a", "6", out])
    return out, media_duration(out), None


def _edge(text, out, cfg):
    import edge_tts

    t = cfg["tts"]
    words = []

    async def go():
        try:
            comm = edge_tts.Communicate(text, t["edge_voice"], rate=t.get("edge_rate", "+0%"),
                                        boundary="WordBoundary")
        except TypeError:  # older edge-tts has no boundary argument
            comm = edge_tts.Communicate(text, t["edge_voice"], rate=t.get("edge_rate", "+0%"))
        with open(out, "wb") as f:
            async for ch in comm.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    s = ch["offset"] / 1e7
                    words.append((ch["text"], s, s + ch["duration"] / 1e7))

    asyncio.run(go())
    return out, media_duration(out), (words or None)


def _eleven(text, out, cfg):
    t = cfg["tts"]
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{t['elevenlabs_voice_id']}/with-timestamps",
        headers={"xi-api-key": env("ELEVENLABS_API_KEY")},
        json={"text": text, "model_id": t.get("elevenlabs_model", "eleven_multilingual_v2")},
        timeout=300,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text[:300]}")
    data = r.json()
    pathlib.Path(out).write_bytes(base64.b64decode(data["audio_base64"]))
    al = data.get("alignment") or {}
    chars = al.get("characters") or []
    st = al.get("character_start_times_seconds") or []
    en = al.get("character_end_times_seconds") or []
    words, cur, cs, ce = [], "", None, None
    for ch, a, b in zip(chars, st, en):
        if ch.isspace():
            if cur:
                words.append((cur, cs, ce))
            cur, cs = "", None
            continue
        if cs is None:
            cs = a
        cur += ch
        ce = b
    if cur:
        words.append((cur, cs, ce))
    return out, media_duration(out), (words or None)


def _gtts(text, out, cfg):
    """Google Translate voice: free, no key, robotic but reliable. No word timings."""
    from gtts import gTTS

    tmp = str(out) + ".raw.mp3"
    gTTS(text=text, lang=cfg["tts"].get("gtts_lang", "bn")).save(tmp)
    # slight speed-up sounds more natural; atempo keeps pitch
    run(["ffmpeg", "-y", "-v", "error", "-i", tmp, "-filter:a", "atempo=1.08", out])
    pathlib.Path(tmp).unlink(missing_ok=True)
    return out, media_duration(out), None


_IMPL = {"silent": _silent, "edge": _edge, "elevenlabs": _eleven, "gtts": _gtts}


def synthesize(text, out, cfg):
    """tts.provider may be one name or a list tried in order (e.g. [edge, gtts])."""
    provs = cfg["tts"]["provider"]
    provs = [provs] if isinstance(provs, str) else list(provs)
    errors = []
    for name in provs:
        if name not in _IMPL:
            raise SystemExit(f"unknown tts.provider {name}")
        for attempt in range(2):
            try:
                return _IMPL[name](text, str(out), cfg)
            except (Exception, SystemExit) as e:
                errors.append(f"{name}: {e}")
    raise RuntimeError("all TTS providers failed:\n" + "\n".join(errors[-4:]))
