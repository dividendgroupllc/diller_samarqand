from typing import Dict, List
from dataclasses import dataclass
from contextlib import contextmanager
from collections import defaultdict

import frappe
from frappe import _


@dataclass
class StockEntryConfig:
    """Configuration for Material Transfer Stock Entry creation (real warehouse-to-
    warehouse movement -- both source and target are actual Warehouse records in the
    same Company, no financial document/party involved)."""
    company: str
    source_warehouse: str
    target_warehouse: str
    posting_date: str
    cost_center: str = ""
    posting_time: str = "23:59:59"
    # Optional dedup tag, set on the doc BEFORE insert()/submit() -- see
    # InvoiceConfig's external_ref_field for why this can't be a separate call
    # made after the document is already posted.
    external_ref_field: str = ""
    external_ref_value: str = ""


class StockEntryService:
    """Service for Material Transfer Stock Entry operations."""

    @contextmanager
    def _entry_flags(self, mute_messages: bool = True):
        original_mute = getattr(frappe.flags, "mute_messages", False)

        try:
            frappe.flags.mute_messages = mute_messages
            yield
        finally:
            frappe.flags.mute_messages = original_mute

    def create_material_transfer(
        self,
        items: List[Dict],
        config: StockEntryConfig,
        submit: bool = True,
    ) -> str:
        """Create ONE Material Transfer Stock Entry (e.g. for a single branch-as-
        customer cid group) with item consolidation. No party/financial liability --
        this only moves stock between two real warehouses of the same company."""
        if not items:
            frappe.throw(_("Hech qanday tovar yo'q"))

        if not config.source_warehouse or not config.target_warehouse:
            frappe.throw(_("Manba yoki maqsad ombor tanlanmagan"))

        # Consolidate items by item_code and rate to avoid duplicate lines
        consolidated = defaultdict(float)
        for item in items:
            key = (item.get("item_code"), item.get("rate", 0))
            consolidated[key] += item.get("qty", 0)

        with self._entry_flags(mute_messages=True):
            se = frappe.new_doc("Stock Entry")

            se.company = config.company
            se.stock_entry_type = "Material Transfer"
            se.purpose = "Material Transfer"
            se.set_posting_time = 1
            se.posting_date = config.posting_date
            se.posting_time = config.posting_time
            if config.cost_center:
                se.cost_center = config.cost_center
            if config.external_ref_field:
                se.set(config.external_ref_field, config.external_ref_value)

            for (item_code, rate), qty in consolidated.items():
                se.append("items", {
                    "item_code": item_code,
                    "qty": qty,
                    "basic_rate": rate,
                    "s_warehouse": config.source_warehouse,
                    "t_warehouse": config.target_warehouse,
                    "cost_center": config.cost_center or None,
                })

            se.flags.ignore_permissions = True
            se.insert()

            if submit:
                se.submit()

            return se.name

    def cancel_entry(self, entry_name: str) -> bool:
        if not entry_name or not frappe.db.exists("Stock Entry", entry_name):
            return False

        se = frappe.get_doc("Stock Entry", entry_name)
        if se.docstatus == 1:
            se.flags.ignore_permissions = True
            se.cancel()
            return True

        return False

    def calculate_totals(self, items: List[Dict]) -> Dict:
        total_qty = sum(item.get("qty", 0) for item in items)
        total_amount = sum(item.get("qty", 0) * item.get("rate", 0) for item in items)

        return {"total_qty": total_qty, "total_amount": total_amount}


stock_entry_service = StockEntryService()
