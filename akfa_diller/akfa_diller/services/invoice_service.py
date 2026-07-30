from typing import Dict, List
from dataclasses import dataclass
from contextlib import contextmanager
from collections import defaultdict

import frappe
from frappe import _


@dataclass
class InvoiceConfig:
    """Configuration for Sales Invoice creation."""
    company: str
    warehouse: str
    posting_date: str
    customer: str = ""
    cost_center: str = ""
    posting_time: str = "23:59:59"
    update_stock: bool = True


class InvoiceService:
    """Service for Sales Invoice operations."""

    @contextmanager
    def _invoice_flags(self, mute_messages: bool = True):
        original_mute = getattr(frappe.flags, "mute_messages", False)

        try:
            frappe.flags.mute_messages = mute_messages
            yield
        finally:
            frappe.flags.mute_messages = original_mute

    def create_sales_invoice(
        self,
        items: List[Dict],
        config: InvoiceConfig,
        submit: bool = True,
    ) -> str:
        """Create ONE Sales Invoice (e.g. for a single Mijoz+Sana group) with item consolidation."""
        if not items:
            frappe.throw(_("Hech qanday tovar yo'q"))

        if not config.customer:
            frappe.throw(_("Mijoz (Customer) tanlanmagan"))

        if not config.cost_center:
            frappe.throw(_("Cost Center tanlanmagan"))

        # Consolidate items by item_code and rate to avoid duplicate lines
        consolidated = defaultdict(float)
        for item in items:
            key = (item.get("item_code"), item.get("rate", 0))
            consolidated[key] += item.get("qty", 0)

        with self._invoice_flags(mute_messages=True):
            si = frappe.new_doc("Sales Invoice")

            si.company = config.company
            si.customer = config.customer
            si.set_posting_time = 1
            si.posting_date = config.posting_date
            si.posting_time = config.posting_time
            si.due_date = config.posting_date

            si.payment_terms_template = ""
            si.ignore_default_payment_terms_template = 1

            si.update_stock = 1 if config.update_stock else 0
            si.set_warehouse = config.warehouse
            # Also stamp the doc-level "Accounting Dimensions" cost_center, not
            # just each item row -- otherwise that section shows blank even
            # though every GL entry is correctly cost-centered underneath.
            si.cost_center = config.cost_center

            for (item_code, rate), qty in consolidated.items():
                si.append("items", {
                    "item_code": item_code,
                    "qty": qty,
                    "rate": rate,
                    "warehouse": config.warehouse,
                    "cost_center": config.cost_center,
                    "allow_zero_valuation_rate": 1,
                })

            si.flags.ignore_permissions = True
            si.insert()

            if str(si.due_date) < str(si.posting_date):
                si.db_set("due_date", si.posting_date)
                si.due_date = si.posting_date

            if submit:
                si.submit()

            return si.name

    def cancel_invoice(self, invoice_name: str) -> bool:
        if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
            return False

        si = frappe.get_doc("Sales Invoice", invoice_name)
        if si.docstatus == 1:
            si.flags.ignore_permissions = True
            si.cancel()
            return True

        return False

    def calculate_totals(self, items: List[Dict]) -> Dict:
        total_qty = sum(item.get("qty", 0) for item in items)
        total_amount = sum(item.get("qty", 0) * item.get("rate", 0) for item in items)

        return {"total_qty": total_qty, "total_amount": total_amount}


invoice_service = InvoiceService()
