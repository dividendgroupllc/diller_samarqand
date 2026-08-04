from typing import Dict, List, Optional

import frappe
from frappe import _


class ValidationError(Exception):
    """Custom exception for validation errors."""
    pass


def validate_import_prerequisites(
    company: str,
    warehouse: str,
    posting_date: str,
    cost_center: str = "",
) -> Dict:
    """
    Validate all prerequisites before import.

    NOTE: posting_date here is the PARENT doc's nominal/reference date only
    (which day this import batch is filed under) -- it is never used to fill
    in a missing per-row date. Every Excel row's OWN "Sana" is mandatory (see
    validate_dates()); each Sales Invoice is posted on its row's real date.
    """
    errors = []
    if not company:
        errors.append(_("Компания танланмаган"))
    if not warehouse:
        errors.append(_("Ombor tanlanmagan"))
    # Callers pass str(doc.posting_date); if the field were ever genuinely
    # empty that becomes the truthy string "None", so check for that too.
    if not posting_date or posting_date == "None":
        errors.append(_("Sana tanlanmagan"))
    if not cost_center:
        errors.append(_("Cost Center tanlanmagan"))

    if company and warehouse:
        wh_company = frappe.db.get_value("Warehouse", warehouse, "company")
        if wh_company and wh_company != company:
            errors.append(_("Ombor '{0}' kompaniyaga tegishli emas: {1}").format(warehouse, company))

    if company and cost_center:
        cc = frappe.db.get_value("Cost Center", cost_center, ["company", "is_group"], as_dict=True)
        if not cc or cc.company != company:
            errors.append(_("Cost Center '{0}' kompaniyaga tegishli emas: {1}").format(cost_center, company))
        elif cc.is_group:
            errors.append(_("Guruh (group) Cost Center tanlab bo'lmaydi: {0}").format(cost_center))

    return {"success": len(errors) == 0, "message": "\n".join(errors), "errors": errors}


def validate_warehouse_company(warehouse: str, company: str):
    """Validate that warehouse belongs to the given company."""
    wh_company = frappe.db.get_value("Warehouse", warehouse, "company")
    if wh_company and wh_company != company:
        raise ValidationError(
            _("Ombor '{0}' kompaniyaga tegishli emas: {1}").format(warehouse, company)
        )


def validate_items_exist(items: List[Dict]) -> Dict:
    """Validate that all items exist in ERPNext (exact match, then partial match)."""
    valid_items = []
    errors = []

    # Cache for found items to speed up large files
    item_cache = {}

    for item in items:
        original_name = (item.get("item_name") or "").strip()
        row_num = item.get("row_num", 0)

        if not original_name:
            continue

        if original_name in item_cache:
            item_code = item_cache[original_name]
        else:
            # 1. Try exact match on name (Item Code)
            item_code = frappe.db.get_value("Item", {"name": original_name}, "name")

            # 2. Try exact match on item_name
            if not item_code:
                item_code = frappe.db.get_value("Item", {"item_name": original_name}, "name")

            # 3. Try partial match if still not found -- order by closest name
            # length so this is deterministic (the shortest superset of
            # original_name is the most likely correct match) rather than
            # whichever row the DB happens to return first among several
            # substring collisions.
            if not item_code:
                item_code = frappe.db.get_value(
                    "Item", {"item_name": ["like", f"%{original_name}%"]}, "name",
                    order_by="length(item_name) asc",
                )

            item_cache[original_name] = item_code

        if item_code:
            item["item_code"] = item_code
            item["found"] = True
            valid_items.append(item)
        else:
            errors.append({
                "row": row_num,
                "item_name": original_name,
                "error": _("Tovar topilmadi: '{0}'").format(original_name),
            })

    return {
        "valid_items": valid_items,
        "errors": errors,
        "success": len(errors) == 0,
    }


def validate_dates(items: List[Dict]) -> Dict:
    """
    Har bir qatorda haqiqiy (Excel'ning o'zidan o'qilgan) Sana borligini
    tekshiradi. Sana endi MAJBURIY qator ma'lumoti -- import shaklidagi
    umumiy sana buning o'rnini bosmaydi. Bo'sh yoki noto'g'ri formatdagi
    sana -- tovar/mijoz kabi QATTIQ xato, butun importni to'xtatadi.
    """
    errors = []
    for item in items:
        if not item.get("date"):
            row_num = item.get("row_num", 0)
            errors.append({
                "row": row_num,
                "item_name": item.get("item_name", ""),
                "error": _("Qator {0}: Sana ko'rsatilmagan yoki formati noto'g'ri").format(row_num),
            })

    return {"errors": errors, "success": len(errors) == 0}


def _validate_party_exists(party_texts: List[str], doctype: str, name_field: str, party_label: str) -> Dict:
    """
    Excel'dan o'qilgan har bir xil tomon (Mijoz/Ta'minotchi) matnini mavjud
    Customer/Supplier bilan solishtiradi -- FAQAT ANIQ moslik (<doctype>.name
    yoki <doctype>.<name_field>), hech qanday qisman/LIKE moslik yo'q (erkin
    matn bo'lgani uchun taxminiy moslik noto'g'ri tomonni tanlab qo'yishi
    mumkin).

    Tovarlar tekshiruvi kabi QATTIQ: mos kelmasa yoki bo'sh bo'lsa -- xato,
    hech qanday standart/fallback tomon ishlatilmaydi. Foydalanuvchi avval
    yozuvni tizimga qo'shishi (yoki Excel'dagi nomni to'g'rilashi) kerak.

    Returns:
        Dict with keys: resolved ({party_text: real doctype name}), errors, success
    """
    resolved = {}
    errors = []

    for text in set(party_texts):
        text = (text or "").strip()

        if not text:
            errors.append({
                "row": 0,
                "item_name": "",
                "error": _("{0} ko'rsatilmagan").format(party_label),
            })
            continue

        party = frappe.db.get_value(doctype, {"name": text}, "name")
        if not party:
            party = frappe.db.get_value(doctype, {name_field: text}, "name")

        if party:
            resolved[text] = party
        else:
            errors.append({
                "row": 0,
                "item_name": "",
                "error": _("{0} topilmadi: '{1}'").format(party_label, text),
            })

    return {"resolved": resolved, "errors": errors, "success": len(errors) == 0}


def validate_customers_exist(customer_texts: List[str]) -> Dict:
    """See _validate_party_exists() -- Customer-specific wrapper (Sales side)."""
    return _validate_party_exists(customer_texts, "Customer", "customer_name", _("Mijoz"))


def validate_suppliers_exist(supplier_texts: List[str]) -> Dict:
    """See _validate_party_exists() -- Supplier-specific wrapper (Purchase side)."""
    return _validate_party_exists(supplier_texts, "Supplier", "supplier_name", _("Ta'minotchi"))


def check_duplicate_import(
    excel_hash: str,
    current_doc_name: str,
    import_doctype: str = "Sales Import",
    invoice_fieldname: str = "sales_invoice",
) -> Dict:
    """
    Check if this Excel file was already imported (or is currently being
    imported) by another doc of the given import_doctype (Daily Sales Import
    or Daily Purchase Import both use this, each with its own doctype name
    and invoice-list fieldname).

    "Processing"/"Processed" always block (running or fully done). A
    "Failed" doc also blocks IF it already committed at least one invoice
    before erroring out (see _process_import_sync's per-group commits) --
    otherwise re-uploading the same file into a brand-new doc would silently
    re-create those already-committed invoices. A Failed doc with zero
    progress does not block, since nothing would actually be duplicated. The
    correct recovery path for a Failed doc with progress is to Retry it
    (resumes via processed_groups), not start a new one.
    """
    if not excel_hash:
        return {"is_duplicate": False, "existing_doc": None}

    candidates = frappe.get_all(
        import_doctype,
        filters={"external_ref": excel_hash, "name": ["!=", current_doc_name]},
        fields=["name", "status", invoice_fieldname],
    )

    for candidate in candidates:
        if candidate.status in ("Processing", "Processed"):
            return {"is_duplicate": True, "existing_doc": candidate.name}
        if candidate.status == "Failed" and (candidate.get(invoice_fieldname) or "").strip():
            return {"is_duplicate": True, "existing_doc": candidate.name}

    return {"is_duplicate": False, "existing_doc": None}
