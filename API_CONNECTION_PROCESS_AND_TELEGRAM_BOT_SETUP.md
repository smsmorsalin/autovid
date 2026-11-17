# API connection and Telegram bot setup

This is the end-to-end setup guide: collect every key, store it as a GitHub
Actions secret, connect Telegram, and publish a reviewed video to YouTube
(optionally also to Facebook Reels when you enable it).

Never put API keys, OAuth secrets, refresh tokens, Page tokens, or the bot token
in `config.yaml`, `topics.txt`, source files, or git. They belong in GitHub
Actions secrets (or your local shell for testing).

## 0. What connects to what

```text
Google AI Studio key  -> Gemini script research + Gemini TTS fallback
ElevenLabs key        -> natural Bangla voice (optional)
Cloudflare token      -> images (Pollinations is the no-key fallback)
Telegram bot token    -> review video + commands
YouTube OAuth secrets -> YouTube Shorts upload
Meta Page token       -> Facebook Reels upload

Google AI Studio does NOT publish to Facebook.
Facebook publishing uses the Meta Graph API, which is free and separate.
```

Once configured, the complete loop is:

```text
Daily video workflow renders and sends the video to Telegram
        -> you reply `upload` to that video
        -> Telegram bot workflow publishes to YouTube
           (and Facebook Reels too when publish.facebook: true)
        -> you receive a per-platform success/failure report
```

---

## 1. Gemini / Google AI Studio (script + TTS fallback)

1. Open <https://aistudio.google.com/apikey>.
2. Sign in with the Google account you want to use.
3. Click **Create API key** and choose a Google Cloud project (a new free
   project is fine).
4. Copy the key.
5. In GitHub: **Settings -> Secrets and variables -> Actions -> New repository
   secret**.
6. Name it `GEMINI_API_KEY` and paste the key.

Keep these `config.yaml` values:

```yaml
llm:
  provider: gemini
  model: gemini-3.8-flash

voice:
  fallback_to_gemini: true
  gemini_voice_model: gemini-3.8-flash-tts
  gemini_voice_name: Kore
```

Model names change. If Gemini returns 404, the script generator automatically
tries the newest available Flash models. For TTS, update
`voice.gemini_voice_model` to a model that exists for your key. The free quota is
shown at <https://aistudio.google.com/rate-limit>.

Google AI Studio is used only for Gemini. It is not a Facebook tool.

---

## 2. ElevenLabs (natural Bangla voice, optional)

1. Create a free account at <https://elevenlabs.io>.
2. Open **Profile -> API Keys** and create a key.
3. Store it as the GitHub secret `ELEVENLABS_API_KEY`.
4. Open **Voices -> My Voices**.
5. Choose a Bangla-capable voice.
6. Open the three-dot menu and click **Copy voice ID**.
7. Paste the ID into `config.yaml`:

   ```yaml
   tts:
     provider: [elevenlabs, gemini, edge]
     elevenlabs_voice_id: "YOUR_VOICE_ID"
     elevenlabs_model: eleven_v4
     elevenlabs_fallback_voice_id: "cgSgspJ2msm6clMCkdW9"
   ```

   `eleven_v4` is required because `eleven_multilingual_v2` does not support
   Bengali. The fallback is a premade voice for free keys.

8. Test locally before relying on it:

   ```bash
   export ELEVENLABS_API_KEY="..."
   python -m autovid voice-check
   python -m autovid voice-check --test   # also generates a short Bangla sample
   ```

`voice-check` prints the plan, character usage, model availability, whether the
configured voice is available to the key, and a final `READY` / `NOT READY`.
`--test` performs a real synthesis and uses a few credits.

Important free-tier notes:

* `eleven_multilingual_v2` does **not** support Bengali. Use `eleven_v4` (or
  `eleven_v3`). This was the main reason the ElevenLabs voice was not working.
* A voice from the public Voice Library is **not usable through the API on the
  free tier**, even if the same voice works in the web app. If `voice-check`
  says `NOT AVAILABLE`, the code tries `elevenlabs_fallback_voice_id` and then
  Gemini/Edge.
* Free accounts have a small monthly character allowance. When it runs out the
  pipeline logs `TTS: elevenlabs failed` and falls back.
* The code sends `language_code` only to Turbo/Flash/v3/v4 models.
  `eleven_multilingual_v2` rejects that parameter.
* ElevenLabs' premade/default voices are being retired on 31 December 2026.
  After that, a free-tier account may have no API-usable voice, in which case
  Gemini TTS or Edge TTS remains the working free path.
* The API key must be a secret, never `config.yaml`.

The fallback order is controlled by `tts.provider`. To force ElevenLabs trials
first, keep `[elevenlabs, gemini, edge]`.

---

## 3. Images (Cloudflare, then Pollinations)

The defaults are Cloudflare Workers AI, then Pollinations.

### Cloudflare Workers AI

1. Create a free Cloudflare account.
2. Open **My Profile -> API Tokens -> Create Token**.
3. Use a template or custom token with **Workers AI: Read**.
4. Copy the token.
5. Find the Account ID on the Workers dashboard or in the account URL.
6. Add GitHub secrets:
   * `CF_ACCOUNT_ID`
   * `CF_API_TOKEN`

### Pollinations

Pollinations needs no key. It is rate-limited and slower, so it is the automatic
backup. No secret is required.

### Offline test

`config.offline.yaml` uses placeholder images and silent narration, with no
network keys:

```bash
python -m autovid --config config.offline.yaml make \
  --script samples/explainer_database_index.json \
  --style explainer \
  --out out/demo
```

---

## 4. Telegram bot (review, `upload`, `/add`, `/remaining`)

### 4.1 Create the bot

1. In Telegram, open `@BotFather`.
2. Send `/newbot`.
3. Choose a display name and a username ending in `bot`.
4. Copy the token that BotFather returns.
5. Add it as the GitHub secret `TELEGRAM_BOT_TOKEN`.

### 4.2 Get your chat ID

1. Open your new bot and send `/start`.
2. Visit this URL in a browser, replacing `<TOKEN>`:

   ```text
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```

3. Find `result[].message.chat.id`. It is usually a positive number for a
   private chat.
4. Add it as the GitHub secret `TELEGRAM_CHAT_ID`.

Only this chat ID is allowed to use the bot.

### 4.3 How the bot receives messages

Telegram bots do not push commands to your code. Something must read
`getUpdates`. Autovid has two supported ways:

| Mode | Command | When to use |
|---|---|---|
| GitHub Actions cron | `.github/workflows/telegram-bot.yml` every 5 minutes | Free, no server needed |
| Local long polling | `python -m autovid bot-poll --watch` | Instant replies while your machine runs |

The GitHub workflow is already included in this repository. Enable it:

1. Open **Actions -> Telegram bot**.
2. Click **Run workflow** once to verify it starts.
3. Send `/help` to the bot and wait up to five minutes.
4. Check the workflow log for the processed update.

GitHub's minimum cron interval is five minutes, and scheduled runs can be a few
minutes late under load. That is normal. Use `bot-poll --watch` if you need
instant responses.

### 4.4 Critical state requirement

The render job writes `state/telegram.json` with a `pending` entry for the video
message. The bot job reads that file to match an `upload` reply to the correct
video metadata.

Both workflows commit the state file back to the repo. For this to work:

1. Open **Settings -> Actions -> General**.
2. Under **Workflow permissions**, select **Read and write permissions**.
3. Save.

If Read/write is disabled, the bot cannot see the pending video and `upload`
appears to do nothing. The same happens with the old workflow command
`git add state/telegram.json scripts.json`: if `scripts.json` did not exist, the
entire `git add` failed and the pending state was never committed. The included
workflows add `state` and `scripts.json` separately to avoid this.

### 4.5 Review and upload

1. The `Daily video` workflow sends the rendered video to your chat.
2. **Reply to that exact video message** with:

   ```text
   upload
   ```

   If only one video is waiting, typing plain `upload` also works. If several
   videos are waiting, you must reply to the exact one.

3. The bot downloads the Telegram video and publishes it to every platform
   enabled in `config.yaml`.
4. With the current configuration that means YouTube Shorts only, because
   `publish.facebook` is `false`. Set it to `true` (section 7.7) to also publish
   to Facebook Reels.
5. The bot replies with a per-platform report:

   ```text
   আপলোড রিপোর্ট:
   ✅ YouTube: https://youtube.com/shorts/...
   ```

   A `✅ Facebook: ...` line appears only when Facebook is enabled.

6. If one platform fails, the other still publishes. Reply `upload` again to
   retry only the failed platform.
7. Replying with a replacement video publishes that replacement instead.

`upload` must be a reply to the video message. Replying to the "remaining
scripts" text message cannot identify which video to publish.

### 4.6 Commands

```text
/add আমার নতুন বাংলা ভিডিওর বিষয়
/add timeline3d | বাংলাদেশের সেতুর ইতিহাস
/remaining
/help
```

`/add` appends to the end of `scripts.json`. The bot also sends the remaining
script count after each review video.

### 4.7 Duplicate protection

`state/telegram.json` stores, per video:

* the Telegram `file_unique_id`,
* the SHA-256 hash of the downloaded file,
* the result for each platform.

A second `upload` for the same video returns the stored links instead of
uploading again. Per-platform results mean a failed Facebook upload can be
retried without re-uploading YouTube.

---

## 5. GitHub secrets summary

Add all values at **Settings -> Secrets and variables -> Actions**:

```text
GEMINI_API_KEY
CF_ACCOUNT_ID
CF_API_TOKEN
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
YT_CLIENT_ID
YT_CLIENT_SECRET
YT_REFRESH_TOKEN
FB_PAGE_ID
FB_PAGE_TOKEN
```

Optional:

```text
ELEVENLABS_API_KEY
FAL_KEY
IG_USER_ID
```

Then set **Settings -> Actions -> General -> Workflow permissions** to
**Read and write**.

---

## 6. YouTube upload credentials (free OAuth)

### 6.1 Create the Google Cloud project

1. Open <https://console.cloud.google.com>.
2. Create a project (for example `autovid`).
3. Open **APIs & Services -> Library**.
4. Search for **YouTube Data API v3** and click **Enable**.

### 6.2 Configure the OAuth consent screen

1. Open **APIs & Services -> OAuth consent screen**.
2. Choose **External**.
3. Fill in the app name and your email.
4. Add your Google account as a **Test user**.
5. Add the scope `https://www.googleapis.com/auth/youtube.upload`.
6. Save.

### 6.3 Create the OAuth client

1. Open **APIs & Services -> Credentials**.
2. Click **Create credentials -> OAuth client ID**.
3. Application type: **Desktop app**.
4. Copy the **Client ID** and **Client secret**.

### 6.4 Get the refresh token

The easiest free method is the OAuth 2.0 Playground:

1. Open <https://developers.google.com/oauthplayground>.
2. Click the gear icon.
3. Check **Use your own OAuth credentials**.
4. Paste your Client ID and Client secret.
5. In Step 1, find **YouTube Data API v3** and select
   `https://www.googleapis.com/auth/youtube.upload`.
6. Click **Authorize APIs** and sign in with the channel owner account.
7. In Step 2, click **Exchange authorization code for tokens**.
8. Copy the **Refresh token**.

The refresh token belongs to the channel owner. Never commit it.

### 6.5 Store the secrets and test

Add:

```text
YT_CLIENT_ID
YT_CLIENT_SECRET
YT_REFRESH_TOKEN
```

Test locally with a rendered video:

```bash
python -m autovid publish --dir out/<your-video-directory>
```

Important:

* Keep `publish.privacy: private` until the full flow is verified.
* The default YouTube quota is 10,000 units/day; an upload costs about 1,600
  units, so roughly six uploads per day on the free quota.
* A successful upload returns `https://youtube.com/shorts/<video-id>`.

---

## 7. Facebook Reels automation (Meta Graph API)

> **Status: disabled.** `publish.facebook` is `false` in `config.yaml`, so uploads
> go to YouTube only. Follow this section when you want to turn Facebook on;
> section 7.7 is the final checklist.

Facebook publishing is free. It has nothing to do with Google AI Studio. Google
AI Studio only generates the script and the Gemini TTS fallback.

You need a Facebook **Page**, not a personal profile. Reels are published to the
Page through the Graph API.

### 7.1 Create or choose a Page

1. In Facebook, create a Page or open an existing Page.
2. Make sure your personal account is an **admin** of the Page.
3. Note the Page name.

### 7.2 Create a Meta developer app

1. Open <https://developers.facebook.com>.
2. Click **My Apps -> Create App**.
3. Choose **Other -> Business** (or the current equivalent).
4. Give the app a name and create it.
5. In the app dashboard, add the **Facebook Login** product if prompted.
6. You do not need to submit the app for review just to publish to your own
   Page as an admin; keep it in Development mode.

### 7.3 Generate a User token with Page permissions

1. Open the **Graph API Explorer**:
   <https://developers.facebook.com/tools/explorer/>.
2. Select your app in the top-right.
3. Set **User or Page** to **User token**.
4. Add these permissions:
   * `pages_show_list`
   * `pages_read_engagement`
   * `pages_manage_posts`
5. Click **Generate Access Token**.
6. Sign in and grant access to the Page when prompted.

### 7.4 Exchange for a long-lived token and get the Page token

Short-lived user tokens expire in about an hour. Exchange it first:

```text
GET https://graph.facebook.com/v21.0/oauth/access_token
  ?grant_type=fb_exchange_token
  &client_id=YOUR_APP_ID
  &client_secret=YOUR_APP_SECRET
  &fb_exchange_token=SHORT_LIVED_USER_TOKEN
```

Copy the returned `access_token` (a long-lived user token, about 60 days).

Now list your Pages with that long-lived user token:

```text
GET https://graph.facebook.com/v21.0/me/accounts?access_token=LONG_LIVED_USER_TOKEN
```

The response contains entries like:

```json
{
  "data": [
    {
      "id": "1234567890",
      "name": "My Page",
      "access_token": "EAAB..."
    }
  ]
}
```

Use:

* `id` as `FB_PAGE_ID`
* `access_token` as `FB_PAGE_TOKEN`

A Page token derived from a long-lived user token normally does not expire on its
own, but it can be invalidated by a password change, removing the app, or
revoking permissions. If that happens, repeat section 7.3–7.4 and update the
secret.

Advanced alternative: create a **System User** in Meta Business Settings and
generate a token with `pages_manage_posts`. System-user tokens are designed for
long-running automation, but the setup is more complex.

### 7.5 Store the Facebook secrets

Add:

```text
FB_PAGE_ID
FB_PAGE_TOKEN
```

### 7.6 Verify before publishing

```bash
export FB_PAGE_ID="1234567890"
export FB_PAGE_TOKEN="EAAB..."
python -m autovid facebook-check
```

This checks the Page ID, the token, and reports the Page name. Publishing also
needs `pages_manage_posts`.

### 7.7 Enable Facebook publishing

Facebook publishing is **disabled right now** (`publish.facebook: false`), so
`upload` posts to YouTube only. Re-enable it with this checklist:

1. **Add the secrets.** GitHub -> **Settings -> Secrets and variables ->
   Actions** -> add both:
   * `FB_PAGE_ID`
   * `FB_PAGE_TOKEN` (long-lived **Page** token)
2. **Verify the credentials** locally:

   ```bash
   export FB_PAGE_ID="1234567890"
   export FB_PAGE_TOKEN="EAAB..."
   python -m autovid facebook-check
   ```

   You should see the Page name. Fix any error from section 7.8 first.
3. **Flip the switch** in `config.yaml`:

   ```yaml
   publish:
     require_approval: true
     youtube: true
     facebook: true        # re-enabled
     instagram: false
   ```

4. **Commit and push** the config change:

   ```bash
   git add config.yaml
   git commit -m "Enable Facebook Reels upload"
   git push
   ```

5. **Test once.** Let the next video arrive in Telegram, reply `upload`, and
   confirm both the `✅ YouTube` and `✅ Facebook` lines in the report.

To disable it again, set `facebook: false` and push. Already-published platforms
are never re-uploaded, so switching back and forth is safe.

### 7.8 Common Facebook errors

| Error | Fix |
|---|---|
| `Invalid OAuth access token` | `FB_PAGE_TOKEN` is expired or is a user token; regenerate a Page token. |
| `(#200) Requires pages_manage_posts` | Add the permission and regenerate the token. |
| `(#10) Application does not have permission` | Grant the Page to the app/user, or check app mode and roles. |
| `Unsupported post request` | Use the Page ID, not the personal profile ID. |
| `Video upload failed` | Check that the file is a valid MP4 and retry. |

### 7.9 Instagram Reels (optional, separate)

Instagram Reels are not published by the Telegram video upload. The Instagram
API requires a **public HTTPS URL** to the MP4; a Telegram-hosted file is not
public. To use Instagram you would need to host `final.mp4` (for example on a
GitHub Release asset or S3) and pass it as `--public-url`.

If you set up Instagram later:

1. Convert the Instagram account to **Business** or **Creator**.
2. Link it to the Facebook Page.
3. Get the Instagram Business account ID:

   ```text
   GET https://graph.facebook.com/v21.0/{FB_PAGE_ID}?fields=instagram_business_account&access_token=FB_PAGE_TOKEN
   ```

4. Add `IG_USER_ID`.
5. Grant `instagram_basic` and `instagram_content_publish` permissions.
6. Set `publish.instagram: true` and provide a public URL.

Otherwise keep `publish.instagram: false`; the bot will report it as skipped.

---

## 8. GitHub Actions setup and state

### 8.1 Enable workflows

1. Open the **Actions** tab.
2. Enable workflows if GitHub asks.
3. Run **Daily video** once manually to test rendering.
4. Run **Telegram bot** once manually to test polling.

### 8.2 Workflow schedule

* `Daily video`: `0 4 * * *` UTC (10:00 Bangladesh time).
* `Telegram bot`: `*/5 * * * *` (every five minutes).

Both share the `autovid-state` concurrency group and commit:

* `state/done.json`
* `state/telegram.json`
* `scripts.json`

### 8.3 Workflow permissions

**Settings -> Actions -> General -> Workflow permissions -> Read and write** is
required. Without it, state commits fail and the bot loses the pending review
mapping.

---

## 9. End-to-end verification checklist

1. `python -m autovid voice-check` prints `READY` (or you intentionally use the
   Gemini/Edge fallback).
2. If Facebook is enabled: `python -m autovid facebook-check` prints `READY`.
3. **Actions -> Telegram bot -> Run workflow** succeeds.
4. Send `/help` to the bot and receive the help text.
5. Send `/add Test topic` and receive the new remaining count.
6. Run **Daily video** manually, or wait for the schedule, and receive the review
   video.
7. Reply `upload` to that video.
8. Wait for the bot workflow (up to five minutes) or run `bot-poll --watch`.
9. Confirm the `✅ YouTube` line in the Telegram report (plus `✅ Facebook` if
   enabled).
10. Confirm the YouTube Shorts URL opens, and the Facebook Reel if enabled.
11. Reply `upload` again and confirm the bot returns the same links instead of
    uploading duplicates.

---

## 10. Troubleshooting quick reference

| Symptom | Likely cause | Action |
|---|---|---|
| Video arrives, `upload` does nothing | `Telegram bot` workflow missing/disabled | Enable it and run once manually. |
| `upload` does nothing, workflow runs | `state/telegram.json` not committed | Set workflow permissions to Read and write. |
| `upload` does nothing, state looks fine | Replied to the wrong message | Reply to the video message itself. |
| Bot ignores you | `TELEGRAM_CHAT_ID` mismatch | Use the chat ID from `getUpdates`. |
| ElevenLabs never used | Missing key, bad voice ID, quota, or unsupported model | Run `voice-check`; read `TTS:` logs. |
| YouTube fails | Missing/expired OAuth refresh token, quota | Re-run OAuth Playground; check quota. |
| Facebook fails | User token instead of Page token, missing permission | Recreate Page token with `pages_manage_posts`; run `facebook-check`. |
| Instagram skipped | No public HTTPS video URL | Host the MP4 and pass `--public-url`. |
| Video is private on YouTube | `publish.privacy: private` | Change to `unlisted` or `public` after testing. |
