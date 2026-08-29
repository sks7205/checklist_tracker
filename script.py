import os
import sys
import argparse
from datetime import datetime, timedelta, timezone
import requests
from playwright.sync_api import sync_playwright

# Configuration & Secrets
LOGIN_URL = "https://vedantaconnect.com/NEWMES/Account/Login?ReturnUrl=%2fNEWMES%2fHome%2fIndex"
BASE_URL = "https://vedantaconnect.com/NEWMES/Rodding2/Rodding2/LULChecklistIndex"
USER_ID = os.getenv("MES_USER_ID", "215502")
PASSWORD = os.getenv("MES_PASSWORD", "crystal$84goldenwolf")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8435743171:AAGijQqbqgqAAQ__lTZmeql612K64NWXpZo")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1549335982")

# Indian Standard Time (UTC +5:30)
IST = timezone(timedelta(hours=5, minutes=30))

def get_current_shift_and_date():
    """Calculates active shift and search date based on IST schedule."""
    now = datetime.now(IST)
    current_hour = now.hour + now.minute / 60.0

    # C Shift is typically morning execution checking previous day (or overnight)
    if 5.0 <= current_hour < 13.0:
        shift = "C"
        target_date = (now - timedelta(days=1)).strftime("%d/%m/%Y")
    elif 13.0 <= current_hour < 21.0:
        shift = "A"
        target_date = now.strftime("%d/%m/%Y")
    else:
        shift = "B"
        target_date = now.strftime("%d/%m/%Y")

    return shift, target_date

def send_telegram(text):
    """Sends HTML formatted Telegram messages."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

def apply_date_filter(page, target_date):
    """Fills the date search box and waits for table refresh."""
    page.fill("label > input", target_date)
    page.press("label > input", "Enter")
    page.wait_for_timeout(4000)

def extract_table_rows(page):
    """Extracts all table cells into structured row data."""
    return page.evaluate("""() => {
        const rows = document.querySelectorAll('#tblAEReason tbody tr');
        return Array.from(rows).map(r => 
            Array.from(r.querySelectorAll('td')).map(c => c.innerText.trim())
        );
    }""")

# ----------------- Checklist Processors -----------------

def check_lul(page, shift, target_date):
    page.click(".active > .rodding2 > .showmenu:nth-child(1) > a")
    apply_date_filter(page, target_date)

    expected_equip = ["LUL-1", "LUL-2"]
    expected_details = ["ShiftStart", "Mid-Shift", "ShiftEnd"]
    data = {equip: [] for equip in expected_equip}

    # Extract across possible pages
    while True:
        rows = extract_table_rows(page)
        for cells in rows:
            if len(cells) >= 7 and cells[2] == shift:
                shift_detail = cells[3]
                equip = cells[5]
                performer = cells[6]
                if equip in data:
                    if not any(e["shiftDetail"] == shift_detail and e["performedBy"] == performer for e in data[equip]):
                        data[equip].push({"shiftDetail": shift_detail, "performedBy": performer})

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
    page.click(menu_selector)
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
    page.click(menu_selector)
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

# ----------------- Main Execution -----------------

def run_automation(selected_shift=None, selected_date=None):
    auto_shift, auto_date = get_current_shift_and_date()
    shift = selected_shift or auto_shift
    target_date = selected_date or auto_date

    print(f"Running Checklist for Shift: {shift} | Date: {target_date}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # Login Step
        page.goto(LOGIN_URL, timeout=60000)
        page.fill("input#UserId", USER_ID)
        page.fill("input#Password", PASSWORD)
        page.click("input.btn")
        page.wait_for_load_state("networkidle")

        # Open Navigation
        page.goto(BASE_URL, timeout=60000)
        page.wait_for_timeout(2000)
        page.click("*:nth-child(1) > *:nth-child(8) > *:nth-child(1) > *:nth-child(1)")  # CARBON
        page.click(".active .treeview:nth-child(7) > a")  # Rodding 2
        page.wait_for_timeout(1000)

        # 1. LUL Operation Checklist
        check_lul(page, shift, target_date)

        # 2. TP Operation Checklist
        check_standard_three_stage(
            page=page,
            menu_selector=".active > .rodding2 > .showmenu:nth-child(2) > a",
            title="TP CHECKLIST",
            shift_idx=1,
            detail_idx=2,
            performer_idx=6,
            equipment_name="All TP",
            shift=shift,
            target_date=target_date
        )

        # 3. BP Operation Checklist
        check_standard_three_stage(
            page=page,
            menu_selector=".rodding2 > .showmenu:nth-child(3) > a",
            title="BP CHECKLIST",
            shift_idx=2,
            detail_idx=3,
            performer_idx=6,
            equipment_name="Both BP",
            shift=shift,
            target_date=target_date
        )

        # 4. Monorail Operation Checklist
        check_single_entry(
            page=page,
            menu_selector=".active > .rodding2 > .showmenu:nth-child(5) > a",
            title="MONORAIL CHECKLIST",
            equip_label="Monorail",
            shift_idx=1,
            performer_idx=2,
            shift=shift,
            target_date=target_date
        )

        # 5. AB/SB Operation Checklist
        check_standard_three_stage(
            page=page,
            menu_selector=".menu-level-2 > .showmenu:nth-child(6) > a",
            title="AB/SB CHECKLIST",
            shift_idx=2,
            detail_idx=3,
            performer_idx=4,
            equipment_name="All AB & SB",
            shift=shift,
            target_date=target_date
        )

        # 6. Rod Inspection Checklist
        check_standard_three_stage(
            page=page,
            menu_selector=".menu-level-2 > .showmenu:nth-child(7) > a",
            title="INSPECTION CHECKLIST",
            shift_idx=2,
            detail_idx=3,
            performer_idx=5,
            equipment_name="Inspection",
            shift=shift,
            target_date=target_date
        )

        # 7. Furnace Operation Checklist
        check_standard_three_stage(
            page=page,
            menu_selector=".menu-level-2 > .showmenu:nth-child(9) > a",
            title="FURNACE CHECKLIST",
            shift_idx=2,
            detail_idx=3,
            performer_idx=4,
            equipment_name="Furnace",
            shift=shift,
            target_date=target_date
        )

        # 8. Furnace 5TON Checklist
        check_single_entry(
            page=page,
            menu_selector=".menu-level-2 > .showmenu:nth-child(11) > a",
            title="5T CRANE CHECKLIST",
            equip_label="5ton crane",
            shift_idx=2,
            performer_idx=3,
            shift=shift,
            target_date=target_date
        )

        browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Shift Checklist Automation")
    parser.add_argument("--shift", choices=["A", "B", "C"], help="Force specific shift")
    parser.add_argument("--date", help="Custom Date (DD/MM/YYYY)")
    args = parser.parse_args()

    run_automation(selected_shift=args.shift, selected_date=args.date)