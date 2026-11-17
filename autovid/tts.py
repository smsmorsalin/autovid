"""Text-to-speech providers with word timings where the provider supports them."""
import asyncio
import base64
import pathlib
import wave

import requests

from .util import env, media_duration, run


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


def _eleven(text, out, cfg):
    tts = cfg["tts"]
    voice_id = tts.get("elevenlabs_voice_id")
    if not voice_id:
        raise RuntimeError("tts.elevenlabs_voice_id is not configured")
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
        headers={"xi-api-key": env("ELEVENLABS_API_KEY")},
        json={
            "text": text,
            "model_id": tts.get("elevenlabs_model", "eleven_multilingual_v2"),
            "language_code": "bn",
        },
        timeout=300,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"ElevenLabs error {response.status_code}: {response.text[:300]}")
    data = response.json()
    pathlib.Path(out).write_bytes(base64.b64decode(data["audio_base64"]))
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
            return _IMPLEMENTATIONS[name](text, str(out), cfg)
        except (Exception, SystemExit) as exc:
            errors.append(f"{name}: {exc}")
    if cfg.get("voice", {}).get("fallback_to_gemini", False) and "gemini" not in providers:
        try:
            return _gemini(text, str(out), cfg)
        except (Exception, SystemExit) as exc:
            errors.append(f"gemini: {exc}")
    raise RuntimeError("all TTS providers failed:\n" + "\n".join(errors[-4:]))
