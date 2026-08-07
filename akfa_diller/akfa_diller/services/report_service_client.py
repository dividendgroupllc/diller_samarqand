from datetime import datetime, timedelta
from time import sleep

import frappe
import requests
from frappe.utils import add_days, getdate
from frappe.utils.password import get_decrypted_password
from typing import Dict, List, Tuple

DEFAULT_BASE_URL = "http://api-report.akfadiler.uz"
MAX_WINDOW_DAYS = 30  # API hard limit: 409 "Интервал не должен превышать 30 дней" beyond this
PAGE_LENGTH = 3000
MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = 5
SLEEP_BETWEEN_CALLS = 4.0


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


def _fetch_window_adaptive(base_url: str, token: str, dealer_id, from_date: str, to_date: str, depth: int = 0) -> List[Dict]:
    """Fetches one <=30-day window, recursively bisecting the date range whenever
    a single page (length=PAGE_LENGTH) can't hold everything the API reports via
    recordsFiltered. offset-based pagination ("start") was found unreliable
    against the live API during earlier research (returned empty pages even at
    valid offsets) -- bisecting by DATE instead of by offset sidesteps that
    entirely and was already proven live across an hours-long historical
    backfill (see the one-off _backfill_transactions.py this was ported from).

    This matters even for a normal few-day scheduled sync window, not just a
    historical backfill: Samarqand-1 alone was confirmed (2026-08-07) to
    generate ~660 rows/day, so even a 4-day catch-up window can exceed
    PAGE_LENGTH on a single busy branch. Before this fix, fetch_all_rows_for_dealer
    only ever fetched page 1 of each window and logged a warning on overflow --
    the excess rows (which can include purchases/branch-transfers, not just
    sales) were silently never synced, and since the sync watermark
    (last_synced_date) only moves forward, that gap never gets a second chance
    to be picked up by a later run."""
    page = fetch_report_page(base_url, token, dealer_id, from_date, to_date, length=PAGE_LENGTH)
    sleep(SLEEP_BETWEEN_CALLS)
    rows = page.get("data") or []
    records_filtered = page.get("recordsFiltered") or 0

    if records_filtered <= len(rows) or from_date == to_date:
        if records_filtered > len(rows):
            frappe.log_error(
                title=f"Report Service: bir kunlik chegara oshib ketdi (dealerId={dealer_id})",
                message=(
                    f"{from_date}: recordsFiltered={records_filtered}, faqat {len(rows)} qator olindi "
                    f"(PAGE_LENGTH={PAGE_LENGTH}). Bitta kun ichida shu qadar ko'p tranzaksiya bo'lishi "
                    "kutilmagan -- qo'lda tekshirish tavsiya etiladi."
                ),
            )
        return rows

    start = getdate(from_date)
    end = getdate(to_date)
    mid = start + (end - start) // 2
    mid_str = mid.strftime("%Y-%m-%d")
    left = _fetch_window_adaptive(base_url, token, dealer_id, from_date, mid_str, depth + 1)
    right = _fetch_window_adaptive(base_url, token, dealer_id, add_days(mid_str, 1), to_date, depth + 1)
    return left + right


def fetch_all_rows_for_dealer(base_url: str, token: str, dealer_id, from_date_str: str, to_date_str: str) -> List[Dict]:
    """Fetches every row for a single dealerId across the given date range,
    auto-chunked into <=30-day windows and, within each, adaptively bisected by
    date whenever a single page can't hold everything (see
    _fetch_window_adaptive)."""
    all_rows = []
    for window_from, window_to in _split_windows(from_date_str, to_date_str):
        all_rows.extend(_fetch_window_adaptive(base_url, token, dealer_id, window_from, window_to))

    return all_rows
