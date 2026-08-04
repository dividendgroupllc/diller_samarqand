# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""Restrict the Oyna sex manager user to the Oyna sex company via User Permission.

Guarded + idempotent: does nothing if the user/company is missing or the
permission already exists. This makes standard ERPNext docs (invoices, payments,
list views, etc.) company-scoped for the user; the custom raw-SQL reports honour
the same restriction via report_utils.get_effective_company.
"""

import frappe

TARGET_USER = "oynasexmenedjeri@gmail.com"
TARGET_COMPANY = "Oyna sex"


def execute():
	if not frappe.db.exists("User", TARGET_USER):
		return
	if not frappe.db.exists("Company", TARGET_COMPANY):
		return
	if frappe.db.exists(
		"User Permission",
		{"user": TARGET_USER, "allow": "Company", "for_value": TARGET_COMPANY},
	):
		return

	frappe.get_doc({
		"doctype": "User Permission",
		"user": TARGET_USER,
		"allow": "Company",
		"for_value": TARGET_COMPANY,
		"apply_to_all_doctypes": 1,
	}).insert(ignore_permissions=True)
