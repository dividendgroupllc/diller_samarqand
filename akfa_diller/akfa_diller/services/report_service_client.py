from datetime import datetime, timedelta
from time import sleep
from typing import Dict, List, Tuple

import frappe
import requests
from frappe.utils.password import get_decrypted_password

DEFAULT_BASE_URL = "http://api-report.akfadiler.uz"
MAX_WINDOW_DAYS = 30  # API hard limit: 409 "Интервал не должен превышать 30 дней" beyond this
PAGE_LENGTH = 2000
MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = 5


def get_settings_and_password():
    settings = frappe.get_single("Report Service Settings")
    password = get_decrypted_password(
        "Report Service Settings", "Report Service Settings", "password", raise_exception=False
    )
    if not settings.username or not password:
        frappe.throw("Report Service login/parol sozlanmagan (Report Service Settings)")

    return settings, password


def login(base_url: str, username: str, password: str) -> str:
    try:
        response = requests.post(
            f"{base_url}/api/auth/login",
            json={"username": username, "password": password},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
    except requests.exceptions.Timeout:
        frappe.throw("Report Service login: vaqt tugadi")
    except requests.exceptions.RequestException as e:
        frappe.log_error(title="Report Service login xatosi", message=str(e))
        frappe.throw("Report Service bilan bog'lanib bo'lmadi")

    if not response.ok:
        frappe.log_error(title="Report Service login xatosi", message=response.text[:2000])
        frappe.throw(f"Report Service login xatosi ({response.status_code})")

    token = response.json().get("token")
    if not token:
        frappe.throw("Report Service login javobida token topilmadi")

    return token


def fetch_report_page(
    base_url: str,
    token: str,
    dealer_id,
    from_date: str,
    to_date: str,
    start: int = 0,
    length: int = PAGE_LENGTH,
) -> Dict:
    """One page of POST /api/reports/products-movement-currency for a single dealerId
    (each dealer/branch -- e.g. Jomboy, Cho'pon ota -- has its own complete,
    independent data stream; regionalBaseId is NOT used here since it does not
    reach a branch's own downstream sales, only the parent dealer's direct ones).
    Retries with backoff on HTTP 429 -- confirmed to happen under repeated calls
    against the live API."""
    body = {
        "filter": {
            "fromDate": from_date,
            "toDate": to_date,
            # The API silently returns zero records (no error) if dealerId is sent
            # as a JSON string instead of a number -- confirmed live: "57" -> 0
            # records, 57 -> 365 records for the same window. dealer_id is stored
            # as a Data field (Report Service Dealer Branch), so it must be cast
            # here regardless of what type the caller passes in.
            "dealerId": int(dealer_id),
            "regionalBaseId": None,
            "dealerClientId": None,
            "groupIds": None,
        },
        "length": length,
        "order": [{"field": None, "sort": None}],
        "start": start,
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(
                f"{base_url}/api/reports/products-movement-currency",
                json=body,
                headers=headers,
                timeout=60,
            )
        except requests.exceptions.Timeout:
            frappe.throw("Report Service hisobot so'rovi: vaqt tugadi")
        except requests.exceptions.RequestException as e:
            frappe.log_error(title="Report Service hisobot xatosi", message=str(e))
            frappe.throw("Report Service bilan bog'lanib bo'lmadi")

        if response.status_code == 429:
            sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue

        if not response.ok:
            frappe.log_error(title="Report Service hisobot xatosi", message=response.text[:2000])
            frappe.throw(f"Report Service hisobot xatosi ({response.status_code})")

        return response.json()

    frappe.throw("Report Service: 429 (rate limit) ko'p marta qaytdi, urinishlar tugadi")


def _split_windows(from_date_str: str, to_date_str: str) -> List[Tuple[str, str]]:
    """Splits an arbitrary date range into <=MAX_WINDOW_DAYS chunks."""
    start_dt = datetime.strptime(from_date_str, "%Y-%m-%d")
    end_dt = datetime.strptime(to_date_str, "%Y-%m-%d")

    windows = []
    cursor = start_dt
    while cursor <= end_dt:
        chunk_end = min(cursor + timedelta(days=MAX_WINDOW_DAYS - 1), end_dt)
        windows.append((cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        cursor = chunk_end + timedelta(days=1)

    return windows


def get_token() -> Tuple[str, str]:
    """Logs in once; returns (base_url, token) so a caller looping over multiple
    dealer branches in one sync run can reuse the same token instead of logging in
    once per branch (avoids needlessly multiplying calls against the rate limit)."""
    settings, password = get_settings_and_password()
    base_url = settings.base_url or DEFAULT_BASE_URL
    token = login(base_url, settings.username, password)
    return base_url, token


def fetch_all_rows_for_dealer(base_url: str, token: str, dealer_id, from_date_str: str, to_date_str: str) -> List[Dict]:
    """Fetches every row for a single dealerId across the given date range
    (auto-chunked into <=30-day windows). Only the first page (length=PAGE_LENGTH)
    of each window is fetched -- offset-based pagination ("start") was found
    unreliable against the live API during research (returned empty pages even at
    valid offsets), so looping on it risks silently duplicating or dropping rows.
    A scheduled sync's window is normally just a few days (well under PAGE_LENGTH
    rows per dealer), so this is not a practical limitation; if a window's
    recordsFiltered ever exceeds what a single page returns, that is logged loudly
    rather than silently under-processed."""
    all_rows = []
    for window_from, window_to in _split_windows(from_date_str, to_date_str):
        page = fetch_report_page(base_url, token, dealer_id, window_from, window_to)
        rows = page.get("data") or []
        all_rows.extend(rows)

        records_filtered = page.get("recordsFiltered") or 0
        if records_filtered > len(rows):
            frappe.log_error(
                title="Report Service: sahifa to'liq emas",
                message=(
                    f"dealerId={dealer_id}, oyna {window_from}..{window_to}: "
                    f"recordsFiltered={records_filtered}, lekin faqat {len(rows)} qator olindi "
                    f"(PAGE_LENGTH={PAGE_LENGTH}). Ba'zi tranzaksiyalar shu safar sinxronlanmagan "
                    "bo'lishi mumkin -- sync oynasini qisqartirish (tezroq ishga tushirish) tavsiya etiladi."
                ),
            )

    return all_rows
