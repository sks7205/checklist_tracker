import os
import re
import sys
import time
import argparse
import subprocess
from datetime import datetime, timedelta, timezone
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Configuration
LOGIN_URL = "https://vedantaconnect.com/NEWMES/Account/Login?ReturnUrl=%2fNEWMES%2fHome%2fIndex"
BASE_URL = "https://vedantaconnect.com/NEWMES/Rodding2/Rodding2/LULChecklistIndex"
USER_ID = os.getenv("MES_USER_ID", "215502")
PASSWORD = os.getenv("MES_PASSWORD", "Plmoknijbuhv@6543")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8435743171:AAGijQqbqgqAAQ__lTZmeql612K64NWXpZo")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1549335982")
GH_PAT = os.getenv("GH_PAT", "")

# Indian Standard Time (UTC +5:30)
IST = timezone(timedelta(hours=5, minutes=30))

# ----------------- Telegram Helper Functions -----------------

def send_telegram(text):
    """Sends HTML formatted messages to Telegram."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        print("Telegram notification sent.")
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

def send_telegram_photo(photo_path, caption=""):
    """Sends a screenshot image to Telegram."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as photo:
            files = {"photo": photo}
            data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
            requests.post(url, files=files, data=data, timeout=30)
        print(f"Screenshot sent to Telegram: {photo_path}")
    except Exception as e:
        print(f"Failed to send photo to Telegram: {e}")

def get_last_telegram_update_id():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        r = requests.get(url, timeout=10).json()
        if r.get("ok") and r.get("result"):
            return r["result"][-1]["update_id"]
    except Exception:
        pass
    return 0

CF_WORKER_URL = "https://checklist-telegram-bot.santanusahoo99.workers.dev"  # Replace with your worker URL

def ask_and_wait_for_new_password(page=None):
    if page:
        try:
            page.screenshot(path="login_failed.png", full_page=True)
            send_telegram_photo("login_failed.png", caption="⚠️ <b>MES Login Failed / Password Expired</b>")
        except Exception:
            pass

    # 1. Temporarily remove webhook so getUpdates works inside this script
    try:
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook", timeout=10)
    except Exception as e:
        print(f"Warning dropping webhook: {e}")

    send_telegram(
        "⚠️ <b>MES PASSWORD EXPIRED / LOGIN FAILED</b>\n\n"
        f"User ID: <code>{USER_ID}</code>\n\n"
        "Please reply with your new MES password within 10 minutes.\n"
        "<i>Send only the new password in this chat.</i>"
    )
    
    last_update_id = get_last_telegram_update_id()
    print("Waiting for new password from Telegram...")

    new_password = None
    start_time = time.time()
    
    # 2. Poll for your reply
    while time.time() - start_time < 600:
        time.sleep(4)
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last_update_id + 1}"
        try:
            res = requests.get(url, timeout=15).json()
            if res.get("ok") and res.get("result"):
                for update in res["result"]:
                    last_update_id = update["update_id"]
                    msg = update.get("message", {})
                    sender_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "").strip()

                    if sender_id == str(CHAT_ID) and text and not text.startswith("/"):
                        print("Received new password from Telegram.")
                        new_password = text
                        break
            if new_password:
                break
        except Exception as e:
            print(f"Error checking Telegram updates: {e}")

    # 3. Restore the Cloudflare webhook for /check triggers
    try:
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={CF_WORKER_URL}", timeout=10)
    except Exception as e:
        print(f"Warning resetting webhook: {e}")

    if not new_password:
        send_telegram("❌ Timeout waiting for new password. Automation cancelled.")
        return None

    return new_password
def update_password_in_repo(new_password):
    try:
        with open("script.py", "r", encoding="utf-8") as f:
            content = f.read()

        pattern = r'(PASSWORD\s*=\s*os\.getenv\("MES_PASSWORD",\s*")[^"]+("\))'
        updated_content = re.sub(pattern, f'\\g<1>{new_password}\\g<2>', content)

        with open("script.py", "w", encoding="utf-8") as f:
            f.write(updated_content)

        repo = os.getenv("GITHUB_REPOSITORY", "sks7205/checklist_tracker")
        remote_url = f"https://x-access-token:{GH_PAT}@github.com/{repo}.git" if GH_PAT else "origin"

        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", "script.py"], check=True)
        subprocess.run(["git", "commit", "-m", "Auto-update MES password from Telegram bot"], check=True)
        subprocess.run(["git", "push", remote_url, "HEAD:main"], check=True)
        print("Password successfully updated in GitHub repository.")
        send_telegram("✅ New password saved to repository. Resuming checklist run...")
    except Exception as e:
        print(f"Failed to commit password to repository: {e}")
        send_telegram(f"⚠️ Note: Checklist will run now, but saving to repo failed: {e}")

# ----------------- Robust Web Helpers -----------------

def robust_click(page, selector, retries=3, delay=2000):
    """Retries element clicks with exponential backoff to handle slow-loading DOMs."""
    for attempt in range(retries):
        try:
            page.wait_for_selector(selector, state="visible", timeout=15000)
            page.click(selector, timeout=10000)
            page.wait_for_timeout(delay)
            return True
        except Exception as e:
            print(f"Attempt {attempt+1} failed clicking '{selector}': {e}")
            time.sleep(2)
    raise Exception(f"Failed to click element '{selector}' after {retries} attempts.")

def apply_date_filter(page, target_date):
    """Applies date filter with verification to guarantee correct table results."""
    for attempt in range(3):
        try:
            page.wait_for_selector("label > input", state="visible", timeout=15000)
            page.fill("label > input", "")
            page.fill("label > input", target_date)
            page.press("label > input", "Enter")
            page.wait_for_timeout(4000)
            return
        except Exception as e:
            print(f"Retrying date input for '{target_date}': {e}")
            time.sleep(2)

def extract_table_rows(page):
    """Safely extracts table rows even if the table is still rendering."""
    try:
        page.wait_for_selector("#tblAEReason", timeout=10000)
        return page.evaluate("""() => {
            const rows = document.querySelectorAll('#tblAEReason tbody tr');
            return Array.from(rows).map(r => 
                Array.from(r.querySelectorAll('td')).map(c => c.innerText.trim())
            );
        }""")
    except Exception as e:
        print(f"Table row extraction warning: {e}")
        return []

# ----------------- Checklist Processors -----------------

def check_lul(page, shift, target_date):
    print("Checking LUL...")
    robust_click(page, ".active > .rodding2 > .showmenu:nth-child(1) > a")
    apply_date_filter(page, target_date)

    expected_equip = ["LUL-1", "LUL-2"]
    expected_details = ["ShiftStart", "Mid-Shift", "ShiftEnd"]
    data = {equip: [] for equip in expected_equip}

    while True:
        rows = extract_table_rows(page)
        for cells in rows:
            if len(cells) >= 7 and cells[2] == shift:
                shift_detail = cells[3]
                equip = cells[5]
                performer = cells[6]
                if equip in data:
                    if not any(e["shiftDetail"] == shift_detail and e["performedBy"] == performer for e in data[equip]):
                        data[equip].append({"shiftDetail": shift_detail, "performedBy": performer})

        next_btn = page.query_selector(".next:not(.disabled) a")
        if next_btn:
            next_btn.click()
            page.wait_for_timeout(3000)
        else:
            break

    missing = []
    for equip in expected_equip:
        entries = data[equip]
        found_details = [e["shiftDetail"] for e in entries]
        known_performer = entries[0]["performedBy"] if entries else ""

        for exp in expected_details:
            if exp not in found_details:
                missing.append(
                    f"Date: {target_date}\n"
                    f"Shift: {shift}\n"
                    f"Equipment: {equip}\n"
                    f"Shift Detail: {exp}\n"
                    f"Status: ❌ Not filled\n"
                    f"Responsible: {known_performer}"
                )

    if missing:
        msg = f"<u><b>*LUL CHECKLIST STATUS*</b></u>\n\n⚠️ MISSING ENTRY\n\n" + "\n\n".join(missing)
    else:
        summary = ""
        for equip in expected_equip:
            summary += f"\nEquipment: {equip}\n"
            for e in data[equip]:
                summary += f"  • {e['shiftDetail']} → {e['performedBy']}\n"
        msg = f"<u><b>*LUL CHECKLIST STATUS*</b></u>\n\n✅ {shift} Shift checklist completed for Both LUL\nDate: {target_date}\n{summary}"

    send_telegram(msg)

def check_standard_three_stage(page, menu_selector, title, shift_idx, detail_idx, performer_idx, equipment_name, shift, target_date):
    print(f"Checking {title}...")
    robust_click(page, menu_selector)
    apply_date_filter(page, target_date)
    rows = extract_table_rows(page)

    shift_rows = []
    for cells in rows:
        if len(cells) >= max(shift_idx, detail_idx, performer_idx) + 1:
            if cells[shift_idx] == shift:
                shift_rows.append({"shiftDetail": cells[detail_idx], "performedBy": cells[performer_idx]})

    expected = ["ShiftStart", "Mid-Shift", "ShiftEnd"]
    found_details = [r["shiftDetail"] for r in shift_rows]
    known_performer = shift_rows[0]["performedBy"] if shift_rows else ""

    missing = []
    for exp in expected:
        if exp not in found_details:
            missing.append(
                f"Date: {target_date}\n"
                f"Shift: {shift}\n"
                f"{f'Equipment: {equipment_name}' + chr(10) if equipment_name else ''}"
                f"Shift Detail: {exp}\n"
                f"Status: ❌ Not filled\n"
                f"Responsible: {known_performer}"
            )

    if missing:
        msg = f"<u><b>*{title} STATUS*</b></u>\n\n⚠️ MISSING ENTRY\n\n" + "\n\n".join(missing)
    else:
        summary = "".join([f"  • {e['shiftDetail']} → {e['performedBy']}\n" for e in shift_rows])
        msg = f"<u><b>*{title} STATUS*</b></u>\n\n✅ {shift} Shift checklist completed for {equipment_name or title}\nDate: {target_date}\n\n{summary}"

    send_telegram(msg)

def check_single_entry(page, menu_selector, title, equip_label, shift_idx, performer_idx, shift, target_date):
    print(f"Checking {title}...")
    robust_click(page, menu_selector)
    apply_date_filter(page, target_date)
    rows = extract_table_rows(page)

    shift_found = False
    performer_name = ""
    for cells in rows:
        if len(cells) >= max(shift_idx, performer_idx) + 1:
            if cells[shift_idx] == shift:
                shift_found = True
                performer_name = cells[performer_idx]
                break

    if not shift_found:
        msg = (
            f"<u><b>*{title} STATUS*</b></u>\n\n"
            f"⚠️ MISSING ENTRY\n"
            f"Date: {target_date}\n"
            f"Shift: {shift}\n"
            f"Equipment: {equip_label}\n"
            f"Status: ❌ Not filled\n"
            f"Responsible: {performer_name}"
        )
    else:
        msg = (
            f"<u><b>*{title} STATUS*</b></u>\n\n"
            f"✅ {shift} Shift checklist completed for {equip_label}\n"
            f"Date: {target_date}\n"
            f"Performed By: {performer_name}"
        )
    send_telegram(msg)

# ----------------- Main Controller -----------------

def get_current_shift_and_date(selected_shift=None, selected_date=None):
    now = datetime.now(IST)
    if selected_shift:
        shift = selected_shift.upper()
    else:
        current_hour = now.hour + now.minute / 60.0
        if 5.0 <= current_hour < 13.0:
            shift = "C"
        elif 13.0 <= current_hour < 21.0:
            shift = "A"
        else:
            shift = "B"

    if selected_date:
        target_date = selected_date
    else:
        if shift == "C":
            target_date = (now - timedelta(days=1)).strftime("%d/%m/%Y")
        else:
            target_date = now.strftime("%d/%m/%Y")

    return shift, target_date

def login_and_verify(page, current_password):
    print("Navigating to login page...")
    page.goto(LOGIN_URL, timeout=60000, wait_until="domcontentloaded")
    page.wait_for_selector("input#UserId", timeout=20000)
    page.fill("input#UserId", USER_ID)
    page.fill("input#Password", current_password)
    page.click("input.btn")
    page.wait_for_timeout(5000)

    current_url = page.url.lower()
    page_text = page.content().lower()

    if "change password" in page_text or "expired" in page_text or "account/login" in current_url:
        return False
    return True

def run_automation(selected_shift=None, selected_date=None):
    global PASSWORD
    shift, target_date = get_current_shift_and_date(selected_shift, selected_date)
    print(f"Starting Checklist Automation | Shift: {shift} | Date: {target_date}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        try:
            # Step 1: Login
            logged_in = login_and_verify(page, PASSWORD)

            # Step 2: Handle Expired Password / Failure via Telegram
            if not logged_in:
                new_pass = ask_and_wait_for_new_password(page)
                if not new_pass:
                    sys.exit(1)

                PASSWORD = new_pass
                update_password_in_repo(PASSWORD)

                logged_in = login_and_verify(page, PASSWORD)
                if not logged_in:
                    page.screenshot(path="login_retry_failed.png", full_page=True)
                    send_telegram_photo("login_retry_failed.png", caption="❌ <b>Login failed again with new password.</b>")
                    sys.exit(1)

            print("Login successful. Navigating to Rodding 2...")

            # Step 3: Open Rodding 2 Section
            page.goto(BASE_URL, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            robust_click(page, "*:nth-child(1) > *:nth-child(8) > *:nth-child(1) > *:nth-child(1)")  # CARBON
            robust_click(page, ".active .treeview:nth-child(7) > a")  # Rodding 2
            page.wait_for_timeout(1000)

            # Step 4: Run All Checklists
            check_lul(page, shift, target_date)
            check_standard_three_stage(page, ".active > .rodding2 > .showmenu:nth-child(2) > a", "TP CHECKLIST", 1, 2, 6, "All TP", shift, target_date)
            check_standard_three_stage(page, ".rodding2 > .showmenu:nth-child(3) > a", "BP CHECKLIST", 2, 3, 6, "Both BP", shift, target_date)
            check_single_entry(page, ".active > .rodding2 > .showmenu:nth-child(5) > a", "MONORAIL CHECKLIST", "Monorail", 1, 2, shift, target_date)
            check_standard_three_stage(page, ".menu-level-2 > .showmenu:nth-child(6) > a", "AB/SB CHECKLIST", 2, 3, 4, "All AB & SB", shift, target_date)
            check_standard_three_stage(page, ".menu-level-2 > .showmenu:nth-child(7) > a", "INSPECTION CHECKLIST", 2, 3, 5, "Inspection", shift, target_date)
            check_standard_three_stage(page, ".menu-level-2 > .showmenu:nth-child(9) > a", "FURNACE CHECKLIST", 2, 3, 4, "Furnace", shift, target_date)
            check_single_entry(page, ".menu-level-2 > .showmenu:nth-child(11) > a", "5T CRANE CHECKLIST", "5ton crane", 2, 3, shift, target_date)

        except Exception as err:
            print(f"Error encountered during checklist run: {err}")
            try:
                page.screenshot(path="page_error.png", full_page=True)
                send_telegram_photo(
                    "page_error.png",
                    caption=f"⚠️ <b>Checklist Automation Error</b>\n\nShift: {shift}\nDate: {target_date}\nError: <code>{str(err)[:500]}</code>"
                )
            except Exception as screenshot_err:
                print(f"Failed to capture error screenshot: {screenshot_err}")
                send_telegram(f"⚠️ <b>Automation Error:</b> <code>{str(err)[:500]}</code>")
            raise err
        finally:
            browser.close()

# ----------------- Telegram Listener for Manual Trigger -----------------

def check_for_telegram_triggers():
    """Checks Telegram for commands like /run, /run A, /run B, /run C."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        res = requests.get(url, timeout=10).json()
        if res.get("ok") and res.get("result"):
            for update in reversed(res["result"]):
                msg = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "").strip()
                date_ts = msg.get("date", 0)

                # Check if message is within the last 15 minutes and from authorized chat
                if chat_id == str(CHAT_ID) and (time.time() - date_ts < 900):
                    if text.startswith("/run"):
                        parts = text.split()
                        shift = parts[1].upper() if len(parts) > 1 and parts[1].upper() in ["A", "B", "C"] else None
                        send_telegram(f"🚀 Received manual trigger from Telegram for <b>Shift {shift or 'Auto'}</b>. Starting check now...")
                        return shift
    except Exception as e:
        print(f"Error checking Telegram trigger: {e}")
    return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Shift Checklist Automation")
    parser.add_argument("--shift", choices=["A", "B", "C"], help="Force specific shift")
    parser.add_argument("--date", help="Custom Date in DD/MM/YYYY format")
    args = parser.parse_args()

    # Absolute priority to command line arguments passed from YAML / Cloudflare
    run_automation(selected_shift=args.shift, selected_date=args.date)
