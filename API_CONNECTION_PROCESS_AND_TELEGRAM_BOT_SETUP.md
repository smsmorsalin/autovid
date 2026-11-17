# API connection and Telegram bot setup

This guide uses GitHub Actions secrets. Never put API keys, OAuth client
secrets, refresh tokens, or bot tokens in `config.yaml`, `topics.txt`, or git.

## 1. Gemini (research, scripts, and TTS fallback)

1. Open Google AI Studio and create an API key.
2. Check that the selected Gemini Flash and native TTS models are available to
   the key's free quota.
3. Add the key as the repository secret `GEMINI_API_KEY`.
4. Keep `llm.provider: gemini` and `voice.fallback_to_gemini: true`.

The native TTS model and free quota can change. If Google renames or removes
`gemini-3.8-flash-tts`, update `voice.gemini_voice_model` in
`config.yaml` to the currently available Gemini TTS model.

## 2. ElevenLabs (optional first voice)

1. Create an ElevenLabs account and copy an API key.
2. Choose a Bangla-capable voice. Copy its voice ID.
3. Add the API key as `ELEVENLABS_API_KEY`.
4. Put the voice ID in `tts.elevenlabs_voice_id`.

The code tries ElevenLabs first and automatically tries Gemini, then Edge TTS,
when ElevenLabs returns an error, has no configured voice, or is out of quota.
ElevenLabs' free allowance is limited and can change, so it is optional.

## 3. Images

The default order is Cloudflare Workers AI, then Pollinations.

* Create a Cloudflare account and a Workers AI API token.
* Add `CF_ACCOUNT_ID` and `CF_API_TOKEN` as secrets.
* Pollinations needs no secret but is rate limited; it is a slow backup.

For a no-network layout test, use `config.offline.yaml`.

## 4. Telegram bot

1. In Telegram, open `@BotFather`.
2. Send `/newbot`, choose a name and a username, and copy the bot token.
3. Add it as `TELEGRAM_BOT_TOKEN`.
4. Send `/start` to the new bot.
5. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and find
   `result[].message.chat.id`.
6. Add that number as `TELEGRAM_CHAT_ID`.
7. Enable the Telegram Bot workflow in GitHub Actions.

The daily workflow sends the rendered video. Reply `upload` to that exact
message to upload the generated video automatically. You may instead reply
with a replacement video to upload that file. `/add <text>` appends a
topic/script, and `/remaining` shows the count. Only the configured chat ID is
accepted.

## 5. YouTube upload credentials

1. In Google Cloud Console, create a project.
2. Enable **YouTube Data API v3**.
3. Create an OAuth client ID for a Desktop app.
4. Complete the OAuth consent flow for the Google account owning the channel.
5. Use the resulting refresh-token flow to obtain the refresh token.
6. Add these repository secrets:
   * `YT_CLIENT_ID`
   * `YT_CLIENT_SECRET`
   * `YT_REFRESH_TOKEN`
7. Start with `publish.privacy: private`, confirm the URL in Telegram, then
   change it to `unlisted` or `public`.

YouTube quota and upload limits are controlled by Google. A successful upload
returns `https://youtube.com/shorts/<video-id>`. A failed upload is reported in
Telegram and remains retryable.

## 6. GitHub repository settings

1. Go to **Settings -> Secrets and variables -> Actions** and add the secrets
   above.
2. Go to **Settings -> Actions -> General**.
3. Set workflow permissions to **Read and write**.
4. Check that both `Daily video` and `Telegram bot` are enabled.
5. Run `Telegram bot` once manually, then send `/remaining` to verify the bot.

The workflows commit only queue/progress state. Secrets are injected into
processes by Actions and are not written to repository files.
