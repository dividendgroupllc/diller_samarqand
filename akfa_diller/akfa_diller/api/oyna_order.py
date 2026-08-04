# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""
Oyna sex Sales Order flow.

When a Sales Order for the "Oyna sex" company is submitted (workflow final action
"Mijozga topshirildi"), two documents are created and submitted automatically:

  1) Sales Invoice   -> for the SERVICE (non-stock) items on the order (the sale).
  2) Material Issue  -> a Stock Entry consuming the materials listed in
     `custom_sarflangan_tovarlar`. This is the order's cost of goods (COGS),
     not a plain expense.
"""

import frappe
from frappe import _
from frappe.utils import flt

OYNA_SEX = "Oyna sex"
DEFAULT_MATERIAL_WAREHOUSE = "Oyna sex - Os"


def on_sales_order_submit(doc, method=None):
	"""Sales Order on_submit hook (only acts for the Oyna sex company)."""
	if doc.company != OYNA_SEX:
		return

	_create_sales_invoice(doc)
	_create_material_issue(doc)


def _create_sales_invoice(doc):
	"""Create + submit a Sales Invoice for the SO's service items."""
	# Skip if an invoice already exists for this order (e.g. re-submit / amend).
	if frappe.db.exists("Sales Invoice Item", {"sales_order": doc.name, "docstatus": ["<", 2]}):
		return

	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	si = make_sales_invoice(doc.name)
	si.update_stock = 0  # services -> no stock movement on the invoice
	si.flags.ignore_permissions = True
	si.insert(ignore_permissions=True)
	si.submit()

	frappe.msgprint(
		_("Sales Invoice yaratildi: {0}").format(
			frappe.utils.get_link_to_form("Sales Invoice", si.name)
		),
		alert=True,
	)


def _create_material_issue(doc):
	"""Create + submit a Material Issue Stock Entry for the consumed materials."""
	rows = [
		r for r in (doc.get("custom_sarflangan_tovarlar") or [])
		if r.item_code and flt(r.qty) > 0
	]
	if not rows:
		return

	# Skip if this order already has a submitted Material Issue linked.
	existing = doc.get("custom_material_issue")
	if existing and frappe.db.get_value("Stock Entry", existing, "docstatus") == 1:
		return

	company = doc.company
	expense_account = frappe.get_cached_value("Company", company, "default_expense_account")
	cost_center = frappe.get_cached_value("Company", company, "cost_center")

	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Issue"
	se.purpose = "Material Issue"
	se.company = company
	se.remarks = _("Sales Order {0} — tan narx (Material Issue)").format(doc.name)

	for r in rows:
		se.append("items", {
			"item_code": r.item_code,
			"qty": flt(r.qty),
			"uom": r.uom or None,
			"s_warehouse": r.warehouse or DEFAULT_MATERIAL_WAREHOUSE,
			"expense_account": expense_account,
			"cost_center": cost_center,
		})

	se.flags.ignore_permissions = True
	se.insert(ignore_permissions=True)
	se.submit()

	frappe.db.set_value("Sales Order", doc.name, "custom_material_issue", se.name)
	frappe.msgprint(
		_("Material Issue (tan narx) yaratildi: {0}").format(
			frappe.utils.get_link_to_form("Stock Entry", se.name)
		),
		alert=True,
	)
