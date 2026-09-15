# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class OynaNarxJadvali(Document):
	def validate(self):
		"""Har jadvalda bir xil kalitli ikkita qator bo'lmasin; qirra/burchak tarif-turlari ro'yxatda bo'lsin."""
		turlar = {"Qirra": set(), "Burchak": set(), "Kesim": set()}
		korilgan = set()
		for r in self.get("ishlov_turlari") or []:
			nom = (r.nom or "").strip()
			if not nom:
				frappe.throw(_("Ishlov turlari, {0}-qator: nom bo'sh").format(r.idx))
			key = (r.guruh, nom.lower())
			if key in korilgan:
				frappe.throw(_("Ishlov turlari: «{0}» ({1}) ikki marta — bir xil nom bo'lmasin").format(nom, r.guruh))
			korilgan.add(key)
			turlar.setdefault(r.guruh, set()).add(nom.lower())
			r.nom = nom
		if turlar["Qirra"] or turlar["Burchak"]:
			for r in self.get("narx_qirra") or []:
				if (r.tur or "").strip().lower() not in turlar["Qirra"]:
					frappe.throw(_("Qirra narxlari, {0}-qator: «{1}» turi Ishlov turlari ro'yxatida yo'q — avval ro'yxatga qo'shing").format(r.idx, r.tur))
			for r in self.get("narx_burchak") or []:
				if (r.tur or "").strip().lower() not in turlar["Burchak"]:
					frappe.throw(_("Burchak narxlari, {0}-qator: «{1}» turi Ishlov turlari ro'yxatida yo'q — avval ro'yxatga qo'shing").format(r.idx, r.tur))
			if turlar["Kesim"]:
				for r in self.get("narx_kesim") or []:
					if (r.tur or "").strip().lower() not in turlar["Kesim"]:
						frappe.throw(_("Kesim narxlari, {0}-qator: «{1}» turi Ishlov turlari ro'yxatida yo'q — avval ro'yxatga qo'shing").format(r.idx, r.tur))
		self._dublikat("narx_oyna", lambda r: (r.oyna_item,), _("Oyna narxlari"))
		self._dublikat("narx_qirra", lambda r: ((r.tur or "").strip().lower(), cint(r.qalinlik_mm), cint(r.kenglik_gacha)), _("Qirra narxlari"))
		self._dublikat("narx_burchak", lambda r: ((r.tur or "").strip().lower(), cint(r.qalinlik_mm)), _("Burchak narxlari"))
		self._dublikat("narx_teshik", lambda r: (cint(r.qalinlik_mm), cint(r.diametr_gacha)), _("Teshik narxlari"))
		self._dublikat("narx_kesim", lambda r: ((r.tur or "").strip().lower(), cint(r.qalinlik_mm)), _("Kesim narxlari"))
		self._dublikat("narx_ishlov", lambda r: (r.xizmat, cint(r.qalinlik_mm)), _("Ishlov narxlari"))
		qal = set()
		for r in self.get("chiqindi") or []:
			if cint(r.qalinlik_mm) in qal:
				frappe.throw(_("Chiqindi jadvalida {0} mm ikki marta").format(cint(r.qalinlik_mm)))
			qal.add(cint(r.qalinlik_mm))

	def _dublikat(self, field, key, nom):
		korilgan = {}
		for r in self.get(field) or []:
			k = key(r)
			if k in korilgan:
				frappe.throw(_("{0}: {1}- va {2}-qatorlar bir xil kalitga ega ({3}) — bittasini o'chiring").format(
					nom, korilgan[k], r.idx, " / ".join(str(x) for x in k)))
			korilgan[k] = r.idx

	def on_update(self):
		from akfa_diller.akfa_diller.api.oyna_konfigurator import ishlov_optionlarini_yangila
		ishlov_optionlarini_yangila(self)
