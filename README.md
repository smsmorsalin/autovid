# Autovid

Autovid creates Bengali vertical short videos, sends them to Telegram for
review, and uploads an approved video to YouTube.

```text
topics.txt or Telegram /add
        -> Gemini research and Bengali script
        -> Cloudflare/Pollinations images
        -> ElevenLabs natural voice
        -> Gemini natural Bengali TTS fallback
        -> Edge Bengali neural voice fallback
        -> rendered vertical MP4
        -> Telegram review
        -> reply "upload"
        -> YouTube upload and Telegram URL
```

## Free-plan services

The project is designed to work with free quotas. Provider limits and model
names can change, so check the provider dashboards if a service rejects a
request.

| Purpose | Primary provider | Fallback | Secret |
|---|---|---|---|
| Research and script | Gemini | — | `GEMINI_API_KEY` |
| Images | Cloudflare Workers AI | Pollinations | `CF_ACCOUNT_ID`, `CF_API_TOKEN` |
| Voice | ElevenLabs | Gemini TTS, then Edge TTS | `ELEVENLABS_API_KEY` optional |
| Telegram review and commands | Telegram Bot API | — | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| YouTube upload | YouTube Data API | — | OAuth secrets |
| Scheduling and state | GitHub Actions | — | automatic `GITHUB_TOKEN` |

ElevenLabs is optional. The configured voice order is:

1. ElevenLabs `eleven_multilingual_v2` (most natural when available).
2. Gemini `gemini-3.8-flash-tts`, with a warm Bangladeshi Bengali style.
3. Microsoft Edge `bn-BD-NabanitaNeural`, which needs no API key.

## Required GitHub Actions secrets

Add secrets at **Repository → Settings → Secrets and variables → Actions**:

```text
GEMINI_API_KEY
CF_ACCOUNT_ID
CF_API_TOKEN
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
YT_CLIENT_ID
YT_CLIENT_SECRET
YT_REFRESH_TOKEN
```

Optional:

```text
ELEVENLABS_API_KEY
FAL_KEY
```

`FAL_KEY` is only needed if you change the image or image-to-video provider to
Fal. ElevenLabs also requires a Bangla-capable voice ID in
`config.yaml`; the API key itself must remain a GitHub secret.

### GitHub workflow permission

No separate GitHub API key is required. The workflows use GitHub's automatic
`GITHUB_TOKEN` to commit queue and progress state.

Enable it once:

1. Open **Repository → Settings → Actions → General**.
2. Find **Workflow permissions**.
3. Select **Read and write permissions**.
4. Click **Save**.

This allows Actions to commit `state/done.json`, `state/telegram.json`, and
`scripts.json`.

For complete provider and OAuth instructions, read
[API_CONNECTION_PROCESS_AND_TELEGRAM_BOT_SETUP.md](./API_CONNECTION_PROCESS_AND_TELEGRAM_BOT_SETUP.md).

## Local offline test

Install the system and Python dependencies:

```bash
pip install -r requirements.txt
python -m playwright install chromium
sudo apt install ffmpeg fonts-noto-core
```

Run without network APIs:

```bash
python -m autovid --config config.offline.yaml make \
  --script samples/explainer_database_index.json \
  --style explainer \
  --out out/demo
```

For a real local run, export the required environment variables and run:

```bash
python -m autovid next
python -m autovid make \
  --topic "ক্যাশ কী" \
  --style explainer \
  --out out/cache \
  --notify
```

## Scheduled video process

The `Daily video` workflow:

1. Checks `scripts.json` first, then finds the first unfinished line in
   `topics.txt`.
2. Generates the researched Bengali script.
3. Creates images and narration using the configured fallback order.
4. Renders `final.mp4`, `meta.json`, and `script.json`.
5. Sends the finished video to your Telegram chat.
6. Records the completed topic in `state/done.json`.
7. Uploads the generated files as a GitHub Actions artifact.

The schedule is configured in
[daily.yml](./.github/workflows/daily.yml). Its default cron is 04:00 UTC,
which is 10:00 Bangladesh time. Change the cron if another time is required.

The workflow can also be started manually with a topic and style.

## Telegram workflow

The Telegram bot is implemented in
[telegram_bot.py](./autovid/telegram_bot.py), and
[telegram-bot.yml](./.github/workflows/telegram-bot.yml) polls Telegram every
five minutes. This polling design works on the free GitHub Actions plan without
running a paid, always-on server.

### Review and upload the generated video

1. The scheduled workflow sends the generated video to Telegram.
2. Reply to that exact video message with:

   ```text
   upload
   ```

3. The bot downloads the original generated video from Telegram.
4. It uploads that video to YouTube using the saved title, description, and
   tags.
5. On success, it sends the YouTube Shorts URL back to Telegram.
6. On failure, it sends the error message back to Telegram and leaves the
   review item retryable.

You can upload a replacement instead: reply to the review message with another
video. The replacement video is uploaded with the original review metadata.

Only messages from `TELEGRAM_CHAT_ID` are accepted.

### Telegram commands

Add a topic or script to the end of the queue:

```text
/add আমার নতুন বাংলা ভিডিওর বিষয়
```

Add a timeline-style topic:

```text
/add timeline3d | বাংলাদেশের সেতুর ইতিহাস
```

Check the number of unfinished topics and queued scripts:

```text
/remaining
```

Show the instructions:

```text
/help
```

The bot sends the remaining count with each generated video as well.

### Duplicate upload protection

After a successful upload, the bot stores the Telegram file identifier and
SHA-256 hash in `state/telegram.json`. If `upload` is sent again for the same
video, it will not upload a second copy and will return the existing YouTube
URL. The pending review item is removed only after a successful upload, so a
failed upload can be retried.

The daily workflow and Telegram polling workflow share a concurrency group to
avoid racing while committing state.

## Topic and queue files

`topics.txt` supports:

```text
explainer | API কী: রেস্টুরেন্টের ওয়েটারের উদাহরণ
timeline3d | মোবাইল ফোনের ইতিহাস
```

Telegram additions are stored in `scripts.json`. Completed topics are recorded
in `state/done.json`. Telegram update offsets, pending review messages, and
duplicate-upload records are stored in `state/telegram.json`.

Do not delete or edit these state files while a workflow is running.

## Configuration

Important settings in `config.yaml`:

```yaml
tts:
  provider: [elevenlabs, gemini, edge]
  elevenlabs_voice_id: ""

voice:
  fallback_to_gemini: true
  gemini_voice_model: gemini-3.8-flash-tts
  gemini_voice_name: Kore

publish:
  youtube: true
  privacy: private
```

For ElevenLabs:

1. Open **ElevenLabs → Voices → My Voices**.
2. Select a Bangla-capable voice.
3. Open the three-dot menu.
4. Choose **Copy voice ID**.
5. Paste only the ID into `tts.elevenlabs_voice_id`.
6. Store the API key as the GitHub secret `ELEVENLABS_API_KEY`.

Start with `privacy: private`, verify the full Telegram-to-YouTube flow, and
only then change it to `unlisted` or `public`.

## YouTube requirements

YouTube upload needs a one-time OAuth setup and these three secrets:

```text
YT_CLIENT_ID
YT_CLIENT_SECRET
YT_REFRESH_TOKEN
```

The refresh token belongs to the Google account that owns the channel. Never
commit it to the repository. A successful upload returns:

```text
https://youtube.com/shorts/<video-id>
```

## Safety and quality

Review every generated video before publishing. AI research can misstate facts,
and generated images or narration may need correction. Keep `privacy: private`
until the output, metadata, voice, and Telegram approval flow are trusted.
