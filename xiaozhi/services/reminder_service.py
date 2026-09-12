"""Reminder/alarm service for voice-based scheduling."""
import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xiaozhi.reminder")


def parse_time_from_text(text: str) -> Optional[datetime]:
    """Parse time from natural language text."""
    text = text.lower().strip()
    now = datetime.now()

    # Pattern: "jam 3 sore", "jam 15:00", "pukul 3"
    time_match = re.search(r'(?:jam|pukul)\s*(\d{1,2})(?:[:.](\d{2}))?\s*(pagi|siang|sore|malam)?', text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2)) if time_match.group(2) else 0
        period = time_match.group(3)

        if period == "sore" and hour < 12:
            hour += 12
        elif period == "malam" and hour < 12:
            hour += 12
        elif period == "pagi" and hour == 12:
            hour = 0

        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    # Pattern: "dalam 5 menit", "dalam 1 jam"
    delay_match = re.search(r'dalam\s*(\d+)\s*(menit|jam|detik)', text)
    if delay_match:
        amount = int(delay_match.group(1))
        unit = delay_match.group(2)
        if unit == "detik":
            delta = timedelta(seconds=amount)
        elif unit == "menit":
            delta = timedelta(minutes=amount)
        else:  # jam
            delta = timedelta(hours=amount)
        return now + delta

    # Pattern: "besok", "lusa"
    if "besok" in text:
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    if "lusa" in text:
        return (now + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)

    return None


def parse_reminder_text(text: str) -> Dict[str, Any]:
    """Parse reminder details from natural language."""
    target_time = parse_time_from_text(text)

    # Extract the reminder message (everything after the time)
    # Remove time-related words
    message = re.sub(r'(?:ingatkan\s*(?:saya)?|ingat|alarm|reminder)\s*', '', text, flags=re.IGNORECASE)
    message = re.sub(r'(?:jam|pukul)\s*\d{1,2}(?:[:.]\d{2})?\s*(?:pagi|siang|sore|malam)?', '', message, flags=re.IGNORECASE)
    message = re.sub(r'dalam\s*\d+\s*(?:menit|jam|detik)', '', message, flags=re.IGNORECASE)
    message = re.sub(r'untuk|agar|supaya', '', message, flags=re.IGNORECASE)
    message = message.strip()

    if not message:
        message = "Pengingat"

    return {
        "time": target_time,
        "message": message,
        "original": text,
    }


def add_reminder(store, owner_id: int, text: str) -> Dict[str, Any]:
    """Add a new reminder for a user."""
    parsed = parse_reminder_text(text)

    if not parsed["time"]:
        return {
            "success": False,
            "message": "Tidak bisa memahami waktu dari kalimat. Coba: 'ingatkan saya jam 3 sore untuk minum obat'",
        }

    reminder_id = uuid.uuid4().hex[:12]
    reminder = store.add_reminder(
        owner_id=owner_id,
        reminder_id=reminder_id,
        message=parsed["message"],
        scheduled_at=parsed["time"].isoformat(),
    )

    time_str = parsed["time"].strftime("%H:%M")
    return {
        "success": True,
        "message": f"Pengingat diset untuk jam {time_str}: {parsed['message']}",
        "reminder": reminder,
    }


def list_reminders(store, owner_id: int) -> List[Dict[str, Any]]:
    """List all reminders for a user."""
    return store.list_reminders(owner_id)


def delete_reminder(store, owner_id: int, reminder_id: str) -> bool:
    """Delete a reminder."""
    return store.delete_reminder(owner_id, reminder_id)


async def check_reminders_periodically(store):
    """Background task to check for due reminders."""
    while True:
        try:
            due = store.get_due_reminders()
            for reminder in due:
                owner_id = reminder["owner_id"]
                message = reminder["message"]
                reminder_id = reminder["id"]
                logger.info(f"Reminder triggered for user {owner_id}: {message}")

                # Send reminder as chat message to trigger XiaoZhi voice response
                try:
                    # Add to chat history so XiaoZhi can respond
                    store.add_chat_history(
                        owner_id,
                        source="reminder",
                        tool_name="system_reminder",
                        user_message=f"[Pengingat] {message}",
                        xiaozhi_answer="",
                    )

                    # Also queue audio with spoken text
                    reminder_text = f"Pengingat: {message}"
                    store.queue_audio_command(
                        owner_id,
                        title=reminder_text,
                        stream_url="",
                        video_url="",
                        duration="",
                        video_id="",
                    )
                except Exception as e:
                    logger.warning(f"Failed to send reminder to user {owner_id}: {e}")

                # Mark as sent
                store.mark_reminder_sent(reminder_id)

        except Exception:
            logger.exception("Error checking reminders")

        await asyncio.sleep(30)  # Check every 30 seconds
