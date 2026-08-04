# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""
Sales Order link-field filters for the "Oyna sex" company.

When a Sales Order's company is "Oyna sex":
  - Customer list  -> only customers whose Customer Group is under the
    "Oyna sex" parent Customer Group.
  - Item list      -> only NON-stock items (is_stock_item = 0) whose Item Group
    is under the "Oyna sex" parent Item Group.

The Company, Customer Group and Item Group all share the same name ("Oyna sex").
Descendant matching uses the nested-set (lft/rgt) columns of the tree, so the
whole subtree of the parent group is included.
"""

import frappe

# Company / Customer Group / Item Group name that drives the filtered lists.
OYNA_SEX = "Oyna sex"


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def oyna_sex_customers(doctype, txt, searchfield, start, page_len, filters):
	"""Customers whose Customer Group is inside the 'Oyna sex' group subtree."""
	node = frappe.db.get_value("Customer Group", OYNA_SEX, ["lft", "rgt"], as_dict=True)
	if not node:
		return []

	return frappe.db.sql(
		"""
		SELECT c.name, c.customer_name
		FROM `tabCustomer` c
		INNER JOIN `tabCustomer Group` cg ON cg.name = c.customer_group
		WHERE cg.lft >= %(lft)s AND cg.rgt <= %(rgt)s
		  AND c.disabled = 0
		  AND (c.name LIKE %(txt)s OR c.customer_name LIKE %(txt)s)
		ORDER BY c.customer_name, c.name
		LIMIT %(start)s, %(page_len)s
		""",
		{
			"lft": node.lft,
			"rgt": node.rgt,
			"txt": "%%%s%%" % txt,
			"start": start,
			"page_len": page_len,
		},
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def oyna_sex_items(doctype, txt, searchfield, start, page_len, filters):
	"""Non-stock items (is_stock_item = 0) inside the 'Oyna sex' item-group subtree.

	Used for the Sales Order item lines (the SERVICE being sold).
	"""
	return _oyna_sex_items_by_stock(txt, start, page_len, is_stock_item=0)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def oyna_sex_stock_items(doctype, txt, searchfield, start, page_len, filters):
	"""Stock items (is_stock_item = 1) inside the 'Oyna sex' item-group subtree.

	Used for the "Tovar rasxod" dialog (the raw MATERIALS consumed to fulfil the
	order -> Material Issue / COGS).
	"""
	return _oyna_sex_items_by_stock(txt, start, page_len, is_stock_item=1)


def _oyna_sex_items_by_stock(txt, start, page_len, is_stock_item):
	"""Shared item search under the 'Oyna sex' item-group subtree, filtered by
	is_stock_item (0 = service, 1 = stock material)."""
	node = frappe.db.get_value("Item Group", OYNA_SEX, ["lft", "rgt"], as_dict=True)
	if not node:
		return []

	return frappe.db.sql(
		"""
		SELECT i.name, i.item_name, i.item_group
		FROM `tabItem` i
		INNER JOIN `tabItem Group` ig ON ig.name = i.item_group
		WHERE ig.lft >= %(lft)s AND ig.rgt <= %(rgt)s
		  AND i.is_stock_item = %(is_stock_item)s
		  AND i.disabled = 0
		  AND (i.name LIKE %(txt)s OR i.item_name LIKE %(txt)s)
		ORDER BY i.item_name, i.name
		LIMIT %(start)s, %(page_len)s
		""",
		{
			"lft": node.lft,
			"rgt": node.rgt,
			"is_stock_item": is_stock_item,
			"txt": "%%%s%%" % txt,
			"start": start,
			"page_len": page_len,
		},
	)
