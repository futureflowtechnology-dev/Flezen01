# Telegram Media Scheduler Bot

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env: set BOT_TOKEN (from @BotFather) and ADMIN_ID (your own Telegram user id, get it via /id)
python bot.py
```

State (queues, destinations, settings, stats) persists to `data.json` next
to the script, so restarting the bot doesn't wipe your queue — **except on
Railway and similar platforms, see below.**

## Deploying on Railway

1. Push this folder to a GitHub repo. **Do not commit `.env`** — it's in
   `.gitignore`. Set `BOT_TOKEN` and `ADMIN_ID` as environment variables in
   the Railway dashboard instead (Variables tab), plus any other settings
   from `.env.example` you want to override.
2. Railway will detect Python via `requirements.txt` and use the `Procfile`
   (`worker: python bot.py`) to start it as a background worker — it does
   **not** need a public port, since `run_polling()` isn't an HTTP server.
   If Railway shows a "no open port detected" warning, ignore it or set the
   service type to "Worker" explicitly in settings.
3. **Attach a Volume.** Railway's container filesystem resets on every
   redeploy. Without a volume, `data.json` (all your queues/settings/stats)
   and `media_cache/` (extracted zip files) disappear each time you push.
   In Railway: your service → Settings → Volumes → add one, mount it at
   e.g. `/data`, then set `DATA_FILE=/data/data.json` and
   `MEDIA_DIR=/data/media_cache` as environment variables.
4. Deploy. Check the Railway logs for `🤖 Bot running...` — if the token or
   admin id is wrong you'll see the error there immediately.

**Optional — `ffmpeg` for zip video duration.** Videos sent directly to the
bot already come with a duration from Telegram, so sorting/filtering/trimming
by length works out of the box for those. Videos bulk-added via `.zip` need
`ffprobe` (part of `ffmpeg`) to detect duration — the included
`nixpacks.toml` tells Railway's Nixpacks builder to install it. If it's
missing, the bot doesn't error; those zip-sourced videos just show up with
an unknown duration and get skipped by length filters / sorted last.

## How it fits together

- `config.py` — loads `.env`
- `storage.py` — JSON persistence, one record per chat
- `utils.py` — duplicate detection, zip extraction (+ ffprobe duration),
  queue sort/filter/trim helpers, delay math, admin DM helper
- `scheduler.py` — APScheduler engine: each tick posts one item to every
  broadcast destination plus one independent item to every dedicated-queue
  destination, reschedules with a fixed or random delay, stops + notifies
  when every queue involved is empty
- `bot.py` — all Telegram command handlers + entrypoint
- `nixpacks.toml` — optional, installs `ffmpeg` on Railway for zip-video
  duration detection

## Commands

**Queues**
- Send a photo/video → added to the active queue (duplicates auto-skipped)
- Send a `.zip` of photos/videos → bulk-extracted into the active queue
- `/queue` — active queue size + destinations
- `/clear` — empty active queue
- `/newqueue <name>` — create and switch to a new named queue
- `/queues` — list all queues, tap to switch
- `/sortqueue size|duration [asc|desc]` — sort the active queue. Items
  missing that field (e.g. photos have no duration) always sort last.
- `/filterqueue <min_sec> <max_sec>` — non-destructive; lists videos in the
  active queue whose duration falls in that range.
- `/trimqueue <min_sec> <max_sec>` — destructive; permanently removes
  videos **outside** that range from the active queue (asks for
  confirmation first via a button; photos and videos with unknown
  duration are always kept).

**Destinations** (channels/groups you post to)
- `/adddest <name> <chat_id>` — register a destination
- `/destinations` — list, tap to enable/disable
- `/checkdest <name>` — health check (confirms the bot can still reach it)
- `/setdestqueue <name> <queue|none>` — give a destination its own
  dedicated queue instead of sharing the broadcast queue (see below)
- `/toggledestshuffle <name>` — random vs. in-order posting for that
  destination's own queue

If no destination is added, the bot posts back into the current chat.

**Broadcast vs. dedicated destinations**
By default every destination is in **broadcast mode**: they all share the
active queue, and each scheduler tick pops ONE item and sends the same
copy to every enabled broadcast destination — same as before.

Assign a destination its own queue with `/setdestqueue <name> <queue>` and
it switches to **dedicated mode**: it pops independently from that queue
every tick (its own FIFO or shuffled order via `/toggledestshuffle`), so
different destinations can post different content, in a different order,
at the same time, on the same schedule. `/sendnow` and the scheduler both
account for whatever's left in every dedicated queue, not just the active
one.

**Shuffle**
- `/toggleshuffle` — flips FIFO vs. random pop order for the shared
  broadcast queue.
- `/toggledestshuffle <name>` — same, but scoped to one destination's own
  dedicated queue (only meaningful once it has one via `/setdestqueue`).

**Sending**
- `/sendnow` — dump the whole active queue immediately, ignoring the interval
- `/startscheduler` — begin auto-posting at the configured interval
- `/stopscheduler` — stop auto-posting
- `/next` — countdown to the next scheduled post

**Dashboards**
- `/dashboard` — queue sizes, scheduler status, countdown, totals
- `/stats` — sent/failed counts and last health check per destination

**Captions**
- Every photo/video you send keeps its own caption automatically.
- `/togglecaption` — flip between: ON (default) = forward each item with the
  caption it originally had; OFF = always use the default caption instead.
- `/setcaption <text|clear>` — sets the fallback caption, used when
  original-caption mode is OFF, or when an item had no caption to begin with
  (e.g. media extracted from a zip never has one).
- `/togglelaststrip` — when ON, finds the **last space** in the outgoing
  caption, deletes everything after it, and replaces it with a fixed piece
  of text you set. Handy when people send items with their own promo tag
  or handle stuck on the end and you want to swap it for yours.
  - `/setlastwordreplacement <text|clear>` — the replacement text (`clear`
    just deletes the trailing token, replacing it with nothing).
- `/toggletglinkreplace` — when ON, finds **any** `t.me/...` link or
  `@mention` **anywhere** in the caption (not just at the end) and replaces
  it with a fixed piece of text you set.
  - `/settglinkreplacement <text|clear>` — the replacement text.
- `/previewcaption` — shows the before/after for the next item in the
  active queue, without sending anything, so you can check the rules do
  what you expect before turning the scheduler on.

**Settings**
- `/setinterval fixed <seconds>`
- `/setinterval random <min> <max>`
- `/setcaption <text|clear>` — caption applied to every post
- `/setmaxqueue <n>`
- `/setfiletypes photo,video`
- `/settimezone Asia/Kolkata` — any IANA timezone name
- `/setdatetimeformat <strftime format>`
- `/toggleshuffle` — random vs. in-order posting from the shared broadcast queue
- `/settings` — show all current settings

**Notifications** (sent automatically)
- Queue completed / scheduler stopped
- Upload finished (`/sendnow`) and upload failed (per item)
- ZIP extraction completed
- Scheduler started / stopped
- Next-post progress after each auto-post
- Admin DMs (`ADMIN_ID`) on zip uploads, scheduler start/stop, and failures

## Known limitations / things to verify yourself

- **Not live-tested against Telegram's servers.** This was built and syntax/import
  checked in a sandbox with no network access to `api.telegram.org`, so please
  run it against your real bot token and watch the console for errors on first launch.
- Duplicate detection is per-chat, based on Telegram's `file_unique_id` for
  bot-sent media, and on extracted filenames for zips — it won't catch a
  pixel-identical image re-uploaded under a different file.
- Broadcasting to *every* enabled destination on each post is a deliberate
  choice for "multiple channels/groups" — if you instead want different
  queues going to different single destinations, that's a small change to
  `scheduler._send_one_item`, happy to add it.
- `ADMIN_ID=0` (default) silently disables admin notifications — the admin
  must have started a DM with the bot at least once for `send_message` to work.
- If you point a destination's dedicated queue (`/setdestqueue`) at the same
  name as the current active/broadcast queue, that queue gets drained by
  both the broadcast pop and that destination's own pop each tick — items
  disappear roughly twice as fast. Use a separate queue name for dedicated
  destinations to avoid this.
- Assigning two destinations to the *same* dedicated queue is fine and
  intentional if you want them to split one pool of content between them —
  each destination pops its own item per tick, so they won't receive
  duplicates of each other, but the queue drains faster (once per assigned
  destination per tick).
- `/trimqueue` only ever removes videos (by duration); photos and any item
  with an undetected duration are always kept, since there's nothing to
  measure them against.
- If both `/togglelaststrip` and `/toggletglinkreplace` are ON at once,
  the link/mention replacement runs first, then the last-token
  strip/replace runs on whatever's left — so a link right at the end of a
  caption could get touched by both rules in sequence. Use
  `/previewcaption` to check the actual result before relying on it.
