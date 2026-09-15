# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""
Oyna sex — parametrik oyna-konfigurator dvigateli («Oyna Zakaz»).

Bir hujjatda: geometriya/hisob (10 mm yaxlitlash, min 0.25 m²), tarif-jadval
(«Oyna Narx Jadvali» Single) bo'yicha narx, GOST 30698 / EN 12150 qoidalari
(teshik-qirra 2t, teshik-teshik 2t, burchak 6t, Ø≥t, zakalka-chegaralari),
avto-sarf (chiqindi foizi bilan) va zakaz-oqimi: submit = «Zakaz olindi»
→ «Tayyor» → «Topshirildi».

⚠ «Oyna Zakaz» FAQAT hisob-kitob uchun ishlaydi (2026-09-15 foydalanuvchi qarori):
Sales Invoice ham, Material Issue ham YARATILMAYDI — ERPNext buxgalteriyasiga
va omboriga hech qanday ta'sir qilmaydi. Yagona yaratiladigan hujjat — «Oyna
Zakaz»ning o'zi. Sales Order esa gitdagi (prodda ishlab turgan) oqimida qoladi —
`api/oyna_order.py` + `oyna_sex_*` patchlari; bu modul unga TEGMAYDI.

Hujjat yaratishni yoqish uchun `topshir()` ichida `_sales_invoice_yarat` va
`_material_issue_yarat` chaqiriladi (funksiyalar saqlangan, hozir chaqirilmaydi)
va `_ensure_custom_fields`ga Sales Invoice havola-maydonlari qaytariladi.

Toza yadro (`_hisobla_core`) DB'siz ishlaydi — fixture bilan test qilinadi.
"""

import hashlib
import json
import math
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, add_days, nowdate

from akfa_diller.akfa_diller.services.oyna_raskroy import raskroy as _raskroy_hisobla, DEF as RASKROY_DEF

OYNA_SEX = "Oyna sex"
OMBOR = "Oyna sex - Os"
SOZLAMA = "Oyna Narx Jadvali"
ROL = "oyna sex menedjer"
TAXMINIY = "TAXMINIY"

TOMONLAR = (("qirra_yuqori", "yuqori", "①"), ("qirra_ong", "o'ng", "②"),
            ("qirra_pastki", "pastki", "③"), ("qirra_chap", "chap", "④"))
BURCHAKLAR = (("burchak_yc", "yuqori-chap"), ("burchak_yo", "yuqori-o'ng"),
              ("burchak_po", "pastki-o'ng"), ("burchak_pc", "pastki-chap"))
KATEGORIYALAR = ("oyna", "reska", "qirra", "burchak", "teshik", "kesim",
                 "matofka", "boyash", "zakalka", "markirovka", "paket")
KATEGORIYA_NOMI = {"oyna": "Oyna", "reska": "Reska (kesish)", "qirra": "Qirra ishlovi",
                   "burchak": "Burchak ishlovi", "teshik": "Teshiklar", "kesim": "Kesimlar",
                   "matofka": "Matofka", "boyash": "Oyna bo'yash", "zakalka": "Zakalka",
                   "markirovka": "Markirovka", "paket": "Paket yig'ish"}

#: Matofka turi → tarif kaliti. Uchalasi ham BUTUN oyna maydoniga hisoblanadi
#: (sex izohi 07.09.2026: polosa — lenta ostidagi joy shaffof, gul — naqsh shaffof).
MATOFKA_TURLARI = ("To'liq", "Polosa", "Gul")

# Ishlov turlari (qirra/burchak) — Narx Jadvalida sozlanadi; ro'yxat bo'sh bo'lsa shu seed
ISHLOV_TURLARI_SEED = [
	# Sexda haqiqatda bor turlar (narx ro'yxati 07.09.2026). Burchak ishlovi yo'q.
	dict(guruh="Qirra", nom="Kromka", chizma="O'rta chiziq", kenglik_kerak=0, min_qalinlik_mm=0,
	     tavsif="Qirrani silliqlash (so'm / metr)"),
	dict(guruh="Kesim", nom="Fortochka", eni_mm=300, boyi_mm=300, tavsif="Ochiladigan kichik oyna o'rni"),
	dict(guruh="Kesim", nom="Qulf", eni_mm=40, boyi_mm=80, tavsif="Qulf o'rni (vыrez)"),
	dict(guruh="Kesim", nom="Rozetka", eni_mm=70, boyi_mm=70, tavsif="Rozetka o'rni (vыrez)"),
	dict(guruh="Kesim", nom="Petlya", eni_mm=60, boyi_mm=40, tavsif="Petlya o'rni (vыrez)"),
	dict(guruh="Kesim", nom="Boshqa", eni_mm=100, boyi_mm=100, tavsif="Boshqa to'rtburchak kesim (vыrez)"),
]
OYNA_TURLARI = "Oddiy\nKo'zgu\nLakobel (bo'yalgan)\nNaqshli\nTayyor zakalangan\nTriplex"


def _ishlov_turlari(s):
	rows = [r for r in (s.get("ishlov_turlari") or []) if (r.get("nom") or "").strip()]
	if not rows:
		rows = [frappe._dict(r) for r in ISHLOV_TURLARI_SEED]
	out = {"Qirra": {}, "Burchak": {}, "Kesim": {}}
	for r in rows:
		out.setdefault(r.get("guruh") or "Qirra", {})[r.get("nom").strip()] = frappe._dict(
			chizma=r.get("chizma"), kenglik_kerak=cint(r.get("kenglik_kerak")), min_qalinlik_mm=cint(r.get("min_qalinlik_mm")),
			eni_mm=cint(r.get("eni_mm")), boyi_mm=cint(r.get("boyi_mm")))
	return out


@frappe.whitelist()
def kesim_turlari():
	frappe.has_permission("Oyna Zakaz", "read", throw=True)
	"""Dialog uchun: {nom: [odatiy eni, odatiy bo'yi]}."""
	return {n: [r.eni_mm, r.boyi_mm] for n, r in _ishlov_turlari(sozlama())["Kesim"].items()}


def ishlov_stillari(s):
	"""Chizma uchun: {qirra: {nom: chizma-uslubi}, burchak: {nom: chizma-uslubi}}."""
	t = _ishlov_turlari(s)
	return {"qirra": {n: r.chizma for n, r in t["Qirra"].items()}, "burchak": {n: r.chizma for n, r in t["Burchak"].items()}}


def _kenglik_kerak(tur, konst):
	r = (konst.get("qirra_turlari") or {}).get(tur or "")
	return bool(cint(r.kenglik_kerak)) if r else (tur == "Faska")


def _min_qalinlik(tur, konst):
	r = (konst.get("burchak_turlari") or {}).get(tur or "")
	return cint(r.min_qalinlik_mm) if r else (6 if tur == "Radius" else 0)


class TarifYoq(Exception):
	pass


# ============================================================ sozlama / tarif

def sozlama():
	return frappe.get_cached_doc(SOZLAMA)


def konst_from(s):
	"""Single'dan toza-yadro uchun konstantalar lug'ati."""
	return frappe._dict(
		yaxlitlash_mm=cint(s.yaxlitlash_mm) or 10,
		min_maydon_m2=flt(s.min_maydon_m2) or 0.25,
		min_olcham_mm=cint(s.min_olcham_mm) or 50,
		max_olcham_mm=cint(s.max_olcham_mm) or 6000,
		ogirlik_koef=flt(s.ogirlik_koef) or 2.5,
		zakalka_min_kichik_mm=cint(s.zakalka_min_kichik_mm) or 200,
		zakalka_min_katta_mm=cint(s.zakalka_min_katta_mm) or 300,
		zakalka_max_katta_mm=cint(s.zakalka_max_katta_mm) or 3000,
		zakalka_max_kichik_mm=cint(s.zakalka_max_kichik_mm) or 2000,
		zakalka_min_qalinlik_mm=cint(s.get("zakalka_min_qalinlik_mm")) or 4,
		zakalka_max_qalinlik_mm=cint(s.get("zakalka_max_qalinlik_mm")) or 19,
		chiqindi={cint(r.qalinlik_mm): flt(r.foiz) for r in (s.get("chiqindi") or [])},
		qirra_turlari=_ishlov_turlari(s)["Qirra"],
		burchak_turlari=_ishlov_turlari(s)["Burchak"],
		kesim_turlari=_ishlov_turlari(s)["Kesim"],
		# raskroy (listdan kesish): Single'da hali yo'q maydon None → default; 0 saqlanadi
		raskroy=frappe._dict(
			chekka_mm=_sq(s, "raskroy_chetlash_mm", RASKROY_DEF["chekka_mm"]),
			kerf_mm=_sq(s, "raskroy_kerf_mm", RASKROY_DEF["kerf_mm"]),
			min_polosa_mm=_sq(s, "raskroy_min_polosa_mm", RASKROY_DEF["min_polosa_mm"]),
			shakl_zaxira_mm=_sq(s, "raskroy_shakl_zaxira_mm", RASKROY_DEF["shakl_zaxira_mm"]),
			qoldiq_min_tomon_mm=_sq(s, "raskroy_min_qoldiq_mm", RASKROY_DEF["qoldiq_min_tomon_mm"]),
			qoldiq_min_m2=_sq(s, "raskroy_min_qoldiq_m2", RASKROY_DEF["qoldiq_min_m2"], flt),
			qoldiq_max_soni=_sq(s, "raskroy_qoldiq_max_soni", RASKROY_DEF["qoldiq_max_soni"]),
		),
		chiqindi_min_foiz=_sq(s, "raskroy_chiqindi_min_foiz", 0.0, flt),  # listga nisbatan, 0 = cheklov yo'q
		chiqindi_max_foiz=_sq(s, "raskroy_chiqindi_max_foiz", 100.0, flt),
		raskroy_burish=_sq(s, "raskroy_burish", 1),
		raskroy_list=(_sq(s, "raskroy_list_eni_mm", 3600) or 3600, _sq(s, "raskroy_list_boyi_mm", 2600) or 2600),
	)


def _sq(s, k, default, conv=cint):
	"""Single-qiymat: None/"" → default (yangi maydon hali saqlanmagan), 0 saqlanadi."""
	v = s.get(k) if hasattr(s, "get") else None
	return default if v in (None, "") else conv(v)


def _tables_to_rows(s):
	"""Narx Jadvalidagi oddiy jadvallar → ichki tarif-qatorlar (kategoriya/kalit/diapazon)."""
	rows = []
	R = lambda **kw: rows.append(dict({"oyna_item": None, "qalinlik_mm": 0, "kalit": None, "diapazon_dan": 0, "diapazon_gacha": 0}, **kw))
	for r in s.get("narx_oyna") or []:
		R(kategoriya="Oyna", oyna_item=r.oyna_item, narx=flt(r.narx), izoh=r.izoh)
	grp = {}
	for r in s.get("narx_qirra") or []:
		grp.setdefault(((r.tur or "").strip().lower(), cint(r.qalinlik_mm)), []).append(r)
	for lst in grp.values():
		prev = 0
		for r in sorted(lst, key=lambda x: cint(x.kenglik_gacha)):
			g = cint(r.kenglik_gacha)
			R(kategoriya="Qirra", kalit=(r.tur or "").strip(), qalinlik_mm=cint(r.qalinlik_mm),
			  diapazon_dan=prev if g else 0, diapazon_gacha=g, narx=flt(r.narx), izoh=r.izoh)
			if g:
				prev = g
	for r in s.get("narx_burchak") or []:
		R(kategoriya="Burchak", kalit=(r.tur or "").strip(), qalinlik_mm=cint(r.qalinlik_mm), narx=flt(r.narx), izoh=r.izoh)
	grp = {}
	for r in s.get("narx_teshik") or []:
		grp.setdefault(cint(r.qalinlik_mm), []).append(r)
	for lst in grp.values():
		prev = 0
		for r in sorted(lst, key=lambda x: cint(x.diametr_gacha)):
			g = cint(r.diametr_gacha)
			R(kategoriya="Teshik", qalinlik_mm=cint(r.qalinlik_mm), diapazon_dan=prev if g else 0, diapazon_gacha=g,
			  narx=flt(r.narx), izoh=r.izoh)
			if g:
				prev = g
	for r in s.get("narx_kesim") or []:
		R(kategoriya="Kesim", kalit=r.tur, qalinlik_mm=cint(r.qalinlik_mm), narx=flt(r.narx), izoh=r.izoh)
	XIZMAT = {"Matofka to'liq": ("Matofka", "To'liq"), "Matofka polosa": ("Matofka", "Polosa"),
	          "Matofka gul": ("Matofka", "Gul"), "Zakalka": ("Zakalka", None),
	          "Markirovka": ("Markirovka", None), "Oyna bo'yash": ("Boyash", None),
	          "Paket yig'ish": ("Paket", None), "Reska (kesish)": ("Reska", None),
	          # eski nom — 2026-09-12 gacha «polosa» shu kalit bilan yozilgan edi
	          "Matofka qisman": ("Matofka", "Polosa")}
	for r in s.get("narx_ishlov") or []:
		kat, kalit = XIZMAT.get(r.xizmat, (None, None))
		if kat:
			R(kategoriya=kat, kalit=kalit, qalinlik_mm=cint(r.qalinlik_mm), narx=flt(r.narx), izoh=r.izoh)
	return rows


def _rows_to_tables(rows):
	"""Ichki tarif-qatorlar → Narx Jadvali jadvallari (seed uchun)."""
	t = dict(narx_oyna=[], narx_qirra=[], narx_burchak=[], narx_teshik=[], narx_kesim=[], narx_ishlov=[])
	for r in rows:
		kat = r["kategoriya"]
		if kat == "Oyna":
			t["narx_oyna"].append(dict(oyna_item=r["oyna_item"], narx=r["narx"], izoh=r.get("izoh")))
		elif kat == "Qirra":
			t["narx_qirra"].append(dict(tur=r["kalit"], qalinlik_mm=r["qalinlik_mm"], kenglik_gacha=r.get("diapazon_gacha") or 0, narx=r["narx"], izoh=r.get("izoh")))
		elif kat == "Burchak":
			t["narx_burchak"].append(dict(tur=r["kalit"], qalinlik_mm=r["qalinlik_mm"], narx=r["narx"], izoh=r.get("izoh")))
		elif kat == "Teshik":
			t["narx_teshik"].append(dict(qalinlik_mm=r["qalinlik_mm"], diametr_gacha=r.get("diapazon_gacha") or 0, narx=r["narx"], izoh=r.get("izoh")))
		elif kat == "Kesim":
			t["narx_kesim"].append(dict(tur=r["kalit"], qalinlik_mm=r["qalinlik_mm"], narx=r["narx"], izoh=r.get("izoh")))
		elif kat in ("Matofka", "Zakalka", "Markirovka", "Boyash", "Paket", "Reska"):
			x = ({"To'liq": "Matofka to'liq", "Polosa": "Matofka polosa", "Gul": "Matofka gul"}.get(r.get("kalit"))
			     if kat == "Matofka" else
			     {"Boyash": "Oyna bo'yash", "Paket": "Paket yig'ish", "Reska": "Reska (kesish)"}.get(kat, kat))
			t["narx_ishlov"].append(dict(xizmat=x, qalinlik_mm=r["qalinlik_mm"], narx=r["narx"], izoh=r.get("izoh")))
	return t


def ishlov_optionlarini_yangila(s=None):
	"""Ishlov turlari → pozitsiya Select-tanlovlari (Property Setter). Narx Jadvali saqlanganda chaqiriladi."""
	s = s or sozlama()
	t = _ishlov_turlari(s)
	q = "\n".join(["Yo'q"] + list(t["Qirra"].keys()))
	b = "\n".join(["Yo'q"] + list(t["Burchak"].keys()))
	ks = "\n".join(list(t["Kesim"].keys()) or ["Fortochka", "Qulf", "Rozetka", "Boshqa"])
	targets = ([("Oyna Zakaz Pozitsiya", f, q) for f in ("qirra_yuqori", "qirra_ong", "qirra_pastki", "qirra_chap", "qirra_kontur")]
	           + [("Oyna Zakaz Pozitsiya", f, b) for f in ("burchak_yc", "burchak_yo", "burchak_po", "burchak_pc")]
	           + [("Oyna Zakaz Kesim", "tur", ks)])
	for dt, fn, val in targets:
		frappe.db.delete("Property Setter", {"doc_type": dt, "field_name": fn, "property": "options"})
		frappe.make_property_setter({"doctype": dt, "doctype_or_field": "DocField", "fieldname": fn,
		                             "property": "options", "value": val, "property_type": "Text"},
		                            ignore_validate=True, validate_fields_for_doctype=False)
	# Markirovka — sexda har doim ham bo'lmaydi: narx qatori bo'lmasa maydon umuman ko'rsatilmaydi
	# (qirra/burchak turlari kabi o'zi-o'zidan: Narx Jadvaliga «Markirovka» qatori qo'shilsa paydo bo'ladi)
	mark_bor = any((r.get("xizmat") or "") == "Markirovka" and flt(r.get("narx")) > 0
	               for r in (s.get("narx_ishlov") or []))
	for fn in ("markirovka", "markirovka_matn"):
		frappe.db.delete("Property Setter", {"doc_type": "Oyna Zakaz Pozitsiya", "field_name": fn, "property": "hidden"})
		frappe.make_property_setter({"doctype": "Oyna Zakaz Pozitsiya", "doctype_or_field": "DocField", "fieldname": fn,
		                             "property": "hidden", "value": 0 if mark_bor else 1, "property_type": "Check"},
		                            ignore_validate=True, validate_fields_for_doctype=False)
	frappe.clear_cache(doctype="Oyna Zakaz Pozitsiya")
	frappe.clear_cache(doctype="Oyna Zakaz Kesim")


class Tarif:
	"""Tarif-qatorlari bo'yicha qidiruv (toza, DB'siz)."""

	def __init__(self, rows):
		self.rows = [frappe._dict(r) if not isinstance(r, dict) else r for r in rows]

	@classmethod
	def from_settings(cls, s):
		return cls(_tables_to_rows(s))

	def top(self, kategoriya, qalinlik=0, kalit=None, qiymat=None, oyna_item=None):
		cands = [r for r in self.rows if (r.get("kategoriya") or "") == kategoriya]
		if oyna_item:
			cands = [r for r in cands if (r.get("oyna_item") or "") == oyna_item]
		if kalit:
			k = kalit.strip().lower()
			cands = [r for r in cands if (r.get("kalit") or "").strip().lower() == k]

		def band_ok(r):
			dan, gacha = flt(r.get("diapazon_dan")), flt(r.get("diapazon_gacha"))
			if not dan and not gacha:
				return True
			if qiymat is None:
				return False
			return flt(qiymat) > dan and (not gacha or flt(qiymat) <= gacha)

		cands = [r for r in cands if band_ok(r)]
		# qalinlik POG'ONALI: qator «qalinlik gacha» — t dan katta-teng eng kichik pog'ona; 0 = zaxira
		t = cint(qalinlik)
		# bir xil pog'onada band-li qator (kenglik/diametr oralig'i) «0 = har qanday» qatordan ustun
		bandli = lambda r: 0 if (flt(r.get("diapazon_dan")) or flt(r.get("diapazon_gacha"))) else 1
		pog = sorted([r for r in cands if cint(r.get("qalinlik_mm")) and cint(r.get("qalinlik_mm")) >= t],
		             key=lambda r: (cint(r.get("qalinlik_mm")), bandli(r)))
		umumiy = sorted([r for r in cands if cint(r.get("qalinlik_mm")) == 0], key=bandli)
		pick = pog or umumiy
		if not pick:
			raise TarifYoq(_("Tarif topilmadi: {0} / {1} mm / {2} / {3} — «Oyna Narx Jadvali»ga qo'shing").format(
				kategoriya, cint(qalinlik), kalit or oyna_item or "-", qiymat if qiymat is not None else "-"))
		return pick[0]


# ============================================================ toza yadro

def _ceil_step(mm, step):
	mm = flt(mm)
	if mm <= 0:
		return 0
	return int(math.ceil(round(mm / step, 6)) * step)


def _geom(p, konst):
	"""Bounding o'lchamlar, hisob-o'lchamlar, maydonlar, perimetr (1 dona)."""
	shakl = p.get("shakl") or "To'rtburchak"
	step = konst.yaxlitlash_mm
	if shakl == "Doira":
		W = H = cint(p.get("diametr_mm"))
	else:
		W, H = cint(p.get("eni_mm")), cint(p.get("boyi_mm"))
	hw, hh = _ceil_step(W, step), _ceil_step(H, step)
	# hisob-maydon: yaxlitlangan o'lchamlar + minimal maydon (mijozga hisoblanadi);
	# haqiqiy-maydon: real o'lchamlar (doira — π·D²/4; erkin — gabarit), og'irlik shundan
	bill = flt(hw * hh / 1e6, 4)
	if shakl == "Doira":
		haq = flt(math.pi * W * W / 4e6, 4)
	else:
		haq = flt(W * H / 1e6, 4)
	hisob = max(bill, konst.min_maydon_m2) if bill > 0 else 0.0
	if shakl == "Doira":
		perim = math.pi * hw / 1000.0
		tomon = {}
	elif shakl == "Erkin shakl":
		perim = (cint(p.get("perimetr_mm")) or 2 * (hw + hh)) / 1000.0
		tomon = {}
	else:
		perim = 2 * (hw + hh) / 1000.0
		tomon = {"qirra_yuqori": hw / 1000.0, "qirra_ong": hh / 1000.0,
		         "qirra_pastki": hw / 1000.0, "qirra_chap": hh / 1000.0}
	return frappe._dict(shakl=shakl, W=W, H=H, hw=hw, hh=hh, haqiqiy=haq, hisob=flt(hisob, 4),
	                    perim=flt(perim, 3), tomon=tomon)


def _pul(x):
	return flt(x, 2)


def _shaklga_moslash(p):
	"""Dialog yashiradigan maydonlarni server ham tozalaydi (bir xil qoida): to'rtburchak bo'lmasa tomon-qirralar
	va burchaklar «Yo'q», to'rtburchakda kontur-qirra «Yo'q». dict yoki Document uchun."""
	def st(k, v):
		if hasattr(p, "set") and not isinstance(p, dict):
			p.set(k, v)
		else:
			p[k] = v
	tb = (p.get("shakl") or "To'rtburchak") == "To'rtburchak"
	if tb:
		if (p.get("qirra_kontur") or "Yo'q") != "Yo'q":
			st("qirra_kontur", "Yo'q")
	else:
		for fn, _n, _b in TOMONLAR:
			if (p.get(fn) or "Yo'q") != "Yo'q":
				st(fn, "Yo'q")
		for fn, _n in BURCHAKLAR:
			if (p.get(fn) or "Yo'q") != "Yo'q":
				st(fn, "Yo'q")
			if cint(p.get(fn + "_mm")):
				st(fn + "_mm", 0)
	return p


def _tekshir_pozitsiya(p, g, teshiklar, kesimlar, konst, xato, std, ogoh):
	"""Bitta pozitsiya uchun qoidalar. xato/std/ogoh — matn ro'yxatlari."""
	n = cint(p.get("nr"))
	t = cint(p.get("qalinlik_mm"))
	P = f"P{n}"
	W, H = g.W, g.H
	if not p.get("oyna_item"):
		xato.append(_("{0}: oyna tanlanmagan").format(P))
	elif not t:
		xato.append(_("{0}: {1} uchun qalinlik kiritilmagan (Item → Oyna qalinligi)").format(P, p.get("oyna_item")))
	if g.shakl == "Doira":
		if cint(p.get("diametr_mm")) <= 0:
			xato.append(_("{0}: doira uchun diametr kiritilsin").format(P))
		for fn, nom in BURCHAKLAR:
			if (p.get(fn) or "Yo'q") != "Yo'q":
				xato.append(_("{0}: doirada burchak ishlovi bo'lmaydi ({1})").format(P, nom))
	elif g.shakl == "Erkin shakl":
		if not (p.get("chizma") or (p.get("izoh") or "").strip()):
			xato.append(_("{0}: erkin shakl uchun chizma yoki izoh kiritilsin").format(P))
	if W <= 0 or H <= 0:
		xato.append(_("{0}: o'lcham kiritilmagan").format(P))
	else:
		for v in (W, H):
			if v < konst.min_olcham_mm or v > konst.max_olcham_mm:
				xato.append(_("{0}: o'lcham {1}×{2} mm ruxsat etilgan {3}–{4} mm oralig'ida emas").format(
					P, W, H, konst.min_olcham_mm, konst.max_olcham_mm))
				break
	if cint(p.get("dona")) < 1:
		xato.append(_("{0}: dona kamida 1").format(P))
	# oyna turi (Item → «Oyna turi»): tayyor zakalangan oyna qayta ishlanmaydi; ko'zgu/lakobel zakalka qilinmaydi
	turi = p.get("oyna_turi") or "Oddiy"
	ishlov_bor = bool(teshiklar or kesimlar or any((p.get(fn) or "Yo'q") != "Yo'q" for fn, _n, _c in TOMONLAR)
	                  or (p.get("qirra_kontur") or "Yo'q") != "Yo'q"
	                  or any((p.get(fn) or "Yo'q") != "Yo'q" for fn, _n in BURCHAKLAR))
	if turi == "Tayyor zakalangan":
		if ishlov_bor:
			xato.append(_("{0}: tayyor zakalangan oynani kesib/teshib/ishlab bo'lmaydi (zakalkadan keyin ishlov yo'q)").format(P))
		if cint(p.get("zakalka")):
			xato.append(_("{0}: oyna allaqachon zakalangan — «Zakalka» belgisini olib tashlang").format(P))
	elif turi in ("Ko'zgu", "Lakobel (bo'yalgan)") and cint(p.get("zakalka")):
		std.append(_("{0}: {1} oynaga zakalka qilinmaydi (qoplama pechda buziladi)").format(P, turi))
	if cint(p.get("zakalka")) and t:
		zmin, zmax = cint(konst.get("zakalka_min_qalinlik_mm")) or 4, cint(konst.get("zakalka_max_qalinlik_mm")) or 19
		if t < zmin or t > zmax:
			std.append(_("{0}: zakalka faqat {1}–{2} mm oynada (hozir {3} mm)").format(P, zmin, zmax, t))
	kt = konst.get("kesim_turlari") or {}
	for i, kx in enumerate(kesimlar, 1):
		if kt and (kx.get("tur") or "") not in kt:
			xato.append(_("{0}, kesim #{1}: tur «{2}» ishlov-turlari ro'yxatida yo'q (Oyna Narx Jadvali)").format(P, i, kx.get("tur")))
	qirra_turlar = [p.get(fn) for fn, _n, _c in TOMONLAR] + [p.get("qirra_kontur")]
	qirra_turlar = [x for x in qirra_turlar if x and x != "Yo'q"]
	qt = konst.get("qirra_turlari") or {}
	for x in set(qirra_turlar):
		if qt and x not in qt:
			xato.append(_("{0}: qirra turi «{1}» ishlov-turlari ro'yxatida yo'q (Oyna Narx Jadvali)").format(P, x))
	if any(_kenglik_kerak(x, konst) for x in qirra_turlar) and cint(p.get("faska_kengligi_mm")) <= 0:
		xato.append(_("{0}: faska kengligi kiritilsin").format(P))
	# burchaklar
	if g.shakl == "To'rtburchak":
		for fn, nom in BURCHAKLAR:
			tur = p.get(fn) or "Yo'q"
			if tur == "Yo'q":
				continue
			mm = cint(p.get(fn + "_mm"))
			if mm <= 0:
				xato.append(_("{0}, burchak {1}: R / kesik o'lchami kiritilsin").format(P, nom))
				continue
			bt = konst.get("burchak_turlari") or {}
			if bt and tur not in bt:
				xato.append(_("{0}, burchak {1}: tur «{2}» ishlov-turlari ro'yxatida yo'q").format(P, nom, tur))
			mq = _min_qalinlik(tur, konst)
			if mq and t and t < mq:
				std.append(_("{0}, burchak {1}: {2} faqat ≥ {3} mm oynada (GOST 30698)").format(P, nom, tur, mq))
			if W and H and mm > min(W, H) / 2:
				std.append(_("{0}, burchak {1}: R {2} mm oynaning yarmidan katta").format(P, nom, mm))
	# matofka — uchala tur ham butun oynaga; eski «Qisman» 2026-09-12 da olib tashlandi
	rej_m = p.get("matofka_rejimi") or "Yo'q"
	if rej_m not in ("Yo'q",) + MATOFKA_TURLARI:
		xato.append(_("{0}: matofka turi «{1}» noma'lum — To'liq, Polosa yoki Gul bo'lsin").format(P, rej_m))
	# paket (2 qavat oyna)
	if cint(p.get("paket")):
		if not p.get("paket_oyna_item"):
			xato.append(_("{0}: paket belgilangan — 2-oyna (paket) tanlanmagan").format(P))
		elif p.get("paket_oyna_item") == p.get("oyna_item"):
			std.append(_("{0}: paketning ikkala qavati bir xil oyna ({1}) — shundaymi?").format(P, p.get("oyna_item")))
		if cint(p.get("mijoz_oynasi")):
			std.append(_("{0}: mijoz oynasi + paket — oynalar narxi hisoblanmaydi, faqat yig'ish haqi").format(P))
	# zakalka
	if cint(p.get("zakalka")) and W and H:
		kichik, katta = min(W, H), max(W, H)
		if (kichik < konst.zakalka_min_kichik_mm or katta < konst.zakalka_min_katta_mm
				or katta > konst.zakalka_max_katta_mm or kichik > konst.zakalka_max_kichik_mm):
			std.append(_("{0}: zakalka uchun o'lcham {1}×{2} ruxsat etilgan diapazonda emas (min {3}×{4}, max {5}×{6})").format(
				P, W, H, konst.zakalka_min_kichik_mm, konst.zakalka_min_katta_mm,
				konst.zakalka_max_katta_mm, konst.zakalka_max_kichik_mm))
	# teshiklar
	koordsiz = []
	nuqtalar = []
	for i, tk in enumerate(teshiklar, 1):
		d = cint(tk.get("diametr_mm"))
		if d <= 0:
			xato.append(_("{0}, teshik #{1}: diametr kiritilsin").format(P, i))
			continue
		if t and t < 4:
			std.append(_("{0}: teshik faqat ≥ 4 mm oynada").format(P))
		if t and d < t:
			std.append(_("{0}, teshik #{1}: diametr {2} ≥ qalinlik {3} mm bo'lsin (GOST 30698)").format(P, i, d, t))
		if W and H and d > min(W, H) / 3.0:
			std.append(_("{0}, teshik #{1}: diametr eng kichik tomonning 1/3 idan ({2} mm) katta").format(P, i, int(min(W, H) / 3)))
		# Int-maydon bo'sh bo'lsa 0 — teshik markazi qirrada (0) bo'lolmaydi:
		# (0,0)/bo'sh = koordinatasiz; faqat bittasi 0 = xato
		x, y = cint(tk.get("x_mm")), cint(tk.get("y_mm"))
		if not x and not y:
			koordsiz.append(str(i))
			continue
		if not x or not y:
			xato.append(_("{0}, teshik #{1}: X va Y ikkalasi ham kiritilsin (0 bo'lolmaydi)").format(P, i))
			continue
		if not (0 <= x <= W and 0 <= y <= H):
			xato.append(_("{0}, teshik #{1}: koordinata ({2}, {3}) oyna ichida emas").format(P, i, x, y))
			continue
		if g.shakl == "Doira":
			R = W / 2.0
			dist = math.hypot(x - R, y - R)
			if dist + d / 2.0 + max(2 * t, 6) > R:
				std.append(_("{0}, teshik #{1}: doira ichida qirradan kamida {2} mm masofa saqlanmagan").format(P, i, max(2 * t, 6)))
		else:
			chet = min(x, W - x, y, H - y) - d / 2.0
			kerak = max(2 * t, 6)
			if chet < kerak:
				std.append(_("{0}, teshik #{1}: qirragacha masofa {2} mm < {3} mm").format(P, i, int(chet), kerak))
			dx, dy = min(x, W - x), min(y, H - y)
			bur = math.hypot(dx, dy) - d / 2.0
			if t and bur < 6 * t:
				std.append(_("{0}, teshik #{1}: burchakgacha masofa {2} mm < {3} mm (6t)").format(P, i, int(bur), 6 * t))
		for (j, x2, y2, d2) in nuqtalar:
			orasi = math.hypot(x - x2, y - y2) - (d + d2) / 2.0
			if t and orasi < 2 * t:
				std.append(_("{0}: teshik #{1} va #{2} orasidagi masofa {3} mm < {4} mm").format(P, j, i, int(orasi), 2 * t))
		nuqtalar.append((i, x, y, d))
	if koordsiz:
		ogoh.append(_("{0}: koordinatasiz teshiklar (#{1}) joylashuv bo'yicha tekshirilmadi").format(P, ", ".join(koordsiz)))
	# kesimlar
	kes_rects = []
	kerak = max(2 * t, 6)
	for i, k in enumerate(kesimlar, 1):
		w, h = cint(k.get("eni_mm")), cint(k.get("boyi_mm"))
		if w <= 0 or h <= 0:
			xato.append(_("{0}, kesim #{1}: o'lcham kiritilsin").format(P, i))
			continue
		r = cint(k.get("ichki_radius_mm"))
		if t and r and r < t:
			std.append(_("{0}, kesim #{1}: ichki radius {2} < qalinlik {3} mm").format(P, i, r, t))
		if W and H and (w > W / 3.0 or h > H / 3.0):
			std.append(_("{0}, kesim #{1}: kesim o'lchami tomonning 1/3 idan katta ({2}×{3} > {4}×{5})").format(
				P, i, w, h, int(W / 3), int(H / 3)))
		if (k.get("tur") == "Fortochka") and t and t < 6:
			std.append(_("{0}, kesim #{1}: fortochka faqat ≥ 6 mm oynada").format(P, i))
		x, y = k.get("x_mm"), k.get("y_mm")
		if x not in (None, "") and y not in (None, "") and W and H:
			x0, y0 = cint(x), cint(y)
			if x0 < 0 or y0 < 0 or x0 + w > W or y0 + h > H:
				xato.append(_("{0}, kesim #{1}: kesim oyna chegarasidan chiqib ketgan").format(P, i))
				continue
			kes_rects.append((i, x0, y0, x0 + w, y0 + h))
			# kesim bilan oyna qirrasi orasida yupqa chiziq qolmasin: yo ochiq (0), yo ≥ kerak
			for gap in (x0, W - (x0 + w), y0, H - (y0 + h)):
				if 0 < gap < kerak:
					std.append(_("{0}, kesim #{1}: kesim va qirra orasida {2} mm qoladi (yo ochiq — 0, yo ≥ {3} mm)").format(P, i, gap, kerak))
					break
	# teshik ↔ kesim: kesishmasin, orasi ≥ kerak
	for (j, x, y, d) in nuqtalar:
		for (i, x0, y0, x1, y1) in kes_rects:
			dx = max(x0 - x, 0, x - x1)
			dy = max(y0 - y, 0, y - y1)
			dist = math.hypot(dx, dy)
			if dist < d / 2.0:
				xato.append(_("{0}, teshik #{1}: kesim #{2} bilan kesishadi yoki uning ichida").format(P, j, i))
			elif dist - d / 2.0 < kerak:
				std.append(_("{0}, teshik #{1}: kesim #{2}gacha masofa {3} mm < {4} mm").format(P, j, i, int(dist - d / 2.0), kerak))


def _yumshoq_narx(tarif, kategoriya, qalinlik, tax, res, bayroq):
	"""Tarif topilmasa 0 qaytaradi va hujjatga bayroq qo'yadi (hisobni to'xtatmaydi).
	Markirovka bilan bir xil xulq — yangi xizmatlarga narx kiritilmagan bo'lsa ham zakaz yoziladi."""
	try:
		r = tarif.top(kategoriya, qalinlik=qalinlik)
	except TarifYoq:
		res[bayroq] = 1
		return 0.0
	if TAXMINIY in (r.get("izoh") or "").upper():
		tax[0] = True
	return flt(r.get("narx"))


def _hisobla_pozitsiya(p, teshiklar, kesimlar, tarif, konst, reja_item=None, reja_item2=None):
	"""Bitta pozitsiya: hisob-maydonlar + kategoriya-summalar. Natija dict (yozib qo'yiladi).

	`reja_item` — dolzarb raskroy bo'yicha shu oyna-itemning {narx_foiz, ishlatilgan_m2…} (bo'lmasa chiqindi 0);
	`reja_item2` — paketning 2-qavat oynasi uchun xuddi shunday."""
	g = _geom(p, konst)
	t = cint(p.get("qalinlik_mm"))
	dona = max(cint(p.get("dona")), 1)
	item = p.get("oyna_item")
	mijoz = cint(p.get("mijoz_oynasi"))
	res = frappe._dict(
		hisob_eni_mm=g.hw, hisob_boyi_mm=g.hh, haqiqiy_maydon_m2=g.haqiqiy,
		hisob_maydon_m2=g.hisob, jami_maydon_m2=flt(g.hisob * dona, 4),
		perimetr_m=g.perim,
		ogirlik_kg=flt(g.haqiqiy * t * konst.ogirlik_koef * dona, 2),
		teshik_soni=sum(max(cint(x.get("soni")), 1) for x in teshiklar) * dona,
		kesim_soni=sum(max(cint(x.get("soni")), 1) for x in kesimlar) * dona,
		taxminiy=0, qatorlar=[], teshik_narx=[], kesim_narx=[],
	)
	tax = [False]

	qolda = cint(p.get("narx_qolda"))

	def narx(kategoriya, **kw):
		try:
			r = tarif.top(kategoriya, **kw)
		except TarifYoq:
			if qolda:  # narx qo'lda — tarif yo'qligi to'sqinlik qilmaydi
				return 0.0
			raise
		if TAXMINIY in (r.get("izoh") or "").upper():
			tax[0] = True
		return flt(r.get("narx"))
	summalar = {}
	# --- oyna
	oyna_tarif = 0.0
	if not mijoz and item and g.hisob:
		oyna_tarif = narx("Oyna", oyna_item=item, qalinlik=t)
	res.oyna_tarif = oyna_tarif
	summalar["oyna"] = _pul(g.hisob * dona * oyna_tarif) if not mijoz else 0.0
	if not mijoz and item and g.hisob:
		res.qatorlar.append(("oyna", item, f"{g.hisob:.4f} m² × {dona}", oyna_tarif, summalar["oyna"]))
	# --- chiqindi (raskroy): haqiqiy chiqindi % bo'lak maydoniga pro-rata, oyna kategoriyasi ichida
	res.chiqindi_foiz = res.chiqindi_m2 = res.chiqindi_summa = 0.0
	if reja_item and not mijoz and item and g.hisob and oyna_tarif:
		nf = flt(reja_item.get("narx_foiz"))  # bo'laklarga nisbatan (ichki) — mijozga yozilgan m² pro-rata
		ch_m2 = flt(g.hw * g.hh * dona / 1e6 * nf / 100.0, 4)
		ch = _pul(ch_m2 * oyna_tarif)
		if ch > 0:
			summalar["oyna"] = _pul(summalar["oyna"] + ch)
			mf = flt(reja_item.get("mijoz_foiz"))  # listga nisbatan — foydalanuvchiga ko'rinadigan foiz
			res.qatorlar.append(("oyna", f"List sarfi (chiqindi) {mf:g} % (raskroy)", f"{ch_m2:.4f} m²", oyna_tarif, ch))
			res.chiqindi_foiz, res.chiqindi_m2, res.chiqindi_summa = mf, ch_m2, ch
	# --- paket: 2-qavat oyna o'z m² narxi bilan (yig'ish haqi pastda alohida kategoriya)
	paket_item = p.get("paket_oyna_item") if cint(p.get("paket")) else None
	if paket_item and not mijoz and g.hisob:
		p_t = cint(p.get("paket_qalinlik_mm")) or t
		p_tarif = narx("Oyna", oyna_item=paket_item, qalinlik=p_t)
		p_s = _pul(g.hisob * dona * p_tarif)
		summalar["oyna"] = _pul(flt(summalar.get("oyna")) + p_s)
		res.qatorlar.append(("oyna", _("{0} (paket 2-qavat)").format(paket_item),
		                     f"{g.hisob:.4f} m² × {dona}", p_tarif, p_s))
		if reja_item2 and p_tarif:
			nf2 = flt(reja_item2.get("narx_foiz"))
			ch2_m2 = flt(g.hw * g.hh * dona / 1e6 * nf2 / 100.0, 4)
			ch2 = _pul(ch2_m2 * p_tarif)
			if ch2 > 0:
				summalar["oyna"] = _pul(summalar["oyna"] + ch2)
				mf2 = flt(reja_item2.get("mijoz_foiz"))
				res.qatorlar.append(("oyna", _("List sarfi (chiqindi) {0} % — paket 2-qavat (raskroy)").format(f"{mf2:g}"),
				                     f"{ch2_m2:.4f} m²", p_tarif, ch2))
				res.chiqindi_m2 = flt(flt(res.chiqindi_m2) + ch2_m2, 4)
				res.chiqindi_summa = _pul(flt(res.chiqindi_summa) + ch2)
	# --- qirra
	q = 0.0
	if g.shakl == "To'rtburchak":
		for fn, nom, belgi in TOMONLAR:
			tur = p.get(fn) or "Yo'q"
			if tur == "Yo'q":
				continue
			uz = g.tomon[fn]
			nr = narx("Qirra", qalinlik=t, kalit=tur,
			          qiymat=cint(p.get("faska_kengligi_mm")) if _kenglik_kerak(tur, konst) else None)
			s = _pul(uz * dona * nr)
			q += s
			res.qatorlar.append(("qirra", f"{tur} {belgi}", f"{uz:.3f} m × {dona}", nr, s))
	else:
		tur = p.get("qirra_kontur") or "Yo'q"
		if tur != "Yo'q":
			nr = narx("Qirra", qalinlik=t, kalit=tur,
			          qiymat=cint(p.get("faska_kengligi_mm")) if _kenglik_kerak(tur, konst) else None)
			s = _pul(g.perim * dona * nr)
			q += s
			res.qatorlar.append(("qirra", f"{tur} (kontur)", f"{g.perim:.3f} m × {dona}", nr, s))
	summalar["qirra"] = _pul(q)
	# --- burchak
	b = 0.0
	if g.shakl == "To'rtburchak":
		for fn, nom in BURCHAKLAR:
			tur = p.get(fn) or "Yo'q"
			if tur == "Yo'q":
				continue
			nr = narx("Burchak", qalinlik=t, kalit=tur)
			s = _pul(dona * nr)
			b += s
			res.qatorlar.append(("burchak", f"{tur} {cint(p.get(fn + '_mm'))} ({nom})", f"1 × {dona}", nr, s))
	summalar["burchak"] = _pul(b)
	# --- teshik
	ts = 0.0
	for tk in teshiklar:
		d = cint(tk.get("diametr_mm"))
		soni = max(cint(tk.get("soni")), 1)
		nr = narx("Teshik", qalinlik=t, qiymat=d) if d > 0 else 0.0
		s = _pul(soni * dona * nr)
		ts += s
		res.teshik_narx.append((nr, s))
		res.qatorlar.append(("teshik", f"Ø{d}", f"{soni} × {dona}", nr, s))
	summalar["teshik"] = _pul(ts)
	# --- kesim
	ks = 0.0
	for k in kesimlar:
		soni = max(cint(k.get("soni")), 1)
		tur = k.get("tur") or "Boshqa"
		nr = narx("Kesim", qalinlik=t, kalit=tur)
		s = _pul(soni * dona * nr)
		ks += s
		res.kesim_narx.append((nr, s))
		res.qatorlar.append(("kesim", f"{tur} {cint(k.get('eni_mm'))}×{cint(k.get('boyi_mm'))}", f"{soni} × {dona}", nr, s))
	summalar["kesim"] = _pul(ks)
	# --- matofka: To'liq (splashnoy) / Polosa / Gul — UCHALASI HAM butun oyna maydoniga
	# (sex izohi 07.09.2026: polosa va gulda ham butun oyna matoviy bo'ladi, faqat lenta
	#  yoki naqsh ostidagi joy shaffof qoladi — shuning uchun maydon emas, narx farq qiladi)
	m = 0.0
	rej = p.get("matofka_rejimi") or "Yo'q"
	if rej in MATOFKA_TURLARI and g.hisob:
		nr = narx("Matofka", qalinlik=t, kalit=rej)
		m = _pul(g.hisob * dona * nr)
		res.qatorlar.append(("matofka", rej, f"{g.hisob:.4f} m² × {dona}", nr, m))
	summalar["matofka"] = m
	# --- zakalka
	z = 0.0
	if cint(p.get("zakalka")) and g.hisob:
		nr = narx("Zakalka", qalinlik=t)
		z = _pul(g.hisob * dona * nr)
		res.qatorlar.append(("zakalka", "Zakalka", f"{g.hisob:.4f} m² × {dona}", nr, z))
	summalar["zakalka"] = z
	# --- markirovka (logo / yozuv): tarif bo'lmasa 0 + eslatma (to'xtatmaydi)
	mk = 0.0
	if cint(p.get("markirovka")):
		try:
			r_ = tarif.top("Markirovka", qalinlik=t)
			nr = flt(r_.get("narx"))
			if TAXMINIY in (r_.get("izoh") or "").upper():
				tax[0] = True
		except TarifYoq:
			nr = 0.0
			res.markirovka_tarif_yoq = 1
		mk = _pul(dona * nr)
		res.qatorlar.append(("markirovka", (p.get("markirovka_matn") or "Markirovka")[:40], f"1 × {dona}", nr, mk))
	summalar["markirovka"] = mk
	# --- oyna bo'yash: butun oyna maydoniga (so'm / m²)
	by = 0.0
	if cint(p.get("boyash")) and g.hisob:
		nr = _yumshoq_narx(tarif, "Boyash", t, tax, res, "boyash_tarif_yoq")
		by = _pul(g.hisob * dona * nr)
		res.qatorlar.append(("boyash", _("Oyna bo'yash"), f"{g.hisob:.4f} m² × {dona}", nr, by))
	summalar["boyash"] = by
	# --- paket yig'ish: ikki qavatni birlashtirish haqi (so'm / m²); oynalar narxi yuqorida
	pk = 0.0
	if paket_item and g.hisob:
		nr = _yumshoq_narx(tarif, "Paket", t, tax, res, "paket_tarif_yoq")
		pk = _pul(g.hisob * dona * nr)
		res.qatorlar.append(("paket", _("Paket yig'ish (2 qavat)"), f"{g.hisob:.4f} m² × {dona}", nr, pk))
	summalar["paket"] = pk
	# --- reska (oddiy kesish): har kesiladigan bo'lakka; paketda ikkala qavat ham kesiladi
	rs = 0.0
	if g.hisob:
		qavat = 2 if paket_item else 1
		nr = _yumshoq_narx(tarif, "Reska", t, tax, res, "reska_tarif_yoq")
		rs = _pul(dona * qavat * nr)
		if rs or nr:
			res.qatorlar.append(("reska", _("Reska (kesish)"), f"{qavat} × {dona}", nr, rs))
	summalar["reska"] = rs

	if qolda:
		for k in KATEGORIYALAR:
			summalar[k] = _pul(p.get(k + "_summa"))
		res.qatorlar = [(k, KATEGORIYA_NOMI[k], "qo'lda", 0.0, summalar[k]) for k in KATEGORIYALAR if summalar[k]]
		res.teshik_narx = [(0.0, 0.0) for _ in res.teshik_narx]
		res.kesim_narx = [(0.0, 0.0) for _ in res.kesim_narx]
		tax[0] = False
		res.chiqindi_foiz = res.chiqindi_m2 = res.chiqindi_summa = 0.0
	for k in KATEGORIYALAR:
		res[k + "_summa"] = summalar[k]
	res.summa = _pul(sum(summalar.values()))
	res.taxminiy = 1 if tax[0] else 0
	res.geom = g
	return res


def _xulosa(p, res, teshiklar, kesimlar):
	g = res.geom
	if g.shakl == "Doira":
		olcham = f"Ø{g.W}"
	else:
		olcham = f"{g.W}×{g.H}"
	qism = []
	qir = [ (p.get(fn) or "Yo'q") for fn, _n, _c in TOMONLAR ] if g.shakl == "To'rtburchak" else [p.get("qirra_kontur") or "Yo'q"]
	qs = [x for x in qir if x != "Yo'q"]
	if qs:
		qism.append(f"Q{len(qs)}" if g.shakl == "To'rtburchak" else "Q")
	bs = sum(1 for fn, _n in BURCHAKLAR if (p.get(fn) or "Yo'q") != "Yo'q")
	if bs:
		qism.append(f"B{bs}")
	if teshiklar:
		qism.append(f"T{sum(max(cint(x.get('soni')), 1) for x in teshiklar)}")
	if kesimlar:
		qism.append(f"K{sum(max(cint(x.get('soni')), 1) for x in kesimlar)}")
	if (p.get("matofka_rejimi") or "Yo'q") != "Yo'q":
		qism.append("M")
	if cint(p.get("zakalka")):
		qism.append("Z")
	if cint(p.get("mijoz_oynasi")):
		qism.append("mijoz oynasi")
	return olcham, " · ".join(qism) or "oddiy"


def _tavsif(p, res, teshiklar, kesimlar):
	"""SO/print uchun qisqa matn."""
	g = res.geom
	olcham, _x = _xulosa(p, res, teshiklar, kesimlar)
	b = [f"#{cint(p.get('nr'))} {p.get('oyna_item') or ''} {olcham} ×{cint(p.get('dona')) or 1}"]
	if g.shakl == "To'rtburchak":
		q = [f"{belgi} {p.get(fn)}" for fn, _n, belgi in TOMONLAR if (p.get(fn) or "Yo'q") != "Yo'q"]
	else:
		q = [f"kontur {p.get('qirra_kontur')}"] if (p.get("qirra_kontur") or "Yo'q") != "Yo'q" else []
	if q:
		b.append("qirra: " + ", ".join(q))
	bb = [f"{nom} {p.get(fn)} {cint(p.get(fn + '_mm'))}" for fn, nom in BURCHAKLAR if (p.get(fn) or "Yo'q") != "Yo'q"]
	if bb:
		b.append("burchak: " + ", ".join(bb))
	if teshiklar:
		b.append("teshik: " + ", ".join(
			f"Ø{cint(t.get('diametr_mm'))}" + (f" ({cint(t.get('x_mm'))},{cint(t.get('y_mm'))})" if cint(t.get("x_mm")) and cint(t.get("y_mm")) else "")
			+ (f" ×{cint(t.get('soni'))}" if cint(t.get("soni")) > 1 else "") for t in teshiklar))
	if kesimlar:
		b.append("kesim: " + ", ".join(
			f"{k.get('tur')} {cint(k.get('eni_mm'))}×{cint(k.get('boyi_mm'))}"
			+ (f" ({cint(k.get('x_mm'))},{cint(k.get('y_mm'))})" if k.get("x_mm") not in (None, "") else "") for k in kesimlar))
	if (p.get("matofka_rejimi") or "Yo'q") != "Yo'q":
		b.append("matofka: " + (p.get("matofka_rejimi") if p.get("matofka_rejimi") == "To'liq" else f"qisman {flt(p.get('matofka_maydoni_m2'), 3)} m²"))
	if cint(p.get("zakalka")):
		b.append("ZAKALKA")
	if cint(p.get("mijoz_oynasi")):
		b.append("mijozning o'z oynasi")
	return "; ".join(b)


def _hisobla_core(pozitsiyalar, teshiklar, kesimlar, aksessuarlar, tarif, konst, standart_rejimi="Xato", reja=None, list_rows=None, reja_rows=None):
	"""Toza yadro. Kirish — dict-ro'yxatlar. Chiqish — natija (pozitsiya bo'yicha + jami + xatolar).

	`reja` — DOLZARB raskroy-reja (dict) yoki None: bo'lsa oyna narxiga item bo'yicha chiqindi % qo'shiladi,
	materiallar = rejadagi ishlatilgan m² (list − qoldiq); bo'lmasa eski yo'l (jadvaldagi chiqindi %).

	`xatolar` (hard) va `std` (rejimga qarab) va `ogoh` alohida qaytariladi; throw QILMAYDI.
	"""
	xato, std, ogoh = [], [], []
	nrs = [cint(p.get("nr")) for p in pozitsiyalar]
	for nr in set(nrs):
		if nrs.count(nr) > 1:
			xato.append(_("Pozitsiya raqami {0} takrorlangan").format(nr))
		if nr < 1:
			xato.append(_("Pozitsiya raqami 1 dan kichik bo'lmasin"))
	for nom, rows in (("Teshik", teshiklar), ("Kesim", kesimlar)):
		for i, r in enumerate(rows, 1):
			if cint(r.get("pozitsiya_nr")) not in nrs:
				xato.append(_("{0} #{1}: pozitsiya {2} topilmadi").format(nom, i, cint(r.get("pozitsiya_nr"))))
	for i, a in enumerate(aksessuarlar, 1):
		pn = cint(a.get("pozitsiya_nr"))
		if pn and pn not in nrs:
			xato.append(_("Aksessuar #{0}: pozitsiya {1} topilmadi").format(i, pn))
		if flt(a.get("qty")) <= 0:
			xato.append(_("Aksessuar #{0} ({1}): miqdor 0 dan katta bo'lsin").format(i, a.get("item")))
	if not pozitsiyalar and not aksessuarlar:
		xato.append(_("Kamida bitta pozitsiya yoki aksessuar kiriting"))

	natija = []
	imzolar = {}
	pozitsiyalar = [_shaklga_moslash(dict(p)) for p in pozitsiyalar]
	reja_itemlar = _reja_itemlar(reja, konst, list_rows, reja_rows)
	if reja_itemlar:
		# list-sarfi pro-rata maxraji — faqat NARXLANADIGAN pozitsiyalar (mijoz oynasi / narx qo'lda / tarifsiz chiqindi olmaydi)
		hisob_m2 = {}
		for p in pozitsiyalar:
			it = p.get("oyna_item")
			if it not in reja_itemlar or cint(p.get("mijoz_oynasi")) or cint(p.get("narx_qolda")):
				continue
			g0 = _geom(p, konst)
			if not g0.hisob:
				continue
			try:
				if not flt(tarif.top("Oyna", oyna_item=it, qalinlik=cint(p.get("qalinlik_mm"))).get("narx")):
					continue
			except TarifYoq:
				continue
			hisob_m2[it] = hisob_m2.get(it, 0.0) + g0.hw * g0.hh * max(cint(p.get("dona")), 1) / 1e6
		for it, x in reja_itemlar.items():
			x.narx_foiz = (x.charged_m2 / hisob_m2[it] * 100.0) if hisob_m2.get(it) else 0.0
	for p in pozitsiyalar:
		nr = cint(p.get("nr"))
		tk = [x for x in teshiklar if cint(x.get("pozitsiya_nr")) == nr]
		ks = [x for x in kesimlar if cint(x.get("pozitsiya_nr")) == nr]
		g = _geom(p, konst)
		_tekshir_pozitsiya(p, g, tk, ks, konst, xato, std, ogoh)
		try:
			res = _hisobla_pozitsiya(p, tk, ks, tarif, konst, reja_itemlar.get(p.get("oyna_item")),
			                         reja_itemlar.get(p.get("paket_oyna_item")) if cint(p.get("paket")) else None)
		except TarifYoq as e:
			xato.append(f"P{nr}: {e}")
			res = frappe._dict(geom=g, summa=0.0, qatorlar=[], teshik_narx=[], kesim_narx=[],
			                   hisob_maydon_m2=g.hisob, jami_maydon_m2=flt(g.hisob * max(cint(p.get('dona')), 1), 4),
			                   haqiqiy_maydon_m2=g.haqiqiy, hisob_eni_mm=g.hw, hisob_boyi_mm=g.hh,
			                   perimetr_m=g.perim, ogirlik_kg=0, teshik_soni=0, kesim_soni=0, taxminiy=0, oyna_tarif=0,
			                   chiqindi_foiz=0.0, chiqindi_m2=0.0, chiqindi_summa=0.0)
			for k in KATEGORIYALAR:
				res[k + "_summa"] = 0.0
		if res.get("taxminiy"):
			ogoh.append(_("P{0}: TAXMINIY (tasdiqlanmagan) tarif ishlatildi — narxni tekshiring").format(nr))
		if res.get("markirovka_tarif_yoq"):
			ogoh.append(_("P{0}: markirovka tarifi yo'q — 0 so'm olindi (Oyna Narx Jadvali → Markirovka)").format(nr))
		res.olcham_matn, res.xulosa = _xulosa(p, res, tk, ks)
		res.tavsif = _tavsif(p, res, tk, ks)
		res.ishlov_tartibi = "Kesish → teshik/kesim → qirra → matofka → zakalka" if cint(p.get("zakalka")) else "Kesish → teshik/kesim → qirra → matofka"
		imza = (p.get("oyna_item"), g.W, g.H, p.get("shakl"))
		if imza in imzolar:
			ogoh.append(_("P{0} va P{1} bir xil — miqdorni oshiring?").format(imzolar[imza], nr))
		else:
			imzolar[imza] = nr
		natija.append((p, res, tk, ks))

	# aksessuarlar
	aks_jami = 0.0
	for a in aksessuarlar:
		a["summa"] = _pul(flt(a.get("qty")) * flt(a.get("narx")))
		aks_jami += a["summa"]
		if not flt(a.get("narx")):
			ogoh.append(_("Aksessuar {0}: narx topilmadi — qo'lda kiriting").format(a.get("item")))

	jami = frappe._dict(pozitsiya_soni=len(pozitsiyalar),
	                    dona_jami=sum(max(cint(p.get("dona")), 1) for p in pozitsiyalar),
	                    haqiqiy_kvadrat=flt(sum(r.haqiqiy_maydon_m2 * max(cint(p.get("dona")), 1) for p, r, _a, _b in natija), 3),
	                    jami_kvadrat=flt(sum(r.jami_maydon_m2 for _p, r, _a, _b in natija), 3),
	                    ogirlik_kg=flt(sum(flt(r.ogirlik_kg) for _p, r, _a, _b in natija), 1),
	                    aksessuar_jami=_pul(aks_jami))
	for k in KATEGORIYALAR:
		jami[k + "_jami"] = _pul(sum(flt(r.get(k + "_summa")) for _p, r, _a, _b in natija))
	jami.pozitsiyalar_jami = _pul(sum(flt(r.summa) for _p, r, _a, _b in natija))
	jami.raskroy_chiqindi_summa = _pul(sum(flt(r.get("chiqindi_summa")) for _p, r, _a, _b in natija))

	# materiallar (avto): raskroy bo'lsa — ishlatilgan list-maydoni (list − qoldiq); bo'lmasa jadval chiqindi %
	mat = {}
	for p, r, _a, _b in natija:
		if cint(p.get("mijoz_oynasi")) or not p.get("oyna_item"):
			continue
		# paket — 2-qavat oyna ham omborga tushadi (o'z item'i bilan)
		p2 = p.get("paket_oyna_item") if cint(p.get("paket")) else None
		if p2:
			if p2 in reja_itemlar:
				mat[p2] = flt(reja_itemlar[p2].ishlatilgan_m2, 4)
			else:
				g2 = r.get("geom") or _geom(p, konst)
				f2 = konst.chiqindi.get(cint(p.get("paket_qalinlik_mm")), konst.chiqindi.get(0, 0.0))
				mat[p2] = mat.get(p2, 0.0) + g2.hw * g2.hh * max(cint(p.get("dona")), 1) / 1e6 * (1 + f2 / 100.0)
		if p["oyna_item"] in reja_itemlar:
			mat[p["oyna_item"]] = flt(reja_itemlar[p["oyna_item"]].ishlatilgan_m2, 4)
			continue
		qal = cint(p.get("qalinlik_mm"))
		if qal not in konst.chiqindi and 0 not in konst.chiqindi:
			msg = _("{0} mm qalinlik uchun chiqindi foizi kiritilmagan — 0 % olindi (Oyna Narx Jadvali → Chiqindi)").format(qal)
			if msg not in ogoh:
				ogoh.append(msg)
		foiz = konst.chiqindi.get(qal, konst.chiqindi.get(0, 0.0))
		g_ = r.get("geom") or _geom(p, konst)
		sarf = g_.hw * g_.hh * max(cint(p.get("dona")), 1) / 1e6 * (1 + foiz / 100.0)
		mat[p["oyna_item"]] = mat.get(p["oyna_item"], 0.0) + sarf
	materiallar = [{"item_code": k, "qty": flt(v, 3)} for k, v in mat.items()]
	jami.material_kvadrat = flt(sum(v for v in mat.values()), 3)
	for a in aksessuarlar:
		if a.get("item") and flt(a.get("qty")) > 0:
			materiallar.append({"item_code": a["item"], "qty": flt(a["qty"], 3)})

	if standart_rejimi == "Xato":
		xato.extend(std)
		std = []
	return frappe._dict(natija=natija, jami=jami, materiallar=materiallar,
	                    xato=xato, std=std, ogoh=ogoh, reja_itemlar=reja_itemlar)


# ============================================================ RASKROY — listdan kesish rejasi

def _raskroy_kirish(poz_rows, list_rows, konst):
	"""Pozitsiyalar → packer-bo'laklar (hisob-o'lchamlar), listlar {item: (W, H)}. Mijoz oynasi o'tkaziladi."""
	bol = []
	for p in poz_rows:
		if cint(p.get("mijoz_oynasi")) or not p.get("oyna_item"):
			continue
		g = _geom(p, konst)
		if not (g.hw and g.hh):
			continue
		rot = bool(cint(konst.get("raskroy_burish", 1))) and (p.get("oyna_turi") or "") != "Naqshli"
		bol.append(dict(nr=cint(p.get("nr")), item=p["oyna_item"], w=g.hw, h=g.hh, dona=max(cint(p.get("dona")), 1),
		                burish_mumkin=int(rot), shakl=p.get("shakl") or "To'rtburchak"))
		# paket — 2-qavat ham xuddi shu o'lchamda kesiladi (boshqa item bo'lishi mumkin)
		if cint(p.get("paket")) and p.get("paket_oyna_item"):
			bol.append(dict(nr=cint(p.get("nr")), item=p["paket_oyna_item"], w=g.hw, h=g.hh,
			                dona=max(cint(p.get("dona")), 1), burish_mumkin=int(rot),
			                shakl=p.get("shakl") or "To'rtburchak"))
	listlar = {}
	for r in list_rows:
		if r.get("oyna_item"):
			listlar[r["oyna_item"]] = (cint(r.get("list_eni_mm")), cint(r.get("list_boyi_mm")))
	return bol, listlar


def _raskroy_imzo(bolaklar, listlar, rk):
	"""Reja-dolzarbligi uchun hash: bo'laklar + listlar + packer-konstantalari (narx pol/shifti KIRMAYDI)."""
	src = json.dumps([sorted((b["nr"], b["item"], b["w"], b["h"], b["dona"], b["burish_mumkin"], b["shakl"]) for b in bolaklar),
	                  sorted((k, list(v)) for k, v in listlar.items()), sorted(dict(rk).items())], sort_keys=True, default=str)
	return hashlib.sha1(src.encode()).hexdigest()[:16]


def _reja_dolzarb(doc, poz_rows, konst):
	"""(holat, reja): «Yo'q» — reja yo'q/oyna-pozitsiya yo'q; «Dolzarb» — hash mos (reja qaytadi); «Eskirgan»."""
	js = doc.get("raskroy_json")
	if not js:
		return "Yo'q", None
	bol, listlar = _raskroy_kirish(poz_rows, _rows(doc, "listlar"), konst)
	if not bol:
		return "Yo'q", None
	if _raskroy_imzo(bol, listlar, konst.raskroy) != (doc.get("raskroy_imzo") or ""):
		return "Eskirgan", None
	try:
		reja = json.loads(js) if isinstance(js, str) else js
	except ValueError:
		return "Yo'q", None
	return ("Dolzarb", reja) if reja.get("itemlar") else ("Yo'q", None)


def _reja_itemlar(reja, konst, list_rows=None, reja_rows=None):
	"""Reja + «Listlar rejasi» qatorlari (har list) → item bo'yicha jamlanma + har list tafsiloti (`listlar` {list_nr: …}).
	Har list uchun: bo'laklar/chiqindi/qoldiq % (LISTGA nisbatan); mijozga qo'shimcha % — default = chiqindi
	(sozlama min/max ichida), foydalanuvchi qatorda qo'lda oshirsa (qoldiqni sotish) chegara = 100 − bo'laklar;
	«qoldiq mijozga» → maksimum va ombordan butun list. charged_m2 = Σ list_m2 × foiz; narx_foiz — bo'laklarga nisbatan (ichki)."""
	out = {}
	if not reja:
		return out
	mn, mx = flt(konst.get("chiqindi_min_foiz")), flt(konst.get("chiqindi_max_foiz"))
	rr = {(r.get("oyna_item"), cint(r.get("list_nr"))): r for r in (reja_rows or []) if r.get("oyna_item")}
	for item, v in (reja.get("itemlar") or {}).items():
		sheets = [sh for sh in (reja.get("listlar") or []) if sh.get("item") == item]
		bol_t = flt(v.get("bolaklar_m2"))
		if not sheets or not bol_t:
			continue
		det, list_m2, charged, ish = {}, 0.0, 0.0, 0.0
		for sh in sheets:
			sm2 = flt(flt(sh.get("W")) * flt(sh.get("H")) / 1e6, 4)
			if not sm2:
				continue
			bol, ch, qol = flt(sh.get("bolaklar_m2")), flt(sh.get("chiqindi_m2")), flt(sh.get("qoldiq_m2"))
			bol_f, ch_f, qol_f = bol / sm2 * 100, ch / sm2 * 100, qol / sm2 * 100
			max_f = max(100.0 - bol_f, 0.0)
			avto = ch_f
			if mn and avto < mn:
				avto = mn
			if mx and avto > mx and not cint(sh.get("butun_list")):
				avto = mx
			avto = min(avto, max_f)
			r = rr.get((item, cint(sh.get("list_nr")))) or {}
			mijozga, qolda = cint(r.get("qoldiq_mijozga")), cint(r.get("mijoz_foiz_qolda"))
			if mijozga:
				foiz, qolda = max_f, 1
			elif qolda:
				foiz = min(max(flt(r.get("mijoz_foiz")), 0.0), max_f)
			else:
				foiz = avto
			c_m2 = flt(ch, 4) if (not qolda and abs(foiz - ch_f) < 1e-9) else flt(sm2 * foiz / 100.0, 4)
			det[cint(sh.get("list_nr"))] = frappe._dict(
				list_m2=sm2, bolaklar_foiz=flt(bol_f, 1), chiqindi_foiz=flt(ch_f, 1), qoldiq_foiz=flt(qol_f, 1),
				avto_foiz=flt(avto, 1), mijoz_foiz=flt(foiz, 1), mijoz_jami_foiz=flt(bol_f + foiz, 1),
				qolda=qolda, qoldiq_mijozga=mijozga, mijoz_m2=c_m2)
			list_m2 += sm2
			charged += c_m2
			ish += sm2 if mijozga else flt(sh.get("ishlatilgan_m2"))
		if not list_m2:
			continue
		out[item] = frappe._dict(
			list_m2=flt(list_m2, 4), bolaklar_foiz=flt(bol_t / list_m2 * 100, 1),
			chiqindi_foiz=flt(flt(v.get("chiqindi_m2")) / list_m2 * 100, 1), qoldiq_foiz=flt(flt(v.get("qoldiq_m2")) / list_m2 * 100, 1),
			avto_foiz=flt(sum(x.avto_foiz * x.list_m2 for x in det.values()) / list_m2, 1),
			mijoz_foiz=flt(charged / list_m2 * 100, 1), mijoz_jami_foiz=flt((bol_t + charged) / list_m2 * 100, 1),
			qolda=int(any(x.qolda for x in det.values())), qoldiq_mijozga=int(any(x.qoldiq_mijozga for x in det.values())),
			charged_m2=flt(charged, 4), narx_foiz=charged / bol_t * 100.0, haqiqiy_foiz=flt(flt(v.get("chiqindi_m2")) / list_m2 * 100, 1),
			ishlatilgan_m2=flt(ish, 4), chiqindi_m2=flt(v.get("chiqindi_m2"), 4), qoldiq_m2=flt(v.get("qoldiq_m2"), 4),
			list_soni=len(sheets), butun_list=cint(v.get("butun_list")), listlar=det)
	return out


def _oxirgi_list_olchami(item, konst):
	"""Shu oyna uchun oxirgi ishlatilgan list o'lchami (Oyna Zakaz List), bo'lmasa sozlama-default."""
	r = frappe.db.sql("""select l.list_eni_mm, l.list_boyi_mm from `tabOyna Zakaz List` l
		where l.oyna_item=%s and ifnull(l.list_eni_mm,0)>0 and ifnull(l.list_boyi_mm,0)>0
		order by l.modified desc, l.creation desc limit 1""", item)
	if r:
		return cint(r[0][0]), cint(r[0][1])
	return konst.raskroy_list


def _listlarni_sinxronla(doc, s):
	"""`listlar`: har mijozsiz oyna-item uchun bitta qator (yetim/dublikat o'chadi, foydalanuvchi o'lchami saqlanadi)."""
	items = []
	for p in doc.get("pozitsiyalar") or []:
		if p.oyna_item and not cint(p.mijoz_oynasi) and p.oyna_item not in items:
			items.append(p.oyna_item)
	mavjud = {}
	for r in doc.get("listlar") or []:
		if r.oyna_item and r.oyna_item not in mavjud:
			mavjud[r.oyna_item] = r
	if not items and not mavjud:
		return
	konst = konst_from(s)
	yangi = []
	for it in items:
		r = mavjud.get(it)
		if r is None:
			W, H = _oxirgi_list_olchami(it, konst)
			r = doc.append("listlar", {"oyna_item": it, "list_eni_mm": W, "list_boyi_mm": H})
		elif not cint(r.list_eni_mm) and not cint(r.list_boyi_mm):
			r.list_eni_mm, r.list_boyi_mm = _oxirgi_list_olchami(it, konst)
		elif not cint(r.list_eni_mm) or not cint(r.list_boyi_mm):
			frappe.throw(_("Listlar: {0} uchun list eni VA bo'yi ikkalasi ham kiritilsin").format(it))
		if not cint(r.qalinlik_mm):
			r.qalinlik_mm = cint(frappe.db.get_value("Item", it, "custom_oyna_qalinlik_mm"))
		yangi.append(r)
	doc.set("listlar", yangi)
	for i, r in enumerate(doc.get("listlar") or [], 1):
		r.idx = i


def _raskroy_yoz(doc, holat, konst, ri=None, item_summa=None):
	"""Header-jamlanmalar + «Listlar rejasi» child-jadvali (Eskirgan bo'lsa ham ma'lumot uchun); Yo'q → tozalash."""
	doc.raskroy_holat = holat
	reja = None
	if holat != "Yo'q" and doc.get("raskroy_json"):
		try:
			reja = json.loads(doc.raskroy_json) if isinstance(doc.raskroy_json, str) else doc.raskroy_json
		except ValueError:
			reja = None
	eski = {(r.oyna_item, cint(r.list_nr)): r.as_dict() for r in (doc.get("raskroy_reja") or []) if r.oyna_item}
	doc.set("raskroy_reja", [])
	if not reja:
		if holat == "Yo'q":
			doc.raskroy_json = None
			doc.raskroy_imzo = None
		for k in ("raskroy_list_soni", "raskroy_bolaklar_m2", "raskroy_qoldiq_m2", "raskroy_chiqindi_m2",
		          "raskroy_chiqindi_foiz", "raskroy_foydalanish_foiz", "raskroy_mijoz_foiz"):
			doc.set(k, 0)
		for r in doc.get("listlar") or []:
			r.list_soni = 0
		return
	j = reja.get("jami") or {}
	doc.raskroy_list_soni = cint(j.get("list_soni"))
	doc.raskroy_bolaklar_m2 = flt(j.get("bolaklar_m2"), 4)
	doc.raskroy_qoldiq_m2 = flt(j.get("qoldiq_m2"), 4)
	doc.raskroy_chiqindi_m2 = flt(j.get("chiqindi_m2"), 4)
	doc.raskroy_foydalanish_foiz = flt(j.get("foydalanish_foiz"), 1)
	list_m2 = sum(flt(r.list_m2) for r in doc.get("listlar") or []) or sum(
		cint(v.get("list_soni")) * flt(v.get("W")) * flt(v.get("H")) / 1e6 for v in (reja.get("itemlar") or {}).values())
	doc.raskroy_chiqindi_foiz = flt(flt(j.get("chiqindi_m2")) / list_m2 * 100, 1) if list_m2 else 0  # listlarga nisbatan
	doc.raskroy_mijoz_foiz = flt(sum(flt(r.mijoz_m2) for r in doc.get("listlar") or []) / list_m2 * 100, 1) if (list_m2 and holat == "Dolzarb") else 0
	itemlar = reja.get("itemlar") or {}
	for r in doc.get("listlar") or []:
		r.list_soni = cint((itemlar.get(r.oyna_item) or {}).get("list_soni"))
	ri, item_summa = ri or {}, item_summa or {}
	for sh in reja.get("listlar") or []:
		x = (ri.get(sh.get("item")) or {}).get("listlar", {}).get(cint(sh.get("list_nr"))) if ri else None
		o = eski.get((sh.get("item"), cint(sh.get("list_nr")))) or {}  # Eskirgan: qo'lda foizlar saqlanadi
		soni = {}
		for b in sh.get("bolaklar") or []:
			soni[cint(b.get("nr"))] = soni.get(cint(b.get("nr")), 0) + 1
		bmatn = ", ".join(f"№{nr}" + (f" ×{n}" if n > 1 else "") for nr, n in sorted(soni.items()))
		qmatn = ", ".join(f"{cint(q['w'])}×{cint(q['h'])} ({flt(q.get('m2')):.2f} m²)" for q in sh.get("qoldiqlar") or [])
		doc.append("raskroy_reja", {
			"list_nr": cint(sh.get("list_nr")), "oyna_item": sh.get("item"),
			"olcham": f"{cint(sh.get('W'))}×{cint(sh.get('H'))}", "list_eni_mm": cint(sh.get("W")), "list_boyi_mm": cint(sh.get("H")),
			"bolak_soni": len(sh.get("bolaklar") or []), "bolaklar_matn": bmatn[:140],
			"bolaklar_m2": flt(sh.get("bolaklar_m2"), 4), "qoldiq_m2": flt(sh.get("qoldiq_m2"), 4),
			"chiqindi_m2": flt(sh.get("chiqindi_m2"), 4), "foydalanish_foiz": flt(sh.get("foydalanish_foiz"), 1),
			"qoldiqlar_matn": (qmatn or "—")[:140],
			"chiqindi_foiz": flt(x.chiqindi_foiz) if x else flt(flt(sh.get("chiqindi_m2")) / flt(sh.get("list_m2") or 1) * 100, 1),
			"qoldiq_foiz": flt(x.qoldiq_foiz) if x else flt(flt(sh.get("qoldiq_m2")) / flt(sh.get("list_m2") or 1) * 100, 1),
			"mijoz_foiz": flt(x.mijoz_foiz) if x else flt(o.get("mijoz_foiz")), "mijoz_foiz_avto": flt(x.avto_foiz) if x else flt(o.get("mijoz_foiz_avto")),
			"mijoz_foiz_qolda": cint(x.qolda) if x else cint(o.get("mijoz_foiz_qolda")), "qoldiq_mijozga": cint(x.qoldiq_mijozga) if x else cint(o.get("qoldiq_mijozga")),
			"mijoz_jami_foiz": flt(x.mijoz_jami_foiz) if x else flt(o.get("mijoz_jami_foiz")), "mijoz_m2": flt(x.mijoz_m2) if x else 0,
			"mijoz_summa": _pul(flt(item_summa.get(sh.get("item"))) * flt(x.mijoz_m2) / flt((ri.get(sh.get("item")) or {}).get("charged_m2") or 1)) if (x and flt(x.mijoz_m2)) else 0,
		})
	if doc.get("raskroy_reja"):
		doc.raskroy_qoldiq_m2 = flt(sum(flt(r.qoldiq_m2) for r in doc.get("raskroy_reja") if not cint(r.qoldiq_mijozga)), 4)  # omborda qoladigan qoldiq


@frappe.whitelist()
def raskroy_tuz(name):
	"""«Raskroy tuzish»: listlar → packer → reja JSON + imzo → save (validate/hisobla «Dolzarb» qiladi, narx/sarf yangilanadi)."""
	doc = frappe.get_doc("Oyna Zakaz", name)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Raskroy faqat qoralama (saqlangan, tasdiqlanmagan) zakaz uchun tuziladi"))
	s = sozlama()
	konst = konst_from(s)
	_listlarni_sinxronla(doc, s)
	bol, listlar = _raskroy_kirish(_rows(doc, "pozitsiyalar"), _rows(doc, "listlar"), konst)
	if not bol:
		frappe.throw(_("Raskroy uchun oyna-pozitsiya yo'q (mijozning o'z oynasi hisobga olinmaydi)"))
	reja = _raskroy_hisobla(bol, listlar, konst.raskroy)
	if reja.get("xato"):
		frappe.throw("<br>".join(reja["xato"]), title=_("Raskroy tuzilmadi"))
	doc.raskroy_json = json.dumps(reja, ensure_ascii=False)
	doc.raskroy_imzo = _raskroy_imzo(bol, listlar, konst.raskroy)
	doc.set("raskroy_reja", [])  # yangi reja — eski list-qatorlari (qo'lda foizlar) tozalanadi
	doc.save()
	j = reja["jami"]
	return {"holat": doc.raskroy_holat, "jami": j, "itemlar": reja["itemlar"],
	        "xabar": _("Raskroy: {0} list, bo'laklar {1} %, chiqindi {2} % (listga nisbatan), mijozga yozildi {3} % = {4}").format(
	            j["list_soni"], j["foydalanish_foiz"], doc.raskroy_chiqindi_foiz, doc.raskroy_mijoz_foiz, _som(doc.raskroy_chiqindi_summa))}


@frappe.whitelist()
def raskroy_ochir(name):
	"""Rejani o'chirish: holat «Yo'q», sarf yana jadval foizi bilan."""
	doc = frappe.get_doc("Oyna Zakaz", name)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Faqat qoralama zakaz uchun"))
	doc.raskroy_json = None
	doc.raskroy_imzo = None
	doc.save()
	return doc.raskroy_holat


# ============================================================ hujjat bilan ishlash

def _rows(doc, field):
	return [r.as_dict() if hasattr(r, "as_dict") else dict(r) for r in (doc.get(field) or [])]


def tayyorla(doc):
	"""Defaultlar; doc o'zgaradi."""
	# Hujjat faqat Oyna sex uchun: foydalanuvchi-default kompaniyasi (Frappe
	# user-default maydon-default'idan ustun) ustidan yoziladi
	doc.company = OYNA_SEX
	doc.valyuta = doc.valyuta or "UZS"
	if doc.docstatus == 0:  # havolalar faqat topshir() da db_set bilan yoziladi — mijozdan kelgan qiymat rad etiladi
		doc.sales_invoice = None
		doc.material_issue = None
	if doc.get("materiallar_avto") is None:
		doc.materiallar_avto = 1
	if doc.is_new() or not doc.holat:
		doc.holat = "Qoralama"
	if not doc.yetkazish_sanasi and doc.sana:
		doc.yetkazish_sanasi = add_days(doc.sana, 7)
	if doc.yetkazish_sanasi and doc.sana and getdate(doc.yetkazish_sanasi) < getdate(doc.sana):
		frappe.throw(_("Yetkazish sanasi zakaz sanasidan oldin bo'lmasin"))
	if doc.customer:
		node = frappe.db.get_value("Customer Group", OYNA_SEX, ["lft", "rgt"], as_dict=True)
		cg = frappe.db.get_value("Customer", doc.customer, "customer_group")
		if node and cg:
			l, r = frappe.db.get_value("Customer Group", cg, ["lft", "rgt"])
			if not (node.lft <= l and r <= node.rgt):
				frappe.throw(_("Mijoz {0} «Oyna sex» guruhida emas").format(doc.customer))
	# pozitsiya-raqamlari: mavjud raqamlar HECH QACHON o'zgarmaydi (teshik/kesim
	# bog'lari uziladi); faqat 0/dublikat qatorlar max+1 oladi
	nrs = set()
	raqamsiz = []
	for p in doc.get("pozitsiyalar") or []:
		n = cint(p.nr)
		if n > 0 and n not in nrs:
			nrs.add(n)
		else:
			raqamsiz.append(p)
	nxt = max(nrs, default=0) + 1
	for p in raqamsiz:
		p.nr = nxt
		nrs.add(nxt)
		nxt += 1
	for p in doc.get("pozitsiyalar") or []:
		_shaklga_moslash(p)
		if p.shakl == "Doira" and cint(p.diametr_mm):
			p.eni_mm = p.boyi_mm = cint(p.diametr_mm)
		if p.oyna_item and not cint(p.qalinlik_mm):
			p.qalinlik_mm, p.rang = frappe.db.get_value("Item", p.oyna_item, ["custom_oyna_qalinlik_mm", "custom_oyna_rang"]) or (0, None)
		# eski «Qisman» → «Polosa» (2026-09-12: sexda faqat To'liq/Polosa/Gul bor)
		if p.matofka_rejimi == "Qisman":
			p.matofka_rejimi = "Polosa"
		p.matofka_maydoni_m2 = 0
		if not cint(p.paket):
			p.paket_oyna_item = None
			p.paket_qalinlik_mm = 0
	for k in doc.get("kesimlar") or []:
		if not cint(k.ichki_radius_mm):
			poz = next((p for p in doc.get("pozitsiyalar") or [] if cint(p.nr) == cint(k.pozitsiya_nr)), None)
			k.ichki_radius_mm = cint(poz.qalinlik_mm) if poz else 0
	# yetim qatorlar
	for field in ("teshiklar", "kesimlar"):
		keep = [r for r in (doc.get(field) or []) if cint(r.pozitsiya_nr) in nrs]
		if len(keep) != len(doc.get(field) or []):
			doc.set(field, keep)
			for i, r in enumerate(doc.get(field), 1):
				r.idx = i
	for a in doc.get("aksessuarlar") or []:
		if cint(a.pozitsiya_nr) and cint(a.pozitsiya_nr) not in nrs:
			a.pozitsiya_nr = 0
	# aksessuar narxi (Item Price)
	s = sozlama()
	pl = s.aksessuar_narx_royxati
	for a in doc.get("aksessuarlar") or []:
		if a.item and not flt(a.narx) and pl:
			a.narx = flt(frappe.db.get_value("Item Price", {"item_code": a.item, "price_list": pl}, "price_list_rate"))
		if a.item and not a.uom:
			a.uom = frappe.db.get_value("Item", a.item, "stock_uom")
	_listlarni_sinxronla(doc, s)
	# rejim
	if not doc.standart_rejimi:
		doc.standart_rejimi = s.standart_rejimi or "Xato"
	if doc.standart_rejimi == "Ogohlantirish" and not doc.is_new():
		eski = frappe.db.get_value("Oyna Zakaz", doc.name, "standart_rejimi")
		if eski != "Ogohlantirish" and not _menejer():
			frappe.throw(_("Standart tekshiruv rejimini faqat menejer o'zgartira oladi"))
	elif doc.standart_rejimi == "Ogohlantirish" and doc.is_new() and not _menejer():
		frappe.throw(_("Standart tekshiruv rejimini faqat menejer o'zgartira oladi"))


def _menejer():
	rollar = frappe.get_roles()
	return "System Manager" in rollar or "Sales Manager" in rollar


def hisobla(doc, throw=True):
	"""tekshir + hisobla + materiallar — natijani hujjatga yozadi. throw=False → preview."""
	s = sozlama()
	konst = konst_from(s)
	tarif = Tarif.from_settings(s)
	poz = _rows(doc, "pozitsiyalar")
	aks = _rows(doc, "aksessuarlar")
	holat, reja = _reja_dolzarb(doc, poz, konst)
	out = _hisobla_core(poz, _rows(doc, "teshiklar"), _rows(doc, "kesimlar"), aks, tarif, konst,
	                    doc.standart_rejimi or "Xato", reja=reja, list_rows=_rows(doc, "listlar"), reja_rows=_rows(doc, "raskroy_reja"))
	# pozitsiyalarga yozish
	by_nr = {cint(p.get("nr")): (p, r, tk, ks) for p, r, tk, ks in out.natija}
	for row in doc.get("pozitsiyalar") or []:
		_p, r, tk, ks = by_nr[cint(row.nr)]
		for k in ("hisob_eni_mm", "hisob_boyi_mm", "haqiqiy_maydon_m2", "hisob_maydon_m2", "jami_maydon_m2",
		          "perimetr_m", "ogirlik_kg", "teshik_soni", "kesim_soni", "olcham_matn", "xulosa", "tavsif",
		          "ishlov_tartibi", "oyna_tarif", "summa", "taxminiy", "chiqindi_foiz", "chiqindi_summa"):
			row.set(k, r.get(k))
		for k in KATEGORIYALAR:
			row.set(k + "_summa", r.get(k + "_summa"))
		# teshik/kesim narxlari
		trows = [x for x in (doc.get("teshiklar") or []) if cint(x.pozitsiya_nr) == cint(row.nr)]
		for x, (nr_, s_) in zip(trows, r.get("teshik_narx") or []):
			x.narx, x.summa = nr_, s_
		krows = [x for x in (doc.get("kesimlar") or []) if cint(x.pozitsiya_nr) == cint(row.nr)]
		for x, (nr_, s_) in zip(krows, r.get("kesim_narx") or []):
			x.narx, x.summa = nr_, s_
	for row, a in zip(doc.get("aksessuarlar") or [], aks):
		row.summa = a.get("summa")
	for k, v in out.jami.items():
		doc.set(k, v)
	doc.jami_summa = _pul(flt(doc.pozitsiyalar_jami) + flt(doc.aksessuar_jami))
	doc.qoldiq = _pul(flt(doc.jami_summa) - flt(doc.oldindan_tolov))
	# materiallar
	if cint(doc.materiallar_avto):
		doc.set("materiallar", [])
		ombor = s.ombor or OMBOR
		for m in out.materiallar:
			uom, nom, stock = frappe.db.get_value("Item", m["item_code"], ["stock_uom", "item_name", "is_stock_item"]) or (None, None, 0)
			if not cint(stock):
				continue  # xizmat-item (paket, bo'yash…) ombordan chiqmaydi
			doc.append("materiallar", {"item_code": m["item_code"], "item_name": nom, "qty": m["qty"],
			                           "warehouse": ombor, "uom": uom})
	# raskroy: listlar-qatorlariga foizlar (listga nisbatan) va mijozga yozilgan summa
	ri = out.get("reja_itemlar") or {}
	for r in doc.get("listlar") or []:
		x = ri.get(r.oyna_item)
		if x:
			r.list_m2, r.bolaklar_foiz, r.chiqindi_foiz, r.qoldiq_foiz = x.list_m2, x.bolaklar_foiz, x.chiqindi_foiz, x.qoldiq_foiz
			r.mijoz_foiz_avto = x.avto_foiz
			r.mijoz_foiz = x.mijoz_foiz
			r.mijoz_foiz_qolda = x.qolda
			r.qoldiq_mijozga = x.qoldiq_mijozga
			r.mijoz_m2 = x.charged_m2
			r.mijoz_jami_foiz = x.mijoz_jami_foiz
			r.mijoz_summa = _pul(sum(flt(rr.get("chiqindi_summa")) for p_, rr, _a, _b in out.natija if p_.get("oyna_item") == r.oyna_item))
		else:
			r.list_m2 = r.bolaklar_foiz = r.chiqindi_foiz = r.qoldiq_foiz = r.mijoz_foiz_avto = r.mijoz_m2 = r.mijoz_summa = r.mijoz_jami_foiz = r.mijoz_foiz = 0
	# raskroy: holat, jamlanmalar, reja-jadvali (har list — foydalanuvchi foizlari saqlanadi)
	_raskroy_yoz(doc, holat, konst, ri, {p_.get("oyna_item"): _pul(sum(flt(rr.get("chiqindi_summa")) for p2, rr, _a, _b in out.natija if p2.get("oyna_item") == p_.get("oyna_item"))) for p_, _r, _a, _b in out.natija})
	if holat == "Eskirgan":
		out.ogoh.append(_("Raskroy ESKIRGAN (pozitsiya yoki list o'lchami o'zgargan) — «Raskroy tuzish»ni qayta bosing; hozircha sarf jadval foizi bilan, chiqindi mijozga qo'shilmadi"))
	elif holat == "Yo'q" and any(p.get("oyna_item") and not cint(p.get("mijoz_oynasi")) for p in poz):
		out.ogoh.append(_("Raskroy tuzilmagan — sarf jadval foizi bilan (taxminiy), haqiqiy chiqindi mijozga hisoblanmadi. Listlar jadvalini tekshirib «Raskroy tuzish»ni bosing"))
	# xabarlar
	if out.ogoh or out.std:
		frappe.msgprint("<br>".join(out.std + out.ogoh), title=_("Oyna Zakaz — ogohlantirish"), indicator="orange")
	if out.xato:
		if throw:
			frappe.throw("<br>".join(out.xato), title=_("Oyna Zakaz tekshiruvi"))
	return out


@frappe.whitelist()
def hisobla_preview(doc):
	"""Saqlamasdan jonli hisob (forma «Hisoblash»). doc — JSON."""
	d = frappe.get_doc(json.loads(doc) if isinstance(doc, str) else doc)
	d.flags.ignore_permissions = False
	frappe.has_permission("Oyna Zakaz", "read", throw=True)
	tayyorla(d)
	out = hisobla(d, throw=False)
	res = d.as_dict()
	res["_xato"] = out.xato
	res["_std"] = out.std
	res["_ogoh"] = out.ogoh
	return res


# ============================================================ zakaz-oqimi: SI + MI (Sales Order YO'Q)

HOLATLAR = ("Qoralama", "Zakaz olindi", "Tayyor", "Topshirildi", "Bekor qilindi")
OTISHLAR = {"Zakaz olindi": ("Tayyor", "Topshirildi"), "Tayyor": ("Topshirildi", "Zakaz olindi")}
HOLAT_STIL = {"Qoralama": "", "Zakaz olindi": "warning", "Tayyor": "primary", "Topshirildi": "success",
              "Bekor qilindi": "danger"}


def _som(v):
	return f"{int(round(flt(v))):,}".replace(",", " ") + " so'm"


def _xizmat_qatorlar(doc, s):
	"""Sales Invoice uchun xizmat-qatorlar: kategoriya × oyna-item, qty 1 Unit, rate = summa, batafsil tavsif."""
	items = {
		"oyna": s.item_oyna or "Oyna-kesish", "qirra": s.item_qirra or "Qirra ishlovi",
		"burchak": s.item_burchak or "Burchak ishlovi", "teshik": s.item_teshik or "Teshik ochish",
		"kesim": s.item_kesim or "Fortchka ochish", "matofka": s.item_matofka or "Matofka",
		"zakalka": s.item_zakalka or "Zakalka", "markirovka": s.item_markirovka or "Markirovka",
		"reska": s.get("item_reska") or "Reska (kesish)", "boyash": s.get("item_boyash") or "Oyna bo'yash",
		"paket": s.get("item_paket") or "Paket yig'ish",
	}
	guruh = {}  # (kategoriya, item) -> [summa, [tavsif...], birinchi nr]
	for p in doc.get("pozitsiyalar") or []:
		for k in KATEGORIYALAR:
			sm = flt(p.get(k + "_summa"))
			if not sm:
				continue
			key = (k, p.oyna_item if k == "oyna" else None)
			g = guruh.setdefault(key, [0.0, [], cint(p.nr)])
			g[0] += sm
			if k == "oyna":
				ch = f" + list sarfi {flt(p.get('chiqindi_foiz')):g} % {_som(p.get('chiqindi_summa'))}" if flt(p.get("chiqindi_summa")) else ""
				g[1].append(f"#{p.nr} {p.olcham_matn} ×{p.dona} ({flt(p.hisob_maydon_m2, 4)} m² × {p.dona} × {_som(p.oyna_tarif)}{ch})")
			else:
				g[1].append(f"#{p.nr}: {_som(sm)}")
	rows = []
	tartib = {k: i for i, k in enumerate(KATEGORIYALAR)}
	for (k, item), (sm, tav, birinchi_nr) in sorted(guruh.items(), key=lambda x: (tartib[x[0][0]], x[1][2])):
		nom = {"oyna": f"Oyna: {item}", "reska": "Reska (oyna kesish)", "qirra": "Qirra ishlovi",
		       "burchak": "Burchak ishlovi", "teshik": "Teshik ochish",
		       "kesim": "Kesim (fortochka/qulf/rozetka)", "matofka": "Matofka",
		       "boyash": "Oyna bo'yash", "zakalka": "Zakalka",
		       "markirovka": "Markirovka (logo / yozuv)", "paket": "Paket yig'ish (2 qavat)"}[k]
		rows.append({"item_code": items[k], "qty": 1, "uom": "Unit", "conversion_factor": 1,
		             "rate": _pul(sm), "price_list_rate": 0,
		             "description": nom + " — " + "; ".join(tav)})
	# aksessuarlar: ombor-tovarlar bitta «Aksessuarlar» qatorida; xizmat-itemlar (paket, bo'yash…) alohida qator
	aks = [a for a in doc.get("aksessuarlar") or [] if a.item and flt(a.summa)]
	if aks:
		stock = {r.name: cint(r.is_stock_item) for r in frappe.get_all("Item", {"name": ("in", [a.item for a in aks])}, ["name", "is_stock_item"])}
		tovar = [a for a in aks if stock.get(a.item, 1)]
		if tovar:
			tav = "; ".join(f"{a.item_name or a.item} ×{flt(a.qty):g}" for a in tovar)
			rows.append({"item_code": s.item_aksessuar or "Aksessuarlar", "qty": 1, "uom": "Unit",
			             "conversion_factor": 1, "rate": _pul(sum(flt(a.summa) for a in tovar)), "price_list_rate": 0,
			             "description": "Aksessuarlar — " + tav})
		for a in aks:
			if not stock.get(a.item, 1):
				rows.append({"item_code": a.item, "qty": flt(a.qty), "uom": a.uom or "Unit", "conversion_factor": 1,
				             "rate": _pul(a.narx), "price_list_rate": 0,
				             "description": f"{a.item_name or a.item} — {flt(a.qty):g} × {_som(a.narx)}" + (f" (poz. №{a.pozitsiya_nr})" if cint(a.pozitsiya_nr) else "")})
	return rows


def _jonli(doctype, name):
	return bool(name) and frappe.db.get_value(doctype, name, "docstatus") == 1


def on_submit(doc):
	"""Submit = zakaz qabul qilindi (holat «Zakaz olindi»). Summa 0 bo'lsa qabul qilinmaydi."""
	if not _xizmat_qatorlar(doc, sozlama()):
		frappe.throw(_("Zakaz summasi 0 — hech bo'lmasa bitta pozitsiya yoki aksessuar narxlansin (narx > 0)"))
	if doc.get("raskroy_holat") == "Eskirgan":
		frappe.throw(_("Raskroy eskirgan — «Raskroy tuzish»ni qayta bosing yoki rejani o'chiring, keyin tasdiqlang"))
	if doc.get("raskroy_holat") in (None, "", "Yo'q") and any(p.oyna_item and not cint(p.mijoz_oynasi) for p in doc.get("pozitsiyalar") or []):
		frappe.msgprint(_("Raskroy tuzilmagan: sarf jadval foizi bilan, chiqindi mijozga qo'shilmadi"), indicator="orange", alert=True)
	doc.db_set("holat", "Zakaz olindi", update_modified=False)
	doc.holat = "Zakaz olindi"


@frappe.whitelist()
def holat_ozgartir(name, yangi):
	"""Zakaz olindi → Tayyor → Topshirildi (yoki to'g'ridan-to'g'ri Topshirildi; Tayyor → Zakaz olindi qaytarish).
	Faqat holat o'zgaradi — hech qanday buxgalteriya/ombor hujjati yaratilmaydi."""
	frappe.db.sql("select name from `tabOyna Zakaz` where name=%s for update", name)  # parallel bosishlar navbatda
	doc = frappe.get_doc("Oyna Zakaz", name)
	doc.check_permission("submit")
	if doc.docstatus != 1:
		frappe.throw(_("Faqat tasdiqlangan (submit) zakaz uchun"))
	if yangi not in OTISHLAR.get(doc.holat, ()):
		frappe.throw(_("«{0}» holatidan «{1}» ga o'tish yo'q").format(doc.holat, yangi))
	if yangi == "Topshirildi":
		topshir(doc)
	else:
		doc.db_set("holat", yangi, update_modified=False)
	return yangi


def topshir(doc):
	"""Mijozga topshirildi — faqat holat yoziladi.

	HECH QANDAY HUJJAT YARATILMAYDI: buxgalteriya (Sales Invoice) va ombor
	(Material Issue) tegilmagan qoladi. Yoqish kerak bo'lsa — quyidagi ikki
	qatorni tiklash yetarli (funksiyalar pastda saqlangan):
	    s = sozlama()
	    si, mi = _sales_invoice_yarat(doc, s), _material_issue_yarat(doc, s)
	"""
	doc.db_set("holat", "Topshirildi", update_modified=False)
	doc.holat = "Topshirildi"
	frappe.msgprint(
		_("«Oyna Zakaz» faqat hisob-kitob uchun ishlaydi — Sales Invoice va "
		  "Material Issue yaratilmadi, buxgalteriya va omborga ta'sir qilmadi."),
		indicator="orange", alert=True)


# ---------------------------------------------------------------------------
# Quyidagi ikki funksiya HOZIR CHAQIRILMAYDI (yuqoridagi `topshir` ga qarang).
# Jonli rejimga o'tilganda ishlatiladi — kod ishlaydigan holatda saqlangan.
# ---------------------------------------------------------------------------


def _sales_invoice_yarat(doc, s):
	if _jonli("Sales Invoice", doc.sales_invoice):
		return doc.sales_invoice
	mavjud = frappe.db.get_value("Sales Invoice", {"custom_oyna_zakaz": doc.name, "docstatus": 1}, "name")
	if mavjud:
		return mavjud
	from akfa_diller.akfa_diller.api import exchange
	if not exchange.get_company_rate(OYNA_SEX, "USD", "UZS", nowdate()):
		frappe.throw(_("«Oyna sex» uchun USD→UZS kursi topilmadi (Company Currency Exchange)"))
	rows = _xizmat_qatorlar(doc, s)
	if not rows:
		frappe.throw(_("Sales Invoice uchun summa 0 — hech bo'lmasa bitta pozitsiya yoki aksessuar narxlansin"))
	for r in rows:
		if not frappe.db.exists("Item", r["item_code"]):
			frappe.throw(_("Xizmat-itemi topilmadi: {0} (Oyna Narx Jadvali → xizmat-itemlar)").format(r["item_code"]))
	si = frappe.new_doc("Sales Invoice")
	si.flags.ignore_permissions = True
	si.update({
		"company": OYNA_SEX,
		"customer": doc.customer,
		"posting_date": nowdate(),
		"due_date": nowdate(),
		"currency": "UZS",
		# UZS narx-ro'yxati (Item Price ishlatilmaydi — narxlar Oyna Narx Jadvalidan, rate to'g'ridan-to'g'ri);
		# price_list_currency / plc_conversion_rate'ni ERPNext o'zi to'ldiradi
		"selling_price_list": NARX_ROYXATI if frappe.db.exists("Price List", NARX_ROYXATI) else "Standard Selling",
		"ignore_pricing_rule": 1,
		"update_stock": 0,
		"set_warehouse": s.ombor or OMBOR,
		"custom_jami_kvadrat": flt(doc.jami_kvadrat, 3),
		"custom_oyna_zakaz": doc.name,
		"remarks": _("Oyna Zakaz {0}").format(doc.name),
	})
	for r in rows:
		si.append("items", dict(r, warehouse=s.ombor or OMBOR))
	si.insert(ignore_permissions=True)
	si.submit()
	frappe.msgprint(_("Sales Invoice yaratildi: {0}").format(frappe.utils.get_link_to_form("Sales Invoice", si.name)), alert=True)
	return si.name


def _material_issue_yarat(doc, s):
	rows = [m for m in (doc.get("materiallar") or []) if m.item_code and flt(m.qty) > 0]
	if not rows:
		return None
	if _jonli("Stock Entry", doc.material_issue):
		return doc.material_issue
	kam = []
	for m in rows:
		bor = flt(frappe.db.get_value("Bin", {"item_code": m.item_code, "warehouse": m.warehouse or s.ombor or OMBOR}, "actual_qty"))
		if bor < flt(m.qty):
			kam.append(f"{m.item_code}: kerak {flt(m.qty):g}, omborda {bor:g}")
	if kam:
		frappe.msgprint(_("Ombor zaxirasi yetarli emas (chiqim baribir yoziladi):<br>") + "<br>".join(kam), indicator="orange", alert=True)
	expense_account = frappe.get_cached_value("Company", OYNA_SEX, "default_expense_account")
	cost_center = frappe.get_cached_value("Company", OYNA_SEX, "cost_center")
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Issue"
	se.purpose = "Material Issue"
	se.company = OYNA_SEX
	se.remarks = _("Oyna Zakaz {0} — tan narx (Material Issue)").format(doc.name)
	for m in rows:
		se.append("items", {"item_code": m.item_code, "qty": flt(m.qty), "uom": m.uom or None,
		                    "s_warehouse": m.warehouse or s.ombor or OMBOR,
		                    "expense_account": expense_account, "cost_center": cost_center})
	se.flags.ignore_permissions = True
	se.insert(ignore_permissions=True)
	se.submit()
	frappe.msgprint(_("Material Issue (tan narx) yaratildi: {0}").format(frappe.utils.get_link_to_form("Stock Entry", se.name)), alert=True)
	return se.name


def bekor_tekshir(doc):
	"""Oyna Zakaz — asosiy hujjat: bekorida faktura va material-chiqim ham bekor bo'ladi.
	Fakturaga to'lov bog'langan bo'lsa Frappe'ning o'zi to'xtatadi (avval to'lov ajratiladi)."""
	if _jonli("Sales Invoice", doc.sales_invoice):
		si = frappe.db.get_value("Sales Invoice", doc.sales_invoice, ["grand_total", "outstanding_amount"], as_dict=True)
		if si and flt(si.outstanding_amount) < flt(si.grand_total):
			frappe.throw(_("Sales Invoice {0} ga to'lov bog'langan — avval to'lovni (Payment Entry) bekor qiling yoki ajrating").format(doc.sales_invoice))


def on_cancel(doc):
	"""Bekor: bog'liq Sales Invoice va Material Issue ham bekor (yetim qolmasin); holat «Bekor qilindi».
	(Frappe fakturani/chiqimni alohida bekor qilishga yo'l qo'ymaydi — Oyna Zakaz'ga bog'langan.)"""
	for dt, nom in (("Sales Invoice", doc.sales_invoice), ("Stock Entry", doc.material_issue)):
		if _jonli(dt, nom):
			x = frappe.get_doc(dt, nom)
			egasi = x.get("custom_oyna_zakaz") if dt == "Sales Invoice" else (doc.name in (x.remarks or ""))
			if (egasi if dt == "Stock Entry" else egasi == doc.name) is False:
				frappe.throw(_("{0} {1} bu zakazga tegishli emas — bekor qilinmadi").format(_(dt), nom))
			x.flags.ignore_permissions = True
			x.cancel()
			frappe.msgprint(_("{0} {1} ham bekor qilindi").format(_(dt), nom), alert=True)
	doc.db_set("holat", "Bekor qilindi", update_modified=False)


# ============================================================ seed / setup

XIZMAT_ITEMLAR = ("Qirra ishlovi", "Teshik ochish", "Burchak ishlovi", "Aksessuarlar", "Markirovka",
                  "Reska (kesish)", "Oyna bo'yash", "Paket yig'ish")
ITEM_XARITA = {"item_oyna": "Oyna-kesish", "item_qirra": "Qirra ishlovi", "item_burchak": "Burchak ishlovi", "item_markirovka": "Markirovka",
               "item_teshik": "Teshik ochish", "item_kesim": "Fortchka ochish", "item_matofka": "Matofka",
               "item_zakalka": "Zakalka", "item_aksessuar": "Aksessuarlar",
               "item_reska": "Reska (kesish)", "item_boyash": "Oyna bo'yash", "item_paket": "Paket yig'ish"}
NARX_ROYXATI = "Oyna sex sotuv (UZS)"
T = "TAXMINIY — tasdiqlang"


def _tarif_seed():
	rows = []

	def add(kat, narx, birlik, item=None, t=0, kalit=None, dan=0, gacha=0, izoh=None):
		rows.append({"kategoriya": kat, "oyna_item": item, "qalinlik_mm": t, "kalit": kalit,
		             "diapazon_dan": dan, "diapazon_gacha": gacha, "birlik": birlik, "narx": narx, "izoh": izoh})

	for item, narx, izoh in (("4 mm oq", 135000, None), ("4 mm pepel", 200000, T), ("6 mm oq", 160000, None),
	                         ("6 mm pepel", 240000, None), ("6 mm yod", 240000, None),
	                         ("6 mm grey komfort", 260000, T), ("6 mm reflonniy moru", 300000, T),
	                         ("6 mm reflonniy moru uz", 300000, T), ("10 mm oq", 220000, None),
	                         ("10 mm pepel", 390000, None), ("10 mm yod", 390000, T)):
		add("Oyna", narx, "m²", item=item, izoh=izoh)
	# --- Sex narx ro'yxati 07.09.2026 (foydalanuvchi rasmi) ---
	add("Qirra", 5000, "pog.m", kalit="Kromka", izoh="Sex narxi 07.09.2026")
	add("Teshik", 5000, "teshik", izoh="Sex narxi 07.09.2026")
	add("Kesim", 300000, "dona", kalit="Fortochka", izoh="Sex narxi 07.09.2026")
	for k in ("Qulf", "Rozetka", "Petlya", "Boshqa"):
		add("Kesim", 25000, "dona", kalit=k, izoh="Sex narxi 07.09.2026")
	add("Zakalka", 35000, "m²", izoh="Sex narxi 07.09.2026")
	add("Matofka", 40000, "m²", kalit="To'liq", izoh="Sex narxi 07.09.2026")     # splashnoy — butun oyna
	add("Matofka", 80000, "m²", kalit="Polosa", izoh="Sex narxi 07.09.2026")     # lenta ostidagi joy shaffof
	add("Matofka", 120000, "m²", kalit="Gul", izoh="Sex narxi 07.09.2026")       # gul/logo shaffof
	add("Boyash", 200000, "m²", izoh="Sex narxi 07.09.2026")
	add("Paket", 50000, "m²", izoh="Sex narxi 07.09.2026")                        # 2 qavat oynani yig'ish haqi
	add("Reska", 3000, "dona", izoh="Sex narxi 12.09.2026")                       # oddiy kesish, har kesilgan oynaga
	return rows


def ensure_setup():
	"""Idempotent seed: itemlar, custom-fieldlar, price list, sozlama, DocPerm. after_migrate + patch."""
	if not frappe.db.exists("Company", OYNA_SEX) or not frappe.db.exists("DocType", "Oyna Zakaz"):
		return
	_ensure_items()
	_ensure_custom_fields()
	_ensure_price_list()
	_ensure_sozlama()
	_ensure_perms()
	_ensure_print_format()
	frappe.db.commit()


def _ensure_print_format():
	if not frappe.db.exists("Print Format", "Oyna Zakaz Chizma"):
		return
	if frappe.db.exists("Property Setter", {"doc_type": "Oyna Zakaz", "property": "default_print_format"}):
		return
	frappe.make_property_setter({"doctype": "Oyna Zakaz", "doctype_or_field": "DocType",
	                             "property": "default_print_format", "value": "Oyna Zakaz Chizma",
	                             "property_type": "Data"}, ignore_validate=True)


def _ensure_items():
	if not frappe.db.exists("Item Group", "Xizmatlar"):
		frappe.get_doc({"doctype": "Item Group", "item_group_name": "Xizmatlar", "parent_item_group": "All Item Groups",
		                "is_group": 0}).insert(ignore_permissions=True)
	for nom in ITEM_XARITA.values():
		if frappe.db.exists("Item", nom):
			continue
		frappe.get_doc({"doctype": "Item", "item_code": nom, "item_name": nom, "item_group": "Xizmatlar",
		                "stock_uom": "Unit", "is_stock_item": 0, "is_sales_item": 1, "is_purchase_item": 0,
		                "include_item_in_manufacturing": 0, "description": nom + " (Oyna sex konfigurator)"}
		               ).insert(ignore_permissions=True)


def _ensure_custom_fields():
	"""Item maydonlari — konfigurator hisobi uchun.

	Sales Order va Sales Invoice'ga TEGILMAYDI: u doctypelarning
	Oyna sex maydonlari gitdagi `oyna_sex_sales_order_setup` / `oyna_sex_invoice_kvadrat`
	patchlariga tegishli (prodda ishlab turgan oqim). Bu yerdan ularni qayta
	ta'riflash mavjud maydon xossalarini bosib ketardi."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
	maydonlar = {
		"Item": [{"fieldname": "custom_oyna_qalinlik_mm", "label": "Oyna qalinligi (mm)", "fieldtype": "Int",
		          "insert_after": "item_group", "depends_on": "eval:doc.item_group=='Oynalar'"},
		         {"fieldname": "custom_oyna_rang", "label": "Oyna rangi", "fieldtype": "Data",
		          "insert_after": "custom_oyna_qalinlik_mm", "depends_on": "eval:doc.item_group=='Oynalar'"},
		         {"fieldname": "custom_oyna_turi", "label": "Oyna turi", "fieldtype": "Select", "options": OYNA_TURLARI,
		          "default": "Oddiy", "insert_after": "custom_oyna_rang", "depends_on": "eval:doc.item_group=='Oynalar'",
		          "description": "Tayyor zakalangan — kesilmaydi/teshilmaydi; Ko'zgu/Lakobel — zakalka qilinmaydi"}],
	}
	# Sales Invoice havola-maydonlari (custom_jami_kvadrat / custom_oyna_zakaz) ATAYLAB
	# qo'shilmaydi — «Oyna Zakaz» hujjat yaratmaydi, demak havola ham kerak emas.
	# Jonli rejimga o'tilganda shu yerga qaytariladi.
	create_custom_fields(maydonlar, ignore_validate=True)
	frappe.db.sql("""update `tabItem` set custom_oyna_turi='Oddiy' where item_group='Oynalar' and ifnull(custom_oyna_turi,'')=''""")
	for name in frappe.get_all("Item", {"item_group": "Oynalar"}, pluck="name"):
		if frappe.db.get_value("Item", name, "custom_oyna_qalinlik_mm"):
			continue
		m = re.match(r"^\s*(\d+)\s*mm\s+(.+)$", name, re.I)
		if m:
			frappe.db.set_value("Item", name, {"custom_oyna_qalinlik_mm": cint(m.group(1)),
			                                   "custom_oyna_rang": m.group(2).strip()}, update_modified=False)


def _ensure_price_list():
	if not frappe.db.exists("Price List", NARX_ROYXATI):
		frappe.get_doc({"doctype": "Price List", "price_list_name": NARX_ROYXATI, "currency": "UZS",
		                "selling": 1, "buying": 0, "enabled": 1}).insert(ignore_permissions=True)


def _ensure_sozlama():
	s = frappe.get_doc(SOZLAMA)
	ozgardi = False
	defaults = {"yaxlitlash_mm": 10, "min_maydon_m2": 0.25, "min_olcham_mm": 50, "max_olcham_mm": 6000,
	            "ogirlik_koef": 2.5, "zakalka_min_kichik_mm": 200, "zakalka_min_katta_mm": 300,
	            "zakalka_max_katta_mm": 3000, "zakalka_max_kichik_mm": 2000, "standart_rejimi": "Xato",
	            "zakalka_min_qalinlik_mm": 4, "zakalka_max_qalinlik_mm": 19,
	            "valyuta": "UZS", "aksessuar_narx_royxati": NARX_ROYXATI, "ombor": OMBOR}
	for k, v in defaults.items():
		if not s.get(k):
			s.set(k, v)
			ozgardi = True
	raskroy_defaults = {"raskroy_list_eni_mm": 3600, "raskroy_list_boyi_mm": 2600, "raskroy_chetlash_mm": 10,
	                    "raskroy_kerf_mm": 0, "raskroy_min_polosa_mm": 40, "raskroy_shakl_zaxira_mm": 0,
	                    "raskroy_min_qoldiq_mm": 300, "raskroy_min_qoldiq_m2": 0.25, "raskroy_qoldiq_max_soni": 2,
	                    "raskroy_chiqindi_min_foiz": 0, "raskroy_chiqindi_max_foiz": 100, "raskroy_burish": 1}
	saqlangan = frappe.db.get_singles_dict(SOZLAMA)  # tabSingles'dagi haqiqiy qatorlar (frappe.db.exists("Singles") ishlamaydi!)
	for k, v in raskroy_defaults.items():
		if not s.meta.has_field(k):
			continue
		# 0 — qonuniy qiymat (kerf, zaxira); faqat tabSingles'da hali yozilmagan bo'lsa default beriladi
		if k not in saqlangan:
			s.set(k, v)
			ozgardi = True
	if frappe.db.has_column("Oyna Zakaz", "raskroy_holat"):
		frappe.db.sql("update `tabOyna Zakaz` set raskroy_holat='Yo''q' where ifnull(raskroy_holat,'')=''")
	for k, item in ITEM_XARITA.items():
		if not s.get(k) and frappe.db.exists("Item", item):
			s.set(k, item)
			ozgardi = True
	if not s.get("chiqindi"):
		for t in (4, 6, 10, 0):
			s.append("chiqindi", {"qalinlik_mm": t, "foiz": 15})
		ozgardi = True
	elif not any(cint(r.qalinlik_mm) == 0 for r in s.get("chiqindi")):
		s.append("chiqindi", {"qalinlik_mm": 0, "foiz": 15})
		ozgardi = True
	if s.get("ishlov_turlari") and not any(r.guruh == "Kesim" for r in s.get("ishlov_turlari")):
		for r in ISHLOV_TURLARI_SEED:
			if r["guruh"] == "Kesim":
				s.append("ishlov_turlari", r)
		ozgardi = True
	if not s.get("ishlov_turlari"):
		for r in ISHLOV_TURLARI_SEED:
			s.append("ishlov_turlari", r)
		ozgardi = True
	NARX_JADVALLAR = ("narx_oyna", "narx_qirra", "narx_burchak", "narx_teshik", "narx_kesim", "narx_ishlov")
	if not any(s.get(f) for f in NARX_JADVALLAR):
		for f, rows in _rows_to_tables(_tarif_seed()).items():
			for r in rows:
				if r.get("oyna_item") and not frappe.db.exists("Item", r["oyna_item"]):
					continue
				s.append(f, r)
		ozgardi = True
	if ozgardi:
		s.flags.ignore_permissions = True
		s.save()
		frappe.clear_cache(doctype=SOZLAMA)
	ishlov_optionlarini_yangila(s)


def _ensure_perms():
	if not frappe.db.exists("Role", ROL):
		return
	from frappe.permissions import add_permission, update_permission_property
	huquq = {"Oyna Zakaz": ("read", "write", "create", "submit", "cancel", "amend", "delete", "print",
	                        "email", "export", "report", "share"),
	         "Oyna Narx Jadvali": ("read",)}
	for dt, ptypes in huquq.items():
		if not frappe.db.exists("Custom DocPerm", {"parent": dt, "role": ROL, "permlevel": 0}):
			add_permission(dt, ROL, 0)
		for pt in ptypes:
			update_permission_property(dt, ROL, 0, pt, 1, validate=False)


def sozlama_tekshir():
	"""Diagnostika (bench execute): tarif-qamrov, TAXMINIY, kurs, DocPerm."""
	s = sozlama()
	rows = _tables_to_rows(s)
	out = {"tariflar": len(rows),
	       "ishlov_turlari": [f"{r.guruh}: {r.nom}" for r in s.get("ishlov_turlari") or []],
	       "taxminiy": [f"{r['kategoriya']}/{r.get('oyna_item') or r.get('kalit') or ''}/{r['qalinlik_mm']}" for r in rows if TAXMINIY in (r.get("izoh") or "").upper()],
	       "oynasiz_tarif": [i for i in frappe.get_all("Item", {"item_group": "Oynalar", "disabled": 0}, pluck="name")
	                         if not any(r["kategoriya"] == "Oyna" and r.get("oyna_item") == i for r in rows)],
	       "itemlar": {k: (s.get(k), bool(s.get(k) and frappe.db.exists("Item", s.get(k)))) for k in ITEM_XARITA},
	       "docperm": bool(frappe.db.exists("Custom DocPerm", {"parent": "Oyna Zakaz", "role": ROL})),
	       }
	from akfa_diller.akfa_diller.api import exchange
	out["kurs_bugun"] = exchange.get_company_rate(OYNA_SEX, "USD", "UZS", nowdate())
	print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
	return out
