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
				ctx, "bd:{0}".format(section), lambda s=section: _BUILDERS[s](ctx)
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
	"""Minusdagi ombor-pozitsiyalari: soni va qiymati (kompaniya bo'yicha)."""
	row = frappe.db.sql(
		"""
		SELECT COUNT(*) AS positions,
		       SUM(CASE WHEN t.value < 0 THEN t.value ELSE 0 END) AS value
		FROM (
			SELECT sle.item_code, sle.warehouse,
			       SUM(sle.actual_qty) AS qty,
			       SUM(sle.stock_value_difference) AS value
			FROM `tabStock Ledger Entry` sle
			JOIN `tabWarehouse` w ON w.name = sle.warehouse
			WHERE w.company = %(company)s AND sle.is_cancelled = 0
			  AND sle.posting_date <= %(to_date)s
			GROUP BY sle.item_code, sle.warehouse
			HAVING qty < -0.001
		) t
		""",
		{"company": ctx.company, "to_date": to_date},
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

	# ombor qiymati va minus-pozitsiyalar: warehouse bo'yicha
	ombor, minus = {}, {}
	for r in frappe.db.sql(
		"""
		SELECT t.warehouse, SUM(t.value) AS value,
		       SUM(CASE WHEN t.qty < -0.001 THEN 1 ELSE 0 END) AS minus_pos
		FROM (
			SELECT sle.warehouse, sle.item_code,
			       SUM(sle.actual_qty) AS qty,
			       SUM(sle.stock_value_difference) AS value
			FROM `tabStock Ledger Entry` sle
			JOIN `tabWarehouse` w ON w.name = sle.warehouse
			WHERE w.company = %(company)s AND sle.is_cancelled = 0
			  AND sle.posting_date <= %(to_only)s
			GROUP BY sle.warehouse, sle.item_code
		) t
		GROUP BY t.warehouse
		""", params, as_dict=True):
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
	rows = frappe.db.sql(
		"""
		SELECT t.item_code, t.warehouse, t.qty, t.value
		FROM (
			SELECT sle.item_code, sle.warehouse,
			       SUM(sle.actual_qty) AS qty,
			       SUM(sle.stock_value_difference) AS value
			FROM `tabStock Ledger Entry` sle
			JOIN `tabWarehouse` w ON w.name = sle.warehouse
			WHERE w.company = %(company)s AND sle.is_cancelled = 0
			  AND sle.posting_date <= %(to_date)s
			GROUP BY sle.item_code, sle.warehouse
			HAVING qty < -0.001
		) t
		ORDER BY t.qty ASC
		LIMIT 10
		""",
		{"company": ctx.company, "to_date": ctx.to_date},
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
	"orders": od._get_orders,
	"cash": od._get_cash,
	"expenses": od._get_expenses,
	"tops": _tops,
	"branches": _branches,
	"sync_health": _sync_health,
	"minus_stock": _minus_stock,
	"production": _production,
}
