"""
Simple JSON-file persistence layer.

Everything lives under one dict, keyed by chat_id (as string, since JSON
keys must be strings). Each chat gets:

{
  "queues": {"default": [ {unique_id, file_id, type, added_at,
                            size: int|None,       # bytes, when known
                            duration: float|None  # seconds, videos only
                           } ]},
  "active_queue": "default",
  "destinations": {
      "name": {"chat_id": int, "enabled": bool,
                "sent": int, "failed": int, "last_checked": str|None,
                "healthy": bool|None,
                "queue": str|None,   # None = shared broadcast queue (active_queue)
                                     # otherwise: name of a dedicated queue this
                                     # destination drains independently
                "shuffle": bool}     # random pop order from its own/broadcast queue
  },
  "settings": {
      "mode": "fixed"|"random",
      "fixed_delay": int,
      "min_delay": int,
      "max_delay": int,
      "caption": str,
      "max_queue_size": int,
      "allowed_types": [str],
      "timezone": str,
      "datetime_format": str,
      "shuffle": bool,   # random pop order for the shared broadcast queue
      "strip_last_word": bool,        # delete+replace everything after the
                                       # LAST space in the outgoing caption
      "last_word_replacement": str,   # text to put there instead (may be "")
      "replace_tg_links": bool,       # replace any t.me/@mention found
                                       # anywhere in the caption
      "tg_link_replacement": str,     # text to put there instead (may be "")
  },
  "scheduler_running": bool,
  "next_post_time": str|None,   # ISO timestamp, informational
  "seen_unique_ids": [str],     # for duplicate detection, across all queues
  "stats": {"total_sent": int, "total_failed": int}
}
"""

import json
import os
import asyncio
from datetime import datetime

import config

_lock = asyncio.Lock()
_data = None


def _default_chat():
    return {
        "queues": {"default": []},
        "active_queue": "default",
        "destinations": {},
        "settings": {
            "mode": config.SCHEDULE_MODE,
            "fixed_delay": config.DEFAULT_SEND_DELAY,
            "min_delay": config.MIN_SEND_DELAY,
            "max_delay": config.MAX_SEND_DELAY,
            "caption": config.DEFAULT_CAPTION,
            "use_original_caption": True,
            "max_queue_size": config.MAX_QUEUE_SIZE,
            "allowed_types": list(config.ALLOWED_FILE_TYPES),
            "timezone": config.SCHEDULE_TIMEZONE,
            "datetime_format": config.DATETIME_FORMAT,
            "shuffle": False,
            "strip_last_word": False,
            "last_word_replacement": "",
            "replace_tg_links": False,
            "tg_link_replacement": "",
        },
        "scheduler_running": False,
        "next_post_time": None,
        "seen_unique_ids": [],
        "stats": {"total_sent": 0, "total_failed": 0},
    }


def _load():
    global _data
    if _data is not None:
        return _data
    if os.path.exists(config.DATA_FILE):
        try:
            with open(config.DATA_FILE, "r", encoding="utf-8") as f:
                _data = json.load(f)
        except (json.JSONDecodeError, OSError):
            _data = {}
    else:
        _data = {}
    return _data


def _save():
    tmp = config.DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_data, f, indent=2)
    os.replace(tmp, config.DATA_FILE)


async def get_chat(chat_id: int) -> dict:
    async with _lock:
        data = _load()
        key = str(chat_id)
        if key not in data:
            data[key] = _default_chat()
            _save()
        else:
            # migrate older saved chats that predate newer settings keys
            defaults = _default_chat()["settings"]
            for k, v in defaults.items():
                data[key].setdefault("settings", {}).setdefault(k, v)
            # migrate older destinations that predate dedicated-queue/shuffle
            for dest in data[key].get("destinations", {}).values():
                dest.setdefault("queue", None)
                dest.setdefault("shuffle", False)
        return data[key]


async def save():
    async with _lock:
        _save()


async def all_chats() -> dict:
    async with _lock:
        return dict(_load())


def now_str(chat: dict) -> str:
    """Format 'now' using the chat's configured timezone + format."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(chat["settings"]["timezone"])
    except Exception:
        tz = None
    dt = datetime.now(tz) if tz else datetime.now()
    try:
        return dt.strftime(chat["settings"]["datetime_format"])
    except Exception:
        return dt.isoformat()
