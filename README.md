# Autovid

Autovid creates Bengali vertical short videos, sends them to Telegram for review,
and publishes an approved video to **YouTube Shorts** when you reply `upload` to
the review message. **Facebook Reels** is supported but currently disabled; see
[Facebook requirements](#facebook-requirements) to turn it on.

YouTube visibility is currently set to **public** in `config.yaml`. Replying
`upload` approves the video and publishes it publicly. Change
`publish.privacy` to `private` if you want approved test uploads hidden.

```text
topics.txt or Telegram /add
        -> Gemini research and Bengali script
        -> Cloudflare/Pollinations images
        -> ElevenLabs natural voice
        -> Gemini natural Bengali TTS fallback
        -> Edge Bengali neural voice fallback
        -> rendered vertical MP4
        -> Telegram review video
        -> reply "upload"
        -> YouTube upload (Facebook Reels when enabled)
        -> per-platform success/failure report in Telegram
```

## Free-plan services

The project is designed to work within free quotas. Provider limits and model
names change, so check the provider dashboards if a service rejects a request.

| Purpose | Provider and fallback | Where credentials go |
|---|---|---|
| Research and script | Gemini (Google AI Studio) | GitHub Actions secret `GEMINI_API_KEY` |
| Images | Cloudflare Workers AI, then Pollinations | GitHub Actions secrets `CF_ACCOUNT_ID`, `CF_API_TOKEN` (optional; Pollinations is the fallback) |
| Voice | ElevenLabs, then Gemini TTS, then Edge TTS | GitHub Actions secret `ELEVENLABS_API_KEY` (optional) |
| Telegram review video | Telegram Bot API | GitHub Actions secrets `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Telegram webhook commands | Cloudflare Worker | Cloudflare Worker secrets; see [Telegram webhook setup](#instant-telegram-webhook-cloudflare-worker) |
| YouTube upload | YouTube Data API | GitHub Actions secrets `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` |
| Facebook Reels upload | Meta Graph API (disabled by default) | GitHub Actions secrets `FB_PAGE_ID`, `FB_PAGE_TOKEN` when enabled |
| Scheduling and repository state | GitHub Actions | Automatic `GITHUB_TOKEN` provided to each workflow |

Google AI Studio is used **only** for Gemini script generation and Gemini TTS.
It has nothing to do with Facebook. Facebook Reels use Meta's free Graph API,
explained in
[API_CONNECTION_PROCESS_AND_TELEGRAM_BOT_SETUP.md](./API_CONNECTION_PROCESS_AND_TELEGRAM_BOT_SETUP.md).

The configured voice order is:

1. ElevenLabs `eleven_v4` (most natural, supports Bengali).
2. Gemini `gemini-3.8-flash-tts`, with a warm Bangladeshi Bengali style.
3. Microsoft Edge `bn-BD-NabanitaNeural`, which needs no API key.

## GitHub Actions secrets

Add the secrets required by the features you use at **Repository -> Settings ->
Secrets and variables -> Actions**.

Required for daily video generation and Telegram review:

```text
GEMINI_API_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Required to publish to YouTube after you approve a video:

```text
YT_CLIENT_ID
YT_CLIENT_SECRET
YT_REFRESH_TOKEN
```

Optional or feature-specific:

```text
CF_ACCOUNT_ID          # Cloudflare Images; Pollinations is the fallback
CF_API_TOKEN           # Cloudflare Images; Pollinations is the fallback
ELEVENLABS_API_KEY   # enables the most natural voice
FAL_KEY              # only if you switch image/image-to-video providers to Fal
IG_USER_ID           # only for Instagram Reels
FB_PAGE_ID            # required only when Facebook publishing is enabled
FB_PAGE_TOKEN         # required only when Facebook publishing is enabled
```

The Cloudflare Telegram Worker has its own secrets, separate from GitHub
Actions. The same Telegram bot token and chat ID are used in both places. The
Worker also needs `TELEGRAM_WEBHOOK_SECRET` and a GitHub fine-grained token
stored as its `GITHUB_TOKEN` secret.

### GitHub workflow permission

No separate GitHub API key is required. The workflows use the automatic
`GITHUB_TOKEN` to commit queue, review, and deduplication state.

Enable it once:

1. Open **Repository -> Settings -> Actions -> General**.
2. Find **Workflow permissions**.
3. Select **Read and write permissions**.
4. Save.

This lets Actions commit `state/done.json`, `state/telegram.json`, and
`scripts.json`. **If this is disabled, upload workflows cannot save state or
queue changes.** Replying directly to the review video can still provide
fallback metadata, but matching an un-replied upload and duplicate protection
depend on saved state.

## Local setup

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

For a real local run, export the environment variables and use:

```bash
python -m autovid next
python -m autovid make \
  --topic "ক্যাশ কী" \
  --style explainer \
  --out out/cache \
  --notify
```

## GitHub Actions workflows

### `Daily video` (`.github/workflows/daily.yml`)

1. Checks `scripts.json` first, then the first unfinished line in `topics.txt`.
2. Generates the researched Bengali script.
3. Creates images and narration using the configured fallback order.
4. Renders `final.mp4`, `meta.json`, and `script.json`.
5. Sends the finished video to Telegram.
6. Records the completed topic in `state/done.json`.
7. Commits state and uploads the rendered files as an artifact.

The default cron is 04:00 UTC, which is 10:00 Bangladesh time. It can also be
started manually with an optional topic and style.

### `Telegram bot` (`.github/workflows/telegram-bot.yml`)

This is the legacy polling fallback. With the optional Cloudflare webhook
described below enabled, polling is disabled by default. The scheduled job
checks every five minutes but only calls Telegram when the repository variable
`TELEGRAM_POLLING_ENABLED` is set to `true`.

To activate polling as a fallback:

1. Disable the Telegram webhook:
   `curl -X POST "https://api.telegram.org/bot<TOKEN>/deleteWebhook"`.
2. In **Settings -> Secrets and variables -> Actions -> Variables**, create or
   update `TELEGRAM_POLLING_ENABLED` to `true`.
3. To return to webhook mode, set the variable to `false` or delete it first,
   then register the webhook again.

All three workflows (`Daily video`, polling `Telegram bot`, and
`Telegram webhook upload`) share the `autovid-state` concurrency group, so they
do not commit the same state files concurrently.

### Instant local bot (optional)

If you want an immediate response instead of waiting for the five-minute cron,
run the same bot on your own machine:

```bash
python -m autovid bot-poll --watch
```

Keep the terminal open. It uses Telegram long polling and the same
`state/telegram.json` file.

Do not use local or GitHub polling while the Cloudflare webhook is active.
Telegram does not allow webhook delivery and `getUpdates` polling to receive
updates for the same bot at the same time.

### Instant Telegram webhook (Cloudflare Worker)

The repository includes [`cloudflare-worker/`](./cloudflare-worker), deployed as
`autovid-telegram` at
`https://autovid-telegram.smsmorsalin.workers.dev`. It avoids GitHub's five-minute
polling delay.

The Worker handles `/start`, `/help`, `/add`, and `/remaining` directly. For
`upload`, it acknowledges Telegram immediately, dispatches the
`Telegram webhook upload` GitHub Actions workflow in the background, then
confirms that processing started. That workflow downloads the video, publishes
to enabled platforms, and commits updated state. If dispatch fails, the Worker
sends an error reply instead of allowing Telegram to repeat the update.

Setup or redeploy:

1. In Cloudflare **Workers & Pages**, create a Worker named
   `autovid-telegram` if it does not already exist.
2. From the repository's `cloudflare-worker/` directory, log in and deploy:

   ```bash
   npx wrangler login
   npx wrangler deploy
   ```

3. In the Worker's **Settings -> Variables and Secrets**, add these as **Production
   Secrets** (not plain variables):

   ```text
   TELEGRAM_BOT_TOKEN
   TELEGRAM_CHAT_ID
   TELEGRAM_WEBHOOK_SECRET
   GITHUB_TOKEN
   ```

   `GITHUB_TOKEN` is a GitHub fine-grained token limited to `smsmorsalin/autovid`
   with **Contents: read and write**. Store that token as a Cloudflare Worker
   secret; GitHub does not allow a GitHub Actions secret name starting with
   `GITHUB_`. The non-secret `GITHUB_REPO` and `GITHUB_BRANCH`
   values are already in `cloudflare-worker/wrangler.toml`.
4. Register the webhook using the Worker URL and the same webhook secret. Run
   each line separately. When prompted, paste the value; it will not appear on
   screen:

   ```bash
   read -rsp "Telegram bot token: " BOT_TOKEN; echo
   ```

   Enter the token, then run:

   ```bash
   read -rsp "Webhook secret: " WEBHOOK_SECRET; echo
   ```

   Enter the same secret saved in Cloudflare, then run:

   ```bash
   curl -sS -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
     --data-urlencode "url=https://autovid-telegram.smsmorsalin.workers.dev" \
     --data-urlencode "secret_token=${WEBHOOK_SECRET}"
   ```

   Clear the temporary shell values:

   ```bash
   unset BOT_TOKEN WEBHOOK_SECRET
   ```

5. Confirm the `Telegram webhook upload` workflow exists on GitHub and set
   `TELEGRAM_POLLING_ENABLED` to `false` or remove that Actions variable. Test
   `/start` and `/remaining` in Telegram.

Keep the old polling workflow disabled while the webhook is active. Telegram
does not allow a webhook and `getUpdates` polling consumer to run together.

## Telegram workflow

### Review and upload

1. The scheduled workflow sends the review video to Telegram.
2. **Reply to that exact video message** with:

   ```text
   upload
   ```

   If exactly one video is waiting, typing plain `upload` without a reply also
   works. With more than one waiting video, reply to the exact one.

3. The bot downloads the original video from Telegram.
4. It publishes to every platform enabled in `config.yaml`:
   * YouTube Shorts (always on)
   * Facebook Reels (only when `publish.facebook: true`; currently **false**)
5. It reports each result separately:

   ```text
   আপলোড রিপোর্ট:
   ✅ YouTube: https://youtube.com/shorts/...
   ```

   With Facebook enabled the report also includes a `✅ Facebook: ...` line.

6. If one platform fails, the other still completes and the failed platform is
   reported. Reply `upload` again to retry only the failed platform.

You can also reply to the review message with a replacement video file; that
replacement is published with the original review metadata.

Only messages from `TELEGRAM_CHAT_ID` are accepted.

### Commands

```text
/add আমার নতুন বাংলা ভিডিওর বিষয়
/add timeline3d | বাংলাদেশের সেতুর ইতিহাস
/remaining
/help
```

`/add` appends to the end of `scripts.json`. The bot also accepts these commands
without the leading slash, accepts `/command@botname` syntax, and treats the
common `/remaning` spelling as an alias for `/remaining`. The remaining count is
also sent with every generated video.

### Duplicate upload protection

After a successful upload, the bot stores the Telegram file identifier, the
SHA-256 hash, and per-platform results in `state/telegram.json`. Sending `upload`
again returns the existing links. A platform that failed is not marked done, so
a retry uploads only that platform.

## Topic and queue files

`topics.txt` supports:

```text
explainer | API কী: রেস্টুরেন্টের ওয়েটারের উদাহরণ
timeline3d | মোবাইল ফোনের ইতিহাস
```

Telegram additions are stored in `scripts.json`. Completed topics are recorded in
`state/done.json`. Telegram offsets, pending review messages, and duplicate-upload
records are stored in `state/telegram.json`.

Do not delete or edit these state files while a workflow is running.

## Configuration

Important settings in `config.yaml`:

```yaml
tts:
  provider: [elevenlabs, gemini, edge]
  elevenlabs_voice_id: "j5PhP1QJv2DvlhRv3mEv"
  elevenlabs_model: eleven_v4
  elevenlabs_fallback_voice_id: "cgSgspJ2msm6clMCkdW9"

voice:
  fallback_to_gemini: true
  gemini_voice_model: gemini-3.8-flash-tts
  gemini_voice_name: Kore

publish:
  require_approval: true
  youtube: true
  facebook: false        # set true after FB_PAGE_ID / FB_PAGE_TOKEN are configured
  instagram: false
  privacy: public
```

The current setting is `privacy: public`: replying `upload` approves the video
and makes it public on YouTube. `require_approval: true` delays publishing until
you reply; it does not make an approved upload private. For private review
uploads, change this to `privacy: private` before running the workflow.

### ElevenLabs voice

1. Open **ElevenLabs -> Voices -> My Voices**.
2. Select a Bangla-capable voice.
3. Open the three-dot menu and choose **Copy voice ID**.
4. Paste only the ID into `tts.elevenlabs_voice_id`.
5. Store the API key as the GitHub secret `ELEVENLABS_API_KEY`.

Library/community voices can require a paid plan. Check the key, plan, quota, and
voice ID with:

```bash
export ELEVENLABS_API_KEY="..."
python -m autovid voice-check

# Also synthesize a short Bangla sample (uses a few credits):
python -m autovid voice-check --test
```

Two ElevenLabs details matter for Bangla:

* `eleven_multilingual_v2` does **not** support Bengali. The project uses
  `eleven_v4`, which supports `bn`.
* `language_code` is not accepted by multilingual_v2. The code sends it only to
  Turbo/Flash/v3/v4 models.

Also, **Voice Library voices are not available through the API on the free
tier**. If the configured voice is not in **My Voices**, the pipeline logs the
rejection, tries `elevenlabs_fallback_voice_id` (a premade voice), and then falls
back to Gemini. `voice-check` shows exactly which voice IDs your key can use.

## YouTube requirements

YouTube upload needs a one-time OAuth setup and these three secrets:

```text
YT_CLIENT_ID
YT_CLIENT_SECRET
YT_REFRESH_TOKEN
```

The refresh token belongs to the Google account that owns the channel. Never
commit it. A successful upload returns:

```text
https://youtube.com/shorts/<video-id>
```

Full OAuth walkthrough:
[API_CONNECTION_PROCESS_AND_TELEGRAM_BOT_SETUP.md](./API_CONNECTION_PROCESS_AND_TELEGRAM_BOT_SETUP.md).

## Facebook requirements

Facebook upload needs a Facebook **Page** (not a personal profile) and two
secrets:

```text
FB_PAGE_ID
FB_PAGE_TOKEN
```

`FB_PAGE_TOKEN` must be a **Page access token** with `pages_show_list`,
`pages_read_engagement`, and `pages_manage_posts`. The Meta Graph API is free;
Google AI Studio is not involved.

Test the configured values:

```bash
export FB_PAGE_ID="..."
export FB_PAGE_TOKEN="..."
python -m autovid facebook-check
```

The complete app creation, permission, and long-lived-token walkthrough is in
[API_CONNECTION_PROCESS_AND_TELEGRAM_BOT_SETUP.md](./API_CONNECTION_PROCESS_AND_TELEGRAM_BOT_SETUP.md).

### Facebook is currently disabled

`publish.facebook: false` is set in `config.yaml`, so reply `upload` publishes to
YouTube only and the bot stays green without Facebook credentials.

To enable Facebook later:

1. Add the GitHub secrets `FB_PAGE_ID` and `FB_PAGE_TOKEN` (Page access token).
2. Test them locally:

   ```bash
   export FB_PAGE_ID="..."
   export FB_PAGE_TOKEN="..."
   python -m autovid facebook-check
   ```

3. Set `publish.facebook: true` in `config.yaml`.
4. Commit and push:

   ```bash
   git add config.yaml
   git commit -m "Enable Facebook Reels upload"
   git push
   ```

5. Reply `upload` to the next review video and confirm the `✅ Facebook` line
   appears in the report.

Setting `facebook: false` again turns it back off. Videos already published to
YouTube or Facebook are never re-uploaded.

Facebook Page tokens can expire. If uploads start failing with an OAuth error,
generate a fresh long-lived Page token and update the secret.

## Troubleshooting

### Telegram command or `upload` does nothing

Check, in order:

1. **Which Telegram mode is active?** With the Cloudflare webhook, confirm its
   URL is `https://autovid-telegram.smsmorsalin.workers.dev`, its four Worker
   secrets are set, and Telegram's `getWebhookInfo` reports that URL. Do not run
   the polling workflow at the same time.
2. **For polling mode only:** enable `TELEGRAM_POLLING_ENABLED` and run the
   `Telegram bot` workflow. Disable the webhook before polling.
3. **Does the reply point at the video?** Replying `upload` to the "remaining
   scripts" text message will not work.
4. **Is `TELEGRAM_CHAT_ID` correct?** Messages from other chats are ignored.
5. **Did the daily workflow save and commit `state/telegram.json`?** The pending
   entry supplies full metadata, supports plain `upload` when one video is
   waiting, and records duplicate-upload results. Replying directly to the
   review video can recover basic metadata if that entry is missing. The old
   workflow used `git add state/telegram.json scripts.json`; when `scripts.json`
   did not exist yet, that whole `git add` failed. The workflows now add `state`
   and `scripts.json` separately.
6. **Are workflow permissions Read and write?** Without this the state commit
   fails, and the bot cannot match the reply.
7. **Is the `upload` command in the chat you configured?** Only one chat is
   accepted.

For Cloudflare mode, inspect the Worker's **Observability -> Logs** and the
`Telegram webhook upload` workflow run. For polling mode, run
`python -m autovid bot-poll` (or `--watch`) and read its log.

### YouTube upload is public

`config.yaml` currently sets `publish.privacy: public`. Replying `upload` is
approval to publish publicly. Set it to `private` before the next run if you
want test uploads hidden.

### ElevenLabs voice is not used

Run `python -m autovid voice-check`. Common causes:

* `ELEVENLABS_API_KEY` is missing.
* The configured voice ID is not in **My Voices** (library voices can need a paid plan).
* The monthly character quota is exhausted.
* The voice ID was copied with extra spaces or quotes.

The pipeline logs `TTS: elevenlabs failed -> ...` and then falls back to Gemini.

### Facebook upload fails

1. `FB_PAGE_TOKEN` must be a Page token, not a user token.
2. The app must have `pages_manage_posts`.
3. The Facebook user generating the token must be an admin of the Page.
4. The video must be a vertical MP4 (the renderer already produces 1080x1920).
5. Run `python -m autovid facebook-check`.

### Instagram is skipped

Instagram Reels require a public HTTPS URL for the video. A Telegram upload only
exists inside Telegram, so the bot reports `Instagram: skipped` unless you add a
public hosting step. Set `publish.instagram: false` unless you implement one.

## Safety and quality

Review every generated video before publishing. AI research can misstate facts,
and generated images or narration may need correction. The current YouTube
privacy setting is `public`; set `privacy: private` in `config.yaml` if you want
approved test uploads to remain private.
