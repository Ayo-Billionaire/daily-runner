# daily-runner

Sends approved messages from a queue on a schedule, using a saved, encrypted browser session.

This repo is public, so:
1. Every state file in `state/` is encrypted (Fernet, secret `STATE_KEY`).
2. Addresses, ids and the network exit node are in the secret `PRIVATE_CONFIG`.
3. Action logs print only `PUBLIC:` count lines. The full log and screenshots are an encrypted artifact, `private.enc`. To read it, download it and run `python3 dmsender/pack.py unpack private.enc`.
4. Commit messages never carry names.

Runs are started by a Cloudflare Worker cron (`clock/worker.js`) through `workflow_dispatch`.
