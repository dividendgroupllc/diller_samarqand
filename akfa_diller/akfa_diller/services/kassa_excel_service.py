import re
from typing import Dict, List, Optional

import frappe
from frappe import _

from akfa_diller.akfa_diller.utils.helpers import get_file_path, parse_numeric

# Fixed column order of the "kassa" export template (confirmed against real
# files from both sample branches -- unlike the Sales/Purchase Excel format,
# this is a rigid single-template export, not free-form, so positional
# columns (not header-text matching per column) is the right approach here.
_COLUMNS = (
    "date", "diler", "payment_type", "person_text", "target_text",
    "izoh", "amount", "currency", "creator", "updater",
    "created", "updated", "status",
)

_PHONE_RE = re.compile(r"\+?\d[\d\s]{7,}\d")


def _ensure_openpyxl():
    try:
        import openpyxl  # noqa
    except ImportError:
        frappe.throw(_("openpyxl o'rnatilmagan. Ishga tushiring: pip install openpyxl"))


def read_kassa_report(file_url: str) -> List[Dict]:
    """
    Read a "kassa" (cash register) Excel export.

    Returns one dict per ACTIVE (non-DELETED) row, in file order, with keys:
    row_num, date (DD.MM.YYYY string, as-is), diler, payment_type
    (RECEIPT/PAYMENT), person_text (the real party -- "Mijozdan" for RECEIPT,
    "Mijozga" for PAYMENT), izoh, amount (float, sign preserved).

    `DELETED` rows are dropped entirely here -- they are not real
    transactions (confirmed against real data: some show up mid-file with
    every other field identical to an active row, just re-flagged).
    """
    _ensure_openpyxl()
    from openpyxl import load_workbook

    file_path = get_file_path(file_url)
    if not file_path:
        frappe.throw(_("Excel fayl topilmadi: {0}").format(file_url))

    wb = load_workbook(file_path, data_only=True)
    try:
        ws = wb["Template"] if "Template" in wb.sheetnames else wb.active
        header_row = _find_header_row(ws)
        if not header_row:
            frappe.throw(_("Excel'da 'Sana'/'Diler' sarlavha qatori topilmadi"))

        rows = []
        for row_num, row in enumerate(
            ws.iter_rows(min_row=header_row + 1, max_col=len(_COLUMNS), values_only=True), start=header_row + 1
        ):
            if row[0] is None:
                continue

            record = dict(zip(_COLUMNS, row))
            if (record.get("status") or "").strip().upper() == "DELETED":
                continue

            payment_type = (record.get("payment_type") or "").strip().upper()
            # RECEIPT: money comes FROM the real party (person_text) INTO cash.
            # PAYMENT: money goes OUT of cash TO the real party (target_text).
            person_text = record.get("target_text") if payment_type == "PAYMENT" else record.get("person_text")

            rows.append({
                "row_num": row_num,
                "date": (record.get("date") or "").strip(),
                "diler": (record.get("diler") or "").strip(),
                "payment_type": payment_type,
                "person_text": (person_text or "").strip(),
                "izoh": (record.get("izoh") or "").strip(),
                "amount": parse_numeric(record.get("amount")),
            })

        return rows
    finally:
        wb.close()


def _find_header_row(worksheet) -> Optional[int]:
    for row_num, row in enumerate(worksheet.iter_rows(min_row=1, max_row=30), start=1):
        first = str(row[0].value or "").strip().lower()
        second = str(row[1].value or "").strip().lower() if len(row) > 1 else ""
        if first == "sana" and second == "diler":
            return row_num
    return None


def extract_phone(text: str) -> Optional[str]:
    """
    Pull the phone number out of a "Name (+998 XX XXX XX XX)"-style string.

    Returns it exactly as matched (spacing/format varies in the source data --
    the caller normalizes before comparing against Customer.mobile_no).
    """
    if not text:
        return None
    match = _PHONE_RE.search(text)
    return match.group(0).strip() if match else None


def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """Digits only, for tolerant comparison against however Customer.mobile_no is formatted."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return digits or None
