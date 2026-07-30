import hashlib
import re
from typing import Any, Optional

import frappe

# Boshida raqam va ajratuvchi belgilar (bo'shliq, nuqta, vergul) bo'lgan
# qismini oladi -- oxiridagi valyuta/birlik qo'shimchasini ("1 449,5 SHB"
# kabi) chetlab o'tish uchun.
_LEADING_NUMBER_RE = re.compile(r"^(-?[\d\s.,]+)")

_NBSP = " "


def parse_numeric(value: Any) -> float:
    """
    Parse numeric value from various formats.

    Supports:
    - European format: 1.234,56 or 1 234,56
    - US format: 1,234.56
    - Plain numbers: 1234.56
    - A trailing currency/unit suffix glued to the number: "1 449,5 SHB"

    Args:
        value: Any value to parse (string, int, float, None)

    Returns:
        float: Parsed numeric value, 0.0 if parsing fails

    Examples:
        >>> parse_numeric("1,234.56")
        1234.56
        >>> parse_numeric("1.234,56")
        1234.56
        >>> parse_numeric(None)
        0.0
    """
    if value is None:
        return 0.0

    if isinstance(value, (int, float)):
        return float(value)

    str_val = str(value).strip()
    if not str_val:
        return 0.0

    # Strip a trailing non-numeric suffix (e.g. a glued currency/unit label)
    # before applying separator-format logic below.
    match = _LEADING_NUMBER_RE.match(str_val)
    if match:
        str_val = match.group(1)

    # Remove spaces and non-breaking spaces (thousand separators)
    str_val = str_val.replace(_NBSP, "").replace(" ", "").strip()
    if not str_val:
        return 0.0

    # Handle comma/dot formats
    if "," in str_val:
        dots = str_val.count(".")
        commas = str_val.count(",")

        if commas == 1 and dots == 0:
            # Format: 1234,56 -> 1234.56
            str_val = str_val.replace(",", ".")
        elif commas >= 1 and dots >= 1:
            # Both separators present: whichever comes LAST is the decimal
            # separator (the other is a thousands separator, stripped).
            # 1.234,56 (EU) -> last is "," -> 1234.56
            # 1,234.56 (US) -> last is "." -> 1234.56
            if str_val.rfind(",") > str_val.rfind("."):
                str_val = str_val.replace(".", "").replace(",", ".")
            else:
                str_val = str_val.replace(",", "")
        elif dots == 0 and commas > 1:
            # Format: 1,234,567 -> 1234567
            str_val = str_val.replace(",", "")

    # Handle dot as thousand separator: 1.234.567
    if "." in str_val and str_val.count(".") == 1:
        parts = str_val.split(".")
        if len(parts[1]) == 3 and parts[1].isdigit():
            str_val = str_val.replace(".", "")

    try:
        return float(str_val)
    except ValueError:
        return 0.0


def calculate_file_hash(file_url: str) -> str:
    """
    Calculate MD5 hash of a file for duplicate detection.

    Args:
        file_url: Frappe file URL (e.g., /private/files/test.xlsx)

    Returns:
        str: MD5 hash string, empty string if file not found
    """
    file_path = get_file_path(file_url)

    if not file_path:
        return ""

    try:
        with open(file_path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except (IOError, OSError):
        return ""


def get_file_path(file_url: str) -> Optional[str]:
    """
    Convert Frappe file URL to absolute file path.

    Args:
        file_url: Frappe file URL

    Returns:
        str: Absolute file path or None if not found
    """
    if not file_url:
        return None

    if file_url.startswith(("/private/files/", "/files/")):
        return frappe.get_site_path() + file_url

    # Try to get from File doctype
    file_doc = frappe.db.get_value("File", {"file_url": file_url}, "file_url")
    if file_doc:
        return frappe.get_site_path() + file_doc

    return frappe.get_site_path() + file_url


def safe_get_value(doctype: str, filters: dict, fieldname: str, default: Any = None) -> Any:
    """
    Safely get a value from database with default fallback.

    Args:
        doctype: DocType name
        filters: Filter dictionary
        fieldname: Field to retrieve
        default: Default value if not found

    Returns:
        Field value or default
    """
    try:
        value = frappe.db.get_value(doctype, filters, fieldname)
        return value if value is not None else default
    except Exception:
        return default
