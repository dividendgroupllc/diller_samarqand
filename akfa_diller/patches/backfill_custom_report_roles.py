# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""Backfill the standard roles onto existing custom reports (one-time)."""

import frappe

from akfa_diller.akfa_diller.api.report_roles import DEFAULT_REPORT_ROLES, AKFA_MODULE


def execute():
	names = set(
		frappe.get_all("Report", filters={"module": AKFA_MODULE}, pluck="name")
		+ frappe.get_all("Report", filters={"is_standard": "No"}, pluck="name")
	)

	for name in names:
		existing = set(
			frappe.get_all(
				"Has Role",
				filters={"parent": name, "parenttype": "Report"},
				pluck="role",
			)
		)
		missing = [
			r for r in DEFAULT_REPORT_ROLES
			if r not in existing and frappe.db.exists("Role", r)
		]
		if not missing:
			continue

		try:
			doc = frappe.get_doc("Report", name)
			for role in missing:
				doc.append("roles", {"role": role})
			doc.save(ignore_permissions=True)
		except Exception as err:
			frappe.log_error(f"backfill_custom_report_roles failed for {name}: {err}")
