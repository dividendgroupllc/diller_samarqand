# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""Shared helpers for company-scoping custom (raw-SQL) reports.

Raw-SQL script reports bypass ERPNext's User Permission enforcement, so any
report that should honour a per-user company restriction must resolve the
effective company through get_effective_company().
"""

import frappe


def get_allowed_companies(user=None):
	"""Companies the user is restricted to via User Permission (Company).

	Empty list = no restriction (the user may see every company).
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return []
	return (
		frappe.get_all(
			"User Permission",
			filters={"user": user, "allow": "Company"},
			pluck="for_value",
		)
		or []
	)


def get_effective_company(filters):
	"""Resolve which company a report should be scoped to.

	- If the user is restricted (has Company User Permission), force that
	  company (the report can never show other companies for them). If they
	  also picked a company filter and it's within the allowed set, honour it.
	- Otherwise use the chosen company filter, or None (= all companies).
	"""
	allowed = get_allowed_companies()
	filter_company = (filters or {}).get("company")

	if allowed:
		if filter_company and filter_company in allowed:
			return filter_company
		return allowed[0]

	return filter_company or None


@frappe.whitelist()
def is_company_restricted_user():
	"""True if the current user is restricted to a company (Company User
	Permission). Used by client scripts to hide fields (e.g. Kassa cost center)
	for company-scoped managers."""
	return bool(get_allowed_companies())
