# shorts-crossposter

Cross-posts **original** Reels from the Google Drive folder `Reels Autoposter - Published`
to YouTube Shorts (Instagram later). It never touches the `reels-autoposter` repo.

## How it works
1. Runs hourly on GitHub Actions; acts only when a channel's evening slot is due
   (slots and timezones in `config/channels.json`).
2. Lists new `.json` + `.mp4` pairs in the Drive folder for enabled channels.
3. **Originality gate:** skips anything not original (`source` other than `original`,
   unknown `kind`, pack/bundle hints) and anything too similar to a recent upload.
4. Ranks the rest by the `score` in the Sheet's **Performance** tab (newest first if no score).
5. Builds honest metadata: title (Gemini writes 3 options, no clickbait; falls back to the
   Reel title), description (caption + sources + AI note + `#Shorts` + hashtags), tags,
   category, language, made-for-kids = no, altered/synthetic disclosure = yes (AI voice).
6. Uploads (or, in kit mode, writes a phone kit) and logs every item to the **YouTube** tab.
   An item is never picked twice; a crash mid-upload leaves `UPLOADING` and is not retried.

## Modes (repo variable `YT_MODE`, default `kit`)
| mode | what happens |
|---|---|
| `kit` | No upload. Title/description/tags + Drive video link go to the YouTube tab for manual upload. |
| `private` | Uploads as private (preview; unverified API projects are always private). |
| `unlisted` | Uploads as unlisted. |
| `public` | Public, only if repo variable `YT_PUBLIC_APPROVED` = `yes`; otherwise private. |

## Secrets (added by the owner only)
`YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` (personal account: Drive read + Sheets),
`YT_REFRESH_TOKEN_HISTORY` (and `_THROTTLE`, `_PAWSIDE` later), optional `GEMINI_API_KEY`.

## Manual run
Actions > YouTube Shorts > Run workflow (choose mode, channel, limit, dry run).

## Tests
`pip install -r requirements.txt && python -m pytest -q`
