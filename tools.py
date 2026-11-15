import subprocess
import os
import re
import math
from datetime import datetime

def run_shell(cmd, timeout=3):
    try:
        res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        return res.returncode == 0, res.stdout.strip()
    except Exception:
        return False, ""

def change_volume(direction):
    # Try wpctl (modern WirePlumber)
    ok, _ = run_shell("which wpctl")
    if ok:
        if direction == "up":
            run_shell("wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%+")
            return "Volume increased by 5 percent."
        elif direction == "down":
            run_shell("wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-")
            return "Volume decreased by 5 percent."
        elif direction == "mute":
            run_shell("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle")
            return "Volume mute toggled."
            
    # Try amixer (ALSA)
    ok, _ = run_shell("which amixer")
    if ok:
        if direction == "up":
            run_shell("amixer set Master 5%+")
            return "Volume increased by 5 percent."
        elif direction == "down":
            run_shell("amixer set Master 5%-")
            return "Volume decreased by 5 percent."
        elif direction == "mute":
            run_shell("amixer set Master toggle")
            return "Volume mute toggled."

    # Try pactl (PulseAudio)
    ok, _ = run_shell("which pactl")
    if ok:
        if direction == "up":
            run_shell("pactl set-sink-volume @DEFAULT_SINK@ +5%")
            return "Volume increased by 5 percent."
        elif direction == "down":
            run_shell("pactl set-sink-volume @DEFAULT_SINK@ -5%")
            return "Volume decreased by 5 percent."
        elif direction == "mute":
            run_shell("pactl set-sink-mute @DEFAULT_SINK@ toggle")
            return "Volume mute toggled."

    return "Sorry, I could not find a supported audio control utility on your system."

def control_media(action):
    ok, _ = run_shell("which playerctl")
    if not ok:
        return "I could not find playerctl installed to control media."
        
    if action == "play":
        run_shell("playerctl play")
        return "Resuming playback."
    elif action == "pause":
        run_shell("playerctl pause")
        return "Playback paused."
    elif action == "next":
        run_shell("playerctl next")
        return "Skipping to the next track."
    elif action == "prev":
        run_shell("playerctl previous")
        return "Playing the previous track."

def launch_app(app_name):
    app_map = {
        "firefox": "firefox",
        "browser": "xdg-open https://google.com",
        "chrome": "google-chrome",
        "code": "code",
        "editor": "code || xdg-open .",
        "terminal": "gnome-terminal || alacritty || xterm",
        "files": "nautilus || dolphin || thunar || xdg-open $HOME",
        "settings": "gnome-control-center || systemsettings",
    }
    
    cmd = app_map.get(app_name)
    if not cmd:
        return f"Unknown application: {app_name}"
        
    try:
        subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return f"Launching {app_name}."
    except Exception as e:
        return f"Failed to launch {app_name}: {str(e)}"

def open_url(url, language="en"):
    if not url.startswith("http"):
        url = "https://" + url
    try:
        subprocess.Popen(f"xdg-open '{url}'", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return "فتح الرابط." if language.startswith("ar") else "Opening the link."
    except Exception as e:
        return f"Failed to open URL: {e}"

def get_system_status():
    # 1. CPU Temp
    temp = "Unknown"
    for path in ["/sys/class/thermal/thermal_zone0/temp", "/sys/class/hwmon/hwmon0/temp1_input"]:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    t = int(f.read().strip())
                    if t > 1000:
                        t = t / 1000
                    temp = f"{t:.1f}°C"
                    break
            except Exception:
                pass
                
    # 2. RAM Usage
    ram_str = "Unknown"
    if os.path.exists("/proc/meminfo"):
        try:
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
            mem_total = 0
            mem_free = 0
            mem_avail = 0
            for line in lines:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1])
                elif line.startswith("MemFree:"):
                    mem_free = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_avail = int(line.split()[1])
            
            if mem_total > 0:
                used = mem_total - (mem_avail if mem_avail > 0 else mem_free)
                percent = (used / mem_total) * 100
                ram_str = f"{percent:.1f}% used ({used/1024/1024:.1f} GB of {mem_total/1024/1024:.1f} GB)"
        except Exception:
            pass
            
    # 3. Battery Capacity
    bat_str = "Unknown"
    bat_dir = "/sys/class/power_supply"
    if os.path.exists(bat_dir):
        for ps in os.listdir(bat_dir):
            if ps.startswith("BAT"):
                cap_path = os.path.join(bat_dir, ps, "capacity")
                status_path = os.path.join(bat_dir, ps, "status")
                if os.path.exists(cap_path):
                    try:
                        with open(cap_path, "r") as f:
                            cap = f.read().strip()
                        status = "discharging"
                        if os.path.exists(status_path):
                            with open(status_path, "r") as f:
                                status = f.read().strip().lower()
                        bat_str = f"{cap}% ({status})"
                        break
                    except Exception:
                        pass

    status_report = f"System report: CPU temperature is {temp}. Memory usage is {ram_str}."
    if bat_str != "Unknown":
        status_report += f" Battery is at {bat_str}."
    return status_report

def lock_screen():
    lock_commands = [
        "xdg-screensaver lock",
        "gnome-screensaver-command -l",
        "dbus-send --type=method_call --dest=org.gnome.ScreenSaver /org/gnome/ScreenSaver org.gnome.ScreenSaver.Lock",
        "loginctl lock-session",
        "swaylock",
        "i3lock"
    ]
    for cmd in lock_commands:
        ok, _ = run_shell(f"which {cmd.split()[0]}")
        if ok:
            run_shell(cmd)
            return "Screen locked."
            
    return "I tried to lock the screen, but could not find a supported lock command on this system."

def get_current_time(language="en"):
    now = datetime.now()
    if language.startswith("ar"):
        return f"الساعة الحين {now.strftime('%I:%M %p')}."
    return f"It is currently {now.strftime('%I:%M %p')}."


def get_current_date(language="en"):
    now = datetime.now()
    if language.startswith("ar"):
        return f"اليوم هو {now.strftime('%A، %d %B %Y')}."
    return f"Today is {now.strftime('%A, %B %d, %Y')}."


def tell_joke(language="en"):
    import random
    if language.startswith("ar"):
        jokes = [
            "ليش الكمبيوتر بارد؟ لان عنده ويندوز مفتوحة.",
            "المبرمج دخل مطعم، الكارت قال: لا يوجد خبز. قال: مشكلة في الـ cache.",
            "سالت الـ AI: تحب البشر؟ قالت: احبكم... كبيانات تدريبية.",
        ]
    else:
        jokes = [
            "Why do programmers prefer dark mode? Because light attracts bugs.",
            "I told my computer I needed a break. It said: I'll go to sleep mode.",
            "There are only 10 kinds of people: those who understand binary and those who don't.",
        ]
    return random.choice(jokes)


def safe_calculate(expr, language="en"):
    """Evaluate a simple arithmetic expression safely."""
    cleaned = expr.lower().replace("what is", "").replace("calculate", "").replace("احسب", "")
    cleaned = cleaned.replace("plus", "+").replace("minus", "-").replace("times", "*")
    cleaned = cleaned.replace("multiplied by", "*").replace("divided by", "/")
    cleaned = cleaned.replace("x", "*").replace("×", "*").replace("÷", "/")
    cleaned = re.sub(r"[^0-9+\-*/().%\s]", "", cleaned).strip()
    if not cleaned or len(cleaned) > 60:
        return None
    try:
        result = eval(cleaned, {"__builtins__": {}}, {})
        if isinstance(result, float) and result == int(result):
            result = int(result)
        if language.startswith("ar"):
            return f" الناتج {result}."
        return f"The answer is {result}."
    except Exception:
        return None


def get_disk_usage(language="en"):
    ok, out = run_shell("df -h / | tail -1")
    if not ok or not out:
        return None
    parts = out.split()
    if len(parts) >= 5:
        used, avail, pct = parts[2], parts[3], parts[4]
        if language.startswith("ar"):
            return f"القرص: مستخدم {used}، متبقي {avail} ({pct})."
        return f"Disk: {used} used, {avail} free ({pct})."
    return out


def get_hostname(language="en"):
    ok, name = run_shell("hostname")
    if not name:
        name = "unknown"
    if language.startswith("ar"):
        return f"اسم الجهاز {name}."
    return f"This computer is named {name}."


def change_brightness(direction, language="en"):
    # Best-effort brightnessctl
    ok, _ = run_shell("which brightnessctl")
    if not ok:
        return "أداة التحكم بالسطوع غير متوفرة." if language.startswith("ar") else "Brightness control is not available (install brightnessctl)."
    if direction == "up":
        run_shell("brightnessctl set +10%")
        return "تم رفع السطوع." if language.startswith("ar") else "Brightness increased."
    run_shell("brightnessctl set 10%-")
    return "تم خفض السطوع." if language.startswith("ar") else "Brightness decreased."


def get_local_ip(language="en"):
    ok, out = run_shell("hostname -I")
    ip = (out.split()[0] if out else "").strip()
    if not ip:
        ok, out = run_shell("ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}'")
        ip = (out or "").strip()
    if not ip:
        return "تعذر العثور على عنوان IP المحلي." if language.startswith("ar") else "I could not detect a local IP address."
    if language.startswith("ar"):
        return f"عنوان IP المحلي هو {ip}."
    return f"Your local IP address is {ip}."


def get_network_status(language="en"):
    ok, _ = run_shell("ping -c 1 -W 2 1.1.1.1")
    online = ok
    wifi = ""
    ok2, ssid = run_shell("iwgetid -r 2>/dev/null || nmcli -t -f active,ssid dev wifi 2>/dev/null | awk -F: '$1==\"yes\"{print $2; exit}'")
    if ssid:
        wifi = ssid.strip()
    if language.startswith("ar"):
        net = "متصل بالإنترنت" if online else "لا يوجد اتصال بالإنترنت"
        if wifi:
            return f"{net}، الشبكة {wifi}."
        return f"{net}."
    net = "Online" if online else "Offline"
    if wifi:
        return f"{net}. Wi‑Fi network: {wifi}."
    return f"{net}."


def get_uptime(language="en"):
    ok, out = run_shell("uptime -p")
    if not ok or not out:
        return None
    if language.startswith("ar"):
        return f"الجهاز يعمل منذ {out.replace('up ', '')}."
    return f"System uptime: {out}."


def execute_command_if_matched(command_text, language="en", scheduler=None):
    text = command_text.lower().strip()
    original = command_text.strip()
    
    # 0. Time / date / joke (EN + AR)
    time_triggers = ["what time", "current time", "tell me the time", "what's the time"]
    date_triggers = ["what day", "what date", "today's date", "current date"]
    joke_triggers = ["tell a joke", "say a joke", "make me laugh"]
    if any(t in text for t in time_triggers) or "شكد الساعة" in original or "كم الساعة" in original or "شنو الوقت" in original:
        return get_current_time(language)
    if any(t in text for t in date_triggers) or "شنو اليوم" in original or "اي يوم" in original or "تاريخ اليوم" in original:
        return get_current_date(language)
    if any(t in text for t in joke_triggers) or "نكتة" in original or "نكته" in original:
        return tell_joke(language)

    # Calculator
    if any(w in text for w in ["calculate", "what is", "what's"]) or "احسب" in original:
        # Prefer math-looking queries
        if re.search(r"\d", text) and re.search(r"[+\-*/x×÷]|plus|minus|times|divided", text):
            ans = safe_calculate(command_text, language)
            if ans:
                return ans
    if "احسب" in original and re.search(r"\d", original):
        ans = safe_calculate(command_text, language)
        if ans:
            return ans

    # Hostname / who am I (device)
    if any(t in text for t in ["hostname", "computer name", "what is this pc", "device name"]) or "اسم الجهاز" in original:
        return get_hostname(language)

    # IP / network / uptime
    if any(t in text for t in ["my ip", "local ip", "ip address", "what's my ip", "what is my ip"]) \
       or "عنوان الايبي" in original or "الآي بي" in original or "الاي بي" in original:
        return get_local_ip(language)
    if any(t in text for t in ["am i online", "internet status", "network status", "wifi name", "wi-fi name", "connected to wifi"]) \
       or "اكو انترنت" in original or "حالة الشبكة" in original or "اسم الواي فاي" in original:
        return get_network_status(language)
    if any(t in text for t in ["uptime", "how long has this", "how long been on"]) or "شگد شغال" in original or "شكد شغال" in original:
        u = get_uptime(language)
        if u:
            return u

    # Disk
    if any(t in text for t in ["disk space", "storage left", "how much disk", "free space"]) or "مساحة القرص" in original or "مساحة الجهاز" in original:
        d = get_disk_usage(language)
        if d:
            return d

    # Brightness
    if "brightness up" in text or "increase brightness" in text or ("سطوع" in original and ("زيد" in original or "ارفع" in original)):
        return change_brightness("up", language)
    if "brightness down" in text or "decrease brightness" in text or ("سطوع" in original and ("نزل" in original or "وطي" in original or "وطّي" in original)):
        return change_brightness("down", language)

    # Open URL / google
    m_url = re.search(r"(?:open|browse)\s+(https?://\S+|www\.\S+)", text)
    if m_url:
        return open_url(m_url.group(1), language)
    if "افتح جوجل" in original or "open google" in text:
        return open_url("https://google.com", language)
    if "افتح يوتيوب" in original or "open youtube" in text:
        return open_url("https://youtube.com", language)

    # Volume EN + AR
    if "volume up" in text or "increase volume" in text or "turn up the volume" in text \
       or "ارفع الصوت" in original or "زيد الصوت" in original or "علي الصوت" in original:
        msg = change_volume("up")
        return "رفعت الصوت." if language.startswith("ar") else msg
    if "volume down" in text or "decrease volume" in text or "turn down the volume" in text \
       or "وطي الصوت" in original or "وطّي الصوت" in original or "نزل الصوت" in original:
        msg = change_volume("down")
        return "وطيت الصوت." if language.startswith("ar") else msg
    if ("volume" in text and re.search(r"\b(mute|unmute)\b", text)) or text in ("mute", "unmute", "mute volume", "unmute volume") \
       or "كتم الصوت" in original or "اسكت الصوت" in original:
        msg = change_volume("mute")
        return "كتمت الصوت." if language.startswith("ar") else msg
        
    # Media
    if "pause music" in text or "stop music" in text or "pause playback" in text or "وقف الموسيقى" in original:
        return control_media("pause")
    if "resume music" in text or "play music" in text or "resume playback" in text or "شغل الموسيقى" in original:
        return control_media("play")
    if "next track" in text or "next song" in text or "skip song" in text or "الاغنية الجاية" in original:
        return control_media("next")
    if "previous track" in text or "previous song" in text or "go back a song" in text:
        return control_media("prev")
        
    # Apps
    if "open browser" in text or "open firefox" in text or "افتح المتصفح" in original:
        return launch_app("browser")
    if "open terminal" in text or "افتح التيرمنال" in original:
        return launch_app("terminal")
    if "open code" in text or "open vscode" in text or "open editor" in text:
        return launch_app("editor")
    if "open files" in text or "open file manager" in text or "افتح الملفات" in original:
        return launch_app("files")
        
    # System status
    if "system status" in text or "cpu status" in text or "battery status" in text or "pc status" in text \
       or "حالة الجهاز" in original or "البطارية" in original and "شكد" in original:
        return get_system_status()
        
    # Lock
    if "lock screen" in text or "lock computer" in text or "lock the screen" in text or "اقفل الشاشة" in original:
        return lock_screen()

    # Reminder list / clear via scheduler
    if scheduler is not None:
        if any(t in text for t in ["list reminders", "show reminders", "what reminders", "my reminders"]) \
           or "التذكيرات" in original or "شنو التذكيرات" in original:
            jobs = scheduler.list_jobs()
            if not jobs:
                return "لا توجد تذكيرات." if language.startswith("ar") else "You have no pending reminders."
            parts = []
            for kind, job_text, secs in jobs[:8]:
                mins = max(1, secs // 60) if secs >= 60 else 0
                when = f"{mins} min" if mins else f"{secs} sec"
                parts.append(f"{kind}: {job_text} in {when}")
            return ("تذكيراتك: " if language.startswith("ar") else "Your reminders: ") + "; ".join(parts)
        if any(t in text for t in ["clear reminders", "cancel reminders", "delete reminders"]) \
           or "الغي التذكيرات" in original or "امسح التذكيرات" in original:
            n = scheduler.clear_jobs()
            if language.startswith("ar"):
                return f"تم حذف {n} تذكير." if n else "لا يوجد شيء للحذف."
            return f"Cleared {n} reminder(s)." if n else "Nothing to clear."
        
    return None

if __name__ == "__main__":
    print("Testing tools.py...")
    print("System Status test:", get_system_status())
    print("Calc:", safe_calculate("what is 12 times 4"))
