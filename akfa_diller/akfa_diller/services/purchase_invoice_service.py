from typing import Dict, List
from dataclasses import dataclass
from contextlib import contextmanager
from collections import defaultdict

import frappe
from frappe import _


@dataclass
class PurchaseInvoiceConfig:
    """Configuration for Purchase Invoice creation."""
    company: str
    warehouse: str
    posting_date: str
    supplier: str = ""
    cost_center: str = ""
    posting_time: str = "23:59:59"
    update_stock: bool = True
    # Optional dedup tag, set on the doc BEFORE insert()/submit() -- see
    # InvoiceConfig's external_ref_field for why this can't be a separate call
    # made after the document is already posted.
    external_ref_field: str = ""
    external_ref_value: str = ""


class PurchaseInvoiceService:
    """Service for Purchase Invoice operations."""

    @contextmanager
    def _invoice_flags(self, mute_messages: bool = True):
        original_mute = getattr(frappe.flags, "mute_messages", False)

        try:
            frappe.flags.mute_messages = mute_messages
            yield
        finally:
            frappe.flags.mute_messages = original_mute

    def create_purchase_invoice(
        self,
        items: List[Dict],
        config: PurchaseInvoiceConfig,
        submit: bool = True,
    ) -> str:
        """Create ONE Purchase Invoice (e.g. for a single Ta'minotchi+Sana group) with item consolidation."""
        if not items:
            frappe.throw(_("Hech qanday tovar yo'q"))

        if not config.supplier:
            frappe.throw(_("Ta'minotchi (Supplier) tanlanmagan"))

        if not config.cost_center:
            frappe.throw(_("Cost Center tanlanmagan"))

        # Consolidate items by item_code and rate to avoid duplicate lines
        consolidated = defaultdict(float)
        for item in items:
            key = (item.get("item_code"), item.get("rate", 0))
            consolidated[key] += item.get("qty", 0)

        with self._invoice_flags(mute_messages=True):
            pi = frappe.new_doc("Purchase Invoice")

            pi.company = config.company
            pi.supplier = config.supplier
            pi.set_posting_time = 1
            pi.posting_date = config.posting_date
            pi.posting_time = config.posting_time
            pi.due_date = config.posting_date

            pi.payment_terms_template = ""
            pi.ignore_default_payment_terms_template = 1

            # update_stock=1 => goods are received INTO config.warehouse and
            # its rate becomes the incoming valuation rate -- unlike the Sales
            # side, no allow_zero_valuation_rate is set here: for a purchase
            # the paid rate legitimately DEFINES the stock valuation, there is
            # no "unknown cost" ambiguity to suppress a warning for.
            pi.update_stock = 1 if config.update_stock else 0
            pi.set_warehouse = config.warehouse
            # Also stamp the doc-level "Accounting Dimensions" cost_center, not
            # just each item row -- otherwise that section shows blank even
            # though every GL entry is correctly cost-centered underneath.
            pi.cost_center = config.cost_center
            if config.external_ref_field:
                pi.set(config.external_ref_field, config.external_ref_value)

            for (item_code, rate), qty in consolidated.items():
                pi.append("items", {
                    "item_code": item_code,
                    "qty": qty,
                    "rate": rate,
                    "warehouse": config.warehouse,
                    "cost_center": config.cost_center,
                })

            pi.flags.ignore_permissions = True
            pi.insert()

            if str(pi.due_date) < str(pi.posting_date):
                pi.db_set("due_date", pi.posting_date)
                pi.due_date = pi.posting_date

            if submit:
                pi.submit()

            return pi.name

    def cancel_invoice(self, invoice_name: str) -> bool:
        if not invoice_name or not frappe.db.exists("Purchase Invoice", invoice_name):
            return False

        pi = frappe.get_doc("Purchase Invoice", invoice_name)
        if pi.docstatus == 1:
            pi.flags.ignore_permissions = True
            pi.cancel()
            return True

        return False

    def calculate_totals(self, items: List[Dict]) -> Dict:
        total_qty = sum(item.get("qty", 0) for item in items)
        total_amount = sum(item.get("qty", 0) * item.get("rate", 0) for item in items)

        return {"total_qty": total_qty, "total_amount": total_amount}


purchase_invoice_service = PurchaseInvoiceService()
