# Security

## Do not file public issues with secrets

Never open a GitHub/GitLab issue, PR, or discussion that includes:

- `ntfy.env` contents or your ntfy topic name
- `rtsp.env` contents, camera passwords, or full RTSP URLs
- crib photos, `frames/`, or alert logs that describe a child

If you accidentally published a secret, rotate it immediately (new ntfy topic, new camera password) and treat the old value as compromised.

## Private disclosure

If you found a vulnerability in this project (for example a way that photos could leave the machine, or unsafe defaults), email or message the maintainer privately instead of posting exploit details in a public tracker.

## Safe defaults for operators

- Keep `llama-server` on `127.0.0.1` only
- Do not expose RTSP, ffmpeg, or the watch loop to the WAN
- Prefer a long random ntfy topic; `ntfy.sh` topics are world-readable if guessed
- Never commit `ntfy.env`, `rtsp.env`, or `frames/`
