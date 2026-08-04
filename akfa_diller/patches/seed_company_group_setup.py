# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""Seed Company Group Setup for known companies + default Purchase Invoice update_stock=1.

Guarded/idempotent. Users can adjust the mapping afterwards in Company Group Setup.
"""

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

SEED = {
	"Oyna sex": {
		"Item Group": "Oyna sex",
		"Supplier Group": "Oyna sex",
		"Customer Group": "Oyna sex",
	},
	"Samarqand Diller": {
		"Item Group": "Samarqand 1",
		"Supplier Group": "Samarqand 1",
		"Customer Group": "Samarqand 1",
	},
}


def execute():
	_seed_company_groups()
	_pi_update_stock_default()


def _seed_company_groups():
	for company, groups in SEED.items():
		if not frappe.db.exists("Company", company):
			continue
		if frappe.db.exists("Company Group Setup", company):
			continue

		lines = [
			{"group_type": gtype, "group": gname}
			for gtype, gname in groups.items()
			if frappe.db.exists(gtype, gname)
		]
		if not lines:
			continue

		doc = frappe.new_doc("Company Group Setup")
		doc.company = company
		for line in lines:
			doc.append("groups", line)
		doc.insert(ignore_permissions=True)


def _pi_update_stock_default():
	# New Purchase Invoices default to update_stock = 1.
	make_property_setter(
		"Purchase Invoice",
		"update_stock",
		"default",
		"1",
		"Text",
		validate_fields_for_doctype=False,
	)
