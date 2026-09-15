# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""«Oyna Zakaz» forma/print endpointlari: jonli preview (SVG + narx + tekshiruv),
kartalar uchun batch-SVG, item-querylar, PDF (taklifnoma / sex-talon)."""

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt

from akfa_diller.akfa_diller.api import oyna_konfigurator as k
from akfa_diller.akfa_diller.services.oyna_chizma import pozitsiya_svg, zakaz_svgs, list_svg

TEMPLATE_PDF = "akfa_diller/templates/oyna_zakaz/zakaz_pdf.html"


def _j(v, default):
	if v in (None, ""):
		return default
	return json.loads(v) if isinstance(v, str) else v


# ------------------------------------------------------------------ querylar

@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def oyna_sex_customers(doctype, txt, searchfield, start, page_len, filters):
	"""«Oyna sex» mijoz-guruhi (va ichki guruhlari) mijozlari."""
	node = frappe.db.get_value("Customer Group", k.OYNA_SEX, ["lft", "rgt"], as_dict=True)
	if not node:
		return []
	return frappe.db.sql("""select c.name, c.customer_name from `tabCustomer` c
		join `tabCustomer Group` cg on cg.name = c.customer_group
		where cg.lft >= %(lft)s and cg.rgt <= %(rgt)s and c.disabled = 0
		  and (c.name like %(t)s or c.customer_name like %(t)s)
		order by c.customer_name, c.name limit %(s)s, %(p)s""",
		{"lft": node.lft, "rgt": node.rgt, "t": f"%{txt}%", "s": start, "p": page_len})


@frappe.whitelist()
def oyna_variantlar():
	frappe.has_permission("Oyna Zakaz", "read", throw=True)
	"""Oyna-itemlar (qalinlik/rang bilan) — dialog uchun."""
	return frappe.get_all("Item", {"item_group": "Oynalar", "disabled": 0},
	                      ["name", "item_name", "custom_oyna_qalinlik_mm as qalinlik", "custom_oyna_rang as rang", "custom_oyna_turi as turi"],
	                      order_by="custom_oyna_qalinlik_mm, name")


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def oyna_items_query(doctype, txt, searchfield, start, page_len, filters):
	return frappe.db.sql("""select name, item_name, concat(ifnull(custom_oyna_qalinlik_mm,''), ' mm ', ifnull(custom_oyna_rang,''))
		from `tabItem` where item_group='Oynalar' and disabled=0 and (name like %(t)s or item_name like %(t)s)
		order by custom_oyna_qalinlik_mm, name limit %(s)s, %(p)s""", {"t": f"%{txt}%", "s": start, "p": page_len})


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def aksessuar_items_query(doctype, txt, searchfield, start, page_len, filters):
	# Aksessuar-tovarlar (ombor) + foydalanuvchi o'zi qo'shgan xizmat-itemlar (Xizmatlar guruhi, omborsiz),
	# konfiguratorning ichki kategoriya-itemlari (Oyna-kesish, Qirra ishlovi…) ko'rsatilmaydi
	ichki = tuple(k.ITEM_XARITA.values())
	return frappe.db.sql("""select name, item_name, stock_uom from `tabItem`
		where disabled=0 and (name like %(t)s or item_name like %(t)s)
		  and ((item_group='Aksessuarlar' and is_stock_item=1) or (item_group='Xizmatlar' and is_stock_item=0 and name not in %(ichki)s))
		order by is_stock_item desc, item_name limit %(s)s, %(p)s""", {"t": f"%{txt}%", "s": start, "p": page_len, "ichki": ichki})


# ------------------------------------------------------------------ preview

POZ_HISOB_MAYDONLAR = ("hisob_eni_mm", "hisob_boyi_mm", "haqiqiy_maydon_m2", "hisob_maydon_m2", "jami_maydon_m2",
                       "perimetr_m", "ogirlik_kg", "teshik_soni", "kesim_soni", "olcham_matn", "xulosa", "tavsif",
                       "ishlov_tartibi", "oyna_tarif", "summa", "taxminiy")


@frappe.whitelist()
def preview_pozitsiya(pozitsiya, teshiklar=None, kesimlar=None, opts=None):
	"""Bitta pozitsiya: SVG + hisob + narx-taqsimot + tekshiruv (saqlamasdan)."""
	frappe.has_permission("Oyna Zakaz", "read", throw=True)
	p = dict(_j(pozitsiya, {}))
	tesh = [dict(x) for x in _j(teshiklar, []) if cint(x.get("diametr_mm"))]
	kes = [dict(x) for x in _j(kesimlar, []) if cint(x.get("eni_mm")) and cint(x.get("boyi_mm"))]
	o = dict(_j(opts, {}))
	p["nr"] = cint(p.get("nr")) or 1
	for r in tesh + kes:
		r["pozitsiya_nr"] = p["nr"]
	if p.get("oyna_item"):
		q, rang, turi = frappe.db.get_value("Item", p["oyna_item"], ["custom_oyna_qalinlik_mm", "custom_oyna_rang", "custom_oyna_turi"]) or (0, None, None)
		p["qalinlik_mm"], p["rang"], p["oyna_turi"] = cint(q), rang, turi or "Oddiy"
	if p.get("shakl") == "Doira" and cint(p.get("diametr_mm")):
		p["eni_mm"] = p["boyi_mm"] = cint(p["diametr_mm"])
	if p.get("matofka_rejimi") == "Qisman" and cint(p.get("matofka_w")) and cint(p.get("matofka_h")) and not flt(p.get("matofka_maydoni_m2")):
		p["matofka_maydoni_m2"] = flt(cint(p["matofka_w"]) * cint(p["matofka_h"]) / 1e6, 4)
	for kk in kes:
		if not cint(kk.get("ichki_radius_mm")):
			kk["ichki_radius_mm"] = cint(p.get("qalinlik_mm"))
	s = k.sozlama()
	out = k._hisobla_core([p], tesh, kes, [], k.Tarif.from_settings(s), k.konst_from(s),
	                      o.get("standart_rejimi") or s.standart_rejimi or "Xato")
	_p, res, tk, kk = out.natija[0]
	svg = pozitsiya_svg(p, tk, kk, {"box_w": cint(o.get("box_w")) or 380, "box_h": cint(o.get("box_h")) or 300,
	                                "prefix": (re.sub(r"[^A-Za-z0-9_-]", "", str(o.get("prefix") or ""))[:20] or "dlg"), "stil": k.ishlov_stillari(s)})
	xatolar = ([{"daraja": "xato", "matn": m} for m in out.xato] + [{"daraja": "std", "matn": m} for m in out.std]
	           + [{"daraja": "ogoh", "matn": m} for m in out.ogoh])
	hisob = {kk_: res.get(kk_) for kk_ in POZ_HISOB_MAYDONLAR}
	for c in k.KATEGORIYALAR:
		hisob[c + "_summa"] = res.get(c + "_summa")
	hisob["qalinlik_mm"], hisob["rang"] = p.get("qalinlik_mm"), p.get("rang")
	hisob["eni_mm"], hisob["boyi_mm"] = p.get("eni_mm"), p.get("boyi_mm")
	hisob["matofka_maydoni_m2"] = p.get("matofka_maydoni_m2")
	return {"svg": svg, "xatolar": xatolar, "hisob": hisob,
	        "narx": {"qatorlar": [list(q) for q in res.get("qatorlar") or []], "summa": res.get("summa")},
	        "teshik_narx": [list(x) for x in res.get("teshik_narx") or []],
	        "kesim_narx": [list(x) for x in res.get("kesim_narx") or []],
	        "ichki_radius": [cint(x.get("ichki_radius_mm")) for x in kk]}


@frappe.whitelist()
def render_zakaz_previews(doc, box_w=320, box_h=240):
	"""Forma-kartalari uchun barcha pozitsiya-SVG'lari (saqlanmagan hujjat ham)."""
	frappe.has_permission("Oyna Zakaz", "read", throw=True)
	d = frappe._dict(_j(doc, {}))
	return [{"nr": nr, "svg": svg} for nr, svg in zakaz_svgs(d, {"box_w": cint(box_w), "box_h": cint(box_h), "stil": k.ishlov_stillari(k.sozlama())})]


# ------------------------------------------------------------------ print / PDF

@frappe.whitelist()
def oxirgi_list_olchamlari(items):
	"""{item: [eni, boyi]} — har oyna uchun oxirgi ishlatilgan list o'lchami (bo'lmasa sozlama-default)."""
	frappe.has_permission("Oyna Zakaz", "read", throw=True)
	items = (json.loads(items) if isinstance(items, str) else (items or []))[:50]
	konst = k.konst_from(k.sozlama())
	return {it: list(k._oxirgi_list_olchami(it, konst)) for it in items if it}


@frappe.whitelist()
def aksessuar_narx(item):
	"""Aksessuar narxi sozlamadagi narx-ro'yxatidan (Item Price) — menejerda Item Price o'qish huquqi shart emas."""
	frappe.has_permission("Oyna Zakaz", "write", throw=True)
	pl = k.sozlama().aksessuar_narx_royxati or k.NARX_ROYXATI
	return flt(frappe.db.get_value("Item Price", {"item_code": item, "price_list": pl}, "price_list_rate"))


# ------------------------------------------------------------------ raskroy (list xaritalari)

def _raskroy_listlar(doc, box_w=560, box_h=390, prefix="rk", for_print=False):
	"""Hujjat rejasidan listlar ro'yxati [{n, N, item, W, H, svg, bolaklar, qoldiqlar, …}] + reja (yoki [], None)."""
	js = doc.get("raskroy_json")
	if not js:
		return [], None
	try:
		reja = json.loads(js) if isinstance(js, str) else js
	except ValueError:
		return [], None
	olcham = {cint(p.nr): (p.olcham_matn or "") for p in doc.get("pozitsiyalar") or []}
	reja_rows = {(r.oyna_item, cint(r.list_nr)): r for r in doc.get("raskroy_reja") or []}
	dona = {cint(p.nr): cint(p.dona) for p in doc.get("pozitsiyalar") or []}
	sheets = reja.get("listlar") or []
	out = []
	for i, sh in enumerate(sheets, 1):
		s_ = dict(sh, oyna_item=sh.get("item"))
		s_["bolaklar"] = [dict(b, olcham_matn=olcham.get(cint(b.get("nr")), ""), dona=dona.get(cint(b.get("nr")), 1))
		                  for b in sh.get("bolaklar") or []]
		rr = reja_rows.get((sh.get("item"), i)) or {}
		svg = list_svg(s_, {"box_w": box_w, "box_h": box_h, "prefix": f"{prefix}{i}", "for_print": for_print,
		                     "legend": for_print, "tartib": True, "n": i, "N": len(sheets), "dona": dona,
		                     "qoldiq_mijozga": cint(rr.get("qoldiq_mijozga"))})
		out.append(frappe._dict(
			n=i, N=len(sheets), item=sh.get("item"), W=cint(sh.get("W")), H=cint(sh.get("H")), svg=svg,
			bolaklar=s_["bolaklar"], qoldiqlar=sh.get("qoldiqlar") or [], chiziqlar=sh.get("chiziqlar") or [],
			bolaklar_m2=flt(sh.get("bolaklar_m2"), 4), qoldiq_m2=flt(sh.get("qoldiq_m2"), 4),
			chiqindi_m2=flt(sh.get("chiqindi_m2"), 4), foydalanish_foiz=flt(sh.get("foydalanish_foiz"), 1),
			butun_list=cint(sh.get("butun_list")),
			mijoz_foiz=flt((reja_rows.get((sh.get("item"), i)) or {}).get("mijoz_foiz")),
			mijoz_jami_foiz=flt((reja_rows.get((sh.get("item"), i)) or {}).get("mijoz_jami_foiz")),
			qoldiq_mijozga=cint((reja_rows.get((sh.get("item"), i)) or {}).get("qoldiq_mijozga")),
			bolaklar_matn=", ".join(sorted({f"№{cint(b.get('nr'))}" for b in sh.get("bolaklar") or []}, key=lambda x: cint(x[1:]))),
			qoldiqlar_matn=", ".join(f"{cint(q['w'])}×{cint(q['h'])} ({flt(q.get('m2')):.2f} m²)" for q in sh.get("qoldiqlar") or [])))
	return out, reja


@frappe.whitelist()
def render_raskroy_svgs(name, box_w=560, box_h=390):
	"""Forma-kartalar uchun list-xaritalari (saqlangan hujjat rejasidan)."""
	doc = frappe.get_doc("Oyna Zakaz", name)
	doc.check_permission("read")
	lst, _reja = _raskroy_listlar(doc, cint(box_w) or 560, cint(box_h) or 390, prefix="rk")
	return [{k_: v for k_, v in x.items() if k_ not in ("bolaklar", "qoldiqlar", "chiziqlar")} for x in lst]


def _raskroy_ctx(doc, variant):
	"""Print uchun raskroy-jamlanma (talon: list-xaritalari ham)."""
	holat = doc.get("raskroy_holat") or "Yo'q"
	oyna_bor = any(p.oyna_item and not cint(p.mijoz_oynasi) for p in doc.get("pozitsiyalar") or [])
	ctx = frappe._dict(holat=holat, bor=holat != "Yo'q", eskirgan=holat == "Eskirgan", oyna_bor=oyna_bor,
	                   list_soni=cint(doc.get("raskroy_list_soni")), foydalanish=flt(doc.get("raskroy_foydalanish_foiz"), 1),
	                   chiqindi_foiz=flt(doc.get("raskroy_chiqindi_foiz"), 1), qoldiq_m2=flt(doc.get("raskroy_qoldiq_m2"), 2),
	                   chiqindi_m2=flt(doc.get("raskroy_chiqindi_m2"), 2), chiqindi_summa=flt(doc.get("raskroy_chiqindi_summa")),
	                   mijoz_foiz=flt(doc.get("raskroy_mijoz_foiz"), 1), listlar=[], listlar_matn="", itemlar_matn="")
	if not ctx.bor:
		return ctx
	guruh = {}
	for r in doc.get("raskroy_reja") or []:
		key = (r.oyna_item, r.olcham)
		guruh[key] = guruh.get(key, 0) + 1
	ctx.listlar_matn = "; ".join(f"{it} {ol} ×{n}" for (it, ol), n in guruh.items())
	ctx.itemlar_matn = "; ".join(
		f"list {cint(r.list_nr)} ({r.oyna_item}): bo'laklar {flt(r.foydalanish_foiz):g} %, chiqindi {flt(r.chiqindi_foiz):g} %, qoldiq {flt(r.qoldiq_foiz):g} % → mijozga {flt(r.mijoz_foiz):g} %"
		+ (" (qoldiq mijozga)" if cint(r.qoldiq_mijozga) else " (qo'lda)" if cint(r.mijoz_foiz_qolda) else "") for r in doc.get("raskroy_reja") or [])
	if variant == "talon":
		ctx.listlar, _reja = _raskroy_listlar(doc, 760, 520, prefix="ls", for_print=True)
	return ctx


def pul(v):
	"""1 208 216 so'm (butun so'm, bo'sh joy bilan guruhlangan)."""
	v = flt(v)
	butun = f"{int(round(abs(v))):,}".replace(",", " ")
	return ("−" if v < 0 else "") + butun + " so'm"


def build_print_ctx(doc, variant="taklif"):
	variant = "talon" if variant == "talon" else "taklif"
	tesh = [r.as_dict() for r in doc.get("teshiklar") or []]
	kes = [r.as_dict() for r in doc.get("kesimlar") or []]
	aks = [r.as_dict() for r in doc.get("aksessuarlar") or []]
	pozitsiyalar = []
	s_ = k.sozlama()
	stil = k.ishlov_stillari(s_)
	kenglikli = {n for n, r in k.konst_from(s_).qirra_turlari.items() if r.kenglik_kerak}
	for p in doc.get("pozitsiyalar") or []:
		nr = cint(p.nr)
		tk = [t for t in tesh if cint(t.get("pozitsiya_nr")) == nr]
		kk = [x for x in kes if cint(x.get("pozitsiya_nr")) == nr]
		dense = (variant == "talon" or len(tk) + len(kk) >= 5 or p.shakl == "Erkin shakl"
		         or (p.matofka_rejimi == "Qisman" and cint(p.matofka_w)))
		box = (480, 340) if dense else (320, 253)
		svg = pozitsiya_svg(p.as_dict(), tk, kk, {"box_w": box[0], "box_h": box[1], "prefix": f"pr{nr}",
		                                            "for_print": True, "legend": dense, "stil": stil})
		qirra = [(belgi, p.get(fn)) for fn, belgi in (("qirra_yuqori", "①"), ("qirra_ong", "②"), ("qirra_pastki", "③"), ("qirra_chap", "④"))
		         if (p.get(fn) or "Yo'q") != "Yo'q"] if p.shakl == "To'rtburchak" else (
			[("kontur", p.qirra_kontur)] if (p.qirra_kontur or "Yo'q") != "Yo'q" else [])
		burchak = [(nom, p.get(fn), cint(p.get(fn + "_mm"))) for fn, nom in k.BURCHAKLAR if (p.get(fn) or "Yo'q") != "Yo'q"]
		narx_qatorlar = []
		for c, lbl in (("oyna", "Oyna"), ("qirra", "Qirra ishlovi"), ("burchak", "Burchak ishlovi"), ("teshik", "Teshiklar"),
		               ("kesim", "Kesimlar"), ("matofka", "Matofka"), ("zakalka", "Zakalka"), ("markirovka", "Markirovka")):
			sm = flt(p.get(c + "_summa"))
			if not sm:
				continue
			if c == "oyna" and flt(p.get("chiqindi_summa")):  # oyna_summa chiqindini o'z ichiga oladi — alohida ko'rsatiladi
				narx_qatorlar.append((lbl, flt(sm - flt(p.chiqindi_summa), 2)))
				narx_qatorlar.append((f"List sarfi (chiqindi) {flt(p.chiqindi_foiz):g} % (raskroy)", flt(p.chiqindi_summa)))
			else:
				narx_qatorlar.append((lbl, sm))
		pozitsiyalar.append(frappe._dict(
			p=p, svg=svg, dense=dense, teshiklar=tk, kesimlar=kk, qirra=qirra, burchak=burchak, kenglikli=kenglikli,
			aksessuarlar=[a for a in aks if cint(a.get("pozitsiya_nr")) == nr], narx_qatorlar=narx_qatorlar,
			checklist=[("Kesish", True), ("Qirra", bool(qirra)), ("Burchak", bool(burchak)),
			           ("Teshik/Kesim", bool(tk or kk)), ("Matofka", (p.matofka_rejimi or "Yo'q") != "Yo'q"),
			           ("Zakalka", bool(cint(p.zakalka))), ("Markirovka", bool(cint(p.markirovka))), ("Qadoqlash", True)]))
	jami = []
	for c, lbl in (("oyna", "Oyna"), ("qirra", "Qirra ishlovi"), ("burchak", "Burchak ishlovi"), ("teshik", "Teshiklar"),
	               ("kesim", "Kesimlar"), ("matofka", "Matofka"), ("zakalka", "Zakalka"), ("markirovka", "Markirovka"), ("aksessuar", "Aksessuarlar")):
		sm = flt(doc.get(c + "_jami"))
		if not sm:
			continue
		if c == "oyna" and flt(doc.get("raskroy_chiqindi_summa")):
			jami.append((lbl, flt(sm - flt(doc.raskroy_chiqindi_summa), 2)))
			jami.append((f"Chiqindi (raskroy) — list sarfi {flt(doc.get('raskroy_mijoz_foiz')):g} %", flt(doc.raskroy_chiqindi_summa)))
		else:
			jami.append((lbl, sm))
	return {"doc": doc, "variant": variant, "pozitsiyalar": pozitsiyalar, "aksessuarlar": aks, "raskroy": _raskroy_ctx(doc, variant),
	        "umumiy_aks": [a for a in aks if not cint(a.get("pozitsiya_nr"))], "jami": jami,
	        "materiallar": [m.as_dict() for m in doc.get("materiallar") or []],
	        "pul": pul, "son": lambda v: f"{flt(v):g}",
	        "sana": frappe.utils.formatdate, "title": "TAKLIFNOMA" if variant == "taklif" else "SEX-TALON"}


def _faqat_data_uri(url, *a, **kw):
	"""WeasyPrint uchun url_fetcher: faqat data: URI (SVG inline, shrift tizimniki) — foydalanuvchi matni orqali
	tashqi/ichki HTTP so'rov (SSRF) qilib bo'lmaydi."""
	if str(url).startswith("data:"):
		from weasyprint import default_url_fetcher
		return default_url_fetcher(url, *a, **kw)
	raise ValueError("tashqi resurs taqiqlangan: " + str(url)[:80])


def _pdf_bytes(html):
	try:
		from weasyprint import HTML  # noqa
		return HTML(string=html, url_fetcher=_faqat_data_uri).write_pdf()
	except ImportError:
		from frappe.utils.pdf import get_pdf
		return get_pdf(html, options={"page-size": "A4", "orientation": "Portrait", "margin-top": "12mm",
		                              "margin-bottom": "14mm", "margin-left": "12mm", "margin-right": "12mm"})


@frappe.whitelist()
def download_pdf(name, variant="taklif"):
	doc = frappe.get_doc("Oyna Zakaz", name)
	doc.check_permission("print")
	if variant != "talon" and doc.get("raskroy_holat") == "Eskirgan":
		frappe.throw(_("Raskroy eskirgan — taklifnomadan oldin «Raskroy tuzish»ni qayta bosing (narx eski reja bilan)"))
	html = frappe.render_template(TEMPLATE_PDF, build_print_ctx(doc, variant))
	frappe.local.response.filename = f"{name}-{'talon' if variant == 'talon' else 'taklifnoma'}.pdf"
	frappe.local.response.filecontent = _pdf_bytes(html)
	frappe.local.response.type = "download"


def pdf_html(name, variant="taklif"):
	"""Sinov/ko'rish uchun HTML (bench execute)."""
	doc = frappe.get_doc("Oyna Zakaz", name)
	return frappe.render_template(TEMPLATE_PDF, build_print_ctx(doc, variant))
