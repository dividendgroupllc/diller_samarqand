# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""
Company-scoped party/item filtering driven by the "Company Group Setup" doctype.

Each company maps to a set of Customer / Supplier / Item Groups. Transaction
forms (Purchase Invoice, Kassa, ...) then only offer parties/items that belong
to the selected company's groups (group subtree). If a company has no mapping,
no filter is applied (everything is offered).
"""

import frappe

GROUP_DOCTYPE = {
	"Customer Group": ("Customer", "customer_group", "customer_name"),
	"Supplier Group": ("Supplier", "supplier_group", "supplier_name"),
	"Item Group": ("Item", "item_group", "item_name"),
}


def _expand_groups(group_doctype, group_names):
	"""Every descendant group name (incl. self) of the given tree groups."""
	group_names = [g for g in (group_names or []) if g]
	if not group_names:
		return []

	nodes = frappe.get_all(
		group_doctype, filters={"name": ["in", group_names]}, fields=["lft", "rgt"]
	)
	if not nodes:
		return []

	conds = " OR ".join(["(lft >= %s AND rgt <= %s)"] * len(nodes))
	params = []
	for n in nodes:
		params.extend([n.lft, n.rgt])

	rows = frappe.db.sql(
		f"SELECT name FROM `tab{group_doctype}` WHERE {conds}", params
	)
	return [r[0] for r in rows]


def get_company_group_names(company, group_type):
	"""Expanded (subtree) group names configured for a company + group type.

	Returns [] when the company has no mapping for that group type -> callers
	treat [] as "no restriction".
	"""
	if not company or group_type not in GROUP_DOCTYPE:
		return []

	# Company Group Setup is autonamed by company, so parent == company.
	configured = frappe.get_all(
		"Company Group Line",
		filters={
			"parenttype": "Company Group Setup",
			"parent": company,
			"group_type": group_type,
		},
		pluck="group",
	)
	if not configured:
		return []

	return _expand_groups(group_type, configured)


def _search(target_doctype, group_field, name_field, allowed_groups, txt, start, page_len, extra_cond="", extra_params=None):
	params = {
		"txt": "%%%s%%" % (txt or ""),
		"start": start,
		"page_len": page_len,
	}
	if extra_params:
		params.update(extra_params)

	group_clause = ""
	if allowed_groups:
		group_clause = f" AND `{group_field}` IN %(groups)s"
		params["groups"] = tuple(allowed_groups)

	return frappe.db.sql(
		f"""
		SELECT name, `{name_field}`
		FROM `tab{target_doctype}`
		WHERE IFNULL(disabled, 0) = 0
		  {group_clause}
		  {extra_cond}
		  AND (name LIKE %(txt)s OR `{name_field}` LIKE %(txt)s)
		ORDER BY `{name_field}`, name
		LIMIT %(start)s, %(page_len)s
		""",
		params,
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def company_suppliers(doctype, txt, searchfield, start, page_len, filters):
	company = (filters or {}).get("company")
	groups = get_company_group_names(company, "Supplier Group")
	return _search("Supplier", "supplier_group", "supplier_name", groups, txt, start, page_len)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def company_customers(doctype, txt, searchfield, start, page_len, filters):
	company = (filters or {}).get("company")
	groups = get_company_group_names(company, "Customer Group")
	return _search("Customer", "customer_group", "customer_name", groups, txt, start, page_len)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def company_items(doctype, txt, searchfield, start, page_len, filters):
	filters = filters or {}
	company = filters.get("company")
	groups = get_company_group_names(company, "Item Group")

	extra_cond = ""
	if filters.get("is_purchase_item"):
		extra_cond += " AND is_purchase_item = 1"
	if filters.get("is_sales_item"):
		extra_cond += " AND is_sales_item = 1"
	# never offer template items with variants
	extra_cond += " AND IFNULL(has_variants, 0) = 0"

	return _search("Item", "item_group", "item_name", groups, txt, start, page_len, extra_cond=extra_cond)
