# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""«Oyna Zakaz» — Oyna sex zakaz-hujjati (parametrik konfigurator).

Controller yupqa: butun mantiq api/oyna_konfigurator.py da.
Oqim: Qoralama → submit (Zakaz olindi) → Tayyor → Topshirildi (Sales Invoice + Material Issue).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from akfa_diller.akfa_diller.api import oyna_konfigurator as k

QULF = ("holat", "sales_invoice", "material_issue")  # faqat oqim (holat_ozgartir → db_set) o'zgartiradi


class OynaZakaz(Document):
	def validate(self):
		k.tayyorla(self)
		k.hisobla(self)

	def on_update(self):
		self._chizmalarni_bogla()

	def before_update_after_submit(self):
		eski = frappe.db.get_value(self.doctype, self.name, list(QULF), as_dict=True) or {}
		for f in QULF:
			if (self.get(f) or None) != (eski.get(f) or None):
				frappe.throw(_("«{0}» maydoni tasdiqlangan zakazda qo'lda o'zgartirilmaydi — holat tugmalaridan foydalaning").format(self.meta.get_label(f)))
		self.qoldiq = k._pul(flt(self.jami_summa) - flt(self.oldindan_tolov))  # avans o'zgarsa qoldiq qayta

	def on_update_after_submit(self):
		self._chizmalarni_bogla()

	def on_submit(self):
		k.on_submit(self)

	def before_cancel(self):
		k.bekor_tekshir(self)

	def on_cancel(self):
		k.on_cancel(self)

	def _chizmalarni_bogla(self):
		"""Dialogdan yuklangan «Chizma / shablon» fayli hujjatga bog'lanmagan (private, yetim) bo'ladi — boshqa
		foydalanuvchi 403 olmasligi uchun shu hujjatga biriktiriladi."""
		for p in self.get("pozitsiyalar") or []:
			if not p.get("chizma"):
				continue
			f = frappe.db.get_value("File", {"file_url": p.chizma, "attached_to_name": ("in", ("", None))}, "name")
			if f:
				frappe.db.set_value("File", f, {"attached_to_doctype": self.doctype, "attached_to_name": self.name,
				                                "attached_to_field": "chizma"}, update_modified=False)
