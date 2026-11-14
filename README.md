# autovid: automated Bengali short-video factory

Topic in → researched script → images → Bengali voice → captions → 9:16 video → Telegram review → YouTube / Facebook / Instagram.

Two styles, chosen per topic in `topics.txt`:

| style | look | scene kinds |
|---|---|---|
| `explainer` | dark UI + illustrated scenes ("System Ka Scene" style) | image, table, compare, code, stat |
| `timeline3d` | full-screen 3D-look scenes, colour-coded year headings, era colour grading ("bridge history" style) | image (optionally animated by image-to-video) |

```
topics.txt ─► research (Claude + web search, sourced facts)
          ─► script.json (Bengali scenes, JSON)
          ─► images (fal Flux / OpenAI)  [+ optional Kling image-to-video]
          ─► voice (edge-tts free / ElevenLabs) + word timings
          ─► render (HTML cards + ffmpeg: motion, grade, captions)
          ─► final.mp4 + meta.json ─► Telegram ─► (you approve) ─► YouTube/Facebook/Instagram
```

## 1. Try it locally (no API keys needed)

```bash
pip install -r requirements.txt && python -m playwright install chromium
sudo apt install ffmpeg fonts-noto-core          # Bengali font + ffmpeg
python -m autovid --config config.offline.yaml make \
    --script samples/explainer_database_index.json --style explainer --out out/demo
```
The offline config uses placeholder pictures and silent narration, just to check the layout. `config.yaml` is the real configuration.

## 2. Run for real

Put keys in the environment (locally `export ...`, in GitHub: repo → Settings → Secrets and variables → Actions).

| secret | used for |
|---|---|
| `ANTHROPIC_API_KEY` | research + script writing |
| `FAL_KEY` | images (Flux) and optional image-to-video (Kling). Or use `OPENAI_API_KEY` and set `images.provider: openai` |
| `ELEVENLABS_API_KEY` | only if `tts.provider: elevenlabs` (edge-tts needs no key) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | review messages (create a bot with @BotFather) |
| `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | YouTube upload (Google Cloud → YouTube Data API v3 → OAuth client; get a refresh token once with the `youtube.upload` scope) |
| `FB_PAGE_ID`, `FB_PAGE_TOKEN` | Facebook Page Reels |
| `IG_USER_ID` (+ `FB_PAGE_TOKEN`) | Instagram Reels (needs a public URL of the mp4, see below) |

```bash
python -m autovid make --topic "ক্যাশ কী" --style explainer --out out/cache --notify
```

## 3. Put it on a schedule (GitHub Actions)

1. Push this folder to a GitHub repo (private is fine).
2. Add the secrets above.
3. Edit `topics.txt` (one `style | topic` per line) and `config.yaml` (channel name, handle, voice).
4. **Daily video** runs every day at 04:00 UTC (10:00 Bangladesh time; change the cron in `.github/workflows/daily.yml`). It takes the next unused topic, builds the video, uploads it as a run artifact and sends it to your Telegram. You can also trigger it by hand with a custom topic.
5. **Approval:** watch it in Telegram. To publish, run the **Publish** workflow and enter that Daily run's ID (the number in its URL). To skip approval, set `publish.require_approval: false`, and videos go out automatically.

Tip: start with `publish.privacy: private` and one platform until you trust the output.

## Quality and safety checks built in (keep them)

* Script is written **only from researched facts that have source URLs**; sources are appended to the description. Still read the script before publishing: AI can misstate dates and numbers. The Telegram step is your check.
* AI-made visuals get a small **"প্রতীকী দৃশ্য"** (representative scene) label (`images.label_ai_visuals`), and YouTube is told the video has synthetic media (`publish.mark_synthetic`).
* Prompts forbid real people's faces, logos and on-image text.
* YouTube and Meta demote repetitive, mass-produced content. Vary topics and keep your own voice/brand, rather than posting many near-identical videos a day.

## Instagram

The Instagram API only accepts a **public https URL** of the video. Upload `final.mp4` somewhere public (for example a GitHub Release asset on a public repo, or S3) and run:
`python -m autovid publish --dir approved --public-url https://.../final.mp4`.

## Customising

* **Look & feel:** `render.py` (colours, layout, caption style) and `config.yaml` (`images.style_suffix`, series name, end card).
* **Voice:** `tts.edge_voice` (`bn-BD-NabanitaNeural`, `bn-BD-PradeepNeural`, `bn-IN-TanishaaNeural`) or switch to ElevenLabs for more natural narration.
* **More motion in 3D style:** set `i2v.provider: fal_kling` (costs a few dollars per video).
* **Music:** set `video.music` to a royalty-free mp3 you have the right to use.
* **Cost** (rough): script ~ cents, images ~ a few cents each (20-25 per video), edge-tts free. Image-to-video is the main extra cost.

## Known limits

* The YouTube / Facebook / Instagram uploaders call live APIs and were **not** run against real accounts when this was written. Test with a private upload first.
* The research step uses Anthropic's server-side web search tool; the tool version name is in `config.yaml` (`llm.web_search_tool`) in case it changes.
* Placeholder visuals are only for offline testing.
