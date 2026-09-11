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
from frappe.utils import add_days, cint, date_diff, flt, getdate, now_datetime

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
				# Filial-filtr chiplari uchun (faqat trade-kompaniyalar)
				"branches": (
					frappe.db.sql(
						"""SELECT label, cost_center, warehouse
						   FROM `tabReport Service Dealer Branch`
						   WHERE company = %s AND COALESCE(cost_center,'') != ''
						   ORDER BY idx""", (c,), as_dict=True)
					if PROFILLAR[c] == "trade" else []
				),
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
	params = {
		"company": ctx.company,
		"payment_type": payment_type,
		"from_date": from_date,
		"to_date": to_date,
	}
	cc = _cc_sharti(ctx, params, "pe.cost_center")
	row = frappe.db.sql(
		"""
		SELECT SUM(pe.base_paid_amount) AS amount, COUNT(*) AS docs
		FROM `tabPayment Entry` pe
		WHERE pe.company = %(company)s AND pe.docstatus = 1
		  AND pe.payment_type = %(payment_type)s
		  AND pe.posting_date BETWEEN %(from_date)s AND %(to_date)s{cc}
		""".format(cc=cc),
		params,
		as_dict=True,
	)[0]
	return frappe._dict(
		{
			"amount": od._money_add({}, ctx.company_currency, flt(row.amount)),
			"docs": cint(row.docs),
		}
	)


def _cc_subtree(cost_center):
	"""Cost-center (guruh bo'lsa butun ostidagilari). Bo'sh -> None."""
	if not cost_center:
		return None
	lft, rgt = frappe.db.get_value(
		"Cost Center", cost_center, ["lft", "rgt"]) or (None, None)
	if lft is None:
		return [cost_center]
	return [r[0] for r in frappe.db.sql(
		"SELECT name FROM `tabCost Center` WHERE lft >= %s AND rgt <= %s",
		(lft, rgt))] or [cost_center]


def _cc_sharti(ctx, params, ustun):
	"""Filial-filtr sharti (SI-item / PE darajasida). GL so'rovlarda kerak
	emas — ular od._gl_extra_conditions orqali o'zi filtrlanadi."""
	if not ctx.cost_center:
		return ""
	params["cc_list"] = tuple(_cc_subtree(ctx.cost_center))
	return " AND {0} IN %(cc_list)s".format(ustun)


def _wh_sharti(ctx, params, ustun="sle.warehouse"):
	"""Filial-ombor sharti (SLE-tannarx uchun)."""
	if not ctx.warehouse:
		return ""
	params["wh"] = ctx.warehouse
	return " AND {0} = %(wh)s".format(ustun)


def _mi_tannarx(ctx, yil, oylar):
	"""Zakazga bog'langan Material Issue tannarxi — MIJOZ kesimida.

	Oyna sex oqimida hisob-faktura xizmat-itemlardan iborat (`update_stock = 0`),
	shuning uchun SI tomonida ombor-yozuvi (SLE) YO'Q va tannarx nolga tushib
	qolardi (2026: savdo 74 623, tannarx 43, marja 99.9% — jonli tekshiruv
	2026-09-10). Haqiqiy tannarx SO submit bo'lganda yoziladigan Material
	Issue'da: `Sales Order.custom_material_issue` (oyna_order.py).

	SI-tomondagi SLE bilan ustma-ust TUSHMAYDI — bular alohida ombor
	hujjatlari, har biri o'z COGS yozuvini beradi. Yig'indi GL bilan aynan mos
	(2026: sed.amount = SLE = GL COGS = 33 765.57).

	`custom_material_issue` faqat Oyna sex oqimida to'ladi, shuning uchun
	boshqa kompaniyalarda bu qo'shimcha bo'sh qaytadi.

	Qaytadi: {mijoz: tannarx} — kompaniya valyutasida.
	"""
	params = {"company": ctx.company, "yil": yil}
	oy = ""
	if oylar:
		params["oylar"] = tuple(oylar)
		oy = " AND MONTH(se.posting_date) IN %(oylar)s"
	wh = _wh_sharti(ctx, params, "sed.s_warehouse")

	rows = frappe.db.sql(
		"""
		SELECT so.customer, SUM(sed.amount) AS t
		FROM `tabStock Entry` se
		JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		JOIN `tabSales Order` so ON so.custom_material_issue = se.name
		WHERE se.company = %(company)s AND se.docstatus = 1
		  AND YEAR(se.posting_date) = %(yil)s{oy}{wh}
		GROUP BY so.customer
		""".format(oy=oy, wh=wh),
		params,
		as_dict=True,
	)
	return {r.customer: flt(r.t) for r in rows if r.customer}


def _filial_savdo(ctx, from_date, to_date):
	"""Filial savdosi QATOR (item) darajasida — SI sarlavhasida filial yo'q.
	Netto (base_net_amount): Filiallar jadvali bilan bir xil ta'rif."""
	params = {"company": ctx.company, "from_date": from_date, "to_date": to_date}
	cc = _cc_sharti(ctx, params, "sii.cost_center")
	row = frappe.db.sql(
		"""
		SELECT SUM(sii.base_net_amount) AS s,
		       COUNT(DISTINCT si.name) AS docs,
		       COUNT(DISTINCT si.customer) AS customers
		FROM `tabSales Invoice` si
		JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE si.company = %(company)s AND si.docstatus = 1
		  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s{cc}
		""".format(cc=cc), params, as_dict=True)[0]
	return frappe._dict(
		net=od._money_add({}, ctx.company_currency, flt(row.s)),
		docs=cint(row.docs), customers=cint(row.customers))


def _gl_period_net(ctx, accounts, from_date, to_date):
	"""Davr ichidagi GL netto aylanma (hisob valyutasida)."""
	return od._gl_balance(ctx, accounts, to_date, from_date=from_date)


def _mijoz_qoldiq(ctx, to_date):
	"""Receivable qoldig'ini MIJOZ kesimida qarz va pred-oplataga ajratadi.

	Hisob darajasidagi netto (`_gl_balance`) qarz bilan oldindan to'lovni
	bir-biriga yutdirib yuboradi (Oyna sex, 2026-09-10: netto 322 mln = 596 mln
	qarz − 273 mln avans) — shuning uchun ajratish har bir mijozning O'Z
	qoldig'i belgisi bo'yicha qilinadi.

	Qaytadi: qarz/avans — {valyuta: summa} (ikkalasi ham musbat),
	qarzdor/avansli — mijozlar soni.
	"""
	accounts = od.get_receivable_accounts(ctx.company)
	natija = frappe._dict(qarz={}, avans={}, qarzdor=0, avansli=0)
	if not accounts:
		return natija

	params = {"company": ctx.company, "accounts": tuple(accounts), "to_date": to_date}
	conditions = od._gl_extra_conditions(ctx, params)
	rows = frappe.db.sql(
		"""
		SELECT gle.account_currency AS currency, gle.party,
		       SUM(gle.debit_in_account_currency - gle.credit_in_account_currency) AS amount
		FROM `tabGL Entry` gle
		WHERE gle.company = %(company)s
		  AND gle.is_cancelled = 0
		  AND gle.account IN %(accounts)s
		  AND gle.posting_date <= %(to_date)s
		  {conditions}
		GROUP BY gle.account_currency, gle.party
		HAVING ABS(SUM(gle.debit_in_account_currency - gle.credit_in_account_currency)) > 0.005
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)

	# Bir mijoz ikki valyutada qatnashsa, u ikkala tomonda ham sanalmasin
	qarzdor, avansli = set(), set()
	for row in rows:
		if flt(row.amount) > 0:
			od._money_add(natija.qarz, row.currency, row.amount)
			qarzdor.add(row.party)
		else:
			od._money_add(natija.avans, row.currency, -flt(row.amount))
			avansli.add(row.party)
	natija.qarzdor = len(qarzdor)
	natija.avansli = len(avansli)
	return natija


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

	# 1. Savdo (SI, hujjat valyutasida) + delta.
	# Filial tanlangan bo'lsa — item-darajada (SI sarlavhasida filial yo'q).
	if ctx.cost_center:
		now_rev = _filial_savdo(ctx, ctx.from_date, ctx.to_date)
		prev_rev = _filial_savdo(ctx, ctx.prev_from, ctx.prev_to)
		invoices = frappe._dict(invoices=now_rev.docs, customers=now_rev.customers)
	else:
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

	# 6-7. Mijozlar qarzi va Avans (oldindan to'lov) — ATAYLAB ikki karta.
	# Ilgari bitta "Mijozlar balansi" kartasi netto ko'rsatardi va oldindan
	# to'lovlar qarzni yashirib yuborardi (foydalanuvchi 2026-09-10).
	mijoz = _mijoz_qoldiq(ctx, ctx.to_date)
	cards.append({
		"key": "receivable",
		"icon": "customer",
		"label": _("Mijozlar qarzi"),
		"value": od._money_out(mijoz.qarz),
		"format": "currency",
		"tone": "warning",
		"as_of": True,
		"details": [{"label": _("mijoz"), "value": mijoz.qarzdor, "format": "int"}],
		"route": ["query-report", "Accounts Receivable"],
		"route_options": {"company": ctx.company, "report_date": str(ctx.to_date)},
	})
	cards.append({
		"key": "advance",
		"icon": "arrow-down-left",
		"label": _("Avans"),
		"value": od._money_out(mijoz.avans),
		"format": "currency",
		"tone": "info",
		"as_of": True,
		"details": [{"label": _("mijoz"), "value": mijoz.avansli, "format": "int"}],
		"route": ["query-report", "Accounts Receivable"],
		"route_options": {"company": ctx.company, "report_date": str(ctx.to_date)},
	})

	# 8. Minus-ombor (nazorat kartasi)
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


#: Mijozlar ro'yxatida bitta sahifadagi qatorlar soni.
MIJOZ_SAHIFA_OLCHAMI = 50

#: Qarz trendini taqqoslash oynasi (kun): "oshdi/tushdi" shu davrga nisbatan.
MIJOZ_TREND_KUN = 30

#: Qarz/avansni nolga tenglashtirish chegarasi (yaxlitlash qoldiqlari uchun).
MIJOZ_NOL = 0.005

#: Ro'yxatni saralash mumkin bo'lgan ustunlar (kalit -> qator maydoni).
MIJOZ_TARTIBLAR = ("qarz", "ozgarish", "kun", "aylanma", "ulush", "nom")


def _yil_oylar(ctx, filters):
	"""Tahlil davri: (yil, [oylar]). Yil berilmasa — oxirgi savdo yili.

	`get_tahlil` va `get_mijozlar` bitta davrni ko'rsatishi uchun mantiq shu
	yerda yagona joyda turadi.
	"""
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
	return yil, sorted({cint(o) for o in oylar if 1 <= cint(o) <= 12})


def _mijoz_nomlari(partiyalar):
	"""{customer: customer_name} — bo'sh nomlar kodning o'zi bilan to'ldiriladi."""
	if not partiyalar:
		return {}
	nomlar = {}
	for r in frappe.get_all(
		"Customer",
		filters={"name": ("in", list(partiyalar))},
		fields=["name", "customer_name"],
		ignore_permissions=True,
		limit_page_length=0,
	):
		nomlar[r.name] = r.customer_name or r.name
	return nomlar


def _mijoz_aylanma(ctx, yil, oylar):
	"""Mijoz kesimidagi aylanma — TANLANGAN DAVRDAGI savdo hujjatlari bo'yicha.

	Davr paneldagi yil/oy chiplaridan olinadi (foydalanuvchi 2026-09-10:
	"mijozning aylanmasi tanlangan davr orasidagi tranzaksiyalaridan
	hisoblanishi kerak"). Vozvratlar (is_return) manfiy grand_total bilan
	o'z-o'zidan ayiriladi — demak bu SOF aylanma.

	Qaytadi: {mijoz: {valyuta: summa}} — hujjat valyutasida.
	"""
	params = {"company": ctx.company, "yil": yil}
	oy = ""
	if oylar:
		params["oylar"] = tuple(oylar)
		oy = " AND MONTH(si.posting_date) IN %(oylar)s"

	if ctx.cost_center:
		cc = _cc_sharti(ctx, params, "sii.cost_center")
		sql = """
			SELECT si.customer, si.currency, SUM(sii.net_amount) AS summa
			FROM `tabSales Invoice` si
			JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
			WHERE si.company = %(company)s AND si.docstatus = 1
			  AND YEAR(si.posting_date) = %(yil)s{oy}{cc}
			GROUP BY si.customer, si.currency
		""".format(oy=oy, cc=cc)
	else:
		sql = """
			SELECT si.customer, si.currency, SUM(si.grand_total) AS summa
			FROM `tabSales Invoice` si
			WHERE si.company = %(company)s AND si.docstatus = 1
			  AND YEAR(si.posting_date) = %(yil)s{oy}
			GROUP BY si.customer, si.currency
		""".format(oy=oy)
	natija = {}
	for r in frappe.db.sql(sql, params, as_dict=True):
		if r.customer:
			od._money_add(natija.setdefault(r.customer, {}), r.currency, r.summa)
	return natija


@frappe.whitelist()
def get_mijozlar(filters=None):
	"""MIJOZLAR TAHLILI: qarz ro'yxati — qidiruv, saralash va sahifalash bilan.

	Har qator: qarz (yoki avans), 30 kunlik o'zgarish (MIJOZ_TREND_KUN), oxirgi
	to'lovdan beri o'tgan kun va qarzning TANLANGAN DAVRDAGI aylanmaga nisbati.
	Aylanma davri — paneldagi yil/oy chiplari (`yil`, `oylar`).

	Qarz esa DOIM bugungi (to_date) qoldiq: u davr emas, holat ko'rsatkichi.

	Barcha summalar bitta valyutaga (UZS) keltiriladi — aks holda ustunlarni
	saralab ham, foizni hisoblab ham bo'lmaydi. Kurs davr oxiriga qarab
	olinadi (ERPNext hisobotlaridagi bilan bir xil).

	filters: company, yil, oylar, qidiruv, sahifa, tartib, yonalish, olcham.
	"""
	filters = od._parse_filters(filters)
	ctx = _ctx(dict(filters))
	if not od._can("GL Entry"):
		return od._denied("GL Entry")

	yil, oylar = _yil_oylar(ctx, filters)

	qidiruv = str(filters.get("qidiruv") or "").strip()
	sahifa = max(cint(filters.get("sahifa")) or 1, 1)
	olcham = min(max(cint(filters.get("olcham")) or MIJOZ_SAHIFA_OLCHAMI, 10), 200)
	tartib = filters.get("tartib") if filters.get("tartib") in MIJOZ_TARTIBLAR else "qarz"
	teskari = str(filters.get("yonalish") or "desc") != "asc"

	accounts = od.get_receivable_accounts(ctx.company)
	# Dashboardning qolgan bloklari kabi ro'yxat ham UZS taqdimotida
	# (foydalanuvchi 2026-09-10: valyutani o'z holiga qaytarish).
	valyuta = od.PNL_PRESENTATION_CURRENCY
	bosh = {
		"permitted": True,
		"valyuta": valyuta,
		"trend_kun": MIJOZ_TREND_KUN,
		"yil": yil,
		"oylar": oylar,
		"context": od._context_info(ctx),
	}
	if not accounts:
		return dict(
			bosh, rows=[], jami={}, sahifa=1, sahifalar=1, jami_qatorlar=0, olcham=olcham,
			qidiruv=qidiruv, tartib=tartib, yonalish="desc" if teskari else "asc",
		)

	oldin = str(add_days(getdate(ctx.to_date), -MIJOZ_TREND_KUN))

	params = {
		"company": ctx.company,
		"accounts": tuple(accounts),
		"to_date": ctx.to_date,
		"oldin": oldin,
	}
	conditions = od._gl_extra_conditions(ctx, params)

	# Bitta so'rovda: joriy qoldiq, {trend} kun oldingi qoldiq, oxirgi to'lov,
	# oxirgi harakat. To'lov = qarzni kamaytirgan kredit; hisob-faktura va
	# vozvrat (Sales Invoice) to'lov emas, shuning uchun chiqarib tashlanadi.
	rows = frappe.db.sql(
		"""
		SELECT gle.party, gle.account_currency AS currency,
		       SUM(gle.debit_in_account_currency - gle.credit_in_account_currency) AS qarz,
		       SUM(CASE WHEN gle.posting_date <= %(oldin)s
		                THEN gle.debit_in_account_currency - gle.credit_in_account_currency
		                ELSE 0 END) AS qarz_oldin,
		       MAX(CASE WHEN gle.credit_in_account_currency > 0
		                 AND gle.voucher_type <> 'Sales Invoice'
		                THEN gle.posting_date END) AS oxirgi_tolov,
		       MAX(gle.posting_date) AS oxirgi_harakat,
		       MIN(gle.posting_date) AS birinchi_harakat
		FROM `tabGL Entry` gle
		WHERE gle.company = %(company)s
		  AND gle.is_cancelled = 0
		  AND gle.account IN %(accounts)s
		  AND gle.posting_date <= %(to_date)s
		  {conditions}
		GROUP BY gle.party, gle.account_currency
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)

	aylanmalar = _mijoz_aylanma(ctx, yil, oylar)

	# Valyuta bo'yicha yig'ish — bir mijoz bir nechta valyutada bo'lishi mumkin
	yigma = {}
	for r in rows:
		if not r.party:
			continue
		d = yigma.setdefault(
			r.party,
			{
				"qarz": {},
				"qarz_oldin": {},
				"oxirgi_tolov": None,
				"oxirgi_harakat": None,
				"birinchi_harakat": None,
			},
		)
		od._money_add(d["qarz"], r.currency, r.qarz)
		od._money_add(d["qarz_oldin"], r.currency, r.qarz_oldin)
		for kalit in ("oxirgi_tolov", "oxirgi_harakat"):
			sana = r.get(kalit)
			if sana and (not d[kalit] or str(sana) > d[kalit]):
				d[kalit] = str(sana)
		if r.birinchi_harakat and (
			not d["birinchi_harakat"] or str(r.birinchi_harakat) < d["birinchi_harakat"]
		):
			d["birinchi_harakat"] = str(r.birinchi_harakat)

	nomlar = _mijoz_nomlari(set(yigma) | set(aylanmalar))
	bugun = getdate(ctx.to_date)

	natija = []
	jami_qarz = jami_avans = jami_aylanma = 0.0
	qarzdor = avansli = 0
	for mijoz, d in yigma.items():
		qarz = flt(od._money_to_presentation(d["qarz"], ctx.to_date).get(valyuta))
		oldingi = flt(od._money_to_presentation(d["qarz_oldin"], ctx.to_date).get(valyuta))
		aylanma = flt(
			od._money_to_presentation(aylanmalar.get(mijoz) or {}, ctx.to_date).get(valyuta)
		)
		if abs(qarz) < MIJOZ_NOL and abs(aylanma) < MIJOZ_NOL:
			continue  # butunlay jim hisob — ro'yxatni to'ldirib yotmasin

		ozgarish = qarz - oldingi
		if qarz > MIJOZ_NOL:
			jami_qarz += qarz
			qarzdor += 1
		elif qarz < -MIJOZ_NOL:
			jami_avans += -qarz
			avansli += 1
		jami_aylanma += aylanma

		natija.append({
			"customer": mijoz,
			"nom": nomlar.get(mijoz, mijoz),
			"qarz": qarz,
			"avans": -qarz if qarz < -MIJOZ_NOL else 0.0,
			"ozgarish": ozgarish,
			"ozgarish_pct": (
				round(ozgarish / abs(oldingi) * 100, 1) if abs(oldingi) > MIJOZ_NOL else None
			),
			"yonalish": (
				"up" if ozgarish > MIJOZ_NOL else ("down" if ozgarish < -MIJOZ_NOL else "flat")
			),
			"oxirgi_tolov": d["oxirgi_tolov"],
			# "Osilib qolgan kun" faqat QARZI BOR mijozda ma'noga ega. Hech
			# qachon to'lamagan mijozda (masalan qarzi ochilish JE'sidan kelgan)
			# hisob birinchi harakat sanasidan yuritiladi — aks holda eng
			# muzlab qolganlar ustunda bo'sh chiziq bo'lib ko'rinmay ketardi.
			"tolov_yoq": bool(qarz > MIJOZ_NOL and not d["oxirgi_tolov"]),
			"kun": (
				date_diff(bugun, getdate(d["oxirgi_tolov"] or d["birinchi_harakat"]))
				if qarz > MIJOZ_NOL and (d["oxirgi_tolov"] or d["birinchi_harakat"])
				else None
			),
			"oxirgi_harakat": d["oxirgi_harakat"],
			"aylanma": aylanma,
			# Qarz aylanmaning necha foizi (foydalanuvchi tanlovi 2026-09-10)
			"ulush": (
				round(qarz / aylanma * 100, 1)
				if aylanma > MIJOZ_NOL and qarz > MIJOZ_NOL
				else None
			),
		})

	if qidiruv:
		q = qidiruv.lower()
		natija = [
			r for r in natija
			if q in str(r["nom"]).lower() or q in str(r["customer"]).lower()
		]

	def kalit(r):
		if tartib == "nom":
			return str(r["nom"]).lower()
		qiymat = r.get(tartib)
		# Bo'sh qiymatlar (masalan to'lovi yo'q mijoz) doim oxirida tursin
		return -1e18 if qiymat is None else flt(qiymat)

	natija.sort(key=kalit, reverse=teskari)

	jami_qatorlar = len(natija)
	sahifalar = max((jami_qatorlar + olcham - 1) // olcham, 1)
	sahifa = min(sahifa, sahifalar)
	boshi = (sahifa - 1) * olcham

	return dict(
		bosh,
		rows=natija[boshi:boshi + olcham],
		jami={
			"qarz": jami_qarz,
			"avans": jami_avans,
			"aylanma": jami_aylanma,
			"qarzdor": qarzdor,
			"avansli": avansli,
			"mijozlar": jami_qatorlar,
			"ulush": (
				round(jami_qarz / jami_aylanma * 100, 1) if jami_aylanma > MIJOZ_NOL else None
			),
		},
		sahifa=sahifa,
		sahifalar=sahifalar,
		jami_qatorlar=jami_qatorlar,
		olcham=olcham,
		qidiruv=qidiruv,
		tartib=tartib,
		yonalish="desc" if teskari else "asc",
	)


@frappe.whitelist()
def get_ombor(filters=None):
	"""OMBOR TAHLILI: ombor qoldig'i ITEM GURUHI kesimida — bitta jadval.

	Item-lar juda ko'p bo'lgani uchun item darajasi ATAYLAB ko'rsatilmaydi
	(foydalanuvchi 2026-09-11: "item ni o'zi ko'rinmasin, item group bo'yicha
	ostatka ko'rinsa yetarli").

	Qoldiq — bugungi (to_date) holatga, ERPNext «Stock Balance» mantiqidagi
	snapshot bilan: har item-ombor juftligi uchun oxirgi ombor-yozuvi.
	Toolbardagi filial tanlovi ombor-filtri sifatida qo'llanadi.

	Qiymat kompaniya valyutasida (Stock hisobi shu valyutada yuritiladi).
	"""
	ctx = _ctx(filters)
	if not od._can("Stock Ledger Entry", "Item"):
		return od._denied("Stock Ledger Entry", "Item")

	params = {"company": ctx.company, "to_date": ctx.to_date, "nomalum": _("Guruhsiz")}
	snapshot = od._stock_snapshot_sql(ctx, params)

	rows = frappe.db.sql(
		"""
		SELECT COALESCE(NULLIF(item.item_group, ''), %(nomalum)s) AS guruh,
		       COUNT(DISTINCT t.item_code) AS pozitsiya,
		       COUNT(DISTINCT t.warehouse) AS omborlar,
		       SUM(t.qty) AS qty,
		       SUM(t.value) AS qiymat,
		       COUNT(DISTINCT item.stock_uom) AS birliklar,
		       MIN(item.stock_uom) AS birlik,
		       SUM(CASE WHEN t.qty < -0.001 THEN 1 ELSE 0 END) AS minus
		FROM ({snapshot}) t
		LEFT JOIN `tabItem` item ON item.name = t.item_code
		WHERE t.qty <> 0
		GROUP BY guruh
		ORDER BY qiymat DESC
		""".format(snapshot=snapshot),
		params,
		as_dict=True,
	)

	jami_qiymat = sum(flt(r.qiymat) for r in rows)
	natija = []
	for r in rows:
		natija.append({
			"guruh": r.guruh,
			"pozitsiya": cint(r.pozitsiya),
			"omborlar": cint(r.omborlar),
			"qty": flt(r.qty),
			# Guruhda bir nechta o'lchov birligi bo'lsa miqdorni qo'shish
			# ma'nosiz — birlik ko'rsatilmaydi va buni UI ham bildiradi.
			"birlik": od._birlik_yorligi(r.birlik) if cint(r.birliklar) == 1 else None,
			"qiymat": flt(r.qiymat),
			"ulush": (
				round(flt(r.qiymat) / jami_qiymat * 100, 1) if jami_qiymat else None
			),
			"minus": cint(r.minus),
		})

	return {
		"permitted": True,
		"valyuta": ctx.company_currency,
		"sana": str(ctx.to_date),
		"rows": natija,
		"jami": {
			"guruhlar": len(natija),
			"pozitsiya": sum(r["pozitsiya"] for r in natija),
			"qiymat": jami_qiymat,
			"minus": sum(r["minus"] for r in natija),
			# Guruhlar bo'yicha omborlar sonini qo'shib bo'lmaydi (bir ombor
			# bir necha guruhda uchraydi) — shuning uchun tanlangan ombor nomi.
			"ombor": ctx.warehouse or None,
		},
		"context": od._context_info(ctx),
	}


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
		# filial (cost-center) sharti — item-darajada; ombor-sharti tannarx uchun
		cc = _cc_sharti(ctx, oy_params, "sii.cost_center")
		wh = _wh_sharti(ctx, oy_params, "sle.warehouse")

		# --- kalendar (butun oy, mijoz filtri bilan) ---
		# Filial tanlansa item-darajada (netto): SI sarlavhasida filial yo'q
		kunlar = {}
		if ctx.cost_center:
			kalendar_sql = """
			SELECT DAY(si.posting_date) AS kun,
			       SUM(sii.base_net_amount) AS summa,
			       COUNT(DISTINCT si.name) AS docs
			FROM `tabSales Invoice` si
			JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
			WHERE si.company = %(company)s AND si.docstatus = 1
			  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s{mijoz}{cc}
			GROUP BY DAY(si.posting_date)
			""".format(mijoz=mijoz_sharti, cc=cc)
		else:
			kalendar_sql = """
			SELECT DAY(si.posting_date) AS kun,
			       SUM(si.base_grand_total) AS summa, COUNT(*) AS docs
			FROM `tabSales Invoice` si
			WHERE si.company = %(company)s AND si.docstatus = 1
			  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s{mijoz}
			GROUP BY DAY(si.posting_date)
			""".format(mijoz=mijoz_sharti)
		for r in frappe.db.sql(kalendar_sql, oy_params, as_dict=True):
			if r.kun:
				kunlar[cint(r.kun)] = {"summa": flt(r.summa), "docs": cint(r.docs)}

		# --- mijoz-chiplar (oyning top-24 mijozi, mijoz-filtrsiz) ---
		chip_params = {"company": ctx.company, "from_date": oy_boshi,
		               "to_date": oy_oxiri}
		chip_cc = _cc_sharti(ctx, chip_params, "sii.cost_center")
		if ctx.cost_center:
			mijozlar = frappe.db.sql(
				"""
				SELECT si.customer, si.customer_name,
				       SUM(sii.base_net_amount) AS savdo
				FROM `tabSales Invoice` si
				JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
				WHERE si.company = %(company)s AND si.docstatus = 1
				  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s{cc}
				GROUP BY si.customer ORDER BY savdo DESC LIMIT 24
				""".format(cc=chip_cc), chip_params, as_dict=True)
		else:
			mijozlar = frappe.db.sql(
				"""
				SELECT si.customer, si.customer_name,
				       SUM(si.base_grand_total) AS savdo
				FROM `tabSales Invoice` si
				WHERE si.company = %(company)s AND si.docstatus = 1
				  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
				GROUP BY si.customer ORDER BY savdo DESC LIMIT 24
				""", chip_params, as_dict=True)

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
			  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s{mijoz}{cc}
			GROUP BY sii.item_code
			""".format(mijoz=mijoz_sharti, cc=cc),
			q_params, as_dict=True,
		)
		tannarx_t = {}
		for r in frappe.db.sql(
			"""
			SELECT sle.item_code, SUM(-sle.stock_value_difference) AS tannarx
			FROM `tabStock Ledger Entry` sle
			WHERE sle.is_cancelled = 0 AND sle.voucher_type = 'Sales Invoice'{wh}
			  AND sle.voucher_no IN (
				SELECT si.name FROM `tabSales Invoice` si
				WHERE si.company = %(company)s AND si.docstatus = 1
				  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s{mijoz}
			  )
			GROUP BY sle.item_code
			""".format(mijoz=mijoz_sharti, wh=wh),
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
		if ctx.cost_center:
			bosh = frappe.db.sql(
				"""
				SELECT SUM(sii.base_net_amount) AS savdo,
				       COUNT(DISTINCT si.name) AS docs,
				       COUNT(DISTINCT si.customer) AS mijozlar
				FROM `tabSales Invoice` si
				JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
				WHERE si.company = %(company)s AND si.docstatus = 1
				  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s{mijoz}{cc}
				""".format(mijoz=mijoz_sharti, cc=cc),
				q_params, as_dict=True,
			)[0]
		else:
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


#: ABC chegaralari — kumulyativ aylanma ulushi (%): A — 80%, B — 95%, qolgani C.
ABC_CHEGARA = (80.0, 95.0)
#: XYZ chegaralari — oylik variatsiya koeffitsienti CV = σ/μ (%): X ≤ 10, Y ≤ 25.
XYZ_CHEGARA = (10.0, 25.0)
#: XYZ bazasiga oy kirishi uchun uning kompaniya-savdosi eng kuchli oyning
#: kamida shuncha foizi bo'lsin. Migratsiya/ochilish oylari (masalan 29 ta
#: hisob-faktura) bazaga kirsa, har bir mijozning CV'si sun'iy ko'tarilib
#: hammasi "Z" bo'lib qolardi (jonli tekshiruv 2026-09-11).
XYZ_FAOL_ULUSH = 5.0


def _mijoz_reyting(ctx, yil, oylar):
	"""Davrda ishlangan mijozlar soni + ABC/XYZ reytingi.

	Qaytadi: (frontend uchun payload, TO'LIQ mijoz-qatorlari). Qatorlar
	`_cross_sell` blokiga ham beriladi — ikkala blok bir xil ABC sinflaridan
	foydalansin va mijoz-ro'yxati ikki marta hisoblanmasin.

	ABC — davr aylanmasidagi kumulyativ ulush (A: birinchi 80%, B: 95% gacha,
	qolgani C): kim qancha pul keltiradi. XYZ — oylik BARQARORLIK,
	variatsiya koeffitsienti CV = σ/μ (X ≤ 10%, Y ≤ 25%, Z — undan katta):
	har oy muntazammi yoki tasodifiymi.

	Bitta oy tanlansa CV ma'nosiz bo'lgani uchun XYZ bazasi sifatida yilning
	barcha faol oylari olinadi (baza kamida 3 oy bo'lsin). Baza nechta oydan
	iborat ekani `xyz_oylar`/`xyz_davr` bilan frontendga ham beriladi.
	Bazaga savdosi juda kichik (XYZ_FAOL_ULUSH dan past) oylar kirmaydi.
	"""
	params = {"company": ctx.company, "yil": yil}
	if oylar:
		params["oylar"] = tuple(oylar)
	cc = _cc_sharti(ctx, params, "sii.cost_center")

	# Filial-filtr item darajasida bo'lgani uchun o'sha holda savdo ham
	# item-summalardan yig'iladi (KPI va mijozlar-jadvalidagi kabi).
	manba = "`tabSales Invoice` si"
	if ctx.cost_center:
		manba += " JOIN `tabSales Invoice Item` sii ON sii.parent = si.name"
	kalit_ust, nom_ust = "si.customer", "MAX(si.customer_name)"
	summa_ust = "SUM({0})".format(
		"sii.base_net_amount" if ctx.cost_center else "si.base_grand_total")
	qamrov = """FROM {manba}
		   WHERE si.company = %(company)s AND si.docstatus = 1""".format(manba=manba)

	oylik = frappe.db.sql(
		"""SELECT {kalit} AS kalit, {nom} AS nom, MONTH(si.posting_date) AS oy,
		          {summa} AS summa
		   {qamrov} AND YEAR(si.posting_date) = %(yil)s{cc}
		   GROUP BY {kalit}, MONTH(si.posting_date)""".format(
			kalit=kalit_ust, nom=nom_ust, summa=summa_ust,
			qamrov=qamrov, cc=cc), params, as_dict=True)

	davr_oylar = oylar or list(range(1, 13))
	birliklar = {}
	for r in oylik:
		b = birliklar.setdefault(r.kalit, {"nom": r.nom or r.kalit, "oylik": {}})
		b["oylik"][cint(r.oy)] = b["oylik"].get(cint(r.oy), 0.0) + flt(r.summa)

	# XYZ bazasi faqat haqiqiy savdo bo'lgan oylardan iborat (XYZ_FAOL_ULUSH).
	oy_jami = {}
	for b in birliklar.values():
		for o, s in b["oylik"].items():
			oy_jami[o] = oy_jami.get(o, 0.0) + s
	eng_kuchli = max(list(oy_jami.values()) + [0.0])
	faol_oylar = sorted(
		o for o, s in oy_jami.items()
		if eng_kuchli <= 0 or s / eng_kuchli * 100 >= XYZ_FAOL_ULUSH)
	baza_oylar = [o for o in davr_oylar if o in faol_oylar]
	xyz_davr = len(baza_oylar) >= 3
	if not xyz_davr:
		baza_oylar = faol_oylar

	qatorlar = []
	for kalit, b in birliklar.items():
		if not any(o in b["oylik"] for o in davr_oylar):
			continue  # davrda ishlanmagan mijoz reytingga kirmaydi
		qiymatlar = [flt(b["oylik"].get(o, 0.0)) for o in baza_oylar]
		orta = sum(qiymatlar) / len(qiymatlar) if qiymatlar else 0.0
		cv = None
		if orta > 0 and len(qiymatlar) > 1:
			dispersiya = sum((v - orta) ** 2 for v in qiymatlar) / len(qiymatlar)
			cv = dispersiya ** 0.5 / orta * 100
		qatorlar.append({
			"kalit": kalit,
			"name": b["nom"],
			"summa": sum(flt(b["oylik"].get(o, 0.0)) for o in davr_oylar),
			# CV yonida turadi — shuning uchun XYZ BAZASIDAGI faol oylar soni.
			"oylar": len([o for o in baza_oylar if b["oylik"].get(o)]),
			"cv": None if cv is None else round(cv, 1),
			"xyz": "Z" if cv is None else (
				"X" if cv <= XYZ_CHEGARA[0]
				else "Y" if cv <= XYZ_CHEGARA[1] else "Z"),
		})

	qatorlar.sort(key=lambda q: q["summa"], reverse=True)
	jami_savdo = sum(q["summa"] for q in qatorlar if q["summa"] > 0)
	kumulyativ = 0.0
	for q in qatorlar:
		if q["summa"] <= 0 or not jami_savdo:
			q.update({"ulush": 0.0, "kumulyativ": None, "abc": "C"})
			continue
		# Sinf chegaraga YETIB KELGUNCHAGI kumulyativ bo'yicha aniqlanadi —
		# shunda yakka yirik mijoz ham "C" emas, "A" bo'lib qoladi.
		q["abc"] = ("A" if kumulyativ < ABC_CHEGARA[0]
		            else "B" if kumulyativ < ABC_CHEGARA[1] else "C")
		kumulyativ += q["summa"] / jami_savdo * 100
		q["ulush"] = round(q["summa"] / jami_savdo * 100, 1)
		q["kumulyativ"] = round(kumulyativ, 1)

	def yigindi(kalit_f, sinflar):
		xarita = {s: {"sinf": s, "soni": 0, "savdo": 0.0} for s in sinflar}
		for q in qatorlar:
			h = xarita[kalit_f(q)]
			h["soni"] += 1
			h["savdo"] += q["summa"]
		for h in xarita.values():
			h["ulush"] = round(h["savdo"] / jami_savdo * 100, 1) if jami_savdo else 0.0
		return [xarita[s] for s in sinflar]

	matritsa = {}
	for q in qatorlar:
		katak = matritsa.setdefault(
			q["abc"] + q["xyz"], {"soni": 0, "savdo": 0.0})
		katak["soni"] += 1
		katak["savdo"] += q["summa"]

	# Yangi mijoz — birinchi hisob-fakturasi shu davrga tushgani.
	birinchi = dict(frappe.db.sql(
		"""SELECT {kalit}, MIN(si.posting_date)
		   {qamrov}{cc} GROUP BY {kalit}""".format(
			kalit=kalit_ust, qamrov=qamrov, cc=cc), params))
	davr_boshi = getdate("{0}-{1:02d}-01".format(yil, min(davr_oylar)))
	yangi = sum(
		1 for q in qatorlar
		if birinchi.get(q["kalit"]) and getdate(birinchi[q["kalit"]]) >= davr_boshi)

	# O'tgan yilning AYNAN shu oylari bilan solishtirish.
	otgan_params = dict(params, yil=yil - 1)
	otgan = cint(frappe.db.sql(
		"""SELECT COUNT(DISTINCT {kalit})
		   {qamrov} AND YEAR(si.posting_date) = %(yil)s{oy}{cc}""".format(
			kalit=kalit_ust, qamrov=qamrov, cc=cc,
			oy=" AND MONTH(si.posting_date) IN %(oylar)s" if oylar else ""),
		otgan_params)[0][0])

	return {
		"jami": len(qatorlar),
		"yangi": yangi,
		"doimiy": len(qatorlar) - yangi,
		"otgan_yil": otgan,
		"savdo": jami_savdo,
		"ortacha": jami_savdo / len(qatorlar) if qatorlar else 0.0,
		"xyz_oylar": len(baza_oylar),
		"xyz_davr": xyz_davr,
		"xyz_mavjud": len(baza_oylar) > 1,
		"abc": yigindi(lambda q: q["abc"], "ABC"),
		"xyz": yigindi(lambda q: q["xyz"], "XYZ"),
		"matritsa": matritsa,
		"top": qatorlar[:12],
	}, qatorlar


#: Cross-sell tahlilida nechta "asosiy" tovar guruhi qaraladi.
CROSS_GURUH = 10
#: Bitta mijozga nechta guruh tavsiya qilinadi (zaxira ham shu qadar guruhdan).
CROSS_TAVSIYA = 3
#: Ro'yxatda nechta mijoz qaytariladi.
CROSS_MIJOZ = 25


def _mediana(qiymatlar):
	"""Mediana — bitta yirik xarid o'rtachani buzib yubormasligi uchun."""
	q = sorted(qiymatlar)
	n = len(q)
	if not n:
		return 0.0
	return q[n // 2] if n % 2 else (q[n // 2 - 1] + q[n // 2]) / 2.0


def _cross_sell(ctx, yil, oylar, mijozlar):
	"""Cross-sell zaxirasi: mijoz asosiy tovar guruhlaridan qaysilarini
	OLMAYAPTI va bu taxminan qancha pul.

	"Asosiy guruh" — davrda eng ko'p savdo bergan CROSS_GURUH ta tovar guruhi.
	Zaxira "hamyondagi ulush" usuli bilan baholanadi: shu guruhni oladigan
	mijozlarda guruhning aylanmadagi ulushi MEDIANASI olinadi va mijozning
	davr aylanmasiga ko'paytiriladi — ya'ni "o'xshash mijozlar oborotining
	~9%ini shu guruhga sarflaydi". Yig'indi faqat eng katta CROSS_TAVSIYA ta
	yo'q guruh bo'yicha olinadi, shunda raqam real ish rejasiga bog'lanadi
	(hamma yo'q guruhni qo'shsa, baho haqiqatdan uzoqlashadi).

	Tovar guruhi belgilanmagan itemlar tahlilga kirmaydi — ularni tavsiya
	qilib ham bo'lmaydi.
	"""
	params = {"company": ctx.company, "yil": yil}
	oy = ""
	if oylar:
		params["oylar"] = tuple(oylar)
		oy = " AND MONTH(si.posting_date) IN %(oylar)s"
	cc = _cc_sharti(ctx, params, "sii.cost_center")

	qatorlar = frappe.db.sql(
		"""SELECT si.customer, it.item_group AS guruh,
		          SUM(sii.base_net_amount) AS summa
		   FROM `tabSales Invoice` si
		   JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		   JOIN `tabItem` it ON it.name = sii.item_code
		   WHERE si.company = %(company)s AND si.docstatus = 1
		     AND it.item_group IS NOT NULL
		     AND YEAR(si.posting_date) = %(yil)s{oy}{cc}
		   GROUP BY si.customer, it.item_group""".format(oy=oy, cc=cc),
		params, as_dict=True)
	if not qatorlar:
		return {"asosiy": [], "rows": []}

	savati = {}      # mijoz -> {guruh: summa}
	guruh_savdo = {}  # guruh -> davr savdosi
	for r in qatorlar:
		savati.setdefault(r.customer, {})[r.guruh] = flt(r.summa)
		guruh_savdo[r.guruh] = guruh_savdo.get(r.guruh, 0.0) + flt(r.summa)

	asosiy = [g for g, _ in sorted(
		guruh_savdo.items(), key=lambda x: x[1], reverse=True)[:CROSS_GURUH]]
	jami_c = {c: sum(g.values()) for c, g in savati.items()}

	# Har bir asosiy guruh uchun "odatdagi hamyon ulushi".
	ulush, xaridor = {}, {}
	for g in asosiy:
		ulushlar = [savati[c][g] / jami_c[c]
		            for c in savati if g in savati[c] and jami_c[c] > 0]
		ulush[g] = _mediana(ulushlar)
		xaridor[g] = len(ulushlar)

	sinf = {m["kalit"]: m for m in mijozlar}
	rows, zaxira_jami, qamrovlar, tor = [], 0.0, [], 0
	for c, guruhlari in savati.items():
		if jami_c[c] <= 0:
			continue
		bor = [g for g in asosiy if g in guruhlari]
		qamrovlar.append(len(bor))
		if len(bor) <= 2:
			tor += 1
		yoq = sorted(
			({"nom": g, "summa": jami_c[c] * ulush[g]} for g in asosiy if g not in guruhlari),
			key=lambda x: x["summa"], reverse=True)[:CROSS_TAVSIYA]
		if not yoq:
			continue
		zaxira = sum(x["summa"] for x in yoq)
		zaxira_jami += zaxira
		m = sinf.get(c) or {}
		rows.append({
			"customer": c,
			"name": m.get("name") or c,
			"abc": m.get("abc") or "C",
			"savdo": jami_c[c],
			"qamrov": len(bor),
			"yoq": yoq,
			"zaxira": zaxira,
		})

	rows.sort(key=lambda x: x["zaxira"], reverse=True)
	davr_savdo = sum(guruh_savdo.values())
	return {
		"asosiy_ulush": round(
			sum(guruh_savdo[g] for g in asosiy) / davr_savdo * 100, 1) if davr_savdo else 0.0,
		"asosiy": [{"nom": g, "savdo": guruh_savdo[g], "xaridor": xaridor[g],
		            "ulush": round(ulush[g] * 100, 1)} for g in asosiy],
		"asosiy_soni": len(asosiy),
		"mijozlar": len(savati),
		"tor": tor,
		"ortacha_qamrov": round(sum(qamrovlar) / len(qamrovlar), 1) if qamrovlar else 0,
		"zaxira_jami": zaxira_jami,
		"tavsiya_soni": CROSS_TAVSIYA,
		"rows": rows[:CROSS_MIJOZ],
	}


@frappe.whitelist()
def get_tahlil(filters=None):
	"""filters: company, yil, oylar (ro'yxat, bo'sh = butun yil).

	Qaytadi: KPI (savdo/vozvrat/tannarx/marja/dona/chek) + tovarlar-jadvali +
	mijozlar-jadvali (har birida tannarx va marja, JAMI bilan). Tannarx —
	SI'larning ombor-yozuvlaridan (SLE), mijoz kesimida ham xuddi shundan.
	"""
	filters = od._parse_filters(filters)
	ctx = _ctx(dict(filters))

	yil, oylar = _yil_oylar(ctx, filters)

	oy_sharti = ""
	params = {"company": ctx.company, "yil": yil}
	if oylar:
		oy_sharti = " AND MONTH(si.posting_date) IN %(oylar)s"
		params["oylar"] = tuple(oylar)
	cc = _cc_sharti(ctx, params, "sii.cost_center")
	wh = _wh_sharti(ctx, params, "sle.warehouse")

	SI_SCOPE = """FROM `tabSales Invoice` si
			WHERE si.company = %(company)s AND si.docstatus = 1
			  AND YEAR(si.posting_date) = %(yil)s{oy}""".format(oy=oy_sharti)

	def hisobla():
		# --- KPI ---
		if ctx.cost_center:
			bosh = frappe.db.sql(
				"""SELECT SUM(sii.base_net_amount) AS savdo,
				          SUM(CASE WHEN si.is_return = 1
				              THEN -sii.base_net_amount ELSE 0 END) AS vozvrat,
				          COUNT(DISTINCT si.name) AS docs,
				          COUNT(DISTINCT si.customer) AS mijozlar
				   FROM `tabSales Invoice` si
				   JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
				   WHERE si.company = %(company)s AND si.docstatus = 1
				     AND YEAR(si.posting_date) = %(yil)s{oy}{cc}""".format(
					oy=oy_sharti, cc=cc), params, as_dict=True)[0]
		else:
			bosh = frappe.db.sql(
				"""SELECT SUM(si.base_grand_total) AS savdo,
				          SUM(CASE WHEN si.is_return = 1 THEN -si.base_grand_total ELSE 0 END) AS vozvrat,
				          COUNT(*) AS docs, COUNT(DISTINCT si.customer) AS mijozlar
				""" + SI_SCOPE, params, as_dict=True)[0]

		dona = flt(frappe.db.sql(
			"""SELECT SUM(sii.qty) FROM `tabSales Invoice` si
			   JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
			   WHERE si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) = %(yil)s{oy}{cc}""".format(
				oy=oy_sharti, cc=cc),
			params)[0][0])

		# --- tovarlar (savdo + tannarx) ---
		savdo_t = frappe.db.sql(
			"""SELECT sii.item_code, SUM(sii.qty) AS qty,
			          SUM(sii.base_net_amount) AS summa
			   FROM `tabSales Invoice` si
			   JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
			   WHERE si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) = %(yil)s{oy}{cc}
			   GROUP BY sii.item_code ORDER BY summa DESC LIMIT 100
			""".format(oy=oy_sharti, cc=cc), params, as_dict=True)

		tannarx_t = {}
		for r in frappe.db.sql(
			"""SELECT sle.item_code, SUM(-sle.stock_value_difference) AS t
			   FROM `tabStock Ledger Entry` sle
			   JOIN `tabSales Invoice` si ON si.name = sle.voucher_no
			   WHERE sle.is_cancelled = 0 AND sle.voucher_type = 'Sales Invoice'{wh}
			     AND si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) = %(yil)s{oy}
			   GROUP BY sle.item_code""".format(oy=oy_sharti, wh=wh),
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
		if ctx.cost_center:
			savdo_m = frappe.db.sql(
				"""SELECT si.customer, si.customer_name,
				          SUM(sii.base_net_amount) AS summa,
				          COUNT(DISTINCT si.name) AS docs
				   FROM `tabSales Invoice` si
				   JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
				   WHERE si.company = %(company)s AND si.docstatus = 1
				     AND YEAR(si.posting_date) = %(yil)s{oy}{cc}
				   GROUP BY si.customer ORDER BY summa DESC LIMIT 100""".format(
					oy=oy_sharti, cc=cc), params, as_dict=True)
		else:
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
			   WHERE sle.is_cancelled = 0 AND sle.voucher_type = 'Sales Invoice'{wh}
			     AND si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) = %(yil)s{oy}
			   GROUP BY si.customer""".format(oy=oy_sharti, wh=wh),
			params, as_dict=True):
			tannarx_m[r.customer] = flt(r.t)

		# Oyna sex oqimi: tannarx zakazga bog'langan Material Issue'da
		mi_tannarx = _mi_tannarx(ctx, yil, oylar)
		for mijoz_kaliti, qiymat in mi_tannarx.items():
			tannarx_m[mijoz_kaliti] = tannarx_m.get(mijoz_kaliti, 0.0) + qiymat

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
			   WHERE sle.is_cancelled = 0 AND sle.voucher_type = 'Sales Invoice'{wh}
			     AND si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) = %(yil)s{oy}""".format(
				oy=oy_sharti, wh=wh),
			params)[0][0]) + sum(mi_tannarx.values())
		marja = savdo - tannarx

		yillar = [cint(r[0]) for r in frappe.db.sql(
			"""SELECT DISTINCT YEAR(posting_date) FROM `tabSales Invoice`
			   WHERE company = %s AND docstatus = 1 ORDER BY 1""",
			(ctx.company,)) if r[0]]

		# Oylik dinamika — so'nggi 3 yil (grafikda yillar alohida qatlam bo'ladi;
		# hozircha bitta yil bo'lsa bitta qatlam chiqadi)
		tanlangan3 = [y for y in (yillar or [yil]) if y <= yil][-3:] or [yil]
		y3_params = {"company": ctx.company, "yillar3": tuple(tanlangan3)}
		y3_cc = _cc_sharti(ctx, y3_params, "sii.cost_center")
		oylik_yillar = {y: [0.0] * 12 for y in tanlangan3}
		if ctx.cost_center:
			oylik_sql = """SELECT YEAR(si.posting_date) AS y,
			          MONTH(si.posting_date) AS oy,
			          SUM(sii.base_net_amount) AS savdo
			   FROM `tabSales Invoice` si
			   JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
			   WHERE si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) IN %(yillar3)s{cc}
			   GROUP BY YEAR(si.posting_date), MONTH(si.posting_date)""".format(cc=y3_cc)
		else:
			oylik_sql = """SELECT YEAR(si.posting_date) AS y,
			          MONTH(si.posting_date) AS oy,
			          SUM(si.base_grand_total) AS savdo
			   FROM `tabSales Invoice` si
			   WHERE si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) IN %(yillar3)s
			   GROUP BY YEAR(si.posting_date), MONTH(si.posting_date)"""
		for r in frappe.db.sql(oylik_sql, y3_params, as_dict=True):
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
			     AND YEAR(si.posting_date) IN %(yillar3)s{cc}
			   GROUP BY YEAR(si.posting_date), MONTH(si.posting_date)""".format(cc=y3_cc),
			y3_params, as_dict=True):
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
			   WHERE sle.is_cancelled = 0 AND sle.voucher_type = 'Sales Invoice'{wh}
			     AND si.company = %(company)s AND si.docstatus = 1
			     AND YEAR(si.posting_date) = %(yil)s
			   GROUP BY MONTH(si.posting_date)""".format(wh=wh),
			{"company": ctx.company, "yil": yil,
			 **({"wh": ctx.warehouse} if ctx.warehouse else {})}, as_dict=True):
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
		v_params = {"company": ctx.company, "yil": yil}
		v_cc = _cc_sharti(ctx, v_params, "sii.cost_center")
		if ctx.cost_center:
			vozvrat_sql = """SELECT MONTH(si.posting_date) AS oy,
			          SUM(ABS(sii.qty)) AS dona,
			          SUM(ABS(sii.base_net_amount)) AS summa
			   FROM `tabSales Invoice` si
			   JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
			   WHERE si.company = %(company)s AND si.docstatus = 1
			     AND si.is_return = 1 AND YEAR(si.posting_date) = %(yil)s{cc}
			   GROUP BY MONTH(si.posting_date)""".format(cc=v_cc)
		else:
			vozvrat_sql = """SELECT MONTH(posting_date) AS oy,
			          SUM(ABS(COALESCE(total_qty, 0))) AS dona,
			          SUM(ABS(base_grand_total)) AS summa
			   FROM `tabSales Invoice`
			   WHERE company = %(company)s AND docstatus = 1 AND is_return = 1
			     AND YEAR(posting_date) = %(yil)s
			   GROUP BY MONTH(posting_date)"""
		for r in frappe.db.sql(vozvrat_sql, v_params, as_dict=True):
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
		t_cc = _cc_sharti(ctx, trend_p, "posting_gl.cost_center").replace(
			"posting_gl.", "")  # GL Entry ustuni to'g'ridan-to'g'ri
		t_wh = _wh_sharti(ctx, trend_p, "sle.warehouse")
		gl_ochilish = flt(frappe.db.sql(
			"""SELECT SUM(debit - credit) FROM `tabGL Entry`
			   WHERE company = %(company)s AND is_cancelled = 0
			     AND account IN %(schyotlar)s
			     AND posting_date < %(boshi)s{cc}""".format(cc=t_cc),
			trend_p)[0][0]) if gl_schyotlar else 0.0
		gl_oy = {}
		if gl_schyotlar:
			for r in frappe.db.sql(
				"""SELECT MONTH(posting_date) AS oy, SUM(debit - credit) AS f
				   FROM `tabGL Entry`
				   WHERE company = %(company)s AND is_cancelled = 0
				     AND account IN %(schyotlar)s
				     AND YEAR(posting_date) = %(yil)s{cc}
				   GROUP BY MONTH(posting_date)""".format(cc=t_cc),
				trend_p, as_dict=True):
				if r.oy:
					gl_oy[cint(r.oy)] = flt(r.f)
		stok_ochilish = flt(frappe.db.sql(
			"""SELECT SUM(sle.stock_value_difference)
			   FROM `tabStock Ledger Entry` sle
			   JOIN `tabWarehouse` w ON w.name = sle.warehouse
			   WHERE w.company = %(company)s AND sle.is_cancelled = 0
			     AND sle.posting_date < %(boshi)s{wh}""".format(wh=t_wh),
			trend_p)[0][0])
		stok_oy = {}
		for r in frappe.db.sql(
			"""SELECT MONTH(sle.posting_date) AS oy,
			          SUM(sle.stock_value_difference) AS f
			   FROM `tabStock Ledger Entry` sle
			   JOIN `tabWarehouse` w ON w.name = sle.warehouse
			   WHERE w.company = %(company)s AND sle.is_cancelled = 0
			     AND YEAR(sle.posting_date) = %(yil)s{wh}
			   GROUP BY MONTH(sle.posting_date)""".format(wh=t_wh),
			trend_p, as_dict=True):
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

		# Mijoz reytingi va cross-sell bitta mijoz-ro'yxatidan oziqlanadi.
		mijoz_reyting, mijoz_qatorlar = _mijoz_reyting(ctx, yil, oylar)

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
				# Qatorlar JAMI bilan bir xil konvensiyada — XOM GL belgisi
				# bilan beriladi, shunda qatorlarni qo'shsa aynan "UMUMIY
				# BALANS" chiqadi. Ilgari mijoz-avans musbatga aylantirilib,
				# kreditorga abs() qo'llanardi; natijada karta o'zi bilan
				# bog'lanmasdi (Kattaqo'rg'on: 1 158 919 farq, Samarqand
				# Diller: 10 918 517 — audit 2026-09-11).
				"mijoz": od._money_out(mijoz_bal),
				"mijoz_label": (
					"Mijoz avanslari"
					if mijoz_bal and all(flt(v) <= 0.005 for v in mijoz_bal.values())
					else "Mijozlar balansi"),
				"kreditor": od._money_out(kreditor),
				# Kreditorlik schyoti DEBET qoldiqda bo'lsa (to'lov bor,
				# hujjat yo'q) — abs() uni yashirardi; endi belgilanadi.
				"kreditor_ogoh": bool(kreditor) and any(
					flt(v) > 0.005 for v in kreditor.values()),
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
			"mijoz_reyting": mijoz_reyting,
			"cross_sell": _cross_sell(ctx, yil, oylar, mijoz_qatorlar),
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
	cc_sii = _cc_sharti(ctx, params, "sii.cost_center")
	cc_pe = _cc_sharti(ctx, params, "pe.cost_center")

	if ctx.cost_center:
		# Filial rejimi: item-darajada (netto) — sarlavhada filial yo'q
		mijozlar = frappe.db.sql(
			"""
			SELECT si.customer, si.customer_name,
			       SUM(sii.base_net_amount) AS savdo,
			       COUNT(DISTINCT si.name) AS docs
			FROM `tabSales Invoice` si
			JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
			WHERE si.company = %(company)s AND si.docstatus = 1
			  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s{cc}
			GROUP BY si.customer
			ORDER BY savdo DESC
			LIMIT 20
			""".format(cc=cc_sii), params, as_dict=True)
	else:
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
			  AND pe.posting_date BETWEEN %(from_date)s AND %(to_date)s{cc}
			  AND pe.party IN %(mijozlar)s
			GROUP BY pe.party
			""".format(cc=cc_pe),
			dict(params, mijozlar=tuple(m.customer for m in mijozlar)),
			as_dict=True):
			tolovlar[r.party] = flt(r.amount)

	if ctx.cost_center:
		jami_savdo = flt(frappe.db.sql(
			"""
			SELECT SUM(sii.base_net_amount) FROM `tabSales Invoice` si
			JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
			WHERE si.company = %(company)s AND si.docstatus = 1
			  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s{cc}
			""".format(cc=cc_sii), params)[0][0])
	else:
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
		  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s{cc}
		GROUP BY sii.item_code
		ORDER BY summa DESC
		LIMIT 20
		""".format(cc=cc_sii), params, as_dict=True)

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
