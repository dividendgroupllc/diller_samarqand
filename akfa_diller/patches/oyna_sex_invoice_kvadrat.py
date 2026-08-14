# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""«Jami kvadrat (m²)» maydonini Sales Invoice'ga ham qo'shadi.

Oyna sexda kvadrat Sales Order sarlavhasida yuritiladi (`custom_jami_kvadrat`).
Zakaz submit bo'lganda undan Sales Invoice yaratiladi, lekin fakturada bunday
maydon bo'lmagani uchun kvadrat yo'qolib qolardi. Endi maydon fakturada ham
mavjud va `oyna_order._create_sales_invoice()` uni zakazdan ko'chiradi.

Idempotent: `create_custom_fields` mavjud maydonni qayta yaratmaydi.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Sales Invoice": [
				{
					"fieldname": "custom_jami_kvadrat",
					"label": "Jami kvadrat (m²)",
					"fieldtype": "Float",
					"precision": "3",
					"insert_after": "set_warehouse",
					"read_only": 1,
					"depends_on": "eval:doc.company=='Oyna sex'",
					"description": "Zakazdan (Sales Order) avtomatik ko'chiriladi",
				},
			]
		},
		ignore_validate=True,
	)

	_backfill_from_orders()


def _backfill_from_orders():
	"""Zakazdan yaratilgan mavjud fakturalarga kvadratni to'ldiradi.

	Bir faktura bir nechta zakazdan yig'ilishi mumkin, shuning uchun kvadrat
	bog'langan zakazlar bo'yicha jamlanadi.
	"""
	rows = frappe.db.sql(
		"""
		SELECT sii.parent AS invoice, SUM(so.custom_jami_kvadrat) AS sqm
		FROM `tabSales Invoice Item` sii
		INNER JOIN (
			SELECT DISTINCT parent, sales_order FROM `tabSales Invoice Item`
			WHERE IFNULL(sales_order, '') != ''
		) link ON link.parent = sii.parent AND link.sales_order = sii.sales_order
		INNER JOIN `tabSales Order` so ON so.name = sii.sales_order
		INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE si.docstatus < 2
		  AND IFNULL(so.custom_jami_kvadrat, 0) > 0
		  AND IFNULL(si.custom_jami_kvadrat, 0) = 0
		GROUP BY sii.parent
		""",
		as_dict=True,
	)

	for row in rows:
		frappe.db.set_value(
			"Sales Invoice", row.invoice, "custom_jami_kvadrat", row.sqm, update_modified=False
		)

	if rows:
		frappe.db.commit()
