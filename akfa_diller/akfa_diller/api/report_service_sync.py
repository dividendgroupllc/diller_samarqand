import re
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List
from urllib.parse import unquote

import frappe
from frappe.utils import today, getdate

from akfa_diller.akfa_diller.services import report_service_client
from akfa_diller.akfa_diller.services.invoice_service import invoice_service, InvoiceConfig
from akfa_diller.akfa_diller.services.purchase_invoice_service import purchase_invoice_service, PurchaseInvoiceConfig
from akfa_diller.akfa_diller.services.stock_entry_service import stock_entry_service, StockEntryConfig
from akfa_diller.akfa_diller.utils.validators import validate_items_exist

OVERLAP_DAYS = 3
MAX_HEAL_ATTEMPTS = 500  # a single branch-transfer cid can bundle 78+ distinct
# items (confirmed live 2026-08-09: six July transfers of 25-78 lines each were
# permanently lost because the old cap of 30 ran out mid-document -- one
# shortfall is discovered per attempt, so the cap must exceed the worst-case
# line count with several shortfalls per line). Healing is pure local DB work
# (no external API call), and the stall guard in the retry loop stops runaway
# repetition of an identical shortfall, so a high cap only costs time on
# documents that are actually converging.
MAX_SAME_SHORTFALL_REPEATS = 50  # the SAME (qty, item, warehouse) shortfall
# recurring is usually still progress, not a stall: several already-existing
# future SLEs can each be short by the same amount (confirmed live 2026-08-09:
# a 1.0-unit shortfall on the same item recurred a handful of times while the
# deficit was walked down one equal step per heal, and an abort-on-first-repeat
# guard misclassified five converging documents as stuck). Only when one
# identical signature has repeated this many times is it a genuine echo (the
# injected receipt never reaches the draw point).

# Matches both NegativeStockError message shapes ERPNext raises (see
# erpnext/stock/stock_ledger.py raise_exceptions / validate_negative_stock):
# "<strong>30.0</strong> units of <a .../Item/NAME" style...>...</a> needed in
# <a .../Warehouse/WH" ...>...</a> [on DATE TIME for VOUCHER] to complete..."
_NEG_STOCK_RE = re.compile(
    r"(?:<strong>)?([\d.]+)(?:</strong>)?\s+units of\s+"
    r'<a href="/app/Form/Item/([^"]+)"[^>]*>.*?</a>\s+needed in\s+'
    r'<a href="/app/Form/Warehouse/([^"]+)"',
    re.DOTALL,
)
REQUIRED_SETTINGS_FIELDS = (
    "default_territory",
    "default_supplier_group",
    "default_item_group",
    "username",
)

# Some "clients" in the feed are not real end customers -- they're the dealer's own
# branch sub-warehouses, tagged with a clientName like "Самарканд-1 (Акмал ака)(Ш)
# Жомбой 6060" and a stable clientCid. Confirmed against real data across multiple
# months (and independently cross-checked against a parallel Excel-based
# investigation that found the same 6 identities): these always resolve to one of
# the dealer's own already-tracked Warehouse records, never a real external
# customer. Transactions with these clientCids are a real Material Transfer between
# two of the company's own warehouses, not a sale -- regardless of what `type` the
# row carries (one of these, clientCid 765, is even tagged `type: null`, the same
# anomaly rows found earlier -- confirming classification must be by customer
# identity, not by the type field).
#
# This mapping is per-site data (Report Service Branch Warehouse doctype), not a
# hardcoded constant -- this app is known to be copied to other dealer sites
# (e.g. akfa_diller <- Pokiza), each with its own dealer branches, so a single
# global dict here would silently misfile a different dealer's real
# customer/supplier under a reused numeric id. Checked against EVERY branch's
# own feed, not just "main" ones -- see _process_cid_group for why (Nurobod,
# a real sub-branch, needs its own "BAZA" identity mapped here too).
#
# Keyed by "{dealer_id}:{client_cid}", not bare client_cid -- once more than one
# dealer network can be marked is_main (e.g. Samarqand-1's own network AND a
# separately-onboarded Kattako'rg'on network), each has its own independent
# clientCid numbering, so a bare client_cid key would risk two unrelated dealers'
# internal branch identities colliding on the same small number.
def _get_branch_warehouse_map() -> Dict[str, str]:
    rows = frappe.get_all("Report Service Branch Warehouse", fields=["dealer_id", "client_cid", "warehouse"])
    return {f"{row.dealer_id}:{row.client_cid}": row.warehouse for row in rows}


def _try_heal_negative_stock(error_message, branch, cid_rows, all_branches) -> bool:
    """On a NegativeStockError, parse exactly which item/warehouse/qty was short
    and inject a same-day Material Receipt for that shortfall dated just before
    the failing transaction (posting_time 00:00:01, vs the 23:59:59 every real
    synced document uses) -- so the correction lands at the actual point in
    history the real stock was missing, not just "today". Returns True if a
    correction was made (caller should retry the cid).

    The source system (dealer's own POS) allows an item's balance to go
    negative (an oversell on their end); ERPNext structurally cannot represent
    negative stock, so without this, a live sync run hits NegativeStockError,
    logs it, and silently skips that transaction forever -- the gap between
    ERPNext and the source system's balance would then grow every time this
    happens, exactly the drift the initial historical backfill had to heal
    once already. This makes the SAME correction happen automatically on every
    scheduled run, not just during a one-off backfill.

    The shortfall warehouse is not always `branch`'s own: a branch-transfer cid
    (e.g. a Kattaqo'rg'on->Ishtixon PRIXOD_BAZA row, processed while iterating
    Ishtixon) draws down the SENDING branch's warehouse, not the receiving
    branch's -- confirmed live (2026-08-07): several Kattaqo'rg'on->Ishtixon
    transfers were permanently stuck because the shortfall was in
    "Kattaqo'rg'on Asosiy - K" while `branch` was Ishtixon, and blindly
    refusing to touch a warehouse the current branch doesn't own left these
    transactions failing on every single backfill/sync run forever, with no
    path to ever self-heal. `all_branches` (every configured dealer branch,
    not just the one currently being processed) lets this look up whether the
    shortfall warehouse actually belongs to one of OUR OWN branches -- if so,
    it's still fully our own inventory, just under a different branch's
    company/cost_center, and healing it there is exactly as safe as healing
    `branch`'s own warehouse. Only a warehouse matching NO known branch is
    still refused (genuinely unrelated / unexpected)."""
    m = _NEG_STOCK_RE.search(error_message)
    if not m:
        frappe.log_error(title=f"Report Service sync: heal regex mos kelmadi ({branch.label})", message=error_message[:2000])
        return False

    qty_needed = float(m.group(1))
    item_code = unquote(m.group(2))
    warehouse = unquote(m.group(3))

    owning_branch = branch
    if warehouse != branch.warehouse:
        owning_branch = next((b for b in all_branches if b.warehouse == warehouse), None)
        if not owning_branch:
            frappe.log_error(
                title=f"Report Service sync: heal ombor mos kelmadi ({branch.label})",
                message=f"error warehouse={warehouse!r} vs branch.warehouse={branch.warehouse!r}\nitem={item_code!r} qty={qty_needed}",
            )
            return False  # not any known branch's warehouse -- don't guess

    rate = frappe.db.get_value("Bin", {"item_code": item_code, "valuation_rate": [">", 0]}, "valuation_rate")
    allow_zero = not rate
    rate = rate or 0

    posting_date = _parse_date(cid_rows[0]["date"])

    se = frappe.new_doc("Stock Entry")
    se.company = owning_branch.company
    se.stock_entry_type = "Material Receipt"
    se.purpose = "Material Receipt"
    se.set_posting_time = 1
    se.posting_date = posting_date
    se.posting_time = "00:00:01"
    if owning_branch.cost_center:
        se.cost_center = owning_branch.cost_center
    item_row = {
        "item_code": item_code,
        "qty": qty_needed,
        "basic_rate": rate,
        "t_warehouse": warehouse,
        "cost_center": owning_branch.cost_center or None,
    }
    if allow_zero:
        item_row["allow_zero_valuation_rate"] = 1
    se.append("items", item_row)
    se.flags.ignore_permissions = True

    # Retry on lock contention specifically -- a scheduled sync run can overlap
    # with another admin action writing to the same warehouse's Bin; a
    # transient DocumentLockedError/"Lock wait timeout" clears up on its own
    # within a few seconds and is not a real data problem worth giving up on.
    last_err = None
    for lock_attempt in range(4):
        try:
            se.insert()
            se.submit()
            return True
        except Exception as e:
            frappe.db.rollback()  # rollback also undoes a just-succeeded insert() if submit() is what failed
            last_err = e
            msg = str(e)
            if "DocumentLockedError" in type(e).__name__ or "Lock wait timeout" in msg:
                time.sleep(6)
                se.name = None  # force a fresh insert on retry -- the rollback above means the prior insert never durably happened
                continue
            raise
    raise last_err


@frappe.whitelist()
def sync_report_service():
    """Scheduler entry point (every 5 minutes, see hooks.py) -- also whitelisted so it
    can be triggered manually (bench console / UI) for testing.

    Loops over every configured dealer branch (Report Service Settings.dealer_branches
    -- each row is one API dealerId, e.g. Samarqand-1 itself plus its Jomboy/Urgut/
    Cho'pon ota/Bulungur/Urgut 2 branches, each with its own complete, independent
    data stream and its own warehouse/cost_center/customer_group). One shared date
    window is used for all branches for simplicity. Never raises on a single bad
    record: each `cid` group within each branch is isolated in its own try/except,
    matching kz_gps_tracking.py's per-unit isolation pattern.

    PRIXOD_BAZA handling differs by branch: the "main" branch's (Samarqand-1 itself)
    PRIXOD_BAZA rows are real external purchases (confirmed against 91 real
    historical Purchase Invoices) and become Purchase Invoices. A SUB-branch's own
    PRIXOD_BAZA rows, however, were confirmed (live API check) to represent stock
    received FROM the main branch -- i.e. the other half of a transfer the main
    branch's own feed already records via the branch-clientCid Material Transfer
    path above. Processing them again here would double-count the same physical
    movement, so they are deliberately skipped for non-main branches.
    """
    settings = frappe.get_single("Report Service Settings")

    if not settings.sync_enabled:
        return {"status": "skipped", "reason": "sync_enabled off"}

    missing = [f for f in REQUIRED_SETTINGS_FIELDS if not settings.get(f)]
    if not settings.dealer_branches:
        missing.append("dealer_branches")
    if missing:
        _finish(settings, "Failed", f"To'liq sozlanmagan: {', '.join(missing)}")
        return {"status": "failed", "reason": "not configured"}

    from_date, to_date = _compute_window(settings.last_synced_date)

    try:
        base_url, token = report_service_client.get_token()
    except Exception as e:
        frappe.log_error(title="Report Service sync: login xatosi", message=str(e))
        _finish(settings, "Failed", f"Login xatosi: {e}")
        return {"status": "failed", "reason": "login error"}

    branch_warehouse_map = _get_branch_warehouse_map()

    total_processed = 0
    total_skipped = 0
    total_groups = 0
    errors = []
    log_lines = [f"Oyna: {from_date}..{to_date}"]

    fetch_failures = 0

    # Asosiy filiallar BIRINCHI ishlanadi (foydalanuvchi qarori 2026-08-16):
    # transfer hujjatlari asosiy filial yozuviga ustuvorlik bilan yaratilsin.
    # Bir sikl ichida asosiy feed'ning tranzaksiyalari oldin hujjat bo'ladi,
    # filial nusxalari esa egizak-tekshiruvda o'tkaziladi. (Sikllar OSHA
    # kechikkan asosiy yozuv uchun _handle_branch_transfer ichidagi almashtirish
    # qoidasi ishlaydi.)
    ordered_branches = sorted(settings.dealer_branches, key=lambda b: (0 if b.is_main else 1))

    for branch in ordered_branches:
        try:
            rows = report_service_client.fetch_all_rows_for_dealer(base_url, token, branch.dealer_id, from_date, to_date)
        except Exception as e:
            frappe.log_error(title=f"Report Service sync: fetch xatosi ({branch.label})", message=str(e))
            errors.append(f"{branch.label}: fetch xatosi: {e}")
            log_lines.append(f"{branch.label} (dealerId={branch.dealer_id}): FETCH XATOSI")
            fetch_failures += 1
            continue

        groups = _group_by_cid(rows)
        total_groups += len(groups)
        processed = 0
        skipped = 0

        # Process same-day stock INFLOWS before OUTFLOWS, regardless of the API's
        # own row order (which is roughly the real intraday sequence -- confirmed
        # live: a branch's sales and its base restock are freely interleaved by
        # cid, e.g. 9 sales of an item appearing before that same day's restock).
        # Every voucher this sync creates is posted at the same fixed 23:59:59
        # (see InvoiceConfig/StockEntryConfig defaults) -- with an identical
        # posting_datetime, ERPNext's stock ledger falls back to `creation`
        # (insertion order) as the tiebreaker (confirmed against
        # erpnext/stock/stock_ledger.py's update_qty_in_future_sle). So
        # inserting a sale before that same day's restock can produce a false
        # "insufficient stock" error the real end-of-day balance wouldn't have
        # had. Sorting here (stable -- ties keep the API's original relative
        # order) costs nothing and removes that self-inflicted failure mode;
        # it does not touch the far larger number of failures that are a real
        # missing opening-balance (Qoldiq) gap with no same-day inflow at all.
        ordered_cids = sorted(
            groups.items(), key=lambda kv: _cid_group_priority(kv[1], branch, branch_warehouse_map)
        )

        for cid, cid_rows in ordered_cids:
            healed_signatures = {}
            for attempt in range(MAX_HEAL_ATTEMPTS):
                try:
                    result = _process_cid_group(cid, cid_rows, settings, branch, branch_warehouse_map)
                    if result == "processed":
                        processed += 1
                        # Commit this cid's writes now, isolated from whatever the next
                        # cid does -- otherwise everything shares one transaction that
                        # only commits at the very end (_finish()), so a LATER cid's
                        # failure would roll back nothing (see except branch) but a
                        # partial write from that same failed cid could otherwise still
                        # ride along into the final commit.
                        frappe.db.commit()
                    else:
                        skipped += 1
                    break
                except Exception as e:
                    # Undo any partial writes this specific cid made before failing
                    # (e.g. an auto-created Customer/Item right before the invoice
                    # submission itself threw) -- without this, those orphaned records
                    # would sit uncommitted and still get swept into the final commit.
                    frappe.db.rollback()
                    if "NegativeStockError" in type(e).__name__ or "needed in" in str(e):
                        # Stall guard: the same shortfall signature recurring a few
                        # times is normal convergence (see MAX_SAME_SHORTFALL_REPEATS)
                        # -- only an excessive repeat count means the injected receipt
                        # never reaches the draw point and the cid can never converge.
                        sig_m = _NEG_STOCK_RE.search(str(e))
                        sig = (sig_m.group(1), sig_m.group(2), sig_m.group(3)) if sig_m else None
                        if sig is None or healed_signatures.get(sig, 0) < MAX_SAME_SHORTFALL_REPEATS:
                            try:
                                healed = _try_heal_negative_stock(str(e), branch, cid_rows, settings.dealer_branches)
                            except Exception as heal_err:
                                frappe.db.rollback()
                                healed = False
                                frappe.log_error(
                                    title=f"Report Service sync: heal xatosi, {branch.label} cid {cid}",
                                    message=f"{heal_err!r}\n\noriginal: {e}",
                                )
                            if healed:
                                if sig:
                                    healed_signatures[sig] = healed_signatures.get(sig, 0) + 1
                                frappe.db.commit()
                                continue
                    frappe.log_error(title=f"Report Service sync: {branch.label} cid {cid}", message=str(e))
                    errors.append(f"{branch.label} cid {cid}: {e}")
                    skipped += 1
                    break
            else:
                errors.append(f"{branch.label} cid {cid}: {MAX_HEAL_ATTEMPTS} marta tuzatishga urinildi, muvaffaqiyatsiz")
                skipped += 1

        total_processed += processed
        total_skipped += skipped
        log_lines.append(f"{branch.label} (dealerId={branch.dealer_id}): {len(groups)} guruh, yaratildi={processed}, o'tkazib yuborildi={skipped}")

    log_lines.append(f"JAMI: guruh={total_groups}, yaratildi={total_processed}, o'tkazib yuborildi={total_skipped}")
    if errors:
        log_lines.append("Xatolar (birinchi 20 tasi):")
        log_lines.extend(errors[:20])

    # Failed if every branch failed to even fetch (a systemic outage/auth issue,
    # not "a few bad records") -- OR if every group that WAS fetched across every
    # branch raised an unexpected exception while processing. Either check alone
    # misses the other's failure mode: a fetch failure never produces a "group"
    # to count in the second check, and a processing failure never touches
    # fetch_failures. Nothing to sync (zero branches attempted, or fetch
    # succeeded everywhere with zero groups) is not a failure.
    total_branches = len(settings.dealer_branches)
    all_fetches_failed = total_branches and fetch_failures == total_branches
    all_groups_failed = total_groups and errors and len(errors) == total_groups
    overall_status = "Failed" if (all_fetches_failed or all_groups_failed) else "Success"

    _finish(settings, overall_status, "\n".join(log_lines))

    return {"status": overall_status.lower(), "processed": total_processed, "skipped": total_skipped}


def _compute_window(last_synced_date):
    today_str = today()
    if not last_synced_date:
        # First-ever run: deliberately start from today only, no historical
        # backfill -- pulling months of past transactions would flood the books
        # with thousands of documents nobody asked for.
        return today_str, today_str

    from_dt = getdate(last_synced_date) - timedelta(days=OVERLAP_DAYS)
    return from_dt.strftime("%Y-%m-%d"), today_str


def _group_by_cid(rows: List[Dict]) -> Dict:
    groups = {}
    for row in rows:
        cid = row.get("cid")
        if cid is None:
            continue
        groups.setdefault(cid, []).append(row)
    return groups


def _external_client_cid(client_cid, branch) -> str:
    """Same reasoning as _external_ref, applied to clientCid instead of cid: each
    branch's clientCid numbering is its own independent space (confirmed live --
    Jomboy's clientCid=10 is a real, unrelated person, not the same "Bulungur"
    identity that clientCid=10 happens to mean on the main branch's own feed).
    Without this prefix, two different branches' customers sharing a small
    clientCid number would silently collide onto the same Customer record.

    Gated on legacy_unprefixed, NOT is_main -- is_main can legitimately be true
    on MORE than one branch (e.g. Samarqand-1's own top-level feed AND a future
    Kattako'rg'on's own top-level feed both need is_main=1 for their own
    PRIXOD_BAZA-as-purchase logic). If bare-value were gated on is_main, both
    would produce unprefixed IDs and could collide (confirmed live: both
    networks' own feeds have a clientCid meaning "BAZA"). legacy_unprefixed is
    deliberately a SEPARATE flag, set true on exactly one grandfathered branch
    (Samarqand-1, for backward compatibility with the 380+ customers already
    synced before multi-branch support existed) -- every other branch, main or
    not, always gets dealer_id-prefixed IDs."""
    if branch.legacy_unprefixed:
        return str(client_cid)
    return f"{branch.dealer_id}:{client_cid}"


def _external_ref(cid, branch) -> str:
    """Dedup key stamped on the created document. Gated on legacy_unprefixed, NOT
    is_main -- see _external_client_cid's docstring for why (is_main can be true
    on more than one branch once a second dealer network exists, but only the
    original grandfathered branch should ever produce bare, unprefixed IDs).
    Every other branch's cid numbering is its own independent space (confirmed:
    Jomboy's cids are ~44000s, Choponota's ~45000s, Bulungur/Urgut's ~61000s, all
    distinct from each other and from Samarqand-1's ~270000s), so prefixing costs
    nothing and closes off an otherwise-real collision risk."""
    if branch.legacy_unprefixed:
        return str(cid)
    return f"{branch.dealer_id}:{cid}"


def _cid_group_priority(rows, branch, branch_warehouse_map) -> int:
    """Sort key for _process_cid_group's processing order within one branch
    -- see the call site in sync_report_service() for why same-day inflows
    must be inserted before outflows. Mirrors _process_cid_group's own
    classification (kept in sync deliberately -- this only decides ORDER,
    never what gets created)."""
    row_type = rows[0].get("type")
    client_cid = rows[0].get("clientCid")

    if branch_warehouse_map.get(f"{branch.dealer_id}:{client_cid}"):
        # Branch transfer: normal direction is inflow-to-branch (tier 1),
        # VOZVRAT_KLIENT-tagged reverse direction is outflow-from-branch (tier 2).
        # Not gated on is_main -- see _process_cid_group's docstring for why.
        return 2 if row_type == "VOZVRAT_KLIENT" else 1

    if row_type == "PRIXOD_BAZA":
        return 0
    if row_type == "VOZVRAT_KLIENT":
        return 1
    if row_type == "RASXOD_KLIENT":
        return 2
    return 2


def _process_cid_group(cid, rows, settings, branch, branch_warehouse_map) -> str:
    row_type = rows[0].get("type")
    client_cid = rows[0].get("clientCid")

    branch_warehouse = branch_warehouse_map.get(f"{branch.dealer_id}:{client_cid}")
    if branch_warehouse:
        # A known branch/base identity, not a real customer/supplier -- always
        # a Material Transfer, regardless of the row's declared type. Checked
        # for EVERY branch, not just is_main ones: the map is already scoped
        # by "{dealer_id}:{client_cid}" (see _get_branch_warehouse_map), so a
        # sub-branch's own dealer_id can only ever match a row created
        # specifically for that sub-branch -- there's no cross-branch
        # collision risk to gate against. is_main is a fully separate concern
        # (see below: whether this branch's UNMAPPED PRIXOD_BAZA becomes a
        # real Purchase Invoice) -- deliberately decoupled after Nurobod
        # (structurally a sub-branch, is_main=0) needed this same mapping
        # checked against its own feed to redirect its "BAZA" clientCid to a
        # Stock Entry from Samarqand-1's warehouse, exactly like a real
        # is_main branch's own transfer identities.
        #
        # Two distinct reporting perspectives use this same mapping:
        #  - The historical case: a MAIN branch's own feed carries a clientCid
        #    representing "chiqim to a sub-branch" (e.g. Samarqand-1's feed,
        #    clientCid=7 -> Jomboy). RASXOD_KLIENT there means outflow FROM
        #    branch.warehouse (Samarqand-1's own); VOZVRAT_KLIENT means the
        #    sub-branch sending stock back (reverse: INTO branch.warehouse).
        #  - The newer case: a SUB-branch's own feed carries a clientCid
        #    representing its own upstream base (e.g. Nurobod's feed,
        #    clientCid=1 "BAZA" -> Samarqand-1's own warehouse). Here PRIXOD_BAZA
        #    means inflow TO branch.warehouse (Nurobod's own) FROM the mapped
        #    warehouse -- the same "reverse" direction as VOZVRAT_KLIENT, just a
        #    different type label because it's the RECEIVING side reporting it,
        #    not the sending side.
        # Both cases are "stock arrives at branch.warehouse from elsewhere" --
        # VOZVRAT_KLIENT and PRIXOD_BAZA both mean that from branch.warehouse's
        # own point of view, hence sharing the reverse=True direction.
        reverse = row_type in ("VOZVRAT_KLIENT", "PRIXOD_BAZA")
        return _handle_branch_transfer(cid, rows, settings, branch, branch_warehouse, reverse=reverse)

    if row_type == "RASXOD_KLIENT":
        return _handle_sale(cid, rows, settings, branch, is_return=False)
    if row_type == "VOZVRAT_KLIENT":
        return _handle_sale(cid, rows, settings, branch, is_return=True)
    if row_type == "PRIXOD_BAZA":
        # The main branch's 2 known bases ("BAZA (+998 __ ___ ____)", "Самарканд
        # База Катта Курган") already existed as real Suppliers on this site with
        # 91 real, manually-entered Purchase Invoices against them -- confirming
        # the business already books baza stock-arrival as a genuine purchase,
        # not a stock-only movement.
        #
        # 2026-08-16 (foydalanuvchi qarori): SUB-filiallar uchun ham xuddi shu.
        # Avval sub-filialning XARITALANMAGAN PRIXOD_BAZA'si "asosiy feed baribir
        # yozadi" degan taxmin bilan jimgina tashlanardi -- bu taxmin ikki marta
        # noto'g'ri chiqdi: (1) 5 SD sub-filialning o'z BAZA identifikatorlari
        # (keyin xaritalab to'g'irlandi), (2) Ishtixonning 2 ta chindan tashqi
        # manbasi ("Ishtixon Akfa Centry" 97-58, to'g'ridan-to'g'ri "Samarqand-1"
        # 97-2 -- iyulda 1854 dona jim yo'qolgan). Endi: filiallararo transfer
        # bo'lsa XARITAGA yoziladi (yuqoridagi branch_warehouse tarmog'i uni
        # transfer qiladi), xaritada YO'Q PRIXOD_BAZA esa -- haqiqiy tashqi
        # yetkazuvchidan kirim, supplier avto-yaratilib xarid bo'lib yoziladi.
        return _handle_purchase(cid, rows, settings, branch)

    if row_type is None:
        # Confirmed live (2026-08-08): a small, bounded set of real customer
        # transactions come back from the API with type=null (not just the
        # already-handled branch-transfer identities above, which are caught
        # by the branch_warehouse_map check regardless of type). Previously
        # these were silently skipped forever -- with no clientCid match to a
        # known branch/base identity, the far more common case is an ordinary
        # client-facing sale that the source system just didn't tag, not a
        # genuinely new transaction category. Defaulting to a regular sale
        # closes that gap; an actually-new row shape from the API would still
        # need its own explicit handling above, so this fallback is
        # deliberately scoped to exactly type=None, not "any unrecognized
        # type" (a non-null-but-unknown string still logs and skips below,
        # since that could be a real new type worth investigating specifically).
        #
        # 2026-08-16 aniqlashtirish: yuqoridagi qoida faqat HAQIQIY MIJOZLI
        # qatorlar uchun tasdiqlangan edi. clientCid ham, clientName ham bo'sh
        # type=None qator -- POS'ning ichki tuzatish yozuvi bo'lib chiqdi va
        # API balans matematikasida KIRIM sifatida qatnashgan (jonli isbot:
        # Mirbozor cid=11674, 750 dona -- sotuv qilib yuborilgani 1500 dona
        # lik balans farqi bergan edi). Yo'nalishini qatorning o'zidan bilib
        # bo'lmaydi -- hujjat yaratmaymiz, ko'rib-chiqishga log qoldiramiz.
        first = rows[0]
        if first.get("clientCid") is None and not (first.get("clientName") or "").strip():
            title = f"Report Service sync: egasiz type=None qator, {branch.label} cid {cid}"
            # har 5-daqiqalik sync oynada qolgan cid'ni qayta-qayta ko'radi --
            # bitta cid uchun faqat bir marta log (42540 spam saboqlari)
            if not frappe.db.exists("Error Log", {"method": title}):
                frappe.log_error(
                    title=title,
                    message=f"{len(rows)} qator, jami qty={sum(r.get('qty') or 0 for r in rows):g}. "
                    "Mijoz yo'q -- POS ichki tuzatishi bo'lishi mumkin, qo'lda ko'rib chiqiladi.",
                )
            return "skipped"
        return _handle_sale(cid, rows, settings, branch, is_return=False)

    frappe.log_error(
        title=f"Report Service sync: notanish type, {branch.label} cid {cid}",
        message=f"type={row_type!r}, {len(rows)} qator o'tkazib yuborildi.",
    )
    return "skipped"


def _handle_branch_transfer(cid, rows, settings, branch, branch_warehouse, reverse: bool) -> str:
    ref = _external_ref(cid, branch)
    if frappe.db.exists("Stock Entry", {"custom_report_service_cid": ref}):
        return "skipped"

    first = rows[0]

    item_result = _resolve_items(rows, settings)
    if not item_result["success"]:
        if item_result.get("all_zero"):
            return "skipped"  # hamma qatori 0 dona -- jim o'tkazamiz, log shart emas
        frappe.log_error(
            title=f"Report Service sync: tovar topilmadi (filial), {branch.label} cid {cid}",
            message="\n".join(e["error"] for e in item_result["errors"]),
        )
        return "skipped"

    items = [
        {"item_code": r["item_code"], "qty": r["qty"], "rate": r["rate"]}
        for r in item_result["valid_items"]
    ]

    source, target = (branch_warehouse, branch.warehouse) if reverse else (branch.warehouse, branch_warehouse)

    # Kross-feed dublikat himoyasi: uchta juftlikda (Nurobod, Ishtixon, Mitan)
    # transferning IKKALA tomoni ham xaritalangan -- jo'natuvchi filial feed'i ham,
    # qabul qiluvchi filial feed'i ham xuddi shu jismoniy yukni o'z cid'i bilan
    # yozadi. cid'lar feed'lararo bog'lanmagan (36:xxx vs 161:yyy), shuning uchun
    # yuqoridagi exists-tekshiruv bu dublni ushlamaydi -- iyulda shu yo'l bilan 28
    # ta dublikat yaralgan edi (keyin qo'lda bekor qilindi). Bir xil (ombor
    # juftligi, jami dona, qator soni) +-2 kun ichida BOSHQA feed'dan allaqachon
    # yozilgan bo'lsa -- bu o'sha yukning ikkinchi tomoni: yaratmaymiz. Ikkala
    # o'lchov ham teng bo'lishi talab qilinadi (faqat jami dona emas), chunki
    # 120-donalik kabi yumaloq jamilar har xil yuklarda tasodifan teng chiqishi
    # mumkin; tovar nomlari esa ATAYLAB solishtirilmaydi -- ikki feed bir xil
    # tovarni har xil nomlaydi (masalan '(N)' suffiksli variantlar), 2026-08-09
    # dagi Mitan tahlilida aynan nom farqi tufayli bir xil yuk 'boshqa' bo'lib
    # ko'ringan.
    total_qty = round(sum(i["qty"] for i in items), 2)
    my_prefix = f"{branch.dealer_id}:%"
    posting_date = _parse_date(first["date"])
    twin = frappe.db.sql(
        """
        select se.name
        from `tabStock Entry` se
        join (
            select sed.parent, sum(sed.qty) as tq, count(*) as cnt,
                   min(sed.s_warehouse) as s_wh, min(sed.t_warehouse) as t_wh
            from `tabStock Entry Detail` sed
            group by sed.parent
        ) agg on agg.parent = se.name
        where se.docstatus = 1
          and se.custom_report_service_cid is not null
          and se.custom_report_service_cid != ''
          and se.custom_report_service_cid not like %(my_prefix)s
          and se.posting_date between date_sub(%(pd)s, interval 2 day)
                                  and date_add(%(pd)s, interval 2 day)
          and agg.s_wh = %(source)s and agg.t_wh = %(target)s
          and round(agg.tq, 2) = %(total_qty)s and agg.cnt = %(cnt)s
        limit 1
        """,
        {
            "my_prefix": my_prefix,
            "pd": posting_date,
            "source": source,
            "target": target,
            "total_qty": total_qty,
            "cnt": len(items),
        },
    )
    twin_name = twin[0][0] if twin else None

    if twin_name is None:
        # Ikkinchi qoida (o'xshashlik): ikki tomon bir yukni HAR XIL jami bilan
        # yozgan bo'lishi mumkin (iyul tahlilida 21 ta shunday juft chiqqan --
        # masalan biri 241, biri 716 deb yozgan) -- bunda yuqoridagi aniq qoida
        # ojiz. O'rniga qator darajasida solishtiramiz: boshqa feed'dan yozilgan
        # nomzod SE bilan aynan bir xil (tovar, dona) qatorlar kamida 3 ta VA
        # kiruvchi yukning kamida yarmini tashkil qilsa -- bu o'sha yukning
        # ikkinchi talqini. Chegara ataylab qattiq (50%, min 3 qator): yumshoq
        # chegara ikki ALOHIDA haqiqiy yukni ham "egizak" deb yo'q qilib
        # yuborishi mumkin edi -- dublikat ko'rinadi va tozalanadi, yo'qotish
        # esa jimgina yo'qoladi, shuning uchun xato tomonga emas, ehtiyot
        # tomonga og'amiz.
        cand_rows = frappe.db.sql(
            """
            select se.name, sed.item_code, sed.qty
            from `tabStock Entry` se
            join `tabStock Entry Detail` sed on sed.parent = se.name
            where se.docstatus = 1
              and se.custom_report_service_cid is not null
              and se.custom_report_service_cid != ''
              and se.custom_report_service_cid not like %(my_prefix)s
              and se.posting_date between date_sub(%(pd)s, interval 2 day)
                                      and date_add(%(pd)s, interval 2 day)
              and sed.s_warehouse = %(source)s and sed.t_warehouse = %(target)s
            """,
            {"my_prefix": my_prefix, "pd": posting_date, "source": source, "target": target},
        )
        by_doc = {}
        for name, code, q in cand_rows:
            by_doc.setdefault(name, []).append((code, round(float(q or 0), 2)))
        my_lines = Counter((i["item_code"], round(float(i["qty"]), 2)) for i in items)
        for name, lines in by_doc.items():
            other = Counter(lines)
            matched_lines = sum(min(my_lines[k], other[k]) for k in my_lines)
            if matched_lines >= 3 and matched_lines * 2 >= len(items):
                twin_name = name
                break

    if twin_name:
        # Foydalanuvchi qarori (2026-08-16): transferda ASOSIY filial yozuvi
        # ustuvor. Agar hozir ASOSIY feed ishlanayotgan bo'lsa-yu, egizak SUB
        # feed'dan yaratilgan bo'lsa (asosiy kechikib yozgan holat) -- sub
        # hujjatini bekor qilib, asosiynikini yaratamiz: yakuniy holatda doim
        # asosiy filial raqamlari turadi. Aks holda (sub feed ishlanayotgan
        # bo'lsa yoki egizak allaqachon asosiydan bo'lsa) -- avvalgidek skip.
        main_dealer_ids = {str(b.dealer_id) for b in settings.dealer_branches if b.is_main}
        twin_ref = frappe.db.get_value("Stock Entry", twin_name, "custom_report_service_cid") or ""
        twin_prefix = twin_ref.split(":")[0] if ":" in twin_ref else ""
        if branch.is_main and twin_prefix and twin_prefix not in main_dealer_ids:
            if _cancel_se_with_heal(twin_name, branch, settings):
                frappe.logger("report_service_sync").info(
                    f"asosiy-ustuvorlik: sub egizagi {twin_name} ({twin_ref}) bekor qilindi, "
                    f"asosiy {branch.label} cid {cid} yoziladi"
                )
                # bekor qilingan sub hujjatning ref'i o'zida qoladi -- sub feed
                # qayta ishlaganda exists-tekshiruv uni ko'rib skip qiladi,
                # qayta-yaratish sikli bo'lmaydi.
            else:
                frappe.log_error(
                    title=f"Report Service sync: sub egizagini bekor qilib bo'lmadi, {branch.label} cid {cid}",
                    message=f"twin={twin_name} ({twin_ref}); asosiy hujjat yaratilmadi, keyingi siklda qayta uriniladi.",
                )
                return "skipped"
        else:
            # Foydalanuvchi qarori (2026-08-16): ikki tomon HAR XIL miqdor
            # yozgan bo'lsa (asosiy 10 jo'natdi, filial 11 qabul qildi),
            # transfer asosiy miqdorda qoladi, FARQ esa filial omboriga
            # alohida tuzatish (Material Receipt/Issue) bilan kiritiladi --
            # filialning haqiqatda sanagan soni ombor qoldig'ida aks etsin.
            # Faqat qabul qiluvchi tomonda (reverse=True) va jami farq bo'lsa.
            if reverse:
                _reconcile_receiver_delta(twin_name, items, branch, ref, posting_date)
            frappe.logger("report_service_sync").info(
                f"kross-feed dublikat o'tkazildi: {branch.label} cid {cid} -> {twin_name} "
                f"({source} -> {target}, {total_qty} dona, {len(items)} qator, {posting_date})"
            )
            return "skipped"
    config = StockEntryConfig(
        company=branch.company,
        source_warehouse=source,
        target_warehouse=target,
        posting_date=_parse_date(first["date"]),
        cost_center=branch.cost_center,
        external_ref_field="custom_report_service_cid",
        external_ref_value=ref,
    )
    stock_entry_service.create_material_transfer(items, config)
    return "processed"


def _handle_sale(cid, rows, settings, branch, is_return: bool) -> str:
    ref = _external_ref(cid, branch)
    if frappe.db.exists("Sales Invoice", {"custom_report_service_cid": ref}):
        return "skipped"

    first = rows[0]
    customer = _get_or_create_customer(first.get("clientCid"), first.get("clientName"), first.get("phone"), settings, branch)

    item_result = _resolve_items(rows, settings)
    if not item_result["success"]:
        if item_result.get("all_zero"):
            return "skipped"  # hamma qatori 0 dona -- jim o'tkazamiz, log shart emas
        frappe.log_error(
            title=f"Report Service sync: tovar topilmadi, {branch.label} cid {cid}",
            message="\n".join(e["error"] for e in item_result["errors"]),
        )
        return "skipped"

    sign = -1 if is_return else 1
    items = [
        {"item_code": r["item_code"], "qty": sign * r["qty"], "rate": r["rate"]}
        for r in item_result["valid_items"]
    ]

    config = InvoiceConfig(
        company=branch.company,
        warehouse=branch.warehouse,
        posting_date=_parse_date(first["date"]),
        customer=customer,
        cost_center=branch.cost_center,
        update_stock=True,
        is_return=is_return,
        external_ref_field="custom_report_service_cid",
        external_ref_value=ref,
    )
    invoice_service.create_sales_invoice(items, config)
    return "processed"


def _handle_purchase(cid, rows, settings, branch) -> str:
    ref = _external_ref(cid, branch)
    if frappe.db.exists("Purchase Invoice", {"custom_report_service_cid": ref}):
        return "skipped"

    first = rows[0]
    supplier = _get_or_create_supplier(first.get("clientCid"), first.get("clientName"), first.get("phone"), settings, branch)

    item_result = _resolve_items(rows, settings)
    if not item_result["success"]:
        if item_result.get("all_zero"):
            return "skipped"  # hamma qatori 0 dona -- jim o'tkazamiz, log shart emas
        frappe.log_error(
            title=f"Report Service sync: tovar topilmadi (xarid), {branch.label} cid {cid}",
            message="\n".join(e["error"] for e in item_result["errors"]),
        )
        return "skipped"

    items = [
        {"item_code": r["item_code"], "qty": r["qty"], "rate": r["rate"]}
        for r in item_result["valid_items"]
    ]

    config = PurchaseInvoiceConfig(
        company=branch.company,
        warehouse=branch.warehouse,
        posting_date=_parse_date(first["date"]),
        supplier=supplier,
        cost_center=branch.cost_center,
        update_stock=True,
        external_ref_field="custom_report_service_cid",
        external_ref_value=ref,
    )
    purchase_invoice_service.create_purchase_invoice(items, config)
    return "processed"


def _resolve_items(rows, settings) -> Dict:
    candidates = []
    for idx, row in enumerate(rows):
        qty = row.get("qty") or 0
        if not qty:
            # 0-donali qator -- manba tizim artefakti (jonli misol 2026-08-14:
            # S1 cid 271561, yolg'iz qatori qty=0, clientCid=None, amount=None).
            # Avval bunday qator BUTUN tranzaksiyani yiqitardi: bitta buzuq
            # qator tufayli 30-qatorli haqiqiy yuk ham yo'qolar, yolg'iz-qator
            # holatida esa har 5-daqiqalik sync abadiy xato log yozar edi.
            # 0 dona hech qanday ombor/moliya ta'siriga ega emas -- shunchaki
            # tashlab, qolgan qatorlarni ishlaymiz.
            continue
        candidates.append({
            # Frappe'da hujjat nomi (Item.name = item_code) 140 belgidan oshsa
            # "Data too long for column 'name'" bilan yiqiladi -- jonli misol
            # 2026-08-16: Ishtixonning 4 ta cid'ida 140+ belgili productName
            # bor edi. Nomni boshidayoq kesamiz: validate ham, yaratish ham
            # bitta (kesilgan) nom bilan ishlaydi, aks holda yaratish kesilgan,
            # qidiruv esa to'liq nom bilan yurib abadiy topolmay qolardi.
            "item_name": ((row.get("productName") or "").strip())[:140],
            "qty": qty,
            "rate": (row.get("amount") or 0) / qty,
            "row_num": idx,
        })

    if not candidates:
        # hamma qatori 0 dona -- ishlanadigan hech narsa yo'q, jim o'tkazamiz
        # ("success" bilan bo'sh ro'yxat emas: chaqiruvchilar bo'sh items bilan
        # hujjat yaratishga urinmasligi uchun alohida belgi qaytaramiz).
        return {"success": False, "valid_items": [], "errors": [], "all_zero": True}

    result = validate_items_exist(candidates)
    if result["errors"]:
        # Unlike Sales/Purchase Import (Excel, typed by a human -- a typo should
        # be caught, not silently turned into a new Item), this feed is a machine
        # export straight from the dealer's own POS catalogue, so a "not found"
        # name is real, sellable stock that just doesn't have an ERPNext Item
        # yet -- auto-create it (item_code = item_name, matching how existing
        # items in this catalogue are already named) and re-resolve instead of
        # skipping the whole transaction.
        for err in result["errors"]:
            _get_or_create_item(err.get("item_name"), settings)
        # Yangi yaratilgan Item'lar darhol commit qilinadi: aks holda shu cid
        # keyinroq NegativeStockError bilan rollback bo'lsa, endi-yaratilgan
        # Item ham u bilan birga yo'q bo'lib ketadi -- heal esa xatoda nomi
        # turgan, lekin endi mavjud bo'lmagan itemga kirim yozolmay har
        # urinishda qaytadan yiqiladi (2026-08-09: Cho'pon ota'ning 5 ta yuki
        # aynan shu zanjir tufayli abadiy 'is not a stock Item' bilan qolgan).
        # Katalog yozuvi sifatida Item baribir doimiy kerak -- erta commit
        # qilish xavfsiz.
        frappe.db.commit()
        result = validate_items_exist(candidates)

    return result


def _get_or_create_item(item_name, settings):
    if not item_name or frappe.db.exists("Item", item_name):
        return

    item = frappe.new_doc("Item")
    item.item_code = item_name
    item.item_name = item_name
    item.item_group = settings.default_item_group
    item.stock_uom = "Unit"  # every existing Item in this catalogue uses "Unit"
    item.is_stock_item = 1
    item.flags.ignore_permissions = True
    item.insert()


def _get_or_create_customer(client_cid, client_name, phone, settings, branch):
    # Supplier'dagi kabi: 140+ belgili nom Customer.name'ga sig'maydi (1406) --
    # qidiruv va yaratish bitta kesilgan nom bilan.
    client_name = ((client_name or "").strip())[:140] or None
    ref_client_cid = _external_client_cid(client_cid, branch) if client_cid is not None else None
    if ref_client_cid is not None:
        existing = frappe.db.get_value(
            "Customer", {"custom_report_service_client_cid": ref_client_cid}, "name"
        )
        if existing:
            return existing

    # Fall back to an exact name match before creating a new record. This site
    # already had 500+ real Customer records (and several Suppliers) entered
    # before this sync's client_cid tagging existed -- without this, the first
    # transaction naming one of them would either silently create a duplicate
    # (Customer's naming_series autoname just appends "- 1", no error) or hit a
    # raw primary-key IntegrityError (Supplier's autoname doesn't). client_name
    # here always embeds a phone number (the source system's own convention),
    # so an exact string match is a safe identity match, not a coincidence risk.
    if client_name:
        name_match = frappe.db.get_value("Customer", {"customer_name": client_name}, "name")
        if name_match:
            if ref_client_cid is not None:
                frappe.db.set_value("Customer", name_match, "custom_report_service_client_cid", ref_client_cid)
            return name_match

    customer = frappe.new_doc("Customer")
    customer.customer_name = client_name or f"Report Service mijoz {client_cid}"
    customer.customer_group = branch.customer_group
    customer.territory = settings.default_territory
    if ref_client_cid is not None:
        customer.custom_report_service_client_cid = ref_client_cid
    if phone and "_" not in phone:
        customer.mobile_no = phone
    customer.flags.ignore_permissions = True
    customer.insert()
    return customer.name


def _get_or_create_supplier(client_cid, client_name, phone, settings, branch):
    # Frappe hujjat nomi (Supplier.name = supplier_name) 140 belgidan oshsa 1406
    # "Data too long for column 'name'" (jonli: Ishtixonning 4 cid'i, 2026-08-16).
    # Qidiruv ham, yaratish ham bitta kesilgan nom bilan ishlashi shart.
    client_name = ((client_name or "").strip())[:140] or None
    # Same dealer_id-scoping reasoning as _get_or_create_customer/_external_client_cid --
    # _handle_purchase is only ever reached for an is_main branch's own PRIXOD_BAZA rows
    # today (only one is_main branch exists), but once a second dealer network is
    # onboarded as is_main (see _get_branch_warehouse_map's own comment), an unscoped
    # bare client_cid here would let two unrelated networks' suppliers silently share
    # one Supplier record on a coincidental small-number match.
    ref_client_cid = _external_client_cid(client_cid, branch) if client_cid is not None else None
    if ref_client_cid is not None:
        existing = frappe.db.get_value(
            "Supplier", {"custom_report_service_client_cid": ref_client_cid}, "name"
        )
        if existing:
            return existing

    # Same pre-existing-record reasoning as _get_or_create_customer -- this
    # site's 2 real base/supplier records ("BAZA...", "Самарканд База Катта
    # Курган") were entered before client_cid tagging existed.
    if client_name:
        name_match = frappe.db.get_value("Supplier", {"supplier_name": client_name}, "name")
        if name_match:
            if ref_client_cid is not None:
                frappe.db.set_value("Supplier", name_match, "custom_report_service_client_cid", ref_client_cid)
            return name_match

    supplier = frappe.new_doc("Supplier")
    supplier.supplier_name = client_name or f"Report Service ta'minotchi {client_cid}"
    supplier.supplier_group = settings.default_supplier_group
    if ref_client_cid is not None:
        supplier.custom_report_service_client_cid = ref_client_cid
    if phone and "_" not in phone:
        supplier.mobile_no = phone
    supplier.flags.ignore_permissions = True
    supplier.insert()
    return supplier.name


def _parse_date(date_str: str) -> str:
    return datetime.strptime(date_str, "%d-%m-%Y").strftime("%Y-%m-%d")


def _finish(settings, status, log):
    # Only advance the watermark on real success -- advancing it on a "Failed"
    # run (bad config, fetch error, or every group in the window erroring out)
    # would permanently skip whatever the outage/misconfiguration covered once
    # it falls outside the OVERLAP_DAYS re-fetch window.
    if status == "Success":
        settings.db_set("last_synced_date", today())
    settings.db_set("last_sync_status", status)
    settings.db_set("last_sync_log", (log or "")[:140000])
    frappe.db.commit()


def _make_heal_receipt(item_code, warehouse, qty_needed, posting_date):
    """Bekor qilish/qayta-yaratish jarayonida chiqqan NegativeStockError uchun
    nuqtaviy tuzatish-kirim (asosiy heal bilan bir xil uslub: sana boshiga,
    00:00:01)."""
    rate = frappe.db.get_value("Bin", {"item_code": item_code, "valuation_rate": [">", 0]}, "valuation_rate")
    se = frappe.new_doc("Stock Entry")
    se.company = frappe.db.get_value("Warehouse", warehouse, "company")
    se.stock_entry_type = "Material Receipt"
    se.purpose = "Material Receipt"
    se.set_posting_time = 1
    se.posting_date = posting_date
    se.posting_time = "00:00:01"
    se.append("items", {
        "item_code": item_code,
        "qty": qty_needed,
        "basic_rate": rate or 0,
        "t_warehouse": warehouse,
        "allow_zero_valuation_rate": 0 if rate else 1,
    })
    se.flags.ignore_permissions = True
    se.insert()
    se.submit()


def _cancel_with_heal(doctype, name, max_attempts=30):
    """Hujjatni bekor qilish; bekor qilish keyingi iste'molni minusga tushirsa,
    yetishmovchilikni nuqtaviy kirim bilan yopib qayta urinadi. True=bekor bo'ldi."""
    for _attempt in range(max_attempts):
        try:
            doc = frappe.get_doc(doctype, name)
            if doc.docstatus != 1:
                return True
            doc.flags.ignore_permissions = True
            doc.cancel()
            frappe.db.commit()
            return True
        except Exception as e:
            frappe.db.rollback()
            m = _NEG_STOCK_RE.search(str(e))
            if not m:
                frappe.log_error(
                    title=f"Report Service: bekor qilishda heal bo'lmaydigan xato ({name})",
                    message=str(e)[:2000],
                )
                return False
            try:
                _make_heal_receipt(
                    unquote(m.group(2)), unquote(m.group(3)), float(m.group(1)),
                    frappe.db.get_value(doctype, name, "posting_date"),
                )
                frappe.db.commit()
            except Exception as heal_err:
                frappe.db.rollback()
                frappe.log_error(
                    title=f"Report Service: bekor-heal xatosi ({name})",
                    message=f"{heal_err!r}\n\noriginal: {e}",
                )
                return False
    return False


def _cancel_se_with_heal(name, branch, settings):
    return _cancel_with_heal("Stock Entry", name)


def _process_with_heal_loop(cid, rows, settings, branch, wh_map):
    """Sync'dagi bilan bir xil heal-retry + stall-guard sikli (qayta ishlatish uchun)."""
    healed_signatures = {}
    for _attempt in range(MAX_HEAL_ATTEMPTS):
        try:
            result = _process_cid_group(cid, rows, settings, branch, wh_map)
            frappe.db.commit()
            return result
        except Exception as e:
            frappe.db.rollback()
            if "NegativeStockError" in type(e).__name__ or "needed in" in str(e):
                sig_m = _NEG_STOCK_RE.search(str(e))
                sig = (sig_m.group(1), sig_m.group(2), sig_m.group(3)) if sig_m else None
                if sig is None or healed_signatures.get(sig, 0) < MAX_SAME_SHORTFALL_REPEATS:
                    try:
                        healed = _try_heal_negative_stock(str(e), branch, rows, settings.dealer_branches)
                    except Exception as heal_err:
                        frappe.db.rollback()
                        healed = False
                        frappe.log_error(
                            title=f"Report Service qayta-tekshiruv: heal xatosi, {branch.label} cid {cid}",
                            message=f"{heal_err!r}\n\noriginal: {e}",
                        )
                    if healed:
                        if sig:
                            healed_signatures[sig] = healed_signatures.get(sig, 0) + 1
                        frappe.db.commit()
                        continue
            frappe.log_error(
                title=f"Report Service qayta-tekshiruv: {branch.label} cid {cid}",
                message=str(e)[:2000],
            )
            return "failed"
    return "failed"


MAX_REVERIFY_DELETIONS = 20  # bitta filial/bitta yurishda bekor qilinadigan
# "manbada o'chirilgan" hujjatlar chegarasi -- API vaqtincha chala javob
# qaytarsa ommaviy noto'g'ri bekor-qilishdan saqlaydi.

REVERIFY_DAYS = 7


def reverify_recent_transactions(days=None):
    """Kunlik orqaga-qarash tekshiruvi (foydalanuvchi talabi 2026-08-16):
    manba dasturda o'tmish tranzaksiyalar TAHRIRLANSA yoki O'CHIRILSA,
    ERPNext hujjatlari ham moslashtiriladi.

    `days` berilmasa REVERIFY_DAYS (kunlik jadval); bir martalik chuqur yurish
    uchun qo'lda kattaroq qiymat berish mumkin, masalan iyuldan beri:

        bench --site <sayt> execute \\
            akfa_diller.akfa_diller.api.report_service_sync.reverify_recent_transactions \\
            --kwargs "{'days': 47}"

    Oxirgi REVERIFY_DAYS kun bo'yicha, har filial uchun:
      - API'da bor, ERPNextda yo'q (3-kunlik sync oynasidan kech kelganlar
        ham) -> yaratiladi;
      - ikkalasida bor, lekin qator soni / jami dona / jami summa farq
        qilsa (tahrirlangan) -> hujjat bekor qilinib qaytadan yaratiladi;
      - ERPNextda bor, API'da endi yo'q (o'chirilgan) -> bekor qilinadi
        (himoya chegarasi bilan).
    """
    settings = frappe.get_single("Report Service Settings")
    if not settings.sync_enabled:
        return
    base_url, token = report_service_client.get_token()
    wh_map = _get_branch_warehouse_map()

    to_date = today()
    from_date = str(getdate(today()) - timedelta(days=int(days) if days else REVERIFY_DAYS))

    summary = []
    ordered_branches = sorted(settings.dealer_branches, key=lambda b: (0 if b.is_main else 1))
    for branch in ordered_branches:
        try:
            rows = report_service_client.fetch_all_rows_for_dealer(
                base_url, token, branch.dealer_id, from_date, to_date
            )
        except Exception as e:
            frappe.log_error(
                title=f"Report Service qayta-tekshiruv: fetch xatosi ({branch.label})",
                message=str(e)[:1000],
            )
            continue

        groups = _group_by_cid(rows)
        created = replaced = deleted = 0

        for cid, cid_rows in groups.items():
            ref = _external_ref(cid, branch)
            doc = None
            for dt in ("Stock Entry", "Sales Invoice", "Purchase Invoice"):
                name = frappe.db.get_value(dt, {"custom_report_service_cid": ref}, "name")
                if name:
                    doc = (dt, name)
                    break
            if doc is None:
                result = _process_with_heal_loop(cid, cid_rows, settings, branch, wh_map)
                if result == "processed":
                    created += 1
                continue

            dt, name = doc
            if frappe.db.get_value(dt, name, "docstatus") != 1:
                continue  # bekor qilingan (ongli qaror bilan) -- tegmaymiz

            api_count = len([r for r in cid_rows if (r.get("qty") or 0)])
            api_qty = sum(abs(r.get("qty") or 0) for r in cid_rows)
            api_amount = sum(r.get("amount") or 0 for r in cid_rows)
            child = {"Stock Entry": "Stock Entry Detail", "Sales Invoice": "Sales Invoice Item",
                     "Purchase Invoice": "Purchase Invoice Item"}[dt]
            agg = frappe.db.sql(
                f"select count(*), coalesce(sum(abs(qty)), 0), coalesce(sum(abs(qty * rate)), 0) "
                f"from `tab{child}` where parent = %s" if dt != "Stock Entry" else
                f"select count(*), coalesce(sum(abs(qty)), 0), 0 from `tab{child}` where parent = %s",
                (name,),
            )[0]
            doc_count, doc_qty, doc_amount = int(agg[0]), float(agg[1]), float(agg[2])

            qty_ok = abs(doc_qty - api_qty) < 0.01
            count_ok = doc_count == api_count
            amount_ok = dt == "Stock Entry" or abs(doc_amount - abs(api_amount)) < 0.05
            if qty_ok and count_ok and amount_ok:
                continue

            # tahrirlangan: bekor qilib, ref'ni bo'shatib, qaytadan yaratamiz
            if not _cancel_with_heal(dt, name):
                continue
            frappe.db.set_value(dt, name, "custom_report_service_cid", None, update_modified=False)
            frappe.db.commit()
            result = _process_with_heal_loop(cid, cid_rows, settings, branch, wh_map)
            replaced += 1
            frappe.logger("report_service_sync").info(
                f"qayta-tekshiruv: {ref} tahrirlangan -- {name} bekor, qayta yaratildi ({result}); "
                f"API {api_count} qator/{api_qty:g} dona/{api_amount:g} vs hujjat {doc_count}/{doc_qty:g}/{doc_amount:g}"
            )

        # manbada o'chirilganlar
        api_cids = {str(c) for c in groups}
        prefix = f"{branch.dealer_id}:%"
        candidates = []
        for dt in ("Stock Entry", "Sales Invoice", "Purchase Invoice"):
            candidates += [
                (dt, r.name, r.custom_report_service_cid)
                for r in frappe.get_all(
                    dt,
                    filters={
                        "custom_report_service_cid": ["like", prefix],
                        "docstatus": 1,
                        "posting_date": ["between", [from_date, to_date]],
                    },
                    fields=["name", "custom_report_service_cid"],
                )
            ]
        orphans = [c for c in candidates if c[2].split(":", 1)[1] not in api_cids]
        if len(orphans) > MAX_REVERIFY_DELETIONS:
            frappe.log_error(
                title=f"Report Service qayta-tekshiruv: juda ko'p o'chirilgan-nomzod ({branch.label})",
                message=f"{len(orphans)} ta hujjat API'da topilmadi (chegara {MAX_REVERIFY_DELETIONS}) -- "
                "API chala javob bergan bo'lishi mumkin, hech narsa bekor qilinmadi.",
            )
        else:
            for dt, name, ref in orphans:
                if _cancel_with_heal(dt, name):
                    deleted += 1
                    frappe.logger("report_service_sync").info(
                        f"qayta-tekshiruv: {ref} manbada o'chirilgan -- {name} bekor qilindi"
                    )

        if created or replaced or deleted:
            summary.append(f"{branch.label}: yangi={created}, tahrir={replaced}, o'chirilgan={deleted}")

    if summary:
        frappe.log_error(
            title="Report Service qayta-tekshiruv: o'zgarishlar",
            message="\n".join(summary),
        )


def backfill_window(from_date, to_date, dealer_ids=None):
    """Bir martalik orqa-to'ldirish: berilgan oynadagi barcha tranzaksiyalarni
    (mavjudlari o'tkazilib) qayta ishlaydi. Deploy'dan keyin qo'lda chaqiriladi:

        bench --site <sayt> execute \\
            akfa_diller.akfa_diller.api.report_service_sync.backfill_window \\
            --kwargs "{'from_date': '2026-08-01', 'to_date': '2026-08-09'}"

    dealer_ids berilsa (ro'yxat, masalan [56, 57, 58, 63, 234]) faqat o'sha
    filiallar; berilmasa hammasi. Idempotent: allaqachon hujjati borlar skip.
    """
    settings = frappe.get_single("Report Service Settings")
    base_url, token = report_service_client.get_token()
    wh_map = _get_branch_warehouse_map()
    wanted = {str(d) for d in dealer_ids} if dealer_ids else None

    ordered_branches = sorted(settings.dealer_branches, key=lambda b: (0 if b.is_main else 1))
    for branch in ordered_branches:
        if wanted is not None and str(branch.dealer_id) not in wanted:
            continue
        try:
            rows = report_service_client.fetch_all_rows_for_dealer(
                base_url, token, branch.dealer_id, str(from_date), str(to_date)
            )
        except Exception as e:
            print(f"{branch.label}: fetch xatosi: {e}")
            continue
        groups = _group_by_cid(rows)
        created = skipped = failed = 0
        for cid, cid_rows in groups.items():
            result = _process_with_heal_loop(cid, cid_rows, settings, branch, wh_map)
            if result == "processed":
                created += 1
            elif result == "skipped":
                skipped += 1
            else:
                failed += 1
        print(f"{branch.label}: yaratildi={created}, mavjud/o'tkazildi={skipped}, xato={failed}")


def _reconcile_receiver_delta(twin_name, items, branch, ref, posting_date):
    """Egizak (asosiy tomon SE'si) bilan filialning o'z qabul yozuvi orasidagi
    MIQDOR farqini filial omboriga tuzatish sifatida kiritadi (foydalanuvchi
    qarori 2026-08-16: "asosiy 10 jo'natsa, filial 11 qabul qilsa -- transfer
    10 bo'lsin, 1 tasi to'g'ri narxda alohida kirim bo'lsin; teskarisi ham").

    items -- filial feed'idan resolve qilingan qatorlar ({item_code, qty, rate});
    twin_name -- asosiy tomondan yaratilgan Stock Entry. Tuzatish hujjatlariga
    filial tranzaksiyasining o'z ref'i yoziladi -- keyingi synclarda xuddi shu
    tranzaksiya exists-tekshiruvda to'xtaydi (idempotent).
    """
    my_total = sum(i["qty"] for i in items)
    twin_lines = frappe.db.sql(
        "select item_code, sum(qty) from `tabStock Entry Detail` where parent = %s group by item_code",
        (twin_name,),
    )
    twin_map = {code: float(q) for code, q in twin_lines}
    twin_total = sum(twin_map.values())
    if abs(my_total - twin_total) < 0.01:
        return  # miqdor bir xil (sof nom-variant egizagi) -- tuzatish kerak emas

    my_map = {}
    for i in items:
        my_map[i["item_code"]] = my_map.get(i["item_code"], 0) + i["qty"]

    plus, minus = [], []
    for code in set(my_map) | set(twin_map):
        d = my_map.get(code, 0) - twin_map.get(code, 0)
        if d > 0.009:
            plus.append((code, d))
        elif d < -0.009:
            minus.append((code, -d))

    def _make(entry_type, lines, ref_value):
        se = frappe.new_doc("Stock Entry")
        se.company = branch.company
        se.stock_entry_type = entry_type
        se.purpose = entry_type
        se.set_posting_time = 1
        se.posting_date = posting_date
        se.posting_time = "23:59:59"
        if branch.cost_center:
            se.cost_center = branch.cost_center
        se.custom_report_service_cid = ref_value
        for code, qty in lines:
            rate = frappe.db.get_value("Bin", {"item_code": code, "valuation_rate": [">", 0]}, "valuation_rate")
            row = {"item_code": code, "qty": qty, "basic_rate": rate or 0,
                   "allow_zero_valuation_rate": 0 if rate else 1}
            if entry_type == "Material Receipt":
                row["t_warehouse"] = branch.warehouse
            else:
                row["s_warehouse"] = branch.warehouse
            se.append("items", row)
        se.flags.ignore_permissions = True
        se.insert()
        se.submit()
        return se.name

    # custom_report_service_cid UNIQUE (jonli isbot 2026-08-16: IntegrityError
    # 1062, 15 ta cid shu tufayli yiqilgan) -- ikkala tuzatish hujjatiga bir xil
    # ref yozib bo'lmaydi. Birinchisi asl ref'ni oladi (idempotentlik shunga
    # bog'langan: exists-tekshiruv aynan asl ref'ni qidiradi), ikkinchisiga
    # ":q" suffiksi.
    made = []
    if plus:
        made.append(_make("Material Receipt", plus, ref))
    if minus:
        made.append(_make("Material Issue", minus, ref if not plus else f"{ref}:q"))
    frappe.logger("report_service_sync").info(
        f"qabul-farqi tuzatildi: {branch.label} {ref} -- filial {my_total:g} vs asosiy {twin_total:g}; "
        f"tuzatish hujjatlari: {', '.join(made)}"
    )


# 0-narxli tovarlar uchun API'dan topilgan narxlar (2026-08-16 tekshiruvi:
# joriy/30-06/eski suratlar + variant-nom va boshqa-filial manbalari).
# 3 ta tovar hech qayerda narxsiz -- ro'yxatga kirmagan.
_ZERO_VALUATION_PRICES = [
    ("Samarqand -1 - SD", "Moskitnoe Kreplenie (Verh/nij) (7016) (Uz)", 0.12),
    ("Samarqand -1 - SD", "Rezina EPDM CCEP0051", 2.02),
    ("Samarqand -1 - SD", "T LAM (8017-Anthrazit Grey LL) Shtapik BKT 70 G32 (6.5m)", 17.71),
    ("Samarqand -1 - SD", "LAM (7011-Sheffal Dub LL) Moskitniy 544 Alum", 8.75),
    ("Samarqand -1 - SD", "(A) Kosa (V Dub Mokko) (N)", 25.33),
    ("Ishtixon - K", "T LAM (7011-Alyuks LL) Shtapik BKT 70 G24 (6.5m)", 18.69),
    ("Ishtixon - K", "(A) NEO ADC50 V0022 Qanot (Oq) (N)", 24.38),
    ("Ishtixon - K", "(A) NEO ADC50 M0022 Urta (Oq) (N)", 24.38),
    ("Ishtixon - K", "(A) NEO ADC50 G0004 Shtapik O (Oq) (N)", 7.39),
    ("Mitan - K", "(A) NEO ADC50 G0003 Shtapik O (Oq)", 7.39),
    ("Mitan - K", "(A) NEO ADC50 G0003 Shtapik O (SW306G)", 9.17),
    ("Mitan - K", "(A) NEO ADC50 G0003 Shtapik O (Oq) (N)", 7.39),
    ("Mitan - K", "Stanok Rezka IMPAK STORM", 530.0),
    ("Mitan - K", "LAM (8017-Alyuks LL) NEO ADC50 G0004 Shtapik O", 11.34),
    ("Mitan - K", "AKFA 7000 (Dub Mokko) Urta LAM (N)", 36.66),
    ("Mitan - K", "T LAM (8017-Zol Dub) Shtapik BKT 70 G24R (6.5m)", 19.65),
    ("Nurobod - SD", "TRIO 6000 (Zol Dub-S540) Kosa LAM (N)", 36.77),
    ("Nurobod - SD", "(A) NEO ADC50 V0026 Balkon Urta Ispanilet (V Dub Mokko) (N)", 44.37),
    ("Nurobod - SD", "AKFA 7000 (7011-Alyuks) Urta New LAM LL (N)", 36.66),
    ("Nurobod - SD", "PENTA 6500 (9016-Dub Mokko-Oq) Kosa LAM (N)", 25.35),
    ("Nurobod - SD", "PENTA 6500 (9016-Dub Mokko-Oq) Kanot LAM (N)", 27.99),
    ("Nurobod - SD", "PENTA 6500 (9016-Dub Mokko-Oq) Urta LAM (N)", 26.75),
]


def apply_item_valuations():
    """0-narxli tovarlarga API'dan topilgan narxlarni qo'yish (bir martalik,
    deploy'dan keyin qo'lda):

        bench --site <sayt> execute \\
            akfa_diller.akfa_diller.api.report_service_sync.apply_item_valuations

    Har (ombor, tovar) uchun joriy qoldiq bilan yangi valuation_rate'da Stock
    Reconciliation yoziladi (miqdor o'zgarmaydi, faqat narx). Qoldiq 0 yoki
    tovar mavjud bo'lmasa -- o'tkazib yuboriladi.
    """
    by_company = {}
    for wh, item, rate in _ZERO_VALUATION_PRICES:
        if not frappe.db.exists("Item", item):
            print(f"o'tkazildi (item yo'q): {item}")
            continue
        qty = frappe.db.get_value("Bin", {"item_code": item, "warehouse": wh}, "actual_qty") or 0
        if qty <= 0:
            print(f"o'tkazildi (qoldiq {qty:g}): {item} @ {wh}")
            continue
        cur_rate = frappe.db.get_value("Bin", {"item_code": item, "warehouse": wh}, "valuation_rate") or 0
        if cur_rate > 0.001:
            print(f"o'tkazildi (narxi bor {cur_rate:g}): {item} @ {wh}")
            continue
        company = frappe.db.get_value("Warehouse", wh, "company")
        by_company.setdefault(company, []).append((wh, item, float(qty), rate))

    for company, lines in by_company.items():
        sr = frappe.new_doc("Stock Reconciliation")
        sr.company = company
        sr.purpose = "Stock Reconciliation"
        for wh, item, qty, rate in lines:
            sr.append("items", {"item_code": item, "warehouse": wh, "qty": qty, "valuation_rate": rate})
        sr.flags.ignore_permissions = True
        sr.insert()
        sr.submit()
        frappe.db.commit()
        print(f"NARX QO'YILDI: {sr.name} ({company}) -- {len(lines)} tovar")
