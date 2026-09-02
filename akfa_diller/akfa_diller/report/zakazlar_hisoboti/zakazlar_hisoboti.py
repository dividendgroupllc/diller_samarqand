# -*- coding: utf-8 -*-
# Zakazlar Hisoboti — zakaz sanasi, mijoz, kvadrat, summa, faktura (SI)
# sanasi, tovar tannarxi va foyda (Biznes panelidagi Zakazlar bloki bilan
# bir xil mantiq: akfa_diller.api.biznes_dashboard._zakaz_si_tannarx).
#
# Tannarx/foyda KOMPANIYA valyutasida (ombor-yozuvlari shu valyutada);
# Oyna sex zakazlarida xizmat-itemlar bo'lgani uchun tannarx faqat
# ombor-harakati mavjud zakazlarda chiqadi, qolganlarida bo'sh qoladi.

import frappe
from frappe import _
from frappe.utils import flt

from akfa_diller.akfa_diller.api.biznes_dashboard import _zakaz_si_tannarx


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = filters.company or "Oyna sex"

	params = {"company": company}
	shartlar = ""
	if filters.from_date:
		params["from_date"] = filters.from_date
		shartlar += " AND so.transaction_date >= %(from_date)s"
	if filters.to_date:
		params["to_date"] = filters.to_date
		shartlar += " AND so.transaction_date <= %(to_date)s"

	rows = frappe.db.sql(
		"""
		SELECT so.name, so.transaction_date, so.customer, so.customer_name,
		       IFNULL(so.custom_jami_kvadrat, 0) AS sqm,
		       so.currency, so.grand_total, so.base_grand_total,
		       so.workflow_state, so.status
		FROM `tabSales Order` so
		WHERE so.company = %(company)s AND so.docstatus = 1
		{shartlar}
		ORDER BY so.transaction_date DESC, so.name DESC
		""".format(shartlar=shartlar),
		params,
		as_dict=True,
	)

	qo = _zakaz_si_tannarx([r.name for r in rows])
	valyuta = frappe.get_cached_value("Company", company, "default_currency")

	data = []
	for r in rows:
		q = qo.get(r.name) or {}
		t = flt(q.get("tannarx"))
		bor = t > 0.005
		data.append(
			{
				"zakaz": r.name,
				"zakaz_sanasi": r.transaction_date,
				"mijoz": r.customer_name or r.customer,
				"kvadrat": flt(r.sqm),
				"summa": flt(r.grand_total),
				"valyuta": r.currency,
				"faktura_sanasi": q.get("si_sana"),
				"tannarx": t if bor else None,
				"foyda": (flt(r.base_grand_total) - t) if bor else None,
				"holat": r.workflow_state or r.status,
			}
		)

	columns = [
		{"fieldname": "zakaz", "label": _("Zakaz"), "fieldtype": "Link",
		 "options": "Sales Order", "width": 170},
		{"fieldname": "zakaz_sanasi", "label": _("Zakaz sanasi"),
		 "fieldtype": "Date", "width": 105},
		{"fieldname": "mijoz", "label": _("Mijoz"), "fieldtype": "Data",
		 "width": 180},
		{"fieldname": "kvadrat", "label": _("Kvadrat (m²)"),
		 "fieldtype": "Float", "precision": 2, "width": 105},
		{"fieldname": "summa", "label": _("Zakaz summasi"),
		 "fieldtype": "Float", "precision": 0, "width": 130},
		{"fieldname": "valyuta", "label": _("Valyuta"), "fieldtype": "Data",
		 "width": 70},
		{"fieldname": "faktura_sanasi", "label": _("Faktura sanasi"),
		 "fieldtype": "Date", "width": 110},
		{"fieldname": "tannarx", "label": _("Tovar tannarxi ({0})").format(valyuta),
		 "fieldtype": "Float", "precision": 0, "width": 130},
		{"fieldname": "foyda", "label": _("Foyda ({0})").format(valyuta),
		 "fieldtype": "Float", "precision": 0, "width": 120},
		{"fieldname": "holat", "label": _("Holat"), "fieldtype": "Data",
		 "width": 120},
	]
	return columns, data
