import re
import time
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
MAX_HEAL_ATTEMPTS = 30  # a single branch-transfer cid can bundle 50+ distinct
# items (confirmed live 2026-08-07: a Kattaqo'rg'on->Ishtixon transfer needed
# healing on several different items in sequence, one shortfall discovered
# per attempt) -- healing is pure local DB work (no external API call), so a
# higher cap costs nothing but time and closes real transactions that would
# otherwise fail forever at the old cap of 6.

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

    for branch in settings.dealer_branches:
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
        if not branch.is_main:
            # See module docstring: a sub-branch's PRIXOD_BAZA is the other half
            # of a transfer the main branch's own feed already records -- skip to
            # avoid double-counting the same physical stock movement.
            return "skipped"
        # The main branch's 2 known bases ("BAZA (+998 __ ___ ____)", "Самарканд
        # База Катта Курган") already existed as real Suppliers on this site with
        # 91 real, manually-entered Purchase Invoices against them -- confirming
        # the business already books baza stock-arrival as a genuine purchase,
        # not a stock-only movement.
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
            return {
                "success": False,
                "valid_items": [],
                "errors": [{"row": idx, "error": f"qty=0 (productName={row.get('productName')})"}],
            }
        candidates.append({
            "item_name": row.get("productName"),
            "qty": qty,
            "rate": (row.get("amount") or 0) / qty,
            "row_num": idx,
        })

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
