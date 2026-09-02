# -*- coding: utf-8 -*-
# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt
#
# Biznes paneli — 4 kompaniya uchun yagona boshqaruv-dashboard (backend).
#
# Dvigatel `api/oyna_dashboard.py` dan olinadi (davr/delta/money-bag/kesh/GL
# yordamchilari va Oyna sex bo'limlari o'sha yerdan chaqiriladi). Bu modul:
#   • kompaniya-almashtirgich uchun meta (ruxsatdagi kompaniyalar + profil);
#   • profil bo'yicha bo'lim-marshrutlash:
#       trade      (Samarqand Diller, Kattaqo'rg'on) — savdo, filiallar,
#                  kassa, xarajatlar, sinxron-salomatlik, minus-ombor;
#       orders     (Oyna sex) — oyna_dashboard bo'limlari o'zgarishsiz;
#       production (Imzo franshiza) — ishlab-chiqarish va zakazlar.
#
# Ruxsat: har so'rovda kompaniya `report_utils.get_allowed_companies()` bilan
# qayta tekshiriladi — cheklangan foydalanuvchi boshqa kompaniyani so'rasa,
# jimgina o'z kompaniyasiga qaytariladi (xavfsizlik JS'da emas).

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime

from akfa_diller.akfa_diller.api import oyna_dashboard as od
from akfa_diller.akfa_diller.api.report_utils import get_allowed_companies

#: Kompaniya -> dashboard profili. Yangi kompaniya qo'shilsa shu yerga yoziladi.
PROFILLAR = {
	"Samarqand Diller": "trade",
	"Kattaqo'rg'on": "trade",
	"Oyna sex": "orders",
	"Imzo franshiza": "production",
}

#: Profil -> bo'limlar (frontend ham shu ro'yxatga tayanadi, get_meta orqali).
#: STANDART bosh (foydalanuvchi 2026-09-01: "birinchi qatorlari hammasida
#: standart bo'lsin") + kompaniyaga xos dum ENG PASTDA.
STANDART_BOLIMLAR = ("overview", "sales", "tops", "cash", "expenses", "minus_stock")
PROFIL_BOLIMLARI = {
	"trade": STANDART_BOLIMLAR + ("branches",),
	"orders": STANDART_BOLIMLAR + ("orders",),
	"production": STANDART_BOLIMLAR + ("production",),
}

#: Imzo tayyor-mahsulot guruhi (zakaz-importdagi bilan bir xil).
IMZO_TAYYOR_GURUH = "Imzo Tayyor mahsulot"
IMZO_MATERIAL_GURUHLAR = (
	"Imzo Profillar", "Imzo Furnitura", "Imzo Oyna", "Imzo Aksessuarlar",
)


# --------------------------------------------------------------------------
# Kompaniya va kontekst
# --------------------------------------------------------------------------


def _companies():
	"""Foydalanuvchiga ochiq kompaniyalar — PROFILLAR tartibida."""
	mavjud = [c for c in PROFILLAR if frappe.db.exists("Company", c)]
	allowed = get_allowed_companies()
	if allowed:
		mavjud = [c for c in mavjud if c in allowed]
	return mavjud


def _ctx(filters):
	"""oyna_dashboard kontekstiga kompaniya-ruxsatni qo'shib quradi.

	Diqqat: od._resolve_company bo'sh kompaniyada "Oyna sex"ga tushib ketadi —
	shuning uchun kompaniya BU YERDA hal qilinib, filtrga majburan yoziladi.
	"""
	filters = od._parse_filters(filters)
	comps = _companies()
	if not comps:
		frappe.throw(_("Sizga birorta kompaniya ochilmagan. Administratorga murojaat qiling."))
	company = filters.get("company")
	if company not in comps:
		company = comps[0]
	filters["company"] = company
	ctx = od.build_context(filters)
	ctx.profile = PROFILLAR.get(company, "trade")
	# Kesh-kalit uchun: bir xil from/to bilan har xil preset (this_month vs
	# custom) O'TGAN-davr oynasini har xil hisoblaydi — kalitda farqlansin
	ctx.davr_preset = str(filters.get("period") or "")
	return ctx


@frappe.whitelist()
def get_meta():
	comps = _companies()
	return {
		"companies": [
			{
				"name": c,
				"abbr": frappe.get_cached_value("Company", c, "abbr"),
				"profile": PROFILLAR[c],
				"sections": list(PROFIL_BOLIMLARI[PROFILLAR[c]]),
			}
			for c in comps
		],
		"default": comps[0] if comps else None,
		"locked": len(comps) == 1,
	}


@frappe.whitelist()
def get_dashboard(filters=None, sections=None):
	"""Bo'limlarni bitta so'rovda qaytaradi (oyna_dashboard bilan bir xil shakl).

	Bitta bo'limdagi xato butun panelni yiqitmaydi — `error` bilan qaytadi.
	"""
	ctx = _ctx(filters)

	if isinstance(sections, str):
		try:
			sections = json.loads(sections)
		except (ValueError, TypeError):
			sections = [s.strip() for s in sections.split(",") if s.strip()]
	ruxsatli = PROFIL_BOLIMLARI[ctx.profile]
	sections = [s for s in (sections or ruxsatli) if s in ruxsatli]

	result = {"context": dict(od._context_info(ctx), profile=ctx.profile)}
	for section in sections:
		try:
			result[section] = od._cached(
				ctx,
				"bd:{0}:{1}".format(section, ctx.davr_preset),
				lambda s=section: _BUILDERS[s](ctx),
			)
		except Exception:
			frappe.log_error(
				title="Biznes paneli: {0}".format(section),
				message=frappe.get_traceback(),
			)
			result[section] = {
				"permitted": True,
				"error": _("Ushbu bo'limni yuklashda xatolik yuz berdi. Jurnalga yozildi."),
			}
	return result


# --------------------------------------------------------------------------
# TRADE: overview KPI kartalari
# --------------------------------------------------------------------------


def _pe_total(ctx, from_date, to_date, payment_type="Receive"):
	"""Davr ichidagi Payment Entry aylanmasi (kompaniya valyutasida)."""
	row = frappe.db.sql(
		"""
		SELECT SUM(pe.base_paid_amount) AS amount, COUNT(*) AS docs
		FROM `tabPayment Entry` pe
		WHERE pe.company = %(company)s AND pe.docstatus = 1
		  AND pe.payment_type = %(payment_type)s
		  AND pe.posting_date BETWEEN %(from_date)s AND %(to_date)s
		""",
		{
			"company": ctx.company,
			"payment_type": payment_type,
			"from_date": from_date,
			"to_date": to_date,
		},
		as_dict=True,
	)[0]
	return frappe._dict(
		{
			"amount": od._money_add({}, ctx.company_currency, flt(row.amount)),
			"docs": cint(row.docs),
		}
	)


def _gl_period_net(ctx, accounts, from_date, to_date):
	"""Davr ichidagi GL netto aylanma (hisob valyutasida)."""
	return od._gl_balance(ctx, accounts, to_date, from_date=from_date)


def _minus_positions(ctx, to_date):
	"""Minusdagi ombor-pozitsiyalari: soni va qiymati (kompaniya bo'yicha).

	DIQQAT: SUM(actual_qty) EMAS — Stock Reconciliation SLE'lari actual_qty=0
	bilan yozilib, qoldiqni qty_after_transaction orqali o'rnatadi; kumulyativ
	yig'indi ularni ko'rmay, minus-pozitsiyalarni ~5x oshirib ko'rsatgan edi
	(SD 2172 vs real 394). Ombor-kartasi bilan bir xil snapshot-usul.
	"""
	params = {"company": ctx.company, "to_date": to_date}
	snapshot = od._stock_snapshot_sql(frappe._dict(dict(ctx)), params)
	row = frappe.db.sql(
		"""
		SELECT SUM(CASE WHEN t.qty < -0.001 THEN 1 ELSE 0 END) AS positions,
		       SUM(CASE WHEN t.qty < -0.001 AND t.value < 0
		                THEN t.value ELSE 0 END) AS value
		FROM ({snapshot}) t
		""".format(snapshot=snapshot),
		params,
		as_dict=True,
	)[0]
	return frappe._dict(
		{"positions": cint(row.positions), "value": flt(row.value)}
	)


def _trade_overview(ctx):
	cards = []
	valyuta = ctx.company_currency

	# 1. Savdo (SI, hujjat valyutasida) + delta
	now_rev = od._revenue(ctx, ctx.from_date, ctx.to_date)
	prev_rev = od._revenue(ctx, ctx.prev_from, ctx.prev_to)
	invoices = od._invoice_stats(ctx, ctx.from_date, ctx.to_date)
	cards.append({
		"key": "sales",
		"icon": "sell",
		"label": _("Savdo"),
		"value": od._money_out(now_rev.net),
		"format": "currency",
		"tone": "primary",
		"section": "sales",
		"delta": od._delta(now_rev.net, prev_rev.net),
		"details": [
			{"label": _("hujjat"), "value": invoices.invoices, "format": "int"},
			{"label": _("mijoz"), "value": invoices.customers, "format": "int"},
		],
		"route": ["List", "Sales Invoice"],
		"route_options": {"company": ctx.company, "docstatus": 1},
	})

	# 2. Tushum (Payment Entry: Receive)
	now_pe = _pe_total(ctx, ctx.from_date, ctx.to_date)
	prev_pe = _pe_total(ctx, ctx.prev_from, ctx.prev_to)
	cards.append({
		"key": "inflow",
		"icon": "arrow-up-right",
		"label": _("Tushum (to'lovlar)"),
		"value": od._money_out(now_pe.amount),
		"format": "currency",
		"tone": "info",
		"delta": od._delta(now_pe.amount, prev_pe.amount),
		"details": [{"label": _("to'lov"), "value": now_pe.docs, "format": "int"}],
		"route": ["List", "Payment Entry"],
		"route_options": {"company": ctx.company, "payment_type": "Receive", "docstatus": 1},
	})

	# 3. Yalpi natija (GL: daromad − xarajat, kompaniya valyutasida)
	income_accounts = od.get_income_accounts(ctx.company)  # nomlar ro'yxati
	expense_accounts = [a.name for a in od.get_expense_accounts(ctx.company, include_cogs=True)]
	now_in = _gl_period_net(ctx, income_accounts, ctx.from_date, ctx.to_date)
	now_out = _gl_period_net(ctx, expense_accounts, ctx.from_date, ctx.to_date)
	prev_in = _gl_period_net(ctx, income_accounts, ctx.prev_from, ctx.prev_to)
	prev_out = _gl_period_net(ctx, expense_accounts, ctx.prev_from, ctx.prev_to)
	# GL'da daromad kredit (manfiy chiqadi) — belgini o'girib olamiz.
	now_profit = od._money_diff(od._money_neg(now_in), now_out)
	prev_profit = od._money_diff(od._money_neg(prev_in), prev_out)
	margin = od._margin(now_profit, od._money_neg(now_in), valyuta)
	cards.append({
		"key": "profit",
		"icon": "chart",
		"label": _("Sof natija (GL)"),
		"value": od._money_out(now_profit),
		"format": "currency",
		"tone": "success" if all(v >= 0 for v in now_profit.values()) else "danger",
		"delta": od._delta(now_profit, prev_profit),
		"details": (
			[{"label": _("marja"), "value": margin, "format": "percent"}]
			if margin is not None else []
		),
	})

	# 4. Ombor qiymati (qoldiq)
	stock = od._stock_totals(ctx, ctx.to_date)
	cards.append({
		"key": "stock",
		"icon": "stock",
		"label": _("Ombor qiymati"),
		"value": od._money_out(stock.value),
		"format": "currency",
		"tone": "violet",
		"as_of": True,
		"section": "branches",
		"details": [{"label": _("pozitsiya"), "value": stock["items"], "format": "int"}],
	})

	# 5. Kassa jami (qoldiq)
	cash_accounts = [a.name for a in od.get_cash_accounts(ctx.company)]
	cash_bag = od._gl_balance(ctx, cash_accounts, ctx.to_date)
	cards.append({
		"key": "cash",
		"icon": "money-coins-1",
		"label": _("Kassa jami"),
		"value": od._money_out(cash_bag),
		"format": "currency",
		"tone": "success",
		"as_of": True,
		"section": "cash",
		"details": [{"label": _("kassa"), "value": len(cash_accounts), "format": "int"}],
	})

	# 6. Mijozlar balansi (Receivable): manfiy = mijoz avanslari (normal holat)
	recv_accounts = od.get_receivable_accounts(ctx.company)  # nomlar ro'yxati
	recv_bag = od._gl_balance(ctx, recv_accounts, ctx.to_date)
	avans = all(v <= 0 for v in recv_bag.values()) and any(recv_bag.values())
	cards.append({
		"key": "receivable",
		"icon": "customer",
		"label": _("Mijoz avanslari") if avans else _("Mijozlar balansi"),
		"value": od._money_out(od._money_neg(recv_bag) if avans else recv_bag),
		"format": "currency",
		"tone": "info" if avans else "warning",
		"as_of": True,
		"details": (
			[{"label": _("oldindan to'langan"), "value": None, "format": "note"}]
			if avans else []
		),
	})

	# 7. Minus-ombor (nazorat kartasi)
	minus = _minus_positions(ctx, ctx.to_date)
	cards.append({
		"key": "minus",
		"icon": "solid-warning",
		"label": _("Minusdagi pozitsiyalar"),
		"value": minus.positions,
		"format": "int",
		"tone": "danger" if minus.positions else "success",
		"as_of": True,
		"section": "minus_stock",
		"details": [
			{"label": valyuta, "value": minus.value, "format": "int"},
		],
	})

	# details ichidagi format="note" qatorlarini olib tashlaymiz (faqat yorliq)
	for card in cards:
		card["details"] = [
			d for d in card.get("details", []) if d.get("format") != "note"
		]
	return {"permitted": True, "cards": cards}


# --------------------------------------------------------------------------
# KUNLIK PANEL: oy-kalendar heatmap (dashboards-ilovasidagi "daily_dashboard"
# andozasi; farqlar: kompaniya-ruxsat, pul (kg emas), o'zbekcha, bizning ranglar)
# --------------------------------------------------------------------------


@frappe.whitelist()
def get_daily(filters=None):
	"""KUNLIK PANEL: oy-kalendar + mijoz-chiplar + tovarlar (tannarx/marja) + KPI.

	filters: company, yil, oy, mijoz (Customer, ixtiyoriy), kun (1..31, ixtiyoriy).
	- kalendar va mijoz-chiplar DOIM butun oy bo'yicha (mijoz filtri kalendarga
	  ham qo'llanadi — tanlangan mijozning kunlik ritmi ko'rinadi);
	- tovarlar-jadvali va KPI esa tanlangan qamrovda: oy (+mijoz) yoki bitta kun.
	Tannarx — SI'larning ombor-yozuvlaridan (SLE stock_value_difference).
	"""
	import calendar as _cal

	filters = od._parse_filters(filters)
	ctx = _ctx(dict(filters))  # kompaniya-ruxsat shu yerda majburlanadi

	yil = cint(filters.get("yil"))
	oy = cint(filters.get("oy"))
	mijoz = (filters.get("mijoz") or "").strip() or None
	kun = cint(filters.get("kun")) or None

	if not (yil and 1 <= oy <= 12):
		oxirgi = frappe.db.sql(
			"""SELECT MAX(posting_date) FROM `tabSales Invoice`
			   WHERE company = %s AND docstatus = 1""",
			(ctx.company,),
		)[0][0]
		asos = getdate(oxirgi) if oxirgi else getdate()
		yil, oy = asos.year, asos.month

	kunlar_soni = _cal.monthrange(yil, oy)[1]
	if kun and not (1 <= kun <= kunlar_soni):
		kun = None
	oy_boshi = "{0}-{1:02d}-01".format(yil, oy)
	oy_oxiri = "{0}-{1:02d}-{2:02d}".format(yil, oy, kunlar_soni)

	def hisobla():
		oy_params = {"company": ctx.company, "from_date": oy_boshi, "to_date": oy_oxiri}
		mijoz_sharti = ""
		if mijoz:
			oy_params["mijoz"] = mijoz
			mijoz_sharti = " AND si.customer = %(mijoz)s"

		# --- kalendar (butun oy, mijoz filtri bilan) ---
		kunlar = {}
		for r in frappe.db.sql(
			"""
			SELECT DAY(si.posting_date) AS kun,
			       SUM(si.base_grand_total) AS summa, COUNT(*) AS docs
			FROM `tabSales Invoice` si
			WHERE si.company = %(company)s AND si.docstatus = 1
			  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s{mijoz}
			GROUP BY DAY(si.posting_date)
			""".format(mijoz=mijoz_sharti),
			oy_params, as_dict=True,
		):
			if r.kun:
				kunlar[cint(r.kun)] = {"summa": flt(r.summa), "docs": cint(r.docs)}

		# --- mijoz-chiplar (oyning top-24 mijozi, filtrsiz) ---
		mijozlar = frappe.db.sql(
			"""
			SELECT si.customer, si.customer_name, SUM(si.base_grand_total) AS savdo
			FROM `tabSales Invoice` si
			WHERE si.company = %(company)s AND si.docstatus = 1
			  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			GROUP BY si.customer ORDER BY savdo DESC LIMIT 24
			""",
			{"company": ctx.company, "from_date": oy_boshi, "to_date": oy_oxiri},
			as_dict=True,
		)

		# --- qamrov (jadval + KPI): oy(+mijoz) yoki bitta kun ---
		q_from, q_to = oy_boshi, oy_oxiri
		if kun:
			q_from = q_to = "{0}-{1:02d}-{2:02d}".format(yil, oy, kun)
		q_params = dict(oy_params, from_date=q_from, to_date=q_to)

		# tovar kesimi: savdo SI-itemlardan, tannarx SLE'dan (alohida, keyin birlashadi)
		savdo_t = frappe.db.sql(
			"""
			SELECT sii.item_code, SUM(sii.qty) AS qty, SUM(sii.base_net_amount) AS summa
			FROM `tabSales Invoice` si
			JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
			WHERE si.company = %(company)s AND si.docstatus = 1
			  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s{mijoz}
			GROUP BY sii.item_code
			""".format(mijoz=mijoz_sharti),
			q_params, as_dict=True,
		)
		tannarx_t = {}
		for r in frappe.db.sql(
			"""
			SELECT sle.item_code, SUM(-sle.stock_value_difference) AS tannarx
			FROM `tabStock Ledger Entry` sle
			WHERE sle.is_cancelled = 0 AND sle.voucher_type = 'Sales Invoice'
			  AND sle.voucher_no IN (
				SELECT si.name FROM `tabSales Invoice` si
				WHERE si.company = %(company)s AND si.docstatus = 1
				  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s{mijoz}
			  )
			GROUP BY sle.item_code
			""".format(mijoz=mijoz_sharti),
			q_params, as_dict=True,
		):
			tannarx_t[r.item_code] = flt(r.tannarx)

		tovarlar = []
		for r in sorted(savdo_t, key=lambda x: -flt(x.summa)):
			t = tannarx_t.get(r.item_code, 0.0)
			marja = flt(r.summa) - t
			tovarlar.append({
				"item": r.item_code,
				"qty": flt(r.qty),
				"summa": flt(r.summa),
				"tannarx": t,
				"marja": marja,
				"marja_pct": round(marja / flt(r.summa) * 100, 1) if flt(r.summa) else None,
			})

		# --- KPI (qamrov bo'yicha) ---
		bosh = frappe.db.sql(
			"""
			SELECT SUM(si.base_grand_total) AS savdo, COUNT(*) AS docs,
			       COUNT(DISTINCT si.customer) AS mijozlar
			FROM `tabSales Invoice` si
			WHERE si.company = %(company)s AND si.docstatus = 1
			  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s{mijoz}
			""".format(mijoz=mijoz_sharti),
			q_params, as_dict=True,
		)[0]
		savdo = flt(bosh.savdo)
		tannarx = sum(t["tannarx"] for t in tovarlar)
		marja = savdo - tannarx

		yillar = [cint(r[0]) for r in frappe.db.sql(
			"""SELECT DISTINCT YEAR(posting_date) FROM `tabSales Invoice`
			   WHERE company = %s AND docstatus = 1 ORDER BY 1""",
			(ctx.company,),
		) if r[0]]

		eng_kun, eng = None, 0.0
		for k, v in kunlar.items():
			if v["summa"] > eng:
				eng, eng_kun = v["summa"], k

		return {
			"permitted": True,
			"currency": ctx.company_currency,
			"yil": yil, "oy": oy, "kun": kun, "mijoz": mijoz,
			"kunlar_soni": kunlar_soni,
			"kunlar": kunlar,
			"mijozlar": [
				{"customer": m.customer, "name": m.customer_name or m.customer,
				 "savdo": flt(m.savdo)}
				for m in mijozlar
			],
			"tovarlar": tovarlar[:60],
			# JAMI qatori TO'LIQ qamrovdan (jadval 60 qator bilan kesilgan;
			# ilgari frontend ko'rinayotgan 60 tани "JAMI" deb yig'ib,
			# oy jamlamasidan ~37% kam ko'rsatgan edi)
			"tovarlar_jami": {
				"soni": len(tovarlar),
				"qty": sum(t["qty"] for t in tovarlar),
				"summa": sum(t["summa"] for t in tovarlar),
				"tannarx": tannarx,
			},
			"yillar": yillar or [yil],
			"kpi": {
				"savdo": savdo,
				"tannarx": tannarx,
				"marja": marja,
				"marja_pct": round(marja / savdo * 100, 1) if savdo else None,
				"docs": cint(bosh.docs),
				"mijozlar": cint(bosh.mijozlar),
				"ortacha_chek": savdo / cint(bosh.docs) if cint(bosh.docs) else 0,
				"eng_kun": eng_kun,
				"eng_summa": eng,
			},
		}

	kesh_ctx = frappe._dict(dict(
		ctx, from_date=oy_boshi, to_date=oy_oxiri,
		customer=mijoz or "", cash_account=str(kun or ""),
	))
	return od._cached(kesh_ctx, "bd:daily2", hisobla)


# --------------------------------------------------------------------------
# SAVDO TAHLILI PANELI: yil/oy(lar) kesimida tovar va mijoz marja-tahlili
# (dashboards-ilovasidagi "Дашборд" sahifasi mantiqidan)
# --------------------------------------------------------------------------


@frappe.whitelist()
def get_tahlil(filters=None):
	"""filters: company, yil, oylar (ro'yxat, bo'sh = butun yil).

	Qaytadi: KPI (savdo/vozvrat/tannarx/marja/dona/chek) + tovarlar-jadvali +
	mijozlar-jadvali (har birida tannarx va marja, JAMI bilan). Tannarx —
	SI'larning ombor-yozuvlaridan (SLE), mijoz kesimida ham xuddi shundan.
	"""
	filters = od._parse_filters(filters)
	ctx = _ctx(dict(filters))

	yil = cint(filters.get("yil"))
	if not yil:
		oxirgi = frappe.db.sql(
			"""SELECT MAX(posting_date) FROM `tabSales Invoice`
			   WHERE company = %s AND docstatus = 1""", (ctx.company,))[0][0]
		yil = (getdate(oxirgi) if oxirgi else getdate()).year

	oylar = filters.get("oylar") or []
	if isinstance(oylar, str):
		try:
			oylar = json.loads(oylar)
		except (ValueError, TypeError):
			oylar = []
	oylar = sorted({cint(o) for o in oylar if 1 <= cint(o) <= 12})

	oy_sharti = ""
	params = {"company": ctx.company, "yil": yil}
	if oylar:
		oy_sharti = " AND MONTH(si.posting_date) IN %(oylar)s"
		params["oylar"] = tuple(oylar)

	SI_SCOPE = """FROM `tabSales Invoice` si
			WHERE si.company = %(company)s AND si.docstatus = 1
			  AND YEAR(si.posting_date) = %(yil)s{oy}""".format(oy=oy_sharti)

	def hisobla():
		# --- KPI ---
		bosh = frappe.db.sql(
			"""SELECT SUM(si.base_grand_total) AS savdo,
			          SUM(CASE WHEN si.is_return = 1 THEN -si.base_grand_total ELSE 0 END) AS vozvrat,
			          COUNT(*) AS docs, COUNT(DISTINCT si.customer) AS mijozlar
			""" + SI_SCOPE, params, as_dict=True)[0]

		dona = flt(frappe.db.sql(
			"""SELECT SUM(sii.qty) FROM `tabSales Invoice` si
			   JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
			   WHERE si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) = %(yil)s{oy}""".format(oy=oy_sharti),
			params)[0][0])

		# --- tovarlar (savdo + tannarx) ---
		savdo_t = frappe.db.sql(
			"""SELECT sii.item_code, SUM(sii.qty) AS qty,
			          SUM(sii.base_net_amount) AS summa
			   FROM `tabSales Invoice` si
			   JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
			   WHERE si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) = %(yil)s{oy}
			   GROUP BY sii.item_code ORDER BY summa DESC LIMIT 100
			""".format(oy=oy_sharti), params, as_dict=True)

		tannarx_t = {}
		for r in frappe.db.sql(
			"""SELECT sle.item_code, SUM(-sle.stock_value_difference) AS t
			   FROM `tabStock Ledger Entry` sle
			   JOIN `tabSales Invoice` si ON si.name = sle.voucher_no
			   WHERE sle.is_cancelled = 0 AND sle.voucher_type = 'Sales Invoice'
			     AND si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) = %(yil)s{oy}
			   GROUP BY sle.item_code""".format(oy=oy_sharti),
			params, as_dict=True):
			tannarx_t[r.item_code] = flt(r.t)

		tovarlar = []
		for r in savdo_t:
			t = tannarx_t.get(r.item_code, 0.0)
			marja = flt(r.summa) - t
			tovarlar.append({
				"item": r.item_code, "qty": flt(r.qty), "summa": flt(r.summa),
				"tannarx": t, "marja": marja,
				"marja_pct": round(marja / flt(r.summa) * 100, 1) if flt(r.summa) else None,
			})

		# --- mijozlar (savdo + tannarx mijoz kesimida) ---
		savdo_m = frappe.db.sql(
			"""SELECT si.customer, si.customer_name,
			          SUM(si.base_grand_total) AS summa, COUNT(*) AS docs
			""" + SI_SCOPE + " GROUP BY si.customer ORDER BY summa DESC LIMIT 100",
			params, as_dict=True)

		tannarx_m = {}
		for r in frappe.db.sql(
			"""SELECT si.customer, SUM(-sle.stock_value_difference) AS t
			   FROM `tabStock Ledger Entry` sle
			   JOIN `tabSales Invoice` si ON si.name = sle.voucher_no
			   WHERE sle.is_cancelled = 0 AND sle.voucher_type = 'Sales Invoice'
			     AND si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) = %(yil)s{oy}
			   GROUP BY si.customer""".format(oy=oy_sharti),
			params, as_dict=True):
			tannarx_m[r.customer] = flt(r.t)

		mijozlar = []
		for r in savdo_m:
			t = tannarx_m.get(r.customer, 0.0)
			marja = flt(r.summa) - t
			mijozlar.append({
				"customer": r.customer, "name": r.customer_name or r.customer,
				"summa": flt(r.summa), "tannarx": t, "docs": cint(r.docs),
				"marja": marja,
				"marja_pct": round(marja / flt(r.summa) * 100, 1) if flt(r.summa) else None,
			})

		savdo = flt(bosh.savdo)
		# KPI-tannarx TO'LIQ qamrovdan (tovarlar-jadvali top-100 bilan cheklangan,
		# undan yig'ish marjani sun'iy oshirib yuborardi)
		tannarx = flt(frappe.db.sql(
			"""SELECT SUM(-sle.stock_value_difference)
			   FROM `tabStock Ledger Entry` sle
			   JOIN `tabSales Invoice` si ON si.name = sle.voucher_no
			   WHERE sle.is_cancelled = 0 AND sle.voucher_type = 'Sales Invoice'
			     AND si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) = %(yil)s{oy}""".format(oy=oy_sharti),
			params)[0][0])
		marja = savdo - tannarx

		yillar = [cint(r[0]) for r in frappe.db.sql(
			"""SELECT DISTINCT YEAR(posting_date) FROM `tabSales Invoice`
			   WHERE company = %s AND docstatus = 1 ORDER BY 1""",
			(ctx.company,)) if r[0]]

		# Oylik dinamika — so'nggi 3 yil (grafikda yillar alohida qatlam bo'ladi;
		# hozircha bitta yil bo'lsa bitta qatlam chiqadi)
		tanlangan3 = [y for y in (yillar or [yil]) if y <= yil][-3:] or [yil]
		oylik_yillar = {y: [0.0] * 12 for y in tanlangan3}
		for r in frappe.db.sql(
			"""SELECT YEAR(si.posting_date) AS y, MONTH(si.posting_date) AS oy,
			          SUM(si.base_grand_total) AS savdo
			   FROM `tabSales Invoice` si
			   WHERE si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) IN %(yillar3)s
			   GROUP BY YEAR(si.posting_date), MONTH(si.posting_date)""",
			{"company": ctx.company, "yillar3": tuple(tanlangan3)}, as_dict=True):
			if r.y in oylik_yillar and r.oy:
				oylik_yillar[cint(r.y)][cint(r.oy) - 1] = flt(r.savdo)

		# Oylik DONA qatlamlari (grafikning "Dona" rejimi uchun) — vozvratlar
		# manfiy qty bilan o'z-o'zidan ayirilib ketadi (savdo-seriya bilan izchil)
		dona_yillar = {y: [0.0] * 12 for y in tanlangan3}
		for r in frappe.db.sql(
			"""SELECT YEAR(si.posting_date) AS y, MONTH(si.posting_date) AS oy,
			          SUM(sii.qty) AS dona
			   FROM `tabSales Invoice` si
			   JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
			   WHERE si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) IN %(yillar3)s
			   GROUP BY YEAR(si.posting_date), MONTH(si.posting_date)""",
			{"company": ctx.company, "yillar3": tuple(tanlangan3)}, as_dict=True):
			if r.y in dona_yillar and r.oy:
				dona_yillar[cint(r.y)][cint(r.oy) - 1] = flt(r.dona)

		# Oylik marja va rentabellik (tanlangan yil, filtrga qaramay butun yil):
		# tannarx oyma-oy SLE'dan, savdo oylik_yillar'dagi shu yil qatoridan
		tannarx_oy = {}
		for r in frappe.db.sql(
			"""SELECT MONTH(si.posting_date) AS oy,
			          SUM(-sle.stock_value_difference) AS t
			   FROM `tabStock Ledger Entry` sle
			   JOIN `tabSales Invoice` si ON si.name = sle.voucher_no
			   WHERE sle.is_cancelled = 0 AND sle.voucher_type = 'Sales Invoice'
			     AND si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) = %(yil)s
			   GROUP BY MONTH(si.posting_date)""",
			{"company": ctx.company, "yil": yil}, as_dict=True):
			if r.oy:
				tannarx_oy[cint(r.oy)] = flt(r.t)

		joriy_savdo_oylik = oylik_yillar.get(yil) or [0.0] * 12
		marja_oylik = []
		for m in range(1, 13):
			s_oy = flt(joriy_savdo_oylik[m - 1])
			t_oy = tannarx_oy.get(m, 0.0)
			m_oy = s_oy - t_oy
			marja_oylik.append({
				"oy": m, "marja": m_oy,
				"pct": round(m_oy / s_oy * 100, 1) if s_oy else None,
			})

		# Vozvratlar oyma-oy (tanlangan yil bo'yicha 12 qator)
		vozvrat_oylik = {m: {"oy": m, "dona": 0.0, "summa": 0.0} for m in range(1, 13)}
		for r in frappe.db.sql(
			"""SELECT MONTH(posting_date) AS oy,
			          SUM(ABS(COALESCE(total_qty, 0))) AS dona,
			          SUM(ABS(base_grand_total)) AS summa
			   FROM `tabSales Invoice`
			   WHERE company = %(company)s AND docstatus = 1 AND is_return = 1
			     AND YEAR(posting_date) = %(yil)s
			   GROUP BY MONTH(posting_date)""",
			{"company": ctx.company, "yil": yil}, as_dict=True):
			if r.oy:
				vozvrat_oylik[cint(r.oy)] = {
					"oy": cint(r.oy), "dona": flt(r.dona), "summa": flt(r.summa)}

		# Balans detallari (bugungi holat): naqd / plastik / mijoz balans / ombor.
		# Kassa-schyotlar Umumiy paneldagi Kassa kartasi bilan BIR manbadan
		# (Mode of Payment Account), shunda naqd+plastik = o'sha karta aynan.
		bugun = str(getdate())
		kassa_sch = od.get_cash_accounts(ctx.company)
		naqd = od._gl_balance(
			ctx, [a.name for a in kassa_sch if a.get("kind") != "bank"], bugun)
		bank = od._gl_balance(
			ctx, [a.name for a in kassa_sch if a.get("kind") == "bank"], bugun)
		mijoz_bal = od._gl_balance(ctx, od.get_receivable_accounts(ctx.company), bugun)
		kreditor = od._gl_balance(ctx, od.get_payable_accounts(ctx.company), bugun)
		ombor = od._stock_totals(frappe._dict(dict(ctx)), bugun)

		# UMUMIY BALANS trendi (andoza: Kassa + Debitor − Kreditor + Ombor,
		# oy oxirlarida). GL debit/credit KOMPANIYA valyutasida — trend bir
		# valyutada chiqadi; qator-detallar esa schyot-valyutasida qoladi.
		gl_schyotlar = tuple(
			[a.name for a in kassa_sch]
			+ od.get_receivable_accounts(ctx.company)
			+ od.get_payable_accounts(ctx.company))
		trend_p = {"company": ctx.company, "yil": yil,
		           "boshi": "{0}-01-01".format(yil), "schyotlar": gl_schyotlar}
		gl_ochilish = flt(frappe.db.sql(
			"""SELECT SUM(debit - credit) FROM `tabGL Entry`
			   WHERE company = %(company)s AND is_cancelled = 0
			     AND account IN %(schyotlar)s
			     AND posting_date < %(boshi)s""", trend_p)[0][0]) if gl_schyotlar else 0.0
		gl_oy = {}
		if gl_schyotlar:
			for r in frappe.db.sql(
				"""SELECT MONTH(posting_date) AS oy, SUM(debit - credit) AS f
				   FROM `tabGL Entry`
				   WHERE company = %(company)s AND is_cancelled = 0
				     AND account IN %(schyotlar)s
				     AND YEAR(posting_date) = %(yil)s
				   GROUP BY MONTH(posting_date)""", trend_p, as_dict=True):
				if r.oy:
					gl_oy[cint(r.oy)] = flt(r.f)
		stok_ochilish = flt(frappe.db.sql(
			"""SELECT SUM(sle.stock_value_difference)
			   FROM `tabStock Ledger Entry` sle
			   JOIN `tabWarehouse` w ON w.name = sle.warehouse
			   WHERE w.company = %(company)s AND sle.is_cancelled = 0
			     AND sle.posting_date < %(boshi)s""", trend_p)[0][0])
		stok_oy = {}
		for r in frappe.db.sql(
			"""SELECT MONTH(sle.posting_date) AS oy,
			          SUM(sle.stock_value_difference) AS f
			   FROM `tabStock Ledger Entry` sle
			   JOIN `tabWarehouse` w ON w.name = sle.warehouse
			   WHERE w.company = %(company)s AND sle.is_cancelled = 0
			     AND YEAR(sle.posting_date) = %(yil)s
			   GROUP BY MONTH(sle.posting_date)""", trend_p, as_dict=True):
			if r.oy:
				stok_oy[cint(r.oy)] = flt(r.f)

		bugun_d = getdate()
		oxirgi_oy = bugun_d.month if bugun_d.year == yil else (
			12 if yil < bugun_d.year else 0)
		yigindi = gl_ochilish + stok_ochilish
		balans_trend = []
		for m in range(1, 13):
			if m > oxirgi_oy:
				balans_trend.append({"oy": m, "value": None, "pct": None})
				continue
			oldingi = yigindi
			yigindi += gl_oy.get(m, 0.0) + stok_oy.get(m, 0.0)
			balans_trend.append({
				"oy": m, "value": yigindi,
				"pct": round((yigindi - oldingi) / abs(oldingi) * 100, 1)
				if abs(oldingi) > 0.005 else None,
			})
		jami_balans = yigindi if oxirgi_oy else None

		return {
			"permitted": True,
			"currency": ctx.company_currency,
			"yil": yil, "oylar": list(oylar), "yillar": yillar or [yil],
			"oylik_yillar": [
				{"yil": y, "values": oylik_yillar[y]} for y in tanlangan3
			],
			"dona_yillar": [
				{"yil": y, "values": dona_yillar[y]} for y in tanlangan3
			],
			"marja_oylik": marja_oylik,
			"vozvrat_oylik": [vozvrat_oylik[m] for m in range(1, 13)],
			"balans": {
				"naqd": od._money_out(naqd),
				"bank": od._money_out(bank),
				# Umumiy paneldagi kabi: balans manfiy bo'lsa bu mijoz avanslari —
				# musbat qilib, yorlig'i bilan beriladi.
				"mijoz": od._money_out(
					{c: -v for c, v in mijoz_bal.items()}
					if mijoz_bal and all(flt(v) <= 0.005 for v in mijoz_bal.values())
					else mijoz_bal),
				"mijoz_label": (
					"Mijoz avanslari"
					if mijoz_bal and all(flt(v) <= 0.005 for v in mijoz_bal.values())
					else "Mijozlar balansi"),
				"kreditor": od._money_out({c: abs(v) for c, v in kreditor.items()}),
				"ombor": od._money_out(ombor.value),
				"jami": jami_balans,
				"trend": balans_trend,
				"sana": bugun,
			},
			"kpi": {
				"savdo": savdo,
				"vozvrat": flt(bosh.vozvrat),
				"tannarx": tannarx,
				"marja": marja,
				"marja_pct": round(marja / savdo * 100, 1) if savdo else None,
				"dona": dona,
				"docs": cint(bosh.docs),
				"mijozlar": cint(bosh.mijozlar),
				"ortacha_chek": savdo / cint(bosh.docs) if cint(bosh.docs) else 0,
			},
			"tovarlar": tovarlar,
			"mijozlar": mijozlar,
		}

	kesh_ctx = frappe._dict(dict(
		ctx, from_date="{0}-01-01".format(yil), to_date="{0}-12-31".format(yil),
		customer=",".join(str(o) for o in oylar),
	))
	return od._cached(kesh_ctx, "bd:tahlil", hisobla)


# --------------------------------------------------------------------------
# STANDART: Top mijozlar / Top tovarlar
# --------------------------------------------------------------------------


def _tops(ctx):
	"""Davr bo'yicha eng katta 20 mijoz va 20 tovar (kompaniya valyutasida).

	Savdo — tasdiqlangan Sales Invoice (vozvratlar ayirilgan holda, chunki
	ularning summasi manfiy); to'lov — Payment Entry (Receive, mijoz kesimida).
	Ulush % — davrdagi jami savdoga nisbatan.
	"""
	params = {"company": ctx.company, "from_date": ctx.from_date,
	          "to_date": ctx.to_date}

	mijozlar = frappe.db.sql(
		"""
		SELECT si.customer, si.customer_name,
		       SUM(si.base_grand_total) AS savdo,
		       COUNT(*) AS docs
		FROM `tabSales Invoice` si
		WHERE si.company = %(company)s AND si.docstatus = 1
		  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY si.customer
		ORDER BY savdo DESC
		LIMIT 20
		""", params, as_dict=True)

	tolovlar = {}
	if mijozlar:
		for r in frappe.db.sql(
			"""
			SELECT pe.party, SUM(pe.base_paid_amount) AS amount
			FROM `tabPayment Entry` pe
			WHERE pe.company = %(company)s AND pe.docstatus = 1
			  AND pe.payment_type = 'Receive' AND pe.party_type = 'Customer'
			  AND pe.posting_date BETWEEN %(from_date)s AND %(to_date)s
			  AND pe.party IN %(mijozlar)s
			GROUP BY pe.party
			""", dict(params, mijozlar=tuple(m.customer for m in mijozlar)),
			as_dict=True):
			tolovlar[r.party] = flt(r.amount)

	jami_savdo = flt(frappe.db.sql(
		"""
		SELECT SUM(si.base_grand_total) FROM `tabSales Invoice` si
		WHERE si.company = %(company)s AND si.docstatus = 1
		  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
		""", params)[0][0])

	tovarlar = frappe.db.sql(
		"""
		SELECT sii.item_code, SUM(sii.qty) AS qty,
		       SUM(sii.base_net_amount) AS summa,
		       COUNT(DISTINCT si.customer) AS mijozlar
		FROM `tabSales Invoice` si
		JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE si.company = %(company)s AND si.docstatus = 1
		  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY sii.item_code
		ORDER BY summa DESC
		LIMIT 20
		""", params, as_dict=True)

	def ulush(qiymat):
		return round(qiymat / jami_savdo * 100, 1) if jami_savdo else None

	return {
		"permitted": True,
		"currency": ctx.company_currency,
		"customers": [
			{
				"customer": m.customer,
				"name": m.customer_name or m.customer,
				"savdo": flt(m.savdo),
				"tolov": tolovlar.get(m.customer, 0.0),
				"docs": cint(m.docs),
				"ulush": ulush(flt(m.savdo)),
			}
			for m in mijozlar
		],
		"items": [
			{
				"item": t.item_code,
				"qty": flt(t.qty),
				"summa": flt(t.summa),
				"mijozlar": cint(t.mijozlar),
				"ulush": ulush(flt(t.summa)),
			}
			for t in tovarlar
		],
	}


# --------------------------------------------------------------------------
# TRADE: filiallar jadvali
# --------------------------------------------------------------------------


def _dealer_branches(company):
	return frappe.get_all(
		"Report Service Dealer Branch",
		filters={"company": company},
		fields=["dealer_id", "label", "is_main", "warehouse", "cost_center"],
		order_by="is_main desc, label",
	)


def _kassa_accounts_by_dealer():
	"""Report Service Kassa Account: dealer_id -> [account, ...]"""
	xarita = {}
	for r in frappe.get_all("Report Service Kassa Account",
	                        fields=["dealer_id", "account"]):
		if r.account:
			xarita.setdefault(str(r.dealer_id), []).append(r.account)
	return xarita


def _branches(ctx):
	"""Har filial bir qator: savdo, tushum, kassa, ombor, minus-pozitsiyalar.

	Filial o'lchovi: cost_center (SI item va PE'da to'liq), ombor — warehouse.
	Eski 'Main - K'/'Main - SD' qoldiqlari asosiy filialga qo'shiladi.
	"""
	branches = _dealer_branches(ctx.company)
	if not branches:
		return {"permitted": True, "rows": [], "message": _("Filial xaritasi topilmadi.")}

	params = {"company": ctx.company, "from_date": ctx.from_date, "to_date": ctx.to_date,
	          "to_only": ctx.to_date}

	# savdo: SI itemlar bo'yicha (cost_center'da), kompaniya valyutasida
	savdo = {}
	for r in frappe.db.sql(
		"""
		SELECT sii.cost_center AS cc, SUM(sii.base_net_amount) AS amount
		FROM `tabSales Invoice` si
		JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE si.company = %(company)s AND si.docstatus = 1
		  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY sii.cost_center
		""", params, as_dict=True):
		savdo[r.cc or ""] = flt(r.amount)

	# tushum: PE (Receive) cost_center bo'yicha
	tushum = {}
	for r in frappe.db.sql(
		"""
		SELECT pe.cost_center AS cc, SUM(pe.base_paid_amount) AS amount
		FROM `tabPayment Entry` pe
		WHERE pe.company = %(company)s AND pe.docstatus = 1
		  AND pe.payment_type = 'Receive'
		  AND pe.posting_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY pe.cost_center
		""", params, as_dict=True):
		tushum[r.cc or ""] = flt(r.amount)

	# ombor qiymati va minus-pozitsiyalar: warehouse bo'yicha — snapshot-usul
	# (_minus_positions dagi izohga qarang; ombor-kartasi bilan izchil)
	ombor, minus = {}, {}
	snapshot = od._stock_snapshot_sql(
		frappe._dict(dict(ctx)), params, to_date_key="to_only")
	for r in frappe.db.sql(
		"""
		SELECT t.warehouse, SUM(t.value) AS value,
		       SUM(CASE WHEN t.qty < -0.001 THEN 1 ELSE 0 END) AS minus_pos
		FROM ({snapshot}) t
		GROUP BY t.warehouse
		""".format(snapshot=snapshot), params, as_dict=True):
		ombor[r.warehouse] = flt(r.value)
		minus[r.warehouse] = cint(r.minus_pos)

	# kassa qoldiqlari: Report Service Kassa Account xaritasi orqali
	kassa_map = _kassa_accounts_by_dealer()
	kassa = {}
	kassa_jami = {}
	for b in branches:
		accounts = kassa_map.get(str(b.dealer_id)) or []
		if accounts:
			bag = od._gl_balance(ctx, accounts, ctx.to_date)
			kassa[str(b.dealer_id)] = od._money_out(bag)
			for cur, amount in bag.items():
				od._money_add(kassa_jami, cur, amount)
		else:
			kassa[str(b.dealer_id)] = []

	# eski Main-* qoldiqlarini asosiy filialga qo'shamiz
	main = next((b for b in branches if b.is_main), branches[0])
	for eski_cc in ("Main - SD", "Main - K", ""):
		if eski_cc in savdo and main.cost_center:
			savdo[main.cost_center] = savdo.get(main.cost_center, 0) + savdo.pop(eski_cc)
		if eski_cc in tushum and main.cost_center:
			tushum[main.cost_center] = tushum.get(main.cost_center, 0) + tushum.pop(eski_cc)

	rows = []
	for b in branches:
		rows.append({
			"label": b.label,
			"is_main": cint(b.is_main),
			"savdo": flt(savdo.get(b.cost_center, 0)),
			"tushum": flt(tushum.get(b.cost_center, 0)),
			"kassa": kassa.get(str(b.dealer_id)) or [],
			"ombor": flt(ombor.get(b.warehouse, 0)),
			"minus": cint(minus.get(b.warehouse, 0)),
		})

	return {
		"permitted": True,
		"currency": ctx.company_currency,
		"rows": rows,
		"totals": {
			"savdo": sum(r["savdo"] for r in rows),
			"tushum": sum(r["tushum"] for r in rows),
			"kassa": od._money_out(kassa_jami),
			"ombor": sum(r["ombor"] for r in rows),
			"minus": sum(r["minus"] for r in rows),
		},
	}


# --------------------------------------------------------------------------
# TRADE: sinxron-salomatlik
# --------------------------------------------------------------------------


def _sync_health(ctx):
	if not od._can("Report Service Settings"):
		return od._denied("Report Service Settings")

	s = frappe.get_cached_doc("Report Service Settings")
	kecha = frappe.db.sql(
		"""
		SELECT method AS sabab, COUNT(*) AS soni
		FROM `tabError Log`
		WHERE creation >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
		GROUP BY method ORDER BY soni DESC LIMIT 5
		""", as_dict=True)
	xato_jami = sum(cint(r.soni) for r in kecha)
	repost_failed = frappe.db.count("Repost Item Valuation", {"status": "Failed"})

	# holat: yashil — sinxron yoniq va oxirgi holat Success; sariq — xatolar
	# ko'p yoki repost yiqilgan; qizil — sinxron o'chiq yoki oxirgi holat xato.
	if not cint(s.sync_enabled) or (s.last_sync_status or "") not in ("", "Success"):
		holat = "bad"
	elif xato_jami > 50 or repost_failed:
		holat = "warn"
	else:
		holat = "ok"

	return {
		"permitted": True,
		"holat": holat,
		"sync_enabled": cint(s.sync_enabled),
		"last_synced_date": str(s.last_synced_date or ""),
		"last_sync_status": s.last_sync_status or "",
		"closed_until": str(s.closed_until or ""),
		"xato_24h": xato_jami,
		"xato_top": [{"sabab": r.sabab or _("Noma'lum"), "soni": cint(r.soni)} for r in kecha],
		"repost_failed": cint(repost_failed),
	}


# --------------------------------------------------------------------------
# TRADE: minus-ombor tafsiloti
# --------------------------------------------------------------------------


def _minus_stock(ctx):
	# Snapshot-usul (_minus_positions dagi izohga qarang — Stock Reco tuzog'i)
	params = {"company": ctx.company, "to_date": ctx.to_date}
	snapshot = od._stock_snapshot_sql(frappe._dict(dict(ctx)), params)
	rows = frappe.db.sql(
		"""
		SELECT t.item_code, t.warehouse, t.qty, t.value
		FROM ({snapshot}) t
		WHERE t.qty < -0.001
		ORDER BY t.qty ASC
		LIMIT 10
		""".format(snapshot=snapshot),
		params,
		as_dict=True,
	)
	jami = _minus_positions(ctx, ctx.to_date)
	return {
		"permitted": True,
		"currency": ctx.company_currency,
		"rows": [
			{"item": r.item_code, "warehouse": r.warehouse,
			 "qty": flt(r.qty), "value": flt(r.value)}
			for r in rows
		],
		"positions": jami.positions,
		"value": jami.value,
	}


# --------------------------------------------------------------------------
# ORDERS (Oyna sex): zakazlarga SI-sana / tannarx / foyda boyitmasi
# --------------------------------------------------------------------------


def _zakaz_si_tannarx(so_names):
	"""SO -> {si_sana, tannarx}: bog'langan tasdiqlangan SI'lardan.

	si_sana — birinchi (eng erta) faktura sanasi. tannarx — o'sha SI'larning
	ombor-yozuvlaridan (SLE); SLE bo'lmasa item-qatorlardagi incoming_rate.
	Diqqat (Os ma'lumot-reallik): zakazlarda xizmat-itemlar ("Oyna-kesish"),
	SIlar omborni yurgizmaydi — tannarx faqat ma'lumot bor zakazlarda chiqadi.
	"""
	if not so_names:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT sii.sales_order AS so, si.name AS si, si.posting_date,
		       SUM(sii.incoming_rate * sii.qty) AS inc
		FROM `tabSales Invoice Item` sii
		JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE si.docstatus = 1 AND sii.sales_order IN %(names)s
		GROUP BY sii.sales_order, si.name, si.posting_date
		""",
		{"names": tuple(so_names)},
		as_dict=True,
	)
	si_names = tuple({r.si for r in rows})
	sle = {}
	if si_names:
		for r in frappe.db.sql(
			"""
			SELECT voucher_no, SUM(-stock_value_difference) AS t
			FROM `tabStock Ledger Entry`
			WHERE voucher_type = 'Sales Invoice' AND is_cancelled = 0
			  AND voucher_no IN %(si)s
			GROUP BY voucher_no
			""",
			{"si": si_names},
			as_dict=True,
		):
			sle[r.voucher_no] = flt(r.t)

	natija = {}
	for r in rows:
		d = natija.setdefault(r.so, {"si_sana": None, "tannarx": 0.0})
		sana = str(r.posting_date) if r.posting_date else None
		if sana and (not d["si_sana"] or sana < d["si_sana"]):
			d["si_sana"] = sana
		d["tannarx"] += sle[r.si] if r.si in sle else flt(r.inc)

	# 2-manba (Oyna sex oqimi, 2026-09-02): SO submit bo'lganda oyna_order.py
	# sarflangan materiallardan Material Issue yozib, nomini SO'ning
	# custom_material_issue maydoniga qo'yadi. SI-tomondan tannarx chiqmagan
	# zakazlarda tannarx ana shu MI'ning material-qiymatidan olinadi.
	mi_rows = frappe.db.sql(
		"""
		SELECT so.name AS so, se.name AS mi, SUM(sed.amount) AS t
		FROM `tabSales Order` so
		JOIN `tabStock Entry` se ON se.name = so.custom_material_issue
		JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		WHERE so.name IN %(names)s AND se.docstatus = 1
		GROUP BY so.name, se.name
		""",
		{"names": tuple(so_names)},
		as_dict=True,
	)
	for r in mi_rows:
		d = natija.setdefault(r.so, {"si_sana": None, "tannarx": 0.0})
		# hujjat-izi: tannarx qaysi Material Issue'dan shakllangani
		d["mi"] = r.mi
		if flt(d["tannarx"]) <= 0.005:
			d["tannarx"] = flt(r.t)
	return natija


def _orders(ctx):
	"""od._get_orders + har zakazga: faktura sanasi, tannarx, foyda.

	Tannarx/foyda kompaniya valyutasida (SLE shu valyutada); foyda =
	SO base_grand_total − tannarx, faqat tannarx mavjud bo'lganda.
	"""
	data = od._get_orders(ctx)
	if not data.get("permitted"):
		return data
	orders = data.get("orders") or []
	if not orders:
		return data

	qo = _zakaz_si_tannarx([o["name"] for o in orders])
	base = {
		r.name: flt(r.base_grand_total)
		for r in frappe.db.sql(
			"""SELECT name, base_grand_total FROM `tabSales Order`
			   WHERE name IN %(n)s""",
			{"n": tuple(o["name"] for o in orders)},
			as_dict=True,
		)
	}
	for o in orders:
		q = qo.get(o["name"]) or {}
		o["si_sana"] = q.get("si_sana")
		t = flt(q.get("tannarx"))
		if t > 0.005:
			o["tannarx"] = od._money_out({ctx.company_currency: t})
			o["foyda"] = od._money_out(
				{ctx.company_currency: base.get(o["name"], 0.0) - t}
			)
		else:
			o["tannarx"] = []
			o["foyda"] = []
	return data


# --------------------------------------------------------------------------
# PRODUCTION (Imzo franshiza)
# --------------------------------------------------------------------------


def _production_cards(ctx):
	cards = []
	valyuta = ctx.company_currency

	# Davr ishlab-chiqarishi (Manufacture SE: tayyor mahsulot kirimi)
	def ishlab(from_date, to_date):
		row = frappe.db.sql(
			"""
			SELECT COUNT(DISTINCT se.name) AS docs,
			       SUM(sed.qty) AS qty,
			       SUM(sle.stock_value_difference) AS value
			FROM `tabStock Entry` se
			JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
			LEFT JOIN `tabStock Ledger Entry` sle
			       ON sle.voucher_no = se.name AND sle.item_code = sed.item_code
			      AND sle.actual_qty > 0 AND sle.is_cancelled = 0
			WHERE se.company = %(company)s AND se.docstatus = 1
			  AND se.purpose = 'Manufacture'
			  AND sed.t_warehouse IS NOT NULL
			  AND se.posting_date BETWEEN %(from_date)s AND %(to_date)s
			""",
			{"company": ctx.company, "from_date": from_date, "to_date": to_date},
			as_dict=True,
		)[0]
		return frappe._dict({
			"docs": cint(row.docs), "qty": flt(row.qty),
			"value": od._money_add({}, valyuta, flt(row.value)),
		})

	now_p = ishlab(ctx.from_date, ctx.to_date)
	prev_p = ishlab(ctx.prev_from, ctx.prev_to)
	cards.append({
		"key": "production",
		"icon": "organization",
		"label": _("Ishlab chiqarish"),
		"value": od._money_out(now_p.value),
		"format": "currency",
		"tone": "primary",
		"section": "production",
		"delta": od._delta(now_p.value, prev_p.value),
		"details": [
			{"label": _("dona"), "value": now_p.qty, "format": "qty"},
			{"label": _("hujjat"), "value": now_p.docs, "format": "int"},
		],
	})

	# 3. Zakazlar (davr ichida import qilinganlari) — Zakaz Import doctype'i
	# hali o'rnatilmagan muhitda (masalan prod) karta shunchaki chiqmaydi.
	if not frappe.db.exists("DocType", "Zakaz Import"):
		return cards
	zakazlar = frappe.db.sql(
		"""
		SELECT COUNT(*) AS docs, SUM(netto_uzs) AS netto
		FROM `tabZakaz Import`
		WHERE status = 'Processed' AND company = %(company)s
		  AND IFNULL(zakaz_sanasi, '1900-01-01') BETWEEN %(from_date)s AND %(to_date)s
		""",
		{"company": ctx.company, "from_date": ctx.from_date, "to_date": ctx.to_date},
		as_dict=True,
	)[0]
	cards.append({
		"key": "orders",
		"icon": "list",
		"label": _("Zakazlar (davr)"),
		"value": cint(zakazlar.docs),
		"format": "int",
		"tone": "info",
		"section": "production",
		"details": [
			{"label": "UZS netto", "value": flt(zakazlar.netto), "format": "int"},
		],
		"route": ["List", "Zakaz Import"],
		"route_options": {"status": "Processed"},
	})

	# 4. Tayyor mahsulot qoldig'i
	tayyor = frappe.db.sql(
		"""
		SELECT SUM(t.qty) AS qty, SUM(t.value) AS value FROM (
			SELECT sle.item_code, SUM(sle.actual_qty) AS qty,
			       SUM(sle.stock_value_difference) AS value
			FROM `tabStock Ledger Entry` sle
			JOIN `tabItem` i ON i.name = sle.item_code
			JOIN `tabWarehouse` w ON w.name = sle.warehouse
			WHERE w.company = %(company)s AND sle.is_cancelled = 0
			  AND i.item_group = %(guruh)s
			  AND sle.posting_date <= %(to_date)s
			GROUP BY sle.item_code
		) t
		""",
		{"company": ctx.company, "guruh": IMZO_TAYYOR_GURUH, "to_date": ctx.to_date},
		as_dict=True,
	)[0]
	cards.append({
		"key": "finished",
		"icon": "stock",
		"label": _("Tayyor mahsulot (qoldiq)"),
		"value": od._money_out(od._money_add({}, valyuta, flt(tayyor.value))),
		"format": "currency",
		"tone": "success",
		"as_of": True,
		"details": [{"label": _("dona"), "value": flt(tayyor.qty), "format": "qty"}],
	})

	return cards


def _production(ctx):
	"""Imzo bo'limi: so'nggi zakazlar + material-guruh kesimidagi ombor."""
	if not frappe.db.exists("DocType", "Zakaz Import"):
		return {
			"permitted": True, "currency": ctx.company_currency,
			"zakazlar": [], "guruhlar": [], "lugat": 0,
			"empty_reason": _("Zakaz-import moduli bu muhitda hali o'rnatilmagan."),
		}
	zakazlar = frappe.db.sql(
		"""
		SELECT zi.zakaz_raqami, zi.zakaz_sanasi, zi.loyiha, zi.netto_uzs,
		       zi.status, COUNT(zp.name) AS pozitsiyalar
		FROM `tabZakaz Import` zi
		LEFT JOIN `tabZakaz Import Pozitsiya` zp ON zp.parent = zi.name
		WHERE zi.company = %(company)s
		GROUP BY zi.name
		ORDER BY zi.zakaz_sanasi DESC, zi.zakaz_raqami DESC
		LIMIT 15
		""",
		{"company": ctx.company},
		as_dict=True,
	)

	guruhlar = frappe.db.sql(
		"""
		SELECT i.item_group AS guruh, COUNT(DISTINCT t.item_code) AS items,
		       SUM(t.qty) AS qty, SUM(t.value) AS value
		FROM (
			SELECT sle.item_code, SUM(sle.actual_qty) AS qty,
			       SUM(sle.stock_value_difference) AS value
			FROM `tabStock Ledger Entry` sle
			JOIN `tabWarehouse` w ON w.name = sle.warehouse
			WHERE w.company = %(company)s AND sle.is_cancelled = 0
			  AND sle.posting_date <= %(to_date)s
			GROUP BY sle.item_code
			HAVING ABS(qty) > 0.001 OR ABS(value) > 0.01
		) t
		JOIN `tabItem` i ON i.name = t.item_code
		GROUP BY i.item_group
		ORDER BY value DESC
		""",
		{"company": ctx.company, "to_date": ctx.to_date},
		as_dict=True,
	)

	lugat = (frappe.db.count("Klaes Tarjima")
	         if frappe.db.exists("DocType", "Klaes Tarjima") else 0)

	return {
		"permitted": True,
		"currency": ctx.company_currency,
		"zakazlar": [
			{
				"raqam": r.zakaz_raqami, "sana": str(r.zakaz_sanasi or ""),
				"loyiha": r.loyiha, "netto_uzs": flt(r.netto_uzs),
				"status": r.status, "pozitsiyalar": cint(r.pozitsiyalar),
			}
			for r in zakazlar
		],
		"guruhlar": [
			{"guruh": r.guruh, "items": cint(r.items),
			 "qty": flt(r.qty), "value": flt(r.value)}
			for r in guruhlar
		],
		"lugat": cint(lugat),
	}


# --------------------------------------------------------------------------
# Marshrutlash
# --------------------------------------------------------------------------


def _overview(ctx):
	"""STANDART KPI-lar hamma kompaniyada bir xil; Imzoga ishlab-chiqarish
	kartalari QO'SHIMCHA sifatida ulanadi (standartni buzmasdan)."""
	natija = _trade_overview(ctx)
	if ctx.profile == "production":
		natija["cards"] = natija["cards"] + _production_cards(ctx)
	return natija


_BUILDERS = {
	"overview": _overview,
	"sales": od._get_sales,
	"orders": _orders,
	"cash": od._get_cash,
	"expenses": od._get_expenses,
	"tops": _tops,
	"branches": _branches,
	"sync_health": _sync_health,
	"minus_stock": _minus_stock,
	"production": _production,
}
