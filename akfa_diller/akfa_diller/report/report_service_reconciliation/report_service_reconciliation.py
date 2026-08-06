import frappe
from frappe.utils import flt

from akfa_diller.akfa_diller.services import report_service_client
from akfa_diller.akfa_diller.api.report_service_sync import _group_by_cid, _get_branch_warehouse_map, _external_ref

AMOUNT_TOLERANCE = 0.5


def execute(filters=None):
    columns = get_columns()

    if not filters or not filters.get("from_date") or not filters.get("to_date"):
        return columns, []

    settings = frappe.get_single("Report Service Settings")
    if not settings.dealer_branches:
        frappe.throw("Report Service Settings sozlanmagan (filiallar ro'yxati bo'sh)")

    from_date = str(filters["from_date"])
    to_date = str(filters["to_date"])

    base_url, token = report_service_client.get_token()
    branch_map = _get_branch_warehouse_map()

    data = []
    for branch in settings.dealer_branches:
        rows = report_service_client.fetch_all_rows_for_dealer(base_url, token, branch.dealer_id, from_date, to_date)
        groups = _group_by_cid(rows)
        for cid, cid_rows in groups.items():
            data.append(_check_cid(cid, cid_rows, branch, branch_map))

    data.extend(_find_orphans(from_date, to_date, branch_map))

    # Problems first (missing/mismatched/orphan), then OK/expected-skip rows
    priority = {"YETISHMAYAPTI": 0, "SUMMA MOS EMAS": 0, "MIQDOR MOS EMAS": 0, "TEGSIZ HUJJAT (tekshiring)": 0}
    data.sort(key=lambda r: priority.get(r["status"], 1))

    return columns, data


def _check_cid(cid, rows, branch, branch_map):
    first = rows[0]
    row_type = first.get("type")
    client_cid = str(first.get("clientCid"))
    expected_amount = sum(flt(r.get("amount")) for r in rows)
    ref = _external_ref(cid, branch)

    if branch.is_main and f"{branch.dealer_id}:{client_cid}" in branch_map:
        doctype, category = "Stock Entry", "Filial ko'chirish"
    elif row_type == "RASXOD_KLIENT":
        doctype, category = "Sales Invoice", "Sotuv"
    elif row_type == "VOZVRAT_KLIENT":
        doctype, category = "Sales Invoice", "Qaytarish"
    elif row_type == "PRIXOD_BAZA":
        if not branch.is_main:
            # Sub-branch PRIXOD_BAZA is deliberately never synced (the other half
            # of a transfer the main branch's own feed already records) -- see
            # report_service_sync.py's module docstring.
            return {
                "cid": cid,
                "branch": branch.label,
                "category": "Bazadan kirim (filial)",
                "expected_doctype": "-",
                "status": "Kutilganidek o'tkazib yuborilgan",
                "erpnext_doc": None,
                "expected_amount": expected_amount,
                "actual_amount": None,
            }
        doctype, category = "Purchase Invoice", "Xarid"
    else:
        return {
            "cid": cid,
            "branch": branch.label,
            "category": f"Notanish tur ({row_type})",
            "expected_doctype": "-",
            "status": "Kutilganidek o'tkazib yuborilgan",
            "erpnext_doc": None,
            "expected_amount": expected_amount,
            "actual_amount": None,
        }

    doc_name = frappe.db.get_value(doctype, {"custom_report_service_cid": ref}, "name")
    if not doc_name:
        return {
            "cid": cid,
            "branch": branch.label,
            "category": category,
            "expected_doctype": doctype,
            "status": "YETISHMAYAPTI",
            "erpnext_doc": None,
            "expected_amount": expected_amount,
            "actual_amount": None,
        }

    actual_amount = _get_doc_amount(doctype, doc_name)

    if doctype == "Stock Entry":
        # A Material Transfer's *value* is NOT comparable to the API's reported
        # amount by design -- confirmed directly against ERPNext's own source
        # (stock_entry.py's set_rate_for_outgoing_items(), reset_outgoing_rate
        # defaults True): every item row with s_warehouse set has its basic_rate
        # OVERWRITTEN at submit time by the source warehouse's own moving-average
        # valuation, regardless of what basic_rate was supplied at insert. This is
        # correct accounting (an internal transfer carries the company's real
        # historical cost, not an external report's stated price) -- comparing
        # amounts here would flag every single transfer as a false "mismatch".
        # Qty, however, is never touched by valuation and is what's actually
        # guaranteed correct -- compare that instead.
        expected_qty = sum(flt(r.get("qty")) for r in rows)
        actual_qty = flt(frappe.db.sql(
            "SELECT COALESCE(SUM(qty), 0) FROM `tabStock Entry Detail` WHERE parent=%s AND s_warehouse IS NOT NULL",
            doc_name,
        )[0][0])
        status = "MIQDOR MOS EMAS" if abs(actual_qty - expected_qty) > 0.01 else "OK"
    else:
        status = "SUMMA MOS EMAS" if abs(abs(actual_amount) - abs(expected_amount)) > AMOUNT_TOLERANCE else "OK"

    return {
        "cid": cid,
        "branch": branch.label,
        "category": category,
        "expected_doctype": doctype,
        "status": status,
        "erpnext_doc": doc_name,
        "expected_amount": expected_amount,
        "actual_amount": actual_amount,
    }


def _get_doc_amount(doctype, doc_name) -> float:
    """Sales/Purchase Invoice have grand_total; Stock Entry (Material Transfer,
    no financial value) doesn't -- use qty*basic_rate summed across its items
    instead, matching how `amount` is summed on the API side."""
    if doctype == "Stock Entry":
        total = frappe.db.sql("""
            SELECT COALESCE(SUM(qty * basic_rate), 0) FROM `tabStock Entry Detail` WHERE parent = %s
        """, doc_name)
        return flt(total[0][0]) if total else 0.0
    return flt(frappe.db.get_value(doctype, doc_name, "grand_total"))


def _find_orphans(from_date, to_date, branch_map):
    """Documents in this date range against a known branch/baza party that
    carry NO cid tag at all -- exactly the pattern behind 3 real duplicate
    Sales Invoices found earlier via manual investigation. Not necessarily
    wrong (a legitimate unrelated manual entry could match this shape too),
    but always worth a human's eyes."""
    orphans = []

    # branch_map is keyed "{dealer_id}:{client_cid}" (see _get_branch_warehouse_map) --
    # but Customer.custom_report_service_client_cid stores the BARE client_cid for a
    # main branch (see _external_client_cid), so this orphan check needs just that
    # part back out, not the dealer-prefixed key.
    branch_cids = list({key.split(":", 1)[1] for key in branch_map.keys()})
    if branch_cids:
        placeholders = ", ".join(["%s"] * len(branch_cids))
        rows = frappe.db.sql(f"""
            SELECT si.name, si.grand_total
            FROM `tabSales Invoice` si
            INNER JOIN `tabCustomer` c ON c.name = si.customer
            WHERE c.custom_report_service_client_cid IN ({placeholders})
              AND si.posting_date BETWEEN %s AND %s
              AND (si.custom_report_service_cid IS NULL OR si.custom_report_service_cid = '')
              AND si.docstatus = 1
        """, branch_cids + [from_date, to_date], as_dict=True)
        for r in rows:
            orphans.append({
                "cid": "-",
                "branch": "-",
                "category": "Filial (belgisiz)",
                "expected_doctype": "Sales Invoice",
                "status": "TEGSIZ HUJJAT (tekshiring)",
                "erpnext_doc": r.name,
                "expected_amount": None,
                "actual_amount": r.grand_total,
            })

    rows = frappe.db.sql("""
        SELECT pi.name, pi.grand_total
        FROM `tabPurchase Invoice` pi
        INNER JOIN `tabSupplier` s ON s.name = pi.supplier
        WHERE s.custom_report_service_client_cid IS NOT NULL AND s.custom_report_service_client_cid != ''
          AND pi.posting_date BETWEEN %s AND %s
          AND (pi.custom_report_service_cid IS NULL OR pi.custom_report_service_cid = '')
          AND pi.docstatus = 1
    """, [from_date, to_date], as_dict=True)
    for r in rows:
        orphans.append({
            "cid": "-",
            "branch": "-",
            "category": "Baza (belgisiz)",
            "expected_doctype": "Purchase Invoice",
            "status": "TEGSIZ HUJJAT (tekshiring)",
            "erpnext_doc": r.name,
            "expected_amount": None,
            "actual_amount": r.grand_total,
        })

    return orphans


def get_columns():
    return [
        {"label": "CID", "fieldname": "cid", "fieldtype": "Data", "width": 90},
        {"label": "Filial", "fieldname": "branch", "fieldtype": "Data", "width": 100},
        {"label": "Toifa", "fieldname": "category", "fieldtype": "Data", "width": 130},
        {"label": "Kutilgan hujjat turi", "fieldname": "expected_doctype", "fieldtype": "Data", "width": 130},
        {"label": "Holat", "fieldname": "status", "fieldtype": "Data", "width": 200},
        {
            "label": "ERPNext hujjat",
            "fieldname": "erpnext_doc",
            "fieldtype": "Dynamic Link",
            "options": "expected_doctype",
            "width": 160,
        },
        {"label": "Kutilgan summa", "fieldname": "expected_amount", "fieldtype": "Currency", "width": 120},
        {"label": "Haqiqiy summa", "fieldname": "actual_amount", "fieldtype": "Currency", "width": 120},
    ]
