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

## 2. The all-free stack (no credit card, no payment)

| Job | Free service | Secret name(s) |
|---|---|---|
| Research + script | Google Gemini API (Google AI Studio free key, Google Search grounding) | `GEMINI_API_KEY` |
| Images | Cloudflare Workers AI (free daily allowance), with Pollinations (no key) as automatic backup | `CF_ACCOUNT_ID`, `CF_API_TOKEN` |
| Voice | edge-tts (Microsoft Bengali voices), with Google `gTTS` as automatic backup | none |
| Review on your phone | Telegram bot | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Upload | YouTube Data API (optional, later) | `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` |
| Scheduler | GitHub Actions | none |

How to get each key:
* **Gemini:** aistudio.google.com → *Get API key* (needs only a Google account). Check which Flash model is free there and put its name in `llm.model` in `config.yaml`.
* **Cloudflare:** free account at cloudflare.com → note your *Account ID* (dashboard sidebar) → *My Profile → API Tokens → Create Token* with the **Workers AI** permission.
* **Telegram:** message @BotFather → `/newbot` → token. Send your bot a message, open `https://api.telegram.org/bot<TOKEN>/getUpdates`, copy `"chat":{"id": ...}`.

Free tiers change and have daily limits. If a provider hits its limit the run falls back to the next one; if all fail, the run stops with an error instead of posting a bad video.

Paid options still exist in the code (`anthropic`, `fal`, `openai`, `elevenlabs`) but are not used by default.

## 3. Run it

```bash
export GEMINI_API_KEY=... CF_ACCOUNT_ID=... CF_API_TOKEN=...
python -m autovid make --topic "ক্যাশ কী" --style explainer --out out/cache
```

## 4. Put it on a schedule (GitHub Actions)

1. Push this folder to a GitHub repo. Private repos get 2,000 free Actions minutes a month (one video a day fits); public repos are free without that limit (your secrets stay hidden).
2. Settings → Secrets and variables → Actions → add the secrets above.
3. Settings → Actions → General → Workflow permissions → *Read and write*.
4. Edit `topics.txt` (one `style | topic` per line) and `config.yaml` (channel name, handle, voice).
5. **Daily video** runs every day at 04:00 UTC (10:00 Bangladesh time; edit the cron in `.github/workflows/daily.yml`). It also has a *Run workflow* button where you can type a one-off topic.
6. **Approval:** watch the video in Telegram. To publish, run the **Publish** workflow and enter that Daily run's ID. Set `publish.require_approval: false` to post automatically.

Start with `publish.privacy: private` and one platform until you trust the output.

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
* **More motion in 3D style:** image-to-video (`i2v.provider: fal_kling`) is paid, so the free setup uses slow zoom/pan instead.
* **Music:** set `video.music` to a royalty-free mp3 you have the right to use.
* **Free-limit budget:** one video uses about 20 images, 2 Gemini calls and about 10-20 Actions minutes.

## Known limits

* The YouTube / Facebook / Instagram uploaders call live APIs and were **not** run against real accounts when this was written. Test with a private upload first.
* The research step uses Anthropic's server-side web search tool; the tool version name is in `config.yaml` (`llm.web_search_tool`) in case it changes.
* Placeholder visuals are only for offline testing.

## more information
