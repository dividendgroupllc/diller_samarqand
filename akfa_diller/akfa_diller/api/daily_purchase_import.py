from typing import Dict, List
from collections import OrderedDict

import frappe
from frappe import _
from frappe.utils import nowdate

from akfa_diller.akfa_diller.utils import (
    calculate_file_hash,
    validate_import_prerequisites,
    validate_items_exist,
    validate_dates,
    validate_suppliers_exist,
    check_duplicate_import,
)
from akfa_diller.akfa_diller.services import purchase_excel_service, purchase_invoice_service, PurchaseInvoiceConfig

DOCTYPE = "Purchase Import"


@frappe.whitelist()
def get_default_warehouse(company: str) -> Dict:
    """Get default warehouse for a company."""
    if not company:
        return {"warehouse": None}

    warehouse = frappe.db.get_value(
        "Warehouse",
        {"company": company, "is_group": 0, "warehouse_type": "Stores"},
        "name",
    )

    if not warehouse:
        warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")

    return {"warehouse": warehouse}


def _group_key(item: Dict) -> str:
    """
    Guruhlash kaliti: (Ta'minotchi matni, Sana) juftligi. Manba ma'lumotida
    "Chek №" kabi alohida hujjat raqami yo'q -- shuning uchun bitta
    Ta'minotchi + bitta Sana = bitta Purchase Invoice. Xuddi shu ta'minotchidan
    boshqa kunda yana xarid qilinsa, bu ALOHIDA guruh -- alohida Purchase
    Invoice bo'ladi.
    """
    supplier = (item.get("customer") or "").strip()
    date = item.get("date") or ""
    return f"{supplier}@{date}"


def _group_by_supplier_date(items: List[Dict]) -> "OrderedDict[str, List[Dict]]":
    """Group Excel rows by (Ta'minotchi, Sana), preserving first-seen order."""
    groups = OrderedDict()
    for item in items:
        groups.setdefault(_group_key(item), []).append(item)
    return groups


def _group_supplier_text(rows: List[Dict]) -> str:
    return (rows[0].get("customer") or "").strip()


def _group_date(rows: List[Dict]) -> str:
    return rows[0].get("date") or ""


def _distinct_supplier_texts(groups: "OrderedDict[str, List[Dict]]") -> List[str]:
    """Noyob Ta'minotchi matnlari ro'yxati, validate_suppliers_exist() uchun."""
    return list({_group_supplier_text(rows) for rows in groups.values()})


@frappe.whitelist()
def get_preview_data(doc_name: str) -> Dict:
    """Get preview of Excel data (grouped by Ta'minotchi+Sana) before processing."""
    doc = frappe.get_doc(DOCTYPE, doc_name)

    if not doc.excel_file:
        return {"success": False, "message": _("Excel fayl yuklanmagan")}

    try:
        excel_data = purchase_excel_service.read_report(doc.excel_file)
        items = excel_data["items"]

        validation = validate_items_exist(items)
        valid_items = validation["valid_items"]
        not_found_count = len(items) - len(valid_items)

        date_validation = validate_dates(valid_items)
        # Rows without a usable date can't be grouped into a real invoice --
        # exclude them from the preview grouping, but keep their errors visible.
        dated_items = [i for i in valid_items if i.get("date")]

        groups = _group_by_supplier_date(dated_items)
        supplier_validation = validate_suppliers_exist(_distinct_supplier_texts(groups))
        resolved_suppliers = supplier_validation["resolved"]

        receipts = []
        for rows in groups.values():
            supplier_text = _group_supplier_text(rows)
            totals = purchase_invoice_service.calculate_totals(rows)
            receipts.append({
                "date": _group_date(rows),
                "supplier_input": supplier_text,
                "supplier": resolved_suppliers.get(supplier_text),
                "supplier_matched": supplier_text in resolved_suppliers,
                "items": rows,
                "total_qty": totals["total_qty"],
                "total_amount": totals["total_amount"],
            })

        summary = {
            "total_items": len(items),
            "found": len(valid_items),
            "not_found": not_found_count,
            "date_errors": len(date_validation["errors"]),
            "total_receipts": len(receipts),
            "suppliers_not_found": len(supplier_validation["errors"]),
            "total_amount": sum(r["total_amount"] for r in receipts),
        }

        return {
            "success": True,
            "receipts": receipts,
            "summary": summary,
            "date_errors": date_validation["errors"],
        }

    except Exception as e:
        return {"success": False, "message": str(e)}


@frappe.whitelist()
def validate_excel_items(doc_name: str) -> Dict:
    """Validate items in the uploaded Excel file."""
    doc = frappe.get_doc(DOCTYPE, doc_name)

    if not doc.excel_file:
        return {"success": False, "message": _("Excel fayl yuklanmagan"), "errors": [], "items": []}

    try:
        excel_data = purchase_excel_service.read_report(doc.excel_file)
        items = excel_data["items"]

        if not items:
            return {"success": False, "message": _("Excel faylda xarid topilmadi"), "errors": [], "items": []}

        validation = validate_items_exist(items)
        date_validation = validate_dates(validation["valid_items"])
        dated_items = [i for i in validation["valid_items"] if i.get("date")]
        groups = _group_by_supplier_date(dated_items)
        supplier_validation = validate_suppliers_exist(_distinct_supplier_texts(groups))

        excel_hash = calculate_file_hash(doc.excel_file)
        duplicate = check_duplicate_import(excel_hash, doc_name, DOCTYPE, "purchase_invoice")

        errors = validation["errors"] + date_validation["errors"] + supplier_validation["errors"]
        if duplicate["is_duplicate"]:
            errors.insert(0, {
                "row": 0,
                "item_name": "",
                "error": _("Bu Excel avval import qilingan: {0}").format(duplicate["existing_doc"]),
            })

        totals = purchase_invoice_service.calculate_totals(validation["valid_items"])
        receipt_count = len(groups)

        return {
            "success": len(errors) == 0,
            "message": _("{0} ta tovar, {1} ta Purchase Invoice (Ta'minotchi+Sana), {2} ta xato topildi").format(
                len(validation["valid_items"]), receipt_count, len(errors)
            ),
            "errors": errors,
            "items": validation["valid_items"],
            "total_qty": totals["total_qty"],
            "total_amount": totals["total_amount"],
        }

    except Exception as e:
        return {"success": False, "message": str(e), "errors": [], "items": []}


@frappe.whitelist()
def process_import(doc_name: str) -> Dict:
    """Process the import — always runs in background to prevent HTTP timeout."""
    doc = frappe.get_doc(DOCTYPE, doc_name)

    if doc.status == "Processed":
        return {"success": False, "message": _("Bu import allaqachon bajarilgan")}

    if doc.status == "Processing":
        return {"success": False, "message": _("Import hozir jarayonda")}

    if not doc.excel_file:
        return {"success": False, "message": _("Excel fayl yuklanmagan")}

    validation = validate_import_prerequisites(
        doc.company, doc.warehouse, str(doc.posting_date), doc.cost_center or ""
    )
    if not validation["success"]:
        return {"success": False, "message": validation["message"]}

    excel_hash = calculate_file_hash(doc.excel_file)
    duplicate = check_duplicate_import(excel_hash, doc_name, DOCTYPE, "purchase_invoice")
    if duplicate["is_duplicate"]:
        return {
            "success": False,
            "message": _("Bu Excel avval import qilingan: {0}").format(duplicate["existing_doc"]),
        }

    # Mark as Processing before enqueueing to prevent double-clicks. Stamp
    # external_ref (unique field) here too, not just on success -- if two
    # requests for the same file race past the check above nearly at once,
    # the second one's db_set below hits the field's DB-level unique
    # constraint instead of silently starting a duplicate run.
    doc.db_set("status", "Processing")
    doc.db_set("error_log", "")
    doc.db_set("import_log", "")
    try:
        doc.db_set("external_ref", excel_hash)
    except Exception:
        frappe.db.rollback()
        return {
            "success": False,
            "message": _("Bu Excel bir vaqtning o'zida boshqa joyda import qilinmoqda"),
        }
    frappe.db.commit()

    frappe.enqueue(_process_import_job, queue="long", timeout=3600, doc_name=doc_name)
    return {"success": True, "message": _("Import fonada boshlandi. Sahifani yangilab turing.")}


def _process_import_job(doc_name: str):
    """Background job wrapper."""
    try:
        result = _process_import_sync(doc_name)
        event = "akfa_daily_purchase_success" if result["success"] else "akfa_daily_purchase_failed"
        frappe.publish_realtime(event, {"doc_name": doc_name, "result": result}, doctype=DOCTYPE, docname=doc_name)
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error("Daily Purchase Import", f"Import Error: {doc_name}\n{str(e)}")
        try:
            doc = frappe.get_doc(DOCTYPE, doc_name)
            doc.db_set("status", "Failed")
            doc.db_set("error_log", str(e))
            frappe.db.commit()
        except Exception:
            pass
        frappe.publish_realtime(
            "akfa_daily_purchase_failed",
            {"doc_name": doc_name, "result": {"success": False, "message": str(e)}},
            doctype=DOCTYPE,
            docname=doc_name,
        )


def _process_import_sync(doc_name: str) -> Dict:
    """Synchronous import processing — one Purchase Invoice per (Ta'minotchi, Sana) group."""
    doc = frappe.get_doc(DOCTYPE, doc_name)

    log_lines = []

    def log(msg, publish=True):
        log_lines.append(msg)
        current_log = "\n".join(log_lines)
        frappe.db.set_value(DOCTYPE, doc_name, "import_log", current_log, update_modified=False)
        if publish:
            frappe.publish_realtime(
                "akfa_daily_purchase_log",
                {"doc_name": doc_name, "msg": msg, "full_log": current_log},
                doctype=DOCTYPE,
                docname=doc_name,
            )

    if doc.status == "Processed":
        return {"success": False, "message": _("Bu import allaqachon bajarilgan")}

    if doc.status != "Processing":
        doc.db_set("status", "Processing")
        doc.db_set("error_log", "")
        frappe.db.commit()

    log("=" * 50)
    log(f"IMPORT BOSHLANDI: {nowdate()}")
    log(f"Company: {doc.company}")
    log(f"Ombor: {doc.warehouse}")
    log(f"Cost Center: {doc.cost_center}")
    log("=" * 50)

    try:
        log("\n📋 1. Tekshiruvlar...")
        validation = validate_import_prerequisites(
            doc.company, doc.warehouse, str(doc.posting_date), doc.cost_center or ""
        )
        if not validation["success"]:
            raise Exception(validation["message"])

        log("\n📊 2. Excel o'qilmoqda...")
        excel_data = purchase_excel_service.read_report(doc.excel_file)
        items = excel_data["items"]
        if not items:
            raise Exception(_("Excel faylda xarid topilmadi"))
        log(f"   ✅ {len(items)} ta qator o'qildi")

        log("\n🔍 3. Dublikat tekshiruvi...")
        excel_hash = calculate_file_hash(doc.excel_file)
        duplicate = check_duplicate_import(excel_hash, doc_name, DOCTYPE, "purchase_invoice")
        if duplicate["is_duplicate"]:
            raise Exception(_("Bu Excel avval import qilingan: {0}").format(duplicate["existing_doc"]))

        # Stamp the hash NOW (not only on full success) so that if this run
        # fails partway through the loop below, the doc still truthfully
        # records which file it was working on -- check_duplicate_import() can
        # then correctly flag a re-upload of the same file as a duplicate.
        # (process_import() already sets this before enqueueing in the normal
        # flow; re-setting the same value here is a harmless no-op then.)
        doc.db_set("external_ref", excel_hash)

        log("\n🔗 4. Tovarlar tekshirilmoqda...")
        item_validation = validate_items_exist(items)
        if item_validation["errors"]:
            for e in item_validation["errors"]:
                log(f"   ❌ Qator {e['row']}: {e['error']}")
            raise Exception(_("Tovarlarni tekshirishda xatolik yuz berdi. Logga qarang."))

        valid_items = item_validation["valid_items"]
        log(f"   ✅ {len(valid_items)} ta tovar topildi")

        log("\n📅 5. Sanalar tekshirilmoqda...")
        date_validation = validate_dates(valid_items)
        if date_validation["errors"]:
            for e in date_validation["errors"]:
                log(f"   ❌ {e['error']}")
            raise Exception(_("Sanalarni tekshirishda xatolik yuz berdi. Logga qarang."))
        log(f"   ✅ Barcha qatorlarda Sana bor")

        groups = _group_by_supplier_date(valid_items)
        group_keys = list(groups.keys())
        log(f"   🧾 Jami {len(group_keys)} ta Purchase Invoice (Ta'minotchi+Sana) aniqlandi")

        log("\n👤 6. Ta'minotchilar tekshirilmoqda...")
        supplier_validation = validate_suppliers_exist(_distinct_supplier_texts(groups))
        if supplier_validation["errors"]:
            for e in supplier_validation["errors"]:
                log(f"   ❌ {e['error']}")
            raise Exception(_("Ta'minotchilarni tekshirishda xatolik yuz berdi. Logga qarang."))

        resolved_suppliers = supplier_validation["resolved"]
        log(f"   ✅ {len(resolved_suppliers)} ta ta'minotchi topildi")

        all_pi_names = []
        total_amount = 0.0

        # Resume support: skip (Ta'minotchi, Sana) groups already committed in
        # a previous (failed) run.
        already_done = set()
        if doc.purchase_invoice:
            all_pi_names.extend([s.strip() for s in doc.purchase_invoice.split(",") if s.strip()])
        if doc.processed_groups:
            already_done = {r.strip() for r in doc.processed_groups.split(",") if r.strip()}

        if already_done:
            log(f"   ♻️ Resume: {len(already_done)} ta guruh oldin bajarilgan, o'tkazib yuboriladi")

        for idx, key in enumerate(group_keys, 1):
            if key in already_done:
                continue

            rows = groups[key]
            supplier_text = _group_supplier_text(rows)
            group_date = _group_date(rows)
            log(f"\n--- [{idx}/{len(group_keys)}] TA'MINOTCHI: {supplier_text} ({group_date}, {len(rows)} ta tovar) ---")

            try:
                invoice_config = PurchaseInvoiceConfig(
                    company=doc.company,
                    warehouse=doc.warehouse,
                    posting_date=group_date,
                    supplier=resolved_suppliers[supplier_text],
                    cost_center=doc.cost_center,
                )
                pi_name = purchase_invoice_service.create_purchase_invoice(rows, invoice_config, submit=True)
                all_pi_names.append(pi_name)
                already_done.add(key)
                log(f"   ✅ Purchase Invoice yaratildi: {pi_name}")

                doc.db_set("purchase_invoice", ", ".join(all_pi_names))
                doc.db_set("processed_groups", ", ".join(sorted(already_done)))

                totals = purchase_invoice_service.calculate_totals(rows)
                total_amount += totals["total_amount"]

                frappe.db.commit()

            except Exception as group_err:
                frappe.db.rollback()
                log(f"   ❌ TA'MINOTCHI {supplier_text} ({group_date}) BO'YICHA XATO: {str(group_err)}")
                doc.reload()
                raise group_err

        doc.db_set("status", "Processed")

        log("\n" + "=" * 50)
        log("✅ IMPORT MUVAFFAQIYATLI YAKUNLANDI")
        log("=" * 50)
        log(f"🧾 Jami Purchase Invoice: {len(group_keys)}")
        log(f"💰 Jami summa: {total_amount:,.0f}")

        frappe.db.commit()

        return {
            "success": True,
            "message": _("Import muvaffaqiyatli"),
            "purchase_invoice": all_pi_names,
            "total_items": len(valid_items),
            "total_receipts": len(group_keys),
            "total_amount": total_amount,
        }

    except Exception as e:
        frappe.db.rollback()
        log(f"\n❌ XATO: {str(e)}")
        doc.reload()
        doc.db_set("status", "Failed")
        doc.db_set("error_log", str(e))
        frappe.db.commit()
        frappe.log_error("Daily Purchase Import", f"Import Error: {doc_name}\n{str(e)}")
        return {"success": False, "message": str(e)}


@frappe.whitelist()
def cancel_import(doc_name: str) -> Dict:
    """Cancel a processed import (cancels every Purchase Invoice it created)."""
    doc = frappe.get_doc(DOCTYPE, doc_name)

    if doc.status not in ["Processed", "Failed", "Processing"]:
        return {
            "success": False,
            "message": _("Faqat 'Processed', 'Failed' yoki 'Processing' statusdagi importni bekor qilish mumkin"),
        }

    try:
        if doc.purchase_invoice:
            pi_names = [pi.strip() for pi in doc.purchase_invoice.split(",") if pi.strip()]
            for pi in pi_names:
                purchase_invoice_service.cancel_invoice(pi)

        doc.db_set("status", "Draft")
        doc.db_set("external_ref", "")
        doc.db_set("import_log", "")
        doc.db_set("purchase_invoice", "")
        doc.db_set("processed_groups", "")
        doc.db_set("error_log", "")

        frappe.db.commit()
        return {"success": True, "message": _("Import bekor qilindi")}
    except Exception as e:
        frappe.db.rollback()
        return {"success": False, "message": str(e)}
