import os
import re
import sys
import time
import argparse
import subprocess
from datetime import datetime, timedelta, timezone
import requests
from playwright.sync_api import sync_playwright

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

def send_telegram(text):
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

def get_last_telegram_update_id():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        r = requests.get(url, timeout=10).json()
        if r.get("ok") and r.get("result"):
            return r["result"][-1]["update_id"]
    except Exception:
        pass
    return 0

def ask_and_wait_for_new_password():
    send_telegram(
        "⚠️ <b>MES LOGIN FAILED / PASSWORD EXPIRED</b>\n\n"
        f"User ID: <code>{USER_ID}</code>\n\n"
        "Please reply with your new MES password within 10 minutes.\n"
        "<i>Send only your new password in the chat.</i>"
    )
    
    last_update_id = get_last_telegram_update_id()
    print(f"Waiting for user to send new password via Telegram (Offset: {last_update_id})...")

    # Poll Telegram for up to 10 minutes (600 seconds)
    start_time = time.time()
    while time.time() - start_time < 600:
        time.sleep(5)
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last_update_id + 1}"
        try:
            res = requests.get(url, timeout=15).json()
            if res.get("ok") and res.get("result"):
                for update in res["result"]:
                    last_update_id = update["update_id"]
                    msg = update.get("message", {})
                    sender_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "").strip()

                    if sender_id == str(CHAT_ID) and text:
                        print(f"Received new password from Telegram.")
                        return text
        except Exception as e:
            print(f"Error checking Telegram updates: {e}")
            
    send_telegram("❌ Timeout waiting for new password. Automation cancelled.")
    return None

def update_password_in_repo(new_password):
    try:
        # 1. Update script.py locally
        with open("script.py", "r", encoding="utf-8") as f:
            content = f.read()

        pattern = r'(PASSWORD\s*=\s*os\.getenv\("MES_PASSWORD",\s*")[^"]+("\))'
        updated_content = re.sub(pattern, f'\\g<1>{new_password}\\g<2>', content)

        with open("script.py", "w", encoding="utf-8") as f:
            f.write(updated_content)

        # 2. Push commit to GitHub repository
        repo = os.getenv("GITHUB_REPOSITORY", "sks7205/checklist_tracker")
        remote_url = f"https://x-access-token:{GH_PAT}@github.com/{repo}.git" if GH_PAT else "origin"

        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", "script.py"], check=True)
        subprocess.run(["git", "commit", "-m", "Auto-update MES password from Telegram bot"], check=True)
        subprocess.run(["git", "push", remote_url, "HEAD:main"], check=True)
        print("Password updated and committed to repository successfully.")
        send_telegram("✅ New password saved to repository. Resuming checklist run...")
    except Exception as e:
        print(f"Failed to auto-commit password to repository: {e}")
        send_telegram(f"⚠️ Note: Checklist will run now, but saving to repo had an issue: {e}")

def apply_date_filter(page, target_date):
    try:
        page.wait_for_selector("label > input", timeout=10000)
        page.fill("label > input", "")
        page.fill("label > input", target_date)
        page.press("label > input", "Enter")
        page.wait_for_timeout(4000)
    except Exception as e:
        print(f"Warning filtering date: {e}")

def extract_table_rows(page):
    try:
        return page.evaluate("""() => {
            const rows = document.querySelectorAll('#tblAEReason tbody tr');
            return Array.from(rows).map(r => 
                Array.from(r.querySelectorAll('td')).map(c => c.innerText.trim())
            );
        }""")
    except Exception as e:
        print(f"Error extracting table rows: {e}")
        return []

# ----------------- Checklist Processors -----------------

def check_lul(page, shift, target_date):
    print("Checking LUL...")
    try:
        page.click(".active > .rodding2 > .showmenu:nth-child(1) > a", timeout=10000)
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
    except Exception as e:
        print(f"Error in LUL check: {e}")

def check_standard_three_stage(page, menu_selector, title, shift_idx, detail_idx, performer_idx, equipment_name, shift, target_date):
    print(f"Checking {title}...")
    try:
        page.click(menu_selector, timeout=10000)
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
    except Exception as e:
        print(f"Error in {title} check: {e}")

def check_single_entry(page, menu_selector, title, equip_label, shift_idx, performer_idx, shift, target_date):
    print(f"Checking {title}...")
    try:
        page.click(menu_selector, timeout=10000)
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
    except Exception as e:
        print(f"Error in {title} check: {e}")

# ----------------- Main Execution -----------------

def login_and_verify(page, current_password):
    print("Navigating to login page...")
    page.goto(LOGIN_URL, timeout=60000, wait_until="networkidle")
    page.fill("input#UserId", USER_ID)
    page.fill("input#Password", current_password)
    page.click("input.btn")
    page.wait_for_timeout(4000)

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
        context = browser.new_context()
        page = context.new_page()

        try:
            # Step 1: Attempt Login
            logged_in = login_and_verify(page, PASSWORD)

            # Step 2: Handle Failure / Expired Password via Telegram
            if not logged_in:
                new_pass = ask_and_wait_for_new_password()
                if not new_pass:
                    sys.exit(1)

                PASSWORD = new_pass
                update_password_in_repo(PASSWORD)

                # Retry Login with the new password
                logged_in = login_and_verify(page, PASSWORD)
                if not logged_in:
                    send_telegram("❌ Login failed again with the new password. Please check your credentials.")
                    sys.exit(1)

            print("Login successful.")

            # Step 3: Open Rodding 2 Section
            page.goto(BASE_URL, timeout=60000, wait_until="networkidle")
            page.wait_for_timeout(2000)
            page.click("*:nth-child(1) > *:nth-child(8) > *:nth-child(1) > *:nth-child(1)", timeout=10000)  # CARBON
            page.click(".active .treeview:nth-child(7) > a", timeout=10000)  # Rodding 2
            page.wait_for_timeout(1000)

            # Step 4: Execute all checklists
            check_lul(page, shift, target_date)
            check_standard_three_stage(page, ".active > .rodding2 > .showmenu:nth-child(2) > a", "TP CHECKLIST", 1, 2, 6, "All TP", shift, target_date)
            check_standard_three_stage(page, ".rodding2 > .showmenu:nth-child(3) > a", "BP CHECKLIST", 2, 3, 6, "Both BP", shift, target_date)
            check_single_entry(page, ".active > .rodding2 > .showmenu:nth-child(5) > a", "MONORAIL CHECKLIST", "Monorail", 1, 2, shift, target_date)
            check_standard_three_stage(page, ".menu-level-2 > .showmenu:nth-child(6) > a", "AB/SB CHECKLIST", 2, 3, 4, "All AB & SB", shift, target_date)
            check_standard_three_stage(page, ".menu-level-2 > .showmenu:nth-child(7) > a", "INSPECTION CHECKLIST", 2, 3, 5, "Inspection", shift, target_date)
            check_standard_three_stage(page, ".menu-level-2 > .showmenu:nth-child(9) > a", "FURNACE CHECKLIST", 2, 3, 4, "Furnace", shift, target_date)
            check_single_entry(page, ".menu-level-2 > .showmenu:nth-child(11) > a", "5T CRANE CHECKLIST", "5ton crane", 2, 3, shift, target_date)

        except Exception as err:
            print(f"Execution error: {err}")
            raise err
        finally:
            browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Shift Checklist Automation")
    parser.add_argument("--shift", choices=["A", "B", "C"], help="Force specific shift")
    parser.add_argument("--date", help="Custom Date (DD/MM/YYYY)")
    args = parser.parse_args()

    run_automation(selected_shift=args.shift, selected_date=args.date)
