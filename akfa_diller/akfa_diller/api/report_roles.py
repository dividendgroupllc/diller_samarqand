# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""
Auto-assign standard roles to custom reports.

So report roles don't have to be added by hand ("Add Row") every time, these
roles are ensured on every save of a custom report. "Custom report" here means:
a non-standard report (created via the UI) OR any report in the "Akfa diller"
module.
"""

import frappe

DEFAULT_REPORT_ROLES = ["Accounts Manager", "Accounts User", "System Manager"]
AKFA_MODULE = "Akfa diller"


def is_custom_report(doc):
	return doc.get("is_standard") == "No" or doc.get("module") == AKFA_MODULE


def ensure_report_roles(doc, method=None):
	"""Report.validate hook: append the standard roles if missing."""
	if not is_custom_report(doc):
		return

	existing = {r.role for r in (doc.roles or [])}
	for role in DEFAULT_REPORT_ROLES:
		if role in existing:
			continue
		if not frappe.db.exists("Role", role):
			continue
		doc.append("roles", {"role": role})
		existing.add(role)


def ensure_roles_on_all_custom_reports():
	"""after_migrate hook: guarantee the standard roles on every custom report.

	Runs during migrate (frappe.flags.in_migrate is set), which bypasses the
	"standard reports need developer mode" block — so it works even on servers
	that are not in developer mode and covers newly deployed standard reports.
	"""
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
			frappe.log_error(f"ensure_roles_on_all_custom_reports failed for {name}: {err}")
