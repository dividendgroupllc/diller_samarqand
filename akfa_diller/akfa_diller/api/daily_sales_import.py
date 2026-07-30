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
    validate_customers_exist,
    check_duplicate_import,
)
from akfa_diller.akfa_diller.services import excel_service, invoice_service, InvoiceConfig

DOCTYPE = "Sales Import"


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
    Guruhlash kaliti: (Mijoz matni, Sana) juftligi. Manba ma'lumotida "Chek №"
    kabi alohida hujjat raqami yo'q -- shuning uchun bitta Mijoz + bitta Sana =
    bitta Sales Invoice. Xuddi shu mijoz boshqa kunda yana xarid qilsa, bu
    ALOHIDA guruh -- alohida Sales Invoice bo'ladi.
    """
    customer = (item.get("customer") or "").strip()
    date = item.get("date") or ""
    return f"{customer}@{date}"


def _group_by_customer_date(items: List[Dict]) -> "OrderedDict[str, List[Dict]]":
    """Group Excel rows by (Mijoz, Sana), preserving first-seen order."""
    groups = OrderedDict()
    for item in items:
        groups.setdefault(_group_key(item), []).append(item)
    return groups


def _group_customer_text(rows: List[Dict]) -> str:
    return (rows[0].get("customer") or "").strip()


def _group_date(rows: List[Dict]) -> str:
    return rows[0].get("date") or ""


def _distinct_customer_texts(groups: "OrderedDict[str, List[Dict]]") -> List[str]:
    """Noyob Mijoz matnlari ro'yxati, validate_customers_exist() uchun."""
    return list({_group_customer_text(rows) for rows in groups.values()})


@frappe.whitelist()
def get_preview_data(doc_name: str) -> Dict:
    """Get preview of Excel data (grouped by Mijoz+Sana) before processing."""
    doc = frappe.get_doc(DOCTYPE, doc_name)

    if not doc.excel_file:
        return {"success": False, "message": _("Excel fayl yuklanmagan")}

    try:
        excel_data = excel_service.read_report(doc.excel_file)
        items = excel_data["items"]

        validation = validate_items_exist(items)
        valid_items = validation["valid_items"]
        not_found_count = len(items) - len(valid_items)

        date_validation = validate_dates(valid_items)
        # Rows without a usable date can't be grouped into a real invoice --
        # exclude them from the preview grouping, but keep their errors visible.
        dated_items = [i for i in valid_items if i.get("date")]

        groups = _group_by_customer_date(dated_items)
        customer_validation = validate_customers_exist(_distinct_customer_texts(groups))
        resolved_customers = customer_validation["resolved"]

        receipts = []
        for rows in groups.values():
            customer_text = _group_customer_text(rows)
            totals = invoice_service.calculate_totals(rows)
            receipts.append({
                "date": _group_date(rows),
                "customer_input": customer_text,
                "customer": resolved_customers.get(customer_text),
                "customer_matched": customer_text in resolved_customers,
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
            "customers_not_found": len(customer_validation["errors"]),
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
        excel_data = excel_service.read_report(doc.excel_file)
        items = excel_data["items"]

        if not items:
            return {"success": False, "message": _("Excel faylda sotuv topilmadi"), "errors": [], "items": []}

        validation = validate_items_exist(items)
        date_validation = validate_dates(validation["valid_items"])
        dated_items = [i for i in validation["valid_items"] if i.get("date")]
        groups = _group_by_customer_date(dated_items)
        customer_validation = validate_customers_exist(_distinct_customer_texts(groups))

        excel_hash = calculate_file_hash(doc.excel_file)
        duplicate = check_duplicate_import(excel_hash, doc_name)

        errors = validation["errors"] + date_validation["errors"] + customer_validation["errors"]
        if duplicate["is_duplicate"]:
            errors.insert(0, {
                "row": 0,
                "item_name": "",
                "error": _("Bu Excel avval import qilingan: {0}").format(duplicate["existing_doc"]),
            })

        totals = invoice_service.calculate_totals(validation["valid_items"])
        receipt_count = len(groups)

        return {
            "success": len(errors) == 0,
            "message": _("{0} ta tovar, {1} ta Sales Invoice (Mijoz+Sana), {2} ta xato topildi").format(
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
    duplicate = check_duplicate_import(excel_hash, doc_name)
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
        event = "akfa_daily_sales_success" if result["success"] else "akfa_daily_sales_failed"
        frappe.publish_realtime(event, {"doc_name": doc_name, "result": result}, doctype=DOCTYPE, docname=doc_name)
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error("Daily Sales Import", f"Import Error: {doc_name}\n{str(e)}")
        try:
            doc = frappe.get_doc(DOCTYPE, doc_name)
            doc.db_set("status", "Failed")
            doc.db_set("error_log", str(e))
            frappe.db.commit()
        except Exception:
            pass
        frappe.publish_realtime(
            "akfa_daily_sales_failed",
            {"doc_name": doc_name, "result": {"success": False, "message": str(e)}},
            doctype=DOCTYPE,
            docname=doc_name,
        )


def _process_import_sync(doc_name: str) -> Dict:
    """Synchronous import processing — one Sales Invoice per (Mijoz, Sana) group."""
    doc = frappe.get_doc(DOCTYPE, doc_name)

    log_lines = []

    def log(msg, publish=True):
        log_lines.append(msg)
        current_log = "\n".join(log_lines)
        frappe.db.set_value(DOCTYPE, doc_name, "import_log", current_log, update_modified=False)
        if publish:
            frappe.publish_realtime(
                "akfa_daily_sales_log",
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
        excel_data = excel_service.read_report(doc.excel_file)
        items = excel_data["items"]
        if not items:
            raise Exception(_("Excel faylda sotuv topilmadi"))
        log(f"   ✅ {len(items)} ta qator o'qildi")

        log("\n🔍 3. Dublikat tekshiruvi...")
        excel_hash = calculate_file_hash(doc.excel_file)
        duplicate = check_duplicate_import(excel_hash, doc_name)
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

        groups = _group_by_customer_date(valid_items)
        group_keys = list(groups.keys())
        log(f"   🧾 Jami {len(group_keys)} ta Sales Invoice (Mijoz+Sana) aniqlandi")

        log("\n👤 6. Mijozlar tekshirilmoqda...")
        customer_validation = validate_customers_exist(_distinct_customer_texts(groups))
        if customer_validation["errors"]:
            for e in customer_validation["errors"]:
                log(f"   ❌ {e['error']}")
            raise Exception(_("Mijozlarni tekshirishda xatolik yuz berdi. Logga qarang."))

        resolved_customers = customer_validation["resolved"]
        log(f"   ✅ {len(resolved_customers)} ta mijoz topildi")

        all_si_names = []
        total_amount = 0.0

        # Resume support: skip (Mijoz, Sana) groups already committed in a
        # previous (failed) run.
        already_done = set()
        if doc.sales_invoice:
            all_si_names.extend([s.strip() for s in doc.sales_invoice.split(",") if s.strip()])
        if doc.processed_groups:
            already_done = {r.strip() for r in doc.processed_groups.split(",") if r.strip()}

        if already_done:
            log(f"   ♻️ Resume: {len(already_done)} ta guruh oldin bajarilgan, o'tkazib yuboriladi")

        for idx, key in enumerate(group_keys, 1):
            if key in already_done:
                continue

            rows = groups[key]
            customer_text = _group_customer_text(rows)
            group_date = _group_date(rows)
            log(f"\n--- [{idx}/{len(group_keys)}] MIJOZ: {customer_text} ({group_date}, {len(rows)} ta tovar) ---")

            try:
                invoice_config = InvoiceConfig(
                    company=doc.company,
                    warehouse=doc.warehouse,
                    posting_date=group_date,
                    customer=resolved_customers[customer_text],
                    cost_center=doc.cost_center,
                )
                si_name = invoice_service.create_sales_invoice(rows, invoice_config, submit=True)
                all_si_names.append(si_name)
                already_done.add(key)
                log(f"   ✅ Sales Invoice yaratildi: {si_name}")

                doc.db_set("sales_invoice", ", ".join(all_si_names))
                doc.db_set("processed_groups", ", ".join(sorted(already_done)))

                totals = invoice_service.calculate_totals(rows)
                total_amount += totals["total_amount"]

                frappe.db.commit()

            except Exception as group_err:
                frappe.db.rollback()
                log(f"   ❌ MIJOZ {customer_text} ({group_date}) BO'YICHA XATO: {str(group_err)}")
                doc.reload()
                raise group_err

        doc.db_set("status", "Processed")

        log("\n" + "=" * 50)
        log("✅ IMPORT MUVAFFAQIYATLI YAKUNLANDI")
        log("=" * 50)
        log(f"🧾 Jami Sales Invoice: {len(group_keys)}")
        log(f"💰 Jami summa: {total_amount:,.0f}")

        frappe.db.commit()

        return {
            "success": True,
            "message": _("Import muvaffaqiyatli"),
            "sales_invoice": all_si_names,
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
        frappe.log_error("Daily Sales Import", f"Import Error: {doc_name}\n{str(e)}")
        return {"success": False, "message": str(e)}


@frappe.whitelist()
def cancel_import(doc_name: str) -> Dict:
    """Cancel a processed import (cancels every Sales Invoice it created)."""
    doc = frappe.get_doc(DOCTYPE, doc_name)

    if doc.status not in ["Processed", "Failed", "Processing"]:
        return {
            "success": False,
            "message": _("Faqat 'Processed', 'Failed' yoki 'Processing' statusdagi importni bekor qilish mumkin"),
        }

    try:
        if doc.sales_invoice:
            si_names = [si.strip() for si in doc.sales_invoice.split(",") if si.strip()]
            for si in si_names:
                invoice_service.cancel_invoice(si)

        doc.db_set("status", "Draft")
        doc.db_set("external_ref", "")
        doc.db_set("import_log", "")
        doc.db_set("sales_invoice", "")
        doc.db_set("processed_groups", "")
        doc.db_set("error_log", "")

        frappe.db.commit()
        return {"success": True, "message": _("Import bekor qilindi")}
    except Exception as e:
        frappe.db.rollback()
        return {"success": False, "message": str(e)}
