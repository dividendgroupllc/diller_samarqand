import re
from datetime import datetime
from typing import Dict, List, Optional

import frappe
from frappe import _
from frappe.utils import nowdate

from akfa_diller.akfa_diller.utils.helpers import calculate_file_hash
from akfa_diller.akfa_diller.utils.validators import check_duplicate_import
from akfa_diller.akfa_diller.services import kassa_excel_service

DOCTYPE = "Kassa Import"

# Each real branch has its own distinct Karta/Plastik cash account (confirmed
# live 2026-08-08 -- Samarqand Diller shares one across all its sub-branches,
# but the Kattaqo'rg'on network has 4 separate ones). Naqt rows keep using
# whatever Mode of Payment the user picked on the Kassa Import doc itself
# (unchanged); only Plastik rows need this extra lookup. Keyed the same way
# _resolve_mode_of_payment reads it: (company, branch-or-None).
PLASTIK_MODE_OF_PAYMENT = {
    ("Samarqand Diller", None): "Plastik Samarqand diller",
    ("Kattaqo'rg'on", "Ishtixon"): "Plastik Ishtixon - K",
    ("Kattaqo'rg'on", "Mitan"): "Plastik Mitan - K",
    ("Kattaqo'rg'on", "Mirbozor"): "Plastik Mirbozor - K",
    ("Kattaqo'rg'on", "Kattaqo'rg'on"): "Plastik Kattaqo'rg'on - K",
}


_DATE_FORMATS = ("%d.%m.%Y", "%d-%m-%Y")  # "payments" format uses dots, "receipts list" format uses dashes


def _parse_date(date_str: str) -> str:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    frappe.throw(_("Sana formati tanilmadi: {0!r}").format(date_str))


def _is_expense_flow(row: Dict) -> bool:
    """A row is an outflow (Расход) either because its amount is negative
    (the only real-data case seen so far) OR because the source explicitly
    tagged it PAYMENT (money out) regardless of the amount's sign -- the two
    sample files' active PAYMENT rows all happened to be soft-deleted, so a
    PAYMENT row with a POSITIVE amount was never exercised against real data
    and would otherwise have been silently booked as a customer receipt."""
    return row["amount"] < 0 or row.get("payment_type") == "PAYMENT"


def _resolve_mode_of_payment(doc, payment_account: str) -> str:
    """Naqt rows always use the doc's own selected `mode_of_payment` (unchanged
    behavior). Plastik rows are routed to a separate, per-(company, branch)
    Mode of Payment -- see PLASTIK_MODE_OF_PAYMENT."""
    if payment_account != "PLASTIK":
        return doc.mode_of_payment

    key = (doc.company, doc.branch if doc.company == "Kattaqo'rg'on" else None)
    plastik_mode = PLASTIK_MODE_OF_PAYMENT.get(key)
    if not plastik_mode:
        frappe.throw(
            _("\"{0}\" (filial: {1}) uchun Plastik to'lov usuli sozlanmagan -- "
              "avval uni yarating (Mode of Payment + Mode of Payment Account).").format(
                doc.company, doc.branch or "-"
            )
        )
    return plastik_mode


def _extract_name_part(text: str) -> str:
    """Strip the trailing "(+998 ...)"-style phone parenthetical, leaving just the name."""
    return re.sub(r"\(?\+?\d[\d\s\-()]{7,}\d\)?", "", text or "").strip(" ()-")


def _resolve_branch_for_diler(diler_text: str):
    """Match the Excel 'Diler' text against Report Service Dealer Branch labels
    (contains-match) -- reuses the same branch config the Report Service sync
    already maintains (customer_group/cost_center/company per branch), instead
    of duplicating that mapping here.

    Checks non-main branches FIRST, the "is_main" branch only as a fallback --
    every sub-branch's Diler text is prefixed with the main branch's own name
    (e.g. "Samarqand-1 (Akmal Aka) (SH) Bulungur" contains BOTH "Samarqand-1"
    and "Bulungur"), and "Samarqand-1" is even the LONGER string of the two --
    so neither first-found nor longest-label-wins correctly picks "Bulungur"
    here. The main branch's own label being a substring of literally every one
    of its sub-branches' Diler text is exactly why it has to be checked last.
    """
    settings = frappe.get_single("Report Service Settings")
    diler_lower = (diler_text or "").lower()
    branches = sorted(settings.dealer_branches or [], key=lambda b: bool(b.is_main))
    for branch in branches:
        if branch.label and branch.label.lower() in diler_lower:
            return branch
    return None


def _resolve_branch(doc, diler_text: str):
    """Prefer matching the Excel row's own "Diler" text (works for the
    "payments" format); fall back to the Kassa Import doc's own `branch`
    field for formats that carry no per-row branch text at all (the
    "Список получения" format -- always single-branch per file, so the doc's
    own selection is the only source of truth there)."""
    branch = _resolve_branch_for_diler(diler_text)
    if branch:
        return branch
    if not doc.branch:
        return None
    settings = frappe.get_single("Report Service Settings")
    for b in settings.dealer_branches or []:
        if b.label == doc.branch:
            return b
    return None


def _find_customer_by_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 7:
        return None
    tail = digits[-9:]
    rows = frappe.db.sql(
        """
        SELECT name FROM `tabCustomer`
        WHERE REPLACE(REPLACE(REPLACE(REPLACE(mobile_no, ' ', ''), '-', ''), '(', ''), ')', '') LIKE %s
        LIMIT 1
        """,
        (f"%{tail}",),
    )
    return rows[0][0] if rows else None


def _match_customer(person_text: str) -> Optional[str]:
    """Phone-first (reliable -- same source POS as the Report Service sync), then
    exact customer_name match as a fallback. No partial/fuzzy name match -- see
    validators.py's _validate_party_exists precedent: guessing a wrong party is
    worse than asking."""
    phone = kassa_excel_service.extract_phone(person_text)
    matched = _find_customer_by_phone(phone)
    if matched:
        return matched

    name_part = _extract_name_part(person_text)
    if name_part:
        matched = frappe.db.get_value("Customer", {"customer_name": person_text.strip()}, "name")
        if matched:
            return matched

    return None


def _get_or_create_customer(doc, person_text: str, diler_text: str, log: callable) -> str:
    matched = _match_customer(person_text)
    if matched:
        return matched

    branch = _resolve_branch(doc, diler_text)
    if not branch:
        # Neither the Excel row's own "Diler" text nor the Kassa Import doc's
        # `branch` field matched a known Report Service Dealer Branch -- there
        # is no safe customer_group to guess, so ask rather than silently
        # misfile this customer under the wrong branch.
        frappe.throw(
            _("\"{0}\" uchun mos filial topilmadi -- avval shu filialni qo'shing, Excel'dagi "
              "Diler nomini tekshiring, yoki Kassa Import'da 'Filial' maydonini tanlang.").format(diler_text)
        )

    settings = frappe.get_single("Report Service Settings")
    # ERPNext's own Customer.on_update() -> create_primary_contact() builds
    # Contact.name as "{full_name}-{link_name}" (link_name defaults to the
    # customer's own name) -- for a long source name this can exceed the
    # `name` column's 140-char limit and crash the insert entirely.
    # Confirmed live 2026-08-08 on a real 70-char name (two phone numbers in
    # parens). Capped well under half of 140 so the doubled construction
    # stays safe even with Frappe's own "-1"/"-2" dedup suffixes.
    customer_name = (person_text.strip() or _("Noma'lum mijoz"))[:60]

    if not person_text.strip():
        # Blank source name -- reuse ONE shared "Noma'lum mijoz" record per
        # branch instead of creating a fresh near-duplicate for every such
        # row (confirmed live 2026-08-08: this silently flooded the Customer
        # list with "Noma'lum mijoz", "Noma'lum mijoz - 1", "- 2", ...).
        existing = frappe.db.get_value(
            "Customer", {"customer_name": customer_name, "customer_group": branch.customer_group}, "name"
        )
        if existing:
            return existing

    customer = frappe.new_doc("Customer")
    customer.customer_name = customer_name
    customer.customer_group = branch.customer_group
    customer.territory = settings.default_territory
    phone = kassa_excel_service.extract_phone(person_text)
    if phone:
        customer.mobile_no = phone
    customer.flags.ignore_permissions = True
    customer.insert()

    log(f"   ⚠️ Yangi mijoz yaratildi (avvalgi mos yozuv topilmadi): {customer.name}")
    return customer.name


@frappe.whitelist()
def get_preview_data(doc_name: str) -> Dict:
    """Read the Excel, classify rows, and write the negative-amount ones into
    review_rows for the user to classify before Process Import runs."""
    doc = frappe.get_doc(DOCTYPE, doc_name)

    if not doc.excel_file:
        return {"success": False, "message": _("Excel fayl yuklanmagan")}

    if doc.processed_rows:
        return {
            "success": False,
            "message": _("Import qisman bajarilgan -- Load Preview qayta ishlatilmaydi, "
                          "to'g'ridan-to'g'ri Process Import bilan davom eting yoki avval Cancel Import qiling."),
        }

    try:
        rows = kassa_excel_service.read_kassa_report(doc.excel_file)
    except Exception as e:
        return {"success": False, "message": str(e)}

    if not rows:
        return {"success": False, "message": _("Excel faylda faol (DELETED bo'lmagan) qator topilmadi")}

    positive_count = 0
    zero_count = 0
    negative_rows = []
    matched_count = 0
    unmatched_count = 0

    for row in rows:
        if row["amount"] == 0:
            # A real row, but no actual cash moved -- Kassa itself rejects a
            # zero amount (validate_amount() throws), and there is nothing to
            # classify (no direction, no party decision) -- always skipped,
            # never shown for review.
            zero_count += 1
            continue

        if not _is_expense_flow(row):
            positive_count += 1
            if _match_customer(row["person_text"]):
                matched_count += 1
            else:
                unmatched_count += 1
            continue

        matched_customer = _match_customer(row["person_text"])
        branch = _resolve_branch(doc, row["diler"])
        negative_rows.append({
            "row_num": row["row_num"],
            "date": row["date"],
            "person_text": row["person_text"],
            "izoh": row["izoh"] + (" [PAYMENT, musbat summa]" if row["amount"] > 0 and row.get("payment_type") == "PAYMENT" else ""),
            "amount": abs(row["amount"]),
            "matched_customer": matched_customer,
            "cost_center": branch.cost_center if branch else None,
        })

    doc.set("review_rows", [])
    for nr in negative_rows:
        doc.append("review_rows", nr)
    doc.save()
    frappe.db.commit()

    return {
        "success": True,
        "message": _(
            "{0} ta faol qator: {1} ta oddiy tushum (musbat), {2} ta tekshirish kerak (manfiy), "
            "{3} ta summasiz (0, o'tkazib yuboriladi)."
        ).format(len(rows), positive_count, len(negative_rows), zero_count),
        "total_rows": len(rows),
        "positive_count": positive_count,
        "negative_count": len(negative_rows),
        "zero_count": zero_count,
        "matched_count": matched_count,
        "unmatched_count": unmatched_count,
    }


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

    if not doc.company or not doc.mode_of_payment:
        return {"success": False, "message": _("Компания va To'lov usuli tanlanmagan")}

    if doc.company == "Kattaqo'rg'on" and not doc.branch:
        return {"success": False, "message": _("Kattaqo'rg'on tarmog'i uchun 'Filial' tanlanishi shart")}

    unclassified = [r.row_num for r in doc.review_rows if not r.classification]
    if unclassified:
        return {
            "success": False,
            "message": _("Quyidagi qatorlar hali tekshirilmagan (Turi tanlanmagan): {0}").format(
                ", ".join(str(r) for r in unclassified)
            ),
        }

    excel_hash = calculate_file_hash(doc.excel_file)
    duplicate = check_duplicate_import(excel_hash, doc_name, DOCTYPE, "kassa_docs")
    if duplicate["is_duplicate"]:
        return {
            "success": False,
            "message": _("Bu Excel avval import qilingan: {0}").format(duplicate["existing_doc"]),
        }

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
    try:
        result = _process_import_sync(doc_name)
        event = "akfa_kassa_import_success" if result["success"] else "akfa_kassa_import_failed"
        frappe.publish_realtime(event, {"doc_name": doc_name, "result": result}, doctype=DOCTYPE, docname=doc_name)
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error("Kassa Import", f"Import Error: {doc_name}\n{str(e)}")
        try:
            doc = frappe.get_doc(DOCTYPE, doc_name)
            doc.db_set("status", "Failed")
            doc.db_set("error_log", str(e))
            frappe.db.commit()
        except Exception:
            pass
        frappe.publish_realtime(
            "akfa_kassa_import_failed",
            {"doc_name": doc_name, "result": {"success": False, "message": str(e)}},
            doctype=DOCTYPE,
            docname=doc_name,
        )


def _process_import_sync(doc_name: str) -> Dict:
    """One Kassa document per active Excel row (no grouping -- each row is
    already one real payment/receipt event)."""
    doc = frappe.get_doc(DOCTYPE, doc_name)

    log_lines = []

    def log(msg, publish=True):
        log_lines.append(msg)
        current_log = "\n".join(log_lines)
        frappe.db.set_value(DOCTYPE, doc_name, "import_log", current_log, update_modified=False)
        if publish:
            frappe.publish_realtime(
                "akfa_kassa_import_log",
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
    log(f"To'lov usuli: {doc.mode_of_payment}")
    log("=" * 50)

    try:
        log("\n📊 1. Excel o'qilmoqda...")
        rows = kassa_excel_service.read_kassa_report(doc.excel_file)
        if not rows:
            raise Exception(_("Excel faylda faol qator topilmadi"))
        log(f"   ✅ {len(rows)} ta faol qator o'qildi")

        log("\n🔍 2. Dublikat tekshiruvi...")
        excel_hash = calculate_file_hash(doc.excel_file)
        duplicate = check_duplicate_import(excel_hash, doc_name, DOCTYPE, "kassa_docs")
        if duplicate["is_duplicate"]:
            raise Exception(_("Bu Excel avval import qilingan: {0}").format(duplicate["existing_doc"]))
        doc.db_set("external_ref", excel_hash)

        unclassified = [r.row_num for r in doc.review_rows if not r.classification]
        if unclassified:
            raise Exception(
                _("Quyidagi qatorlar hali tekshirilmagan: {0}").format(", ".join(str(r) for r in unclassified))
            )
        review_by_row = {r.row_num: r for r in doc.review_rows}

        all_kassa_names = []
        total_amount = 0.0

        already_done = set()
        if doc.kassa_docs:
            all_kassa_names.extend([s.strip() for s in doc.kassa_docs.split(",") if s.strip()])
        if doc.processed_rows:
            already_done = {r.strip() for r in doc.processed_rows.split(",") if r.strip()}

        if already_done:
            log(f"   ♻️ Resume: {len(already_done)} ta qator oldin bajarilgan, o'tkazib yuboriladi")

        for idx, row in enumerate(rows, 1):
            row_key = str(row["row_num"])
            if row_key in already_done:
                continue

            if row["amount"] == 0:
                # Real row, no actual cash movement -- Kassa itself would
                # reject a zero amount. Never a case to guess at: just skip.
                log(f"   ⏭️ Qator {row['row_num']}: summa 0, o'tkazib yuborildi")
                already_done.add(row_key)
                doc.db_set("processed_rows", ", ".join(sorted(already_done, key=int)))
                frappe.db.commit()
                continue

            log(f"\n--- [{idx}/{len(rows)}] Qator {row['row_num']}: {row['person_text']} ({row['date']}) ---")

            try:
                kassa_name = _create_kassa_doc(doc, row, review_by_row.get(row["row_num"]), log)
                all_kassa_names.append(kassa_name)
                already_done.add(row_key)
                log(f"   ✅ Kassa yaratildi: {kassa_name}")

                doc.db_set("kassa_docs", ", ".join(all_kassa_names))
                doc.db_set("processed_rows", ", ".join(sorted(already_done, key=int)))

                total_amount += abs(row["amount"])
                frappe.db.commit()

            except Exception as row_err:
                frappe.db.rollback()
                log(f"   ❌ Qator {row['row_num']} BO'YICHA XATO: {str(row_err)}")
                doc.reload()
                raise row_err

        doc.db_set("status", "Processed")

        log("\n" + "=" * 50)
        log("✅ IMPORT MUVAFFAQIYATLI YAKUNLANDI")
        log("=" * 50)
        log(f"💰 Jami Kassa hujjati: {len(all_kassa_names)}")
        log(f"💵 Jami summa: {total_amount:,.2f}")

        frappe.db.commit()

        return {
            "success": True,
            "message": _("Import muvaffaqiyatli"),
            "kassa_docs": all_kassa_names,
            "total_rows": len(rows),
            "total_amount": total_amount,
        }

    except Exception as e:
        frappe.db.rollback()
        log(f"\n❌ XATO: {str(e)}")
        doc.reload()
        doc.db_set("status", "Failed")
        doc.db_set("error_log", str(e))
        frappe.db.commit()
        frappe.log_error("Kassa Import", f"Import Error: {doc_name}\n{str(e)}")
        return {"success": False, "message": str(e)}


def _create_kassa_doc(doc, row: Dict, review_row, log: callable) -> str:
    posting_date = _parse_date(row["date"])
    is_expense_flow = _is_expense_flow(row)

    kassa = frappe.new_doc("Kassa")
    kassa.date = posting_date
    kassa.company = doc.company
    kassa.mode_of_payment = _resolve_mode_of_payment(doc, row.get("payment_account", "NAQT"))
    kassa.remarks = row["izoh"] or None

    if not is_expense_flow:
        # Positive RECEIPT -- a straightforward cash receipt from a real customer.
        customer = _get_or_create_customer(doc, row["person_text"], row["diler"], log)
        kassa.transaction_type = "Приход"
        kassa.party_type = "Customer"
        kassa.party = customer
        kassa.amount = abs(row["amount"])
    elif review_row and review_row.classification == "Mijozga qaytarim":
        customer = review_row.matched_customer or _get_or_create_customer(doc, row["person_text"], row["diler"], log)
        kassa.transaction_type = "Расход"
        kassa.party_type = "Customer"
        kassa.party = customer
        kassa.amount = abs(row["amount"])
    elif review_row and review_row.classification == "Xarajat":
        kassa.transaction_type = "Расход"
        kassa.party_type = "Расходы"
        kassa.expense_account = review_row.expense_account
        kassa.cost_center = review_row.cost_center
        kassa.amount = abs(row["amount"])
    else:
        raise Exception(
            _("Qator {0} chiqim (manfiy summa yoki PAYMENT), lekin tekshirish qatori (review_rows) topilmadi").format(row["row_num"])
        )

    kassa.flags.ignore_permissions = True
    kassa.insert()
    kassa.submit()
    return kassa.name


@frappe.whitelist()
def cancel_import(doc_name: str) -> Dict:
    """Cancel a processed import (cancels every Kassa doc it created, which
    cascades to cancel each one's linked Payment/Journal Entry)."""
    doc = frappe.get_doc(DOCTYPE, doc_name)

    if doc.status not in ["Processed", "Failed", "Processing"]:
        return {
            "success": False,
            "message": _("Faqat 'Processed', 'Failed' yoki 'Processing' statusdagi importni bekor qilish mumkin"),
        }

    try:
        if doc.kassa_docs:
            names = [n.strip() for n in doc.kassa_docs.split(",") if n.strip()]
            for name in names:
                if not frappe.db.exists("Kassa", name):
                    continue
                kassa = frappe.get_doc("Kassa", name)
                if kassa.docstatus == 1:
                    kassa.flags.ignore_permissions = True
                    kassa.cancel()

        doc.db_set("status", "Draft")
        # NULL, not "" -- external_ref has a unique index, and MySQL enforces
        # uniqueness on empty string (unlike NULL, which is exempt). Clearing
        # to "" meant the FIRST cancel on a site succeeded but permanently
        # occupied the one allowed empty-string slot -- every cancel after
        # that failed with (1062, "Duplicate entry '' for key 'external_ref'"),
        # confirmed live 2026-08-08.
        doc.db_set("external_ref", None)
        doc.db_set("import_log", "")
        doc.db_set("kassa_docs", "")
        doc.db_set("processed_rows", "")
        doc.db_set("error_log", "")

        frappe.db.commit()
        return {"success": True, "message": _("Import bekor qilindi")}
    except Exception as e:
        frappe.db.rollback()
        return {"success": False, "message": str(e)}
