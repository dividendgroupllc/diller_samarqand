# -*- coding: utf-8 -*-
"""PL Hisoboti — PDF eksport (dizayn umumiy report/pl_pdf.py modulida)."""

import frappe

from akfa_diller.akfa_diller.report import pl_pdf
from akfa_diller.akfa_diller.report.pl_hisoboti.pl_hisoboti import execute


@frappe.whitelist()
def generate_pl_pdf(filters=None):
	import json
	f = json.loads(filters) if isinstance(filters, str) else (filters or {})
	pl_pdf.send(
		f,
		execute,
		title="%s · P&L hisoboti" % (f.get("company") or ""),
		subtitle="Akfa diller",
		filename_prefix="PL_Hisoboti",
		report_name="PL Hisoboti",
	)
