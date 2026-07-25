"""
One AsyncIOScheduler shared across all chats. Each chat that has its
scheduler "running" gets a single one-shot job scheduled at a time;
after it fires we look at how much is left across that chat's queues
and either reschedule (fixed or random delay) or stop and notify.

Two posting modes coexist per chat, per destination:

- BROADCAST (default): a destination with no "queue" assigned shares
  the chat's active queue. Every tick, ONE item is popped from the
  active queue (FIFO, or randomly if settings.shuffle is on) and sent
  identically to every broadcast destination -- this is the original
  "multi-channel" behavior.

- DEDICATED: a destination assigned its own queue (via /setdestqueue)
  pops independently from that queue every tick, in its own FIFO or
  shuffled order (per-destination "shuffle" flag), so different
  destinations can post different items in a different order at the
  same time, on the same schedule.

This gives: fixed interval, random interval range, per-chat and
per-destination shuffle, countdown to next post (next_post_time is
stored so /next can read it), queue completion notification,
scheduler started/stopped notifications, and posting to every enabled
destination (multi-channel/group support, broadcast or dedicated).
"""

from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import storage
import utils

scheduler = AsyncIOScheduler()
_jobs = {}  # chat_id -> job id


def start():
    if not scheduler.running:
        scheduler.start()


def _job_id(chat_id):
    return f"post_{chat_id}"


def _pop_item(chat, queue_name, shuffle):
    """Pop one item from the named queue -- randomly if shuffle, else
    FIFO. Returns None if the queue doesn't exist or is empty."""
    import random
    queue = chat["queues"].get(queue_name)
    if not queue:
        return None
    if shuffle:
        idx = random.randrange(len(queue))
        return queue.pop(idx)
    return queue.pop(0)


def _remaining_total(chat):
    """Everything left to send: the active/broadcast queue plus every
    distinct dedicated queue assigned to an enabled destination."""
    total = utils.queue_len(chat, chat["active_queue"])
    seen = {chat["active_queue"]}
    for d in chat["destinations"].values():
        if not d.get("enabled", True):
            continue
        qname = d.get("queue")
        if qname and qname not in seen:
            total += utils.queue_len(chat, qname)
            seen.add(qname)
    return total


async def _deliver(bot, chat_id, chat, name, dest, item):
    """Send a single item to a single destination, updating stats/notifying
    on failure. Caption resolution honours the chat's original-caption
    setting the same way regardless of broadcast/dedicated mode."""
    settings = chat["settings"]
    original_caption = item.get("caption")
    if settings.get("use_original_caption", True) and original_caption:
        caption = original_caption
    else:
        caption = settings.get("caption") or None
    caption = utils.process_caption(caption, settings)

    dest_chat_id = dest["chat_id"]
    try:
        if item.get("source") == "local":
            path = item["path"]
            if item["type"] == "video":
                with open(path, "rb") as f:
                    await bot.send_video(dest_chat_id, f, caption=caption)
            else:
                with open(path, "rb") as f:
                    await bot.send_photo(dest_chat_id, f, caption=caption)
        else:
            file_id = item["file_id"]
            if item["type"] == "video":
                await bot.send_video(dest_chat_id, file_id, caption=caption)
            else:
                await bot.send_photo(dest_chat_id, file_id, caption=caption)

        dest["sent"] = dest.get("sent", 0) + 1
        chat["stats"]["total_sent"] += 1
        return True

    except Exception as e:
        dest["failed"] = dest.get("failed", 0) + 1
        chat["stats"]["total_failed"] += 1
        await bot.send_message(chat_id, f"❌ Upload failed to {name}: {e}")
        await utils.notify_admin(
            bot, f"⚠️ Upload failed for chat {chat_id} -> {name}: {e}"
        )
        return False


async def _tick(bot, chat_id: int):
    """One posting tick for a chat: one item to every broadcast
    destination (shared active queue) plus one independent item to
    every dedicated-queue destination. Returns (chat, sent_count)."""
    chat = await storage.get_chat(chat_id)
    settings = chat["settings"]

    enabled = {n: d for n, d in chat["destinations"].items() if d.get("enabled", True)}
    if not enabled:
        # nowhere configured -> fall back to posting into the chat itself
        enabled = {"this chat": {"chat_id": chat_id, "enabled": True,
                                  "sent": 0, "failed": 0, "queue": None, "shuffle": False}}
        chat["destinations"].setdefault("this chat", enabled["this chat"])

    broadcast_dests = {n: d for n, d in enabled.items() if not d.get("queue")}
    dedicated_dests = {n: d for n, d in enabled.items() if d.get("queue")}

    sent_count = 0

    if broadcast_dests:
        item = _pop_item(chat, chat["active_queue"], settings.get("shuffle", False))
        if item:
            for name, dest in broadcast_dests.items():
                if await _deliver(bot, chat_id, chat, name, dest, item):
                    sent_count += 1

    for name, dest in dedicated_dests.items():
        item = _pop_item(chat, dest["queue"], dest.get("shuffle", False))
        if item:
            if await _deliver(bot, chat_id, chat, name, dest, item):
                sent_count += 1

    await storage.save()
    return chat, sent_count


async def _post_job(app, chat_id: int):
    bot = app.bot
    chat = await storage.get_chat(chat_id)

    if not chat.get("scheduler_running"):
        return

    if _remaining_total(chat) == 0:
        chat["scheduler_running"] = False
        chat["next_post_time"] = None
        await storage.save()
        await bot.send_message(chat_id, "✅ Queue completed — scheduler stopped.")
        if _cfg_admin():
            await bot.send_message(_cfg_admin(), f"✅ Queue for chat {chat_id} completed.")
        return

    chat, sent_count = await _tick(bot, chat_id)

    remaining = _remaining_total(chat)
    if remaining > 0:
        delay = utils.next_delay(chat["settings"])
        run_at = datetime.now() + timedelta(seconds=delay)
        chat["next_post_time"] = run_at.isoformat()
        await storage.save()
        scheduler.add_job(
            _post_job, "date", run_date=run_at, args=[app, chat_id],
            id=_job_id(chat_id), replace_existing=True,
        )
        await bot.send_message(
            chat_id,
            f"📤 Sent {sent_count} item(s) this round. {remaining} left. "
            f"Next post in {utils.fmt_duration(delay)}.",
        )
    else:
        chat["scheduler_running"] = False
        chat["next_post_time"] = None
        await storage.save()
        await bot.send_message(chat_id, "✅ Queue completed — scheduler stopped.")


def _cfg_admin():
    import config
    return config.ADMIN_ID or None


async def start_for_chat(app, chat_id: int):
    chat = await storage.get_chat(chat_id)
    if _remaining_total(chat) == 0:
        return False, "Queue is empty — nothing to schedule."

    chat["scheduler_running"] = True
    delay = utils.next_delay(chat["settings"])
    run_at = datetime.now() + timedelta(seconds=delay)
    chat["next_post_time"] = run_at.isoformat()
    await storage.save()

    scheduler.add_job(
        _post_job, "date", run_date=run_at, args=[app, chat_id],
        id=_job_id(chat_id), replace_existing=True,
    )
    return True, f"⏱ Scheduler started. First post in {utils.fmt_duration(delay)}."


async def stop_for_chat(chat_id: int):
    chat = await storage.get_chat(chat_id)
    chat["scheduler_running"] = False
    chat["next_post_time"] = None
    await storage.save()
    try:
        scheduler.remove_job(_job_id(chat_id))
    except Exception:
        pass
    return "🛑 Scheduler stopped."


async def send_now(bot, chat_id: int):
    """Drain everything immediately (active + all dedicated queues),
    ignoring the interval. Returns total items sent."""
    total_sent = 0
    while _remaining_total(await storage.get_chat(chat_id)) > 0:
        chat, sent_count = await _tick(bot, chat_id)
        if sent_count == 0:
            break
        total_sent += sent_count
    return total_sent
