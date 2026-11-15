import time
import heapq
import threading
import re

class BackgroundScheduler(threading.Thread):
    def __init__(self, tts_queue_ref, state_signal_ref=None, language="en"):
        super().__init__(daemon=True)
        self.jobs = []
        self.lock = threading.Lock()
        self.tts_queue = tts_queue_ref
        self.state_signal = state_signal_ref
        self.running = True
        self.language = language
        
    def run(self):
        print("[SCHEDULER] Background scheduler thread started.")
        while self.running:
            now = time.time()
            to_run = []
            with self.lock:
                while self.jobs and self.jobs[0][0] <= now:
                    _, job_kind, job_text = heapq.heappop(self.jobs)
                    to_run.append((job_kind, job_text))
            
            for job_kind, job_text in to_run:
                print(f"[SCHEDULER] Triggering {job_kind}: {job_text}")
                if self.language.startswith("ar"):
                    if job_kind == "alarm":
                        announcement = f"هيه! المنبه يدق. {job_text}"
                    else:
                        announcement = f"هيه! تذكير: {job_text}."
                else:
                    if job_kind == "alarm":
                        announcement = f"Hey! Your alarm is ringing. {job_text}"
                    else:
                        announcement = f"Hey! Here is your reminder: {job_text}."
                self.tts_queue.put(announcement)
                self.tts_queue.put("<END_OF_RESPONSE>")
            
            time.sleep(0.5)
            
    def add_reminder(self, text, delay_seconds, kind="reminder"):
        with self.lock:
            target_time = time.time() + delay_seconds
            heapq.heappush(self.jobs, (target_time, kind, text))
        print(f"[SCHEDULER] Added {kind} '{text}' in {delay_seconds} seconds (at timestamp {target_time}).")

    def list_jobs(self):
        """Return pending jobs as list of (kind, text, seconds_left)."""
        now = time.time()
        with self.lock:
            items = []
            for target, kind, text in sorted(self.jobs):
                items.append((kind, text, max(0, int(target - now))))
            return items

    def clear_jobs(self):
        with self.lock:
            n = len(self.jobs)
            self.jobs.clear()
        return n
        
    def stop(self):
        self.running = False


def _unit_to_seconds(num, unit):
    unit = unit.lower()
    if unit.startswith("second") or unit in ("ثانية", "ثواني", "ثوان"):
        return num
    if unit.startswith("minute") or unit in ("دقيقة", "دقائق"):
        return num * 60
    if unit.startswith("hour") or unit in ("ساعة", "ساعات"):
        return num * 3600
    return num


def parse_reminder_text(text):
    """Parse English or Arabic reminder/timer/alarm phrases.
    Returns (action, seconds, kind) or None.
    kind is 'reminder', 'timer', or 'alarm'.
    """
    original = text.strip()
    text_l = original.lower().strip()
    
    # --- English alarm: "set an alarm for N unit" / "alarm in N unit" ---
    m_alarm = re.search(
        r"(?:set\s+(?:an?\s+)?alarm\s+(?:for|in)|alarm\s+(?:for|in))\s+(\d+)\s*(second|minute|hour)s?(?:\s+(?:to|for)\s+(.+))?",
        text_l
    )
    if m_alarm:
        num = int(m_alarm.group(1))
        unit = m_alarm.group(2)
        action = (m_alarm.group(3) or "time to wake up").strip()
        return action, _unit_to_seconds(num, unit), "alarm"

    # --- English: "remind me to <action> in <number> <unit>" ---
    m1 = re.search(r"remind me to (.+?) in (\d+)\s*(second|minute|hour)s?", text_l)
    if m1:
        action = m1.group(1).strip()
        num = int(m1.group(2))
        unit = m1.group(3)
        return action, _unit_to_seconds(num, unit), "reminder"
        
    # --- English: "set a timer for <number> <unit> to <action>" ---
    m2 = re.search(r"set a timer for (\d+)\s*(second|minute|hour)s? to (.+)", text_l)
    if m2:
        num = int(m2.group(1))
        unit = m2.group(2)
        action = m2.group(3).strip()
        return action, _unit_to_seconds(num, unit), "timer"

    # --- English: "timer for <number> <unit>" ---
    m3 = re.search(r"(?:set a )?timer for (\d+)\s*(second|minute|hour)s?", text_l)
    if m3:
        num = int(m3.group(1))
        unit = m3.group(2)
        return "your timer is up", _unit_to_seconds(num, unit), "timer"

    # --- Arabic digits helper (Eastern Arabic numerals) ---
    def ar_digits(s):
        trans = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
        return s.translate(trans)

    text_ar = ar_digits(original)

    # --- Arabic alarm: "منبه بعد N ..." / "ضبط منبه ..." ---
    m_ar_alarm = re.search(
        r"(?:منبه|المنبه|ضبط\s*منبه|حط\s*منبه).*?(?:بعد|لمدة)?\s*(\d+)\s*(ثانية|ثواني|ثوان|دقيقة|دقائق|ساعة|ساعات)(?:\s*(.+))?",
        text_ar
    )
    if m_ar_alarm:
        num = int(m_ar_alarm.group(1))
        unit = m_ar_alarm.group(2)
        action = (m_ar_alarm.group(3) or "صحى").strip()
        return action, _unit_to_seconds(num, unit), "alarm"

    # --- Arabic reminder: "ذكرني بـ ... بعد N" / "ذكرني ان ..." ---
    m_ar_rem = re.search(
        r"(?:ذكرني|ذكريني)\s*(?:ب|بـ|أن|ان|الى|إلى)?\s*(.+?)\s*(?:بعد|خلال)\s*(\d+)\s*(ثانية|ثواني|ثوان|دقيقة|دقائق|ساعة|ساعات)",
        text_ar
    )
    if m_ar_rem:
        action = m_ar_rem.group(1).strip()
        num = int(m_ar_rem.group(2))
        unit = m_ar_rem.group(3)
        return action, _unit_to_seconds(num, unit), "reminder"

    # --- Arabic timer: "مؤقت N دقيقة" / "ضبط مؤقت ..." ---
    m_ar_timer = re.search(
        r"(?:مؤقت|المؤقت|ضبط\s*مؤقت).*?(\d+)\s*(ثانية|ثواني|ثوان|دقيقة|دقائق|ساعة|ساعات)",
        text_ar
    )
    if m_ar_timer:
        num = int(m_ar_timer.group(1))
        unit = m_ar_timer.group(2)
        return "انتهى المؤقت", _unit_to_seconds(num, unit), "timer"
        
    return None


def execute_scheduler_if_matched(command_text, scheduler, language="en"):
    parsed = parse_reminder_text(command_text)
    if not parsed:
        return None
        
    action, seconds, kind = parsed
    scheduler.add_reminder(action, seconds, kind=kind)
    
    # Human-readable delay
    if seconds < 60:
        num, unit = seconds, ("ثانية" if language.startswith("ar") else "second")
        if seconds != 1 and not language.startswith("ar"):
            unit += "s"
        if language.startswith("ar") and seconds != 1:
            unit = "ثواني"
    elif seconds < 3600:
        num = seconds // 60
        if language.startswith("ar"):
            unit = "دقيقة" if num == 1 else "دقائق"
        else:
            unit = "minute" if num == 1 else "minutes"
    else:
        num = seconds // 3600
        if language.startswith("ar"):
            unit = "ساعة" if num == 1 else "ساعات"
        else:
            unit = "hour" if num == 1 else "hours"

    if language.startswith("ar"):
        if kind == "alarm":
            return f"تمام، حطيت منبه بعد {num} {unit}."
        if kind == "timer":
            return f"تمام، المؤقت شغال لمدة {num} {unit}."
        return f"تمام، راح اذكرك بـ {action} بعد {num} {unit}."
    
    if kind == "alarm":
        return f"Got it. Alarm set for {num} {unit}."
    if kind == "timer":
        return f"Got it. Timer set for {num} {unit}."
    return f"Got it. I will remind you to {action} in {num} {unit}."


if __name__ == "__main__":
    import queue
    test_q = queue.Queue()
    sched = BackgroundScheduler(test_q)
    sched.start()
    
    print("Testing parser...")
    tests = [
        "remind me to check the oven in 5 seconds",
        "set a timer for 10 minutes to turn off the lights",
        "set a timer for 3 seconds",
        "set an alarm for 2 minutes",
        "ذكرني ادرس بعد 5 دقائق",
        "منبه بعد 1 دقيقة",
    ]
    
    for t in tests:
        res = execute_scheduler_if_matched(t, sched)
        print(f"Query: '{t}' -> Response: '{res}'")
        
    time.sleep(7)
    while not test_q.empty():
        print(f"Triggered TTS Queue item: {test_q.get()}")
    
    sched.stop()
