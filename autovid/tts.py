"""Text-to-speech providers with word timings where the provider supports them."""
import asyncio
import base64
import pathlib
import wave

import requests

from .util import env, load_config, log, media_duration, run


def _silent(text, out, cfg):
    seconds = max(1.5, len(text.split()) * 0.42)
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-t", f"{seconds:.2f}", "-c:a", "libmp3lame", "-q:a", "6", out])
    return out, media_duration(out), None


def _edge(text, out, cfg):
    import edge_tts

    words = []

    async def generate():
        voice = cfg["tts"].get("edge_voice", "bn-BD-NabanitaNeural")
        communicate = edge_tts.Communicate(
            text, voice, rate=cfg["tts"].get("edge_rate", "+0%"), boundary="WordBoundary"
        )
        with open(out, "wb") as audio_file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 1e7
                    words.append((chunk["text"], start, start + chunk["duration"] / 1e7))

    asyncio.run(generate())
    return out, media_duration(out), words or None


# ElevenLabs only accepts `language_code` on its Turbo/Flash/v3/v4 models. Sending it
# to eleven_multilingual_v2 is rejected, which is why the voice silently fell back to
# Gemini before. Keep the payload model-aware.
def _eleven_language_supported(model_id):
    # v3/v4 and Turbo/Flash support `language_code`; multilingual_v2 does not, and it
    # does not support Bengali at all. v3/v4 are the correct models for Bangla.
    return any(model_id.startswith(prefix) for prefix in
               ("eleven_turbo", "eleven_flash", "eleven_v3", "eleven_v4"))


def _eleven(text, out, cfg):
    tts = cfg["tts"]
    voice_id = str(tts.get("elevenlabs_voice_id") or "").strip()
    fallback_voice = str(tts.get("elevenlabs_fallback_voice_id") or "").strip()
    if not voice_id and not fallback_voice:
        raise RuntimeError("tts.elevenlabs_voice_id is not configured")
    api_key = env("ELEVENLABS_API_KEY", False)
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    model_id = tts.get("elevenlabs_model", "eleven_v4")
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": float(tts.get("elevenlabs_stability", 0.45)),
            "similarity_boost": float(tts.get("elevenlabs_similarity_boost", 0.8)),
            "style": float(tts.get("elevenlabs_style", 0.15)),
            "use_speaker_boost": True,
        },
    }
    if _eleven_language_supported(model_id):
        payload["language_code"] = "bn"

    # Free-tier keys cannot use Voice Library voices through the API. If the preferred
    # voice is rejected, try the configured premade voice before falling back to Gemini.
    voices = [v for i, v in enumerate([voice_id, fallback_voice])
              if v and v not in [voice_id, fallback_voice][:i]]
    last_error = ""
    for vid in voices:
        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/with-timestamps",
            params={"output_format": tts.get("elevenlabs_output_format", "mp3_44100_128")},
            headers={"xi-api-key": api_key, "Accept": "application/json",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=300,
        )
        if response.status_code < 400:
            voice_id = vid
            break
        last_error = f"ElevenLabs error {response.status_code}: {response.text[:500]}"
        log(f"TTS: ElevenLabs voice {vid} rejected -> {last_error[:200]}")
    else:
        raise RuntimeError(last_error or "ElevenLabs returned no usable voice")
    data = response.json()
    audio_base64 = data.get("audio_base64")
    if not audio_base64:
        raise RuntimeError(f"ElevenLabs returned no audio: {str(data)[:400]}")
    pathlib.Path(out).write_bytes(base64.b64decode(audio_base64))
    alignment = data.get("alignment") or {}
    words, current, start, end = [], "", None, None
    for char, char_start, char_end in zip(
        alignment.get("characters", []),
        alignment.get("character_start_times_seconds", []),
        alignment.get("character_end_times_seconds", []),
    ):
        if char.isspace():
            if current:
                words.append((current, start, end))
            current, start = "", None
            continue
        if start is None:
            start = char_start
        current, end = current + char, char_end
    if current:
        words.append((current, start, end))
    return out, media_duration(out), words or None


def _gemini(text, out, cfg):
    """Use Gemini's native audio response as the free fallback for ElevenLabs."""
    voice = cfg.get("voice", {})
    model = voice.get("gemini_voice_model", "gemini-2.5-flash-preview-tts")
    api_key = env("GEMINI_API_KEY")
    body = {
        "systemInstruction": {
            "parts": [{
                "text": voice.get(
                    "gemini_voice_style",
                    "Speak naturally in Bangladeshi Bengali with a warm, conversational tone.",
                )
            }]
        },
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "languageCode": "bn-BD",
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice.get("gemini_voice_name", "Kore")
                    }
                },
            },
        },
    }
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": api_key}, json=body, timeout=300,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Gemini TTS error {response.status_code}: {response.text[:400]}")
    parts = response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
    inline = next((p.get("inlineData") for p in parts if p.get("inlineData")), None)
    if not inline or not inline.get("data"):
        raise RuntimeError("Gemini TTS returned no audio data")
    pcm = base64.b64decode(inline["data"])
    mime = inline.get("mimeType", "audio/L16;rate=24000")
    rate = 24000
    if "rate=" in mime:
        rate = int(mime.split("rate=", 1)[1].split(";", 1)[0])
    wav = pathlib.Path(out).with_suffix(".wav")
    with wave.open(str(wav), "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(rate)
        audio_file.writeframes(pcm)
    run(["ffmpeg", "-y", "-v", "error", "-i", wav, "-codec:a", "libmp3lame", "-q:a", "3", out])
    wav.unlink(missing_ok=True)
    return out, media_duration(out), None


def _gtts(text, out, cfg):
    from gtts import gTTS

    raw = str(out) + ".raw.mp3"
    gTTS(text=text, lang=cfg["tts"].get("gtts_lang", "bn")).save(raw)
    run(["ffmpeg", "-y", "-v", "error", "-i", raw, "-filter:a", "atempo=1.08", out])
    pathlib.Path(raw).unlink(missing_ok=True)
    return out, media_duration(out), None


_IMPLEMENTATIONS = {
    "silent": _silent,
    "edge": _edge,
    "elevenlabs": _eleven,
    "gemini": _gemini,
    "gtts": _gtts,
}


def synthesize(text, out, cfg):
    providers = cfg["tts"]["provider"]
    providers = [providers] if isinstance(providers, str) else list(providers)
    errors = []
    for name in providers:
        if name not in _IMPLEMENTATIONS:
            raise SystemExit(f"unknown tts.provider {name}")
        try:
            log(f"TTS: trying {name}")
            return _IMPLEMENTATIONS[name](text, str(out), cfg)
        except (Exception, SystemExit) as exc:
            log(f"TTS: {name} failed -> {str(exc)[:300]}")
            errors.append(f"{name}: {exc}")
    if cfg.get("voice", {}).get("fallback_to_gemini", False) and "gemini" not in providers:
        try:
            log("TTS: trying gemini fallback")
            return _gemini(text, str(out), cfg)
        except (Exception, SystemExit) as exc:
            log(f"TTS: gemini fallback failed -> {str(exc)[:300]}")
            errors.append(f"gemini: {exc}")
    raise RuntimeError("all TTS providers failed:\n" + "\n".join(errors[-4:]))


def voice_check(cfg=None, test=False):
    """Print why ElevenLabs does/does not work: key, plan, model, voice, quota."""
    cfg = cfg or load_config()
    tts = cfg.get("tts", {})
    ok = True
    api_key = env("ELEVENLABS_API_KEY", False)
    voice_id = str(tts.get("elevenlabs_voice_id") or "").strip()
    fallback_id = str(tts.get("elevenlabs_fallback_voice_id") or "").strip()
    model_id = tts.get("elevenlabs_model", "eleven_v4")
    print(f"ElevenLabs model : {model_id}")
    print(f"Configured voice : {voice_id or '(missing in config.yaml)'}")
    print(f"Fallback voice   : {fallback_id or '(none)'}")
    if not api_key:
        print("API key          : MISSING (set ELEVENLABS_API_KEY)")
        return False
    print("API key          : present")
    headers = {"xi-api-key": api_key}
    try:
        user = requests.get("https://api.elevenlabs.io/v1/user", headers=headers, timeout=60)
        if user.status_code >= 400:
            print(f"Account check    : FAILED {user.status_code} {user.text[:300]}")
            return False
        info = user.json()
        sub = info.get("subscription") or {}
        print(f"Plan             : {sub.get('tier', 'unknown')}")
        print(f"Characters       : {sub.get('character_count', '?')}/{sub.get('character_limit', '?')}")
    except Exception as exc:
        print(f"Account check    : FAILED {exc}")
        return False

    try:
        models = requests.get("https://api.elevenlabs.io/v1/models", headers=headers, timeout=60)
        models.raise_for_status()
        model_ids = {m.get("model_id") for m in models.json()}
        if model_id in model_ids:
            print("Model check      : OK")
        else:
            ok = False
            print("Model check      : NOT AVAILABLE to this key")
            print(f"                   Available: {', '.join(sorted(m for m in model_ids if m))[:400]}")
    except Exception as exc:
        print(f"Model check      : FAILED {exc}")

    if "multilingual_v2" in model_id:
        ok = False
        print("Language check   : eleven_multilingual_v2 does NOT support Bengali.")
        print("                   Set tts.elevenlabs_model to eleven_v4 (or eleven_v3).")

    try:
        voices = requests.get("https://api.elevenlabs.io/v1/voices", headers=headers, timeout=60)
        voices.raise_for_status()
        ids = {v.get("voice_id") for v in voices.json().get("voices", [])}
        available = []
        for label, vid in (("primary", voice_id), ("fallback", fallback_id)):
            if not vid:
                continue
            if vid in ids:
                print(f"Voice check      : {label} voice OK")
                available.append(vid)
            else:
                print(f"Voice check      : {label} voice NOT AVAILABLE to this key ({vid})")
        if not available:
            ok = False
            print("                   Copy a voice ID from Voices -> My Voices. Voice Library")
            print("                   voices are NOT usable through the API on the free tier.")
    except Exception as exc:
        print(f"Voice check      : FAILED {exc}")
        return False

    if test and ok:
        import tempfile
        sample = pathlib.Path(tempfile.gettempdir()) / "autovid-voicecheck.mp3"
        try:
            print("Synthesis test   : generating a short Bangla sample...")
            _, duration, _ = _eleven("হ্যালো, এটি একটি ছোট ভয়েস পরীক্ষা।", sample, cfg)
            print(f"Synthesis test   : OK ({duration:.1f}s audio)")
        except (Exception, SystemExit) as exc:
            ok = False
            print(f"Synthesis test   : FAILED {str(exc)[:400]}")
        finally:
            sample.unlink(missing_ok=True)

    print("Result           : " + ("READY" if ok else "NOT READY"))
    return ok
