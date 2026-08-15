# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""Oyna sex — boshqaruv dashboardi uchun backend (server-side aggregation).

Bu modul "Oyna sex" (oyna kesish sexi) kompaniyasi uchun boshqaruv
dashboardining barcha hisob-kitoblarini bajaradi. Barcha summalar ERPNext'ning
o'z hujjatlaridan olinadi — hech qanday demo/hardcode qiymat yo'q.

=======================================================================
MA'LUMOT MANBALARI (source of truth)
=======================================================================
ASOSIY QOIDA: dashboarddagi HAR BIR raqam faqat TASDIQLANGAN (submit qilingan)
hujjatlardan yoki ular yaratgan GL yozuvlaridan olinadi. Shuning uchun:
  • sotuv          → ERPNext «Profit and Loss Statement» bilan aynan mos;
  • qarzdor/haqdor → «Akt Sverka» bilan aynan mos (ishorasi bundan mustasno);
  • qoldiqlar      → «General Ledger» / `get_balance_on()` bilan aynan mos.

| Ko'rsatkich          | Manba                            | Valyuta      |
|----------------------|----------------------------------|--------------|
| Sotuv                | `Sales Invoice` (docstatus = 1)  | hujjatniki   |
| Xarajatlar           | `GL Entry` — root_type = Expense | UZS (P&L kursi)|
| Mijoz qarzi          | `GL Entry` — Receivable          | hisobniki    |
| Yetkazib b. qarzi    | `GL Entry` — Payable             | hisobniki    |
| Kassa / bank         | `Mode of Payment Account` -> GL  | hisobniki    |
| Zaxira (ombor)       | `Stock Ledger Entry`             | kompaniyaniki|
| Kvadrat (m²)         | `Sales Order.custom_jami_kvadrat`| —            |
| Zakazlar portfeli    | `Sales Order` (docstatus = 0)    | hujjatniki   |

=======================================================================
QABUL QILINGAN FARAZLAR (assumptions) — muhim!
=======================================================================
1. SOTUV MANBAI. Oyna sexda savdo `Sales Order` orqali yuritiladi
   ("Zakaz olindi" → "Tayyor" → "Topshirildi" workflow). Sales Invoice faqat
   buyurtma submit bo'lgandan keyin (`oyna_order.py`) yaratiladi.

   Dashboard SOTUV sifatida faqat GL Income aylanmasini oladi, ya'ni
   rasmiylashtirilgan hisob-fakturalarni — bu ERPNext P&L bilan aynan bir xil
   bo'lishi uchun. Hali submit qilinmagan zakazlar (docstatus = 0) SOTUVGA
   QO'SHILMAYDI; ular «Zakazlar portfeli» degan alohida blokda, "hali
   rasmiylashtirilmagan" deb belgilangan holda ko'rsatiladi.

   AMALIY OQIBAT: zakaz «Topshirildi» holatiga o'tkazilmaguncha u sotuvda ham,
   mijoz qarzida ham ko'rinmaydi. Sotuv raqamlari past ko'rinsa — birinchi
   navbatda zakazlar submit qilinganini tekshirish kerak.

2. VALYUTA. Umumiy qoida — konvertatsiya YO'Q, har bir summa O'Z valyutasida:
     • mijoz qarzi, so'm kassalari, bank      -> UZS (hisob valyutasi);
     • tannarx, ombor zaxirasi                -> USD (hisob valyutasi);
     • sotuv                                  -> hujjat (Sales Invoice) valyutasi.

   ISTISNO — XARAJATLAR va SOF FOYDA. Bular UZS da ko'rsatiladi: xarajat
   hisoblari USD da yuritilgani uchun so'mdagi tushum bilan yonma-yon
   solishtirib bo'lmasdi. Kurs ERPNext P&L hisobotidagi bilan bir xil olinadi
   (davrning oxirgi sanasidagi bitta kurs) — qarang `_money_to_presentation`.

   Sotuv ataylab GL'dan emas, hujjatdan olinadi: `Sales - Os` tushum hisobi
   kompaniya valyutasida (USD) yuritiladi, shuning uchun GL'dan olinsa so'mdagi
   savdo dollarda ko'rinardi. Hujjatdan olinsa UZS'da yozilgan faktura UZS'da
   chiqadi.

   Pul qiymati API'da HAR DOIM ro'yxat: [{"currency": "UZS", "amount": ...}, ...].
   Bitta ko'rsatkich bir nechta valyutadan iborat bo'lishi mumkin (masalan naqd
   kassa: so'm + dollar) — ular qo'shilmaydi, alohida ko'rsatiladi. Shu sababli
   taqqoslash foizi ham har bir valyuta ichida alohida hisoblanadi.

   Grafiklarda faqat ENG KATTA ulushli valyuta chiziladi — turli valyutadagi
   summalarni bitta o'qda yoki bitta doirada solishtirib bo'lmaydi.

3. MATERIAL TANNARXI. Buyurtma submit bo'lmaguncha ERPNext'da Stock Entry
   yaratilmaydi, ya'ni haqiqiy COGS provodkasi bo'lmaydi. Ochiq buyurtmalar
   uchun tannarx `Oyna Sarflangan Tovar` jadvalidagi miqdorni joriy
   `Bin.valuation_rate` bo'yicha baholash orqali chiqariladi va UI'da
   "baholangan" deb belgilanadi. Submit bo'lgan hujjatlar uchun esa GL'dagi
   haqiqiy COGS ishlatiladi (`get_financials`).

4. KASSA / BANK. `Mode of Payment Account` — ilovadagi kassa hisoblarining
   yagona ro'yxati (DDS hisoboti ham shundan foydalanadi). Oyna sexda barcha
   kassa hisoblari `account_type = Cash`, shuning uchun naqd/banksiz ajratish
   hisob nomi bo'yicha (BANK_HINTS) amalga oshiriladi. Har bir hisob
   dashboardda alohida ko'rsatiladi, shuning uchun tasnif shaffof.

5. QARZ YOSHI (aging). Oyna sexda mijoz qarzi Sales Invoice orqali emas,
   ochilish qoldig'i (Journal Entry) va Kassa to'lovlari orqali yuritiladi —
   ya'ni "due date" yo'q. Shu sababli aging GL yozuvlari ustida FIFO usulida
   hisoblanadi: har bir debet (qarz) yozuvi keyingi kreditlar (to'lovlar) bilan
   navbat bo'yicha yopiladi, yopilmagan qoldiq esa yoshi bo'yicha guruhlanadi.
   `OVERDUE_AFTER_DAYS` dan katta yoshdagi qoldiq "muddati o'tgan" deb olinadi.

6. FILTRLAR. `Ombor` filtri faqat zaxira va material sarfiga ta'sir qiladi
   (xizmat qatorlarida ombor ko'rsatilmaydi, shuning uchun sotuvga qo'llanmaydi).
   `Kassa hisobi` filtri pul oqimi bo'limiga qo'llanadi.

=======================================================================
RUXSATLAR
=======================================================================
Raw SQL Frappe'ning User Permission qatlamini chetlab o'tadi, shuning uchun:
  • kompaniya `report_utils.get_effective_company()` orqali majburlanadi;
  • har bir bo'lim tegishli DocType'ga `read` ruxsati bo'lsagina hisoblanadi,
    aks holda `{"permitted": False}` qaytadi va UI o'sha blokni yashiradi.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, date_diff, flt, getdate, nowdate

from akfa_diller.akfa_diller.api.report_utils import get_allowed_companies, get_effective_company

# --------------------------------------------------------------------------
# Konstantalar
# --------------------------------------------------------------------------

#: Dashboard birinchi navbatda shu kompaniyani tanlaydi (agar ruxsat bo'lsa).
PREFERRED_COMPANY = "Oyna sex"

#: Taqdimot valyutasi (standart). Kurs topilmasa kompaniya valyutasiga qaytadi.
DEFAULT_DISPLAY_CURRENCY = "UZS"

#: Shu yoshdan katta ochiq qarz "muddati o'tgan" deb hisoblanadi (kun).
OVERDUE_AFTER_DAYS = 30

#: Zaxira "kam qolgan" deb belgilanadigan chegara — necha kunlik sarfga yetadi.
LOW_STOCK_DAYS_COVER = 14

#: Kassa hisoblarini "bank/plastik" deb tasniflash uchun nom bo'laklari.
#: Oyna sexda barcha kassa hisoblari account_type = "Cash" bo'lgani uchun kerak.
BANK_HINTS = ("bank", "банк", "р/с", "р/c", "plastik", "пластик", "karta", "карта", "card")

#: COGS/baholash hisoblari — bular "operatsion xarajat" emas, tannarx.
COGS_ACCOUNT_TYPES = (
	"Cost of Goods Sold",
	"Stock Adjustment",
	"Expenses Included In Valuation",
	"Expenses Included In Asset Valuation",
)

#: Og'ir so'rovlar uchun kesh muddati (soniya). `refresh=1` bilan chetlab o'tiladi.
CACHE_TTL = 60

#: «Sof foyda» kartasi ERPNext «Profit and Loss Statement» hisobotidan
#: shu taqdimot valyutasida olinadi (hisobotdagi raqam bilan aynan mos bo'lishi uchun).
PNL_PRESENTATION_CURRENCY = "UZS"

#: Tasdiqlangan hujjat = submit qilingan. Dashboardning BARCHA ko'rsatkichlari
#: shu hujjatlardan (yoki ular yaratgan GL yozuvlaridan) olinadi.
DOCSTATUS_SUBMITTED = 1
DOCSTATUS_DRAFT = 0

#: Dashboard bo'limlari — `get_dashboard(sections=[...])` uchun.
SECTIONS = (
	"overview",
	"sales",
	"orders",
	"inventory",
	"receivables",
	"payables",
	"cash",
	"expenses",
)


# --------------------------------------------------------------------------
# Kontekst (filtrlarni yechish)
# --------------------------------------------------------------------------


def _parse_filters(filters):
	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except (ValueError, TypeError):
			filters = {}
	return frappe._dict(filters or {})


def _resolve_company(filters):
	"""Ruxsat etilgan kompaniyani aniqlash (User Permission majburiy)."""
	company = get_effective_company(filters)
	if company:
		return company

	allowed = get_allowed_companies()
	if allowed:
		return allowed[0]

	# Cheklanmagan foydalanuvchi: Oyna sex bo'lsa — o'sha, aks holda birinchisi.
	if frappe.db.exists("Company", PREFERRED_COMPANY):
		return PREFERRED_COMPANY

	return frappe.db.get_value("Company", {}, "name", order_by="creation asc")


def _period_bounds(filters):
	"""Tanlangan davr chegaralarini qaytaradi (from_date, to_date)."""
	preset = filters.get("period") or "this_month"
	today = getdate(nowdate())

	if preset == "custom":
		from_date = getdate(filters.get("from_date") or today)
		to_date = getdate(filters.get("to_date") or today)
		if from_date > to_date:
			from_date, to_date = to_date, from_date
		return from_date, to_date

	if preset == "today":
		return today, today
	if preset == "yesterday":
		day = getdate(add_days(today, -1))
		return day, day
	if preset == "this_week":
		return getdate(add_days(today, -today.weekday())), today
	if preset == "last_week":
		start = getdate(add_days(today, -today.weekday() - 7))
		return start, getdate(add_days(start, 6))
	if preset == "last_month":
		first_of_this = today.replace(day=1)
		start = getdate(add_months(first_of_this, -1))
		return start, getdate(add_days(first_of_this, -1))
	if preset == "this_year":
		return today.replace(month=1, day=1), today
	if preset == "last_year":
		return today.replace(year=today.year - 1, month=1, day=1), today.replace(
			year=today.year - 1, month=12, day=31
		)

	# this_month (standart)
	return today.replace(day=1), today


def _previous_period(preset, from_date, to_date):
	"""Taqqoslash uchun oldingi ekvivalent davr.

	Oy/yil presetlari kalendar bo'yicha suriladi (Avg 1–13 → Iyul 1–13),
	qolganlari esa xuddi shu uzunlikdagi darhol oldingi oraliq bo'ladi.
	"""
	if preset in ("this_month", "last_month"):
		return getdate(add_months(from_date, -1)), getdate(add_months(to_date, -1))
	if preset in ("this_year", "last_year"):
		return getdate(add_months(from_date, -12)), getdate(add_months(to_date, -12))

	days = date_diff(to_date, from_date) + 1
	return getdate(add_days(from_date, -days)), getdate(add_days(from_date, -1))


def build_context(filters=None):
	"""Barcha endpointlar uchun umumiy kontekst.

	Diqqat: valyuta konvertatsiyasi YO'Q — har bir summa o'z valyutasida
	qaytariladi (`_money_add` / `_money_out`).
	"""
	filters = _parse_filters(filters)
	company = _resolve_company(filters)
	if not company:
		frappe.throw(_("Kompaniya topilmadi. Iltimos, administratorga murojaat qiling."))

	preset = filters.get("period") or "this_month"
	from_date, to_date = _period_bounds(filters)
	prev_from, prev_to = _previous_period(preset, from_date, to_date)

	return frappe._dict(
		{
			"company": company,
			"company_currency": frappe.get_cached_value("Company", company, "default_currency")
			or "USD",
			"period": preset,
			"from_date": from_date,
			"to_date": to_date,
			"prev_from": prev_from,
			"prev_to": prev_to,
			"warehouse": filters.get("warehouse"),
			"cost_center": filters.get("cost_center"),
			"customer": filters.get("customer"),
			"cash_account": filters.get("cash_account"),
			"refresh": cint(filters.get("refresh")),
			"filters": filters,
		}
	)


# --------------------------------------------------------------------------
# Pul: har bir summa O'Z valyutasida
# --------------------------------------------------------------------------
#
# Kurs konvertatsiyasi ATAYLAB yo'q. Bitta ko'rsatkich bir nechta valyutadan
# iborat bo'lishi mumkin (masalan naqd kassa: so'm + dollar), shuning uchun pul
# qiymati {valyuta: summa} lug'ati ko'rinishida yuritiladi va API'ga
# [{"currency": ..., "amount": ...}] ro'yxati bo'lib chiqadi.


def _money_add(bag, currency, amount):
	"""To'plamga summa qo'shadi (valyuta bo'yicha guruhlab)."""
	if currency:
		bag[currency] = bag.get(currency, 0.0) + flt(amount)
	return bag


def _money_sum(*bags):
	total = {}
	for bag in bags:
		for currency, amount in (bag or {}).items():
			_money_add(total, currency, amount)
	return total


def _money_neg(bag):
	return {currency: -flt(amount) for currency, amount in (bag or {}).items()}


def _money_diff(left, right):
	"""left − right (valyutalar bo'yicha)."""
	return _money_sum(left, _money_neg(right))


def _money_out(bag):
	"""API uchun: [{"currency", "amount"}] — kattadan kichikka, nollarsiz."""
	items = [
		{"currency": currency, "amount": flt(amount)}
		for currency, amount in (bag or {}).items()
		if abs(flt(amount)) > 0.005
	]
	return sorted(items, key=lambda row: -abs(row["amount"]))


def _money_get(bag, currency):
	return flt((bag or {}).get(currency))


def _money_to_presentation(bag, to_date):
	"""{valyuta: summa} → {UZS: summa} — barcha valyutalar bittaga keltiriladi.

	Kurs ERPNext hisobotlaridagi bilan AYNAN bir xil olinadi: davrning oxirgi
	sanasidagi bitta kurs (`erpnext/accounts/report/utils.py`,
	`convert_to_presentation_currency`). Shu sababli «Xarajatlar» «Sof foyda»
	kartasi va P&L hisoboti bilan mos tushadi — boshqa kurs ishlatilsa
	"tushum − xarajat = sof foyda" tengligi buziladi.
	"""
	from erpnext.accounts.report.utils import convert

	total = 0.0
	for currency, amount in (bag or {}).items():
		if currency == PNL_PRESENTATION_CURRENCY:
			total += flt(amount)
		else:
			total += convert(flt(amount), PNL_PRESENTATION_CURRENCY, currency, to_date)
	return {PNL_PRESENTATION_CURRENCY: total}


def _margin(amount_bag, revenue_bag, currency):
	"""Bitta valyuta ichidagi foiz marja. Ma'nosiz bo'lsa None."""
	revenue = _money_get(revenue_bag, currency)
	if not revenue:
		return None
	pct = _money_get(amount_bag, currency) / revenue * 100.0
	return flt(pct, 1) if abs(pct) <= 999 else None


def _delta(current, previous):
	"""Valyutalar bo'yicha o'zgarish: {valyuta: {previous, diff, pct, ...}}.

	Konvertatsiya yo'q, shuning uchun har bir valyuta alohida taqqoslanadi.
	"""
	result = {}
	for currency in set(list((current or {}).keys()) + list((previous or {}).keys())):
		now = _money_get(current, currency)
		before = _money_get(previous, currency)
		diff = now - before
		pct = (diff / abs(before) * 100.0) if before else (100.0 if now else 0.0)
		result[currency] = {
			"previous": before,
			"diff": diff,
			"pct": flt(pct, 2),
			"direction": "up" if diff > 0 else ("down" if diff < 0 else "flat"),
			"comparable": bool(before),
		}
	return result


# --------------------------------------------------------------------------
# Ruxsat va kesh
# --------------------------------------------------------------------------


def _can(*doctypes):
	"""Ko'rsatilgan barcha DocType'larga `read` ruxsati bormi."""
	return all(frappe.has_permission(dt, "read") for dt in doctypes)


def _denied(*doctypes):
	return {
		"permitted": False,
		"message": _("Sizda ushbu ma'lumotni ko'rish huquqi yo'q ({0}).").format(", ".join(doctypes)),
	}


def _cached(ctx, key, builder):
	"""Qimmat hisob-kitoblarni qisqa muddatga keshlash (foydalanuvchi bo'yicha)."""
	# Kalitga kontekstning natijaga ta'sir qiladigan barcha qismlari kiradi.
	# (Ro'yxatdan "::".join qilingani uchun maydon qo'shilsa/olib tashlansa
	# format qatori bilan mos kelmay qolish xavfi yo'q.)
	cache_key = "::".join(
		str(part)
		for part in (
			"oyna_dashboard",
			frappe.session.user,
			key,
			ctx.company,
			ctx.from_date,
			ctx.to_date,
			ctx.warehouse or "",
			ctx.cost_center or "",
			ctx.customer or "",
			ctx.cash_account or "",
		)
	)
	cache = frappe.cache()
	if not ctx.refresh:
		cached = cache.get_value(cache_key)
		if cached is not None:
			return cached

	value = builder()
	cache.set_value(cache_key, value, expires_in_sec=CACHE_TTL)
	return value


# --------------------------------------------------------------------------
# Hisoblar (Chart of Accounts) yordamchilari
# --------------------------------------------------------------------------


def _accounts_by(company, **conditions):
	filters = {"company": company, "is_group": 0}
	filters.update(conditions)
	return frappe.get_all(
		"Account",
		filters=filters,
		fields=["name", "account_name", "account_type", "root_type", "account_currency"],
		ignore_permissions=True,
	)


def get_receivable_accounts(company):
	return [a.name for a in _accounts_by(company, account_type="Receivable")]


def get_payable_accounts(company):
	return [a.name for a in _accounts_by(company, account_type="Payable")]


def get_cash_accounts(company, only=None):
	"""Kassa hisoblari — `Mode of Payment Account` (DDS hisoboti bilan bir xil).

	Agar Mode of Payment sozlanmagan bo'lsa, account_type = Cash/Bank hisoblariga
	qaytadi, shunda dashboard bo'sh qolib ketmaydi.
	"""
	rows = frappe.get_all(
		"Mode of Payment Account",
		filters={"company": company},
		fields=["parent as mode_of_payment", "default_account"],
		ignore_permissions=True,
	)
	names = [r.default_account for r in rows if r.default_account]
	mode_by_account = {r.default_account: r.mode_of_payment for r in rows if r.default_account}

	if not names:
		fallback = [a for a in _accounts_by(company) if a.account_type in ("Cash", "Bank")]
		names = [a.name for a in fallback]

	if not names:
		return []

	accounts = frappe.get_all(
		"Account",
		filters={"name": ("in", list(set(names))), "is_group": 0},
		fields=["name", "account_name", "account_type", "account_currency"],
		ignore_permissions=True,
	)

	result = []
	for acc in accounts:
		haystack = "{} {}".format(acc.name, mode_by_account.get(acc.name) or "").lower()
		is_bank = acc.account_type == "Bank" or any(h in haystack for h in BANK_HINTS)
		result.append(
			frappe._dict(
				{
					"name": acc.name,
					"account_name": acc.account_name,
					"currency": acc.account_currency,
					"mode_of_payment": mode_by_account.get(acc.name),
					"kind": "bank" if is_bank else "cash",
				}
			)
		)

	if only:
		result = [a for a in result if a.name == only]

	return sorted(result, key=lambda a: (a.kind, a.name))


def get_expense_accounts(company, include_cogs=False):
	"""Xarajat hisoblari.

	`include_cogs=True` — root_type = Expense bo'lgan BARCHA hisoblar, ya'ni
	ERPNext P&L hisobotidagi «Total Expense (Debit)» bilan bir xil to'plam.
	`include_cogs=False` — faqat operatsion xarajatlar (tannarx va baholash
	hisoblarisiz); P&L ladderidagi «Indirect Expenses» uchun ishlatiladi.
	"""
	accounts = _accounts_by(company, root_type="Expense")
	if include_cogs:
		return accounts
	return [a for a in accounts if a.account_type not in COGS_ACCOUNT_TYPES]


def get_cogs_accounts(company):
	return [
		a.name
		for a in _accounts_by(company, root_type="Expense")
		if a.account_type in ("Cost of Goods Sold", "Stock Adjustment")
	]


def get_income_accounts(company):
	return [a.name for a in _accounts_by(company, root_type="Income")]


# --------------------------------------------------------------------------
# GL yordamchilari
# --------------------------------------------------------------------------


def _gl_extra_conditions(ctx, params):
	"""Cost center (filial) filtri — barcha GL so'rovlariga qo'llanadi."""
	if not ctx.cost_center:
		return ""
	lft, rgt = frappe.db.get_value("Cost Center", ctx.cost_center, ["lft", "rgt"]) or (None, None)
	if lft is None:
		params["cost_center"] = ctx.cost_center
		return " AND gle.cost_center = %(cost_center)s"
	params["cc_lft"], params["cc_rgt"] = lft, rgt
	return """ AND gle.cost_center IN (
		SELECT cc.name FROM `tabCost Center` cc WHERE cc.lft >= %(cc_lft)s AND cc.rgt <= %(cc_rgt)s
	)"""


def _income_customer_condition(ctx, params):
	"""Mijoz filtri uchun: GL tushum qatorlarini hujjat MIJOZI bo'yicha cheklash.

	Income hisoblaridagi GL yozuvlarida `party` bo'lmaydi (party faqat debitor
	qatorida bo'ladi), shuning uchun filtr hujjat (Sales Invoice) orqali
	qo'llanadi — natija baribir GL'dan olinadi va P&L mantiqidan chiqmaydi.
	"""
	if not ctx.customer:
		return ""
	params["customer"] = ctx.customer
	return """ AND gle.voucher_no IN (
		SELECT si.name FROM `tabSales Invoice` si WHERE si.customer = %(customer)s
	)"""


def _gl_balance(ctx, accounts, to_date, from_date=None, party_type=None, customer_scope=False):
	"""Hisoblar bo'yicha (debet − kredit) qoldiq yoki aylanma.

	Natija — {valyuta: summa}. Har bir hisob O'Z valyutasida hisoblanadi,
	konvertatsiya yo'q; shuning uchun natijada bir nechta valyuta bo'lishi mumkin.
	`from_date` berilmasa — `to_date` holatiga kumulyativ qoldiq.
	"""
	if not accounts:
		return {}

	params = {"company": ctx.company, "accounts": tuple(accounts), "to_date": to_date}
	conditions = ""
	if from_date:
		params["from_date"] = from_date
		conditions += " AND gle.posting_date >= %(from_date)s"
	if party_type:
		params["party_type"] = party_type
		conditions += " AND gle.party_type = %(party_type)s"
	if customer_scope:
		conditions += _income_customer_condition(ctx, params)
	conditions += _gl_extra_conditions(ctx, params)

	rows = frappe.db.sql(
		"""
		SELECT gle.account_currency AS currency,
		       SUM(gle.debit_in_account_currency - gle.credit_in_account_currency) AS amount
		FROM `tabGL Entry` gle
		WHERE gle.company = %(company)s
		  AND gle.is_cancelled = 0
		  AND gle.account IN %(accounts)s
		  AND gle.posting_date <= %(to_date)s
		  {conditions}
		GROUP BY gle.account_currency
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)

	bag = {}
	for row in rows:
		_money_add(bag, row.currency, row.amount)
	return bag


# --------------------------------------------------------------------------
# Sotuv (Sales Order) yordamchilari
# --------------------------------------------------------------------------


def _so_conditions(ctx, params, alias="so"):
	conditions = ""
	if ctx.customer:
		params["customer"] = ctx.customer
		conditions += " AND {}.customer = %(customer)s".format(alias)
	return conditions


def _sales_totals(ctx, from_date, to_date, docstatus=DOCSTATUS_SUBMITTED):
	"""Sales Order bo'yicha jami: summa, soni, m², mijozlar soni.

	`docstatus=1` — tasdiqlangan zakazlar (dashboardning asosiy rejimi),
	`docstatus=0` — rasmiylashtirilmagan portfel (faqat «Zakazlar portfeli» bloki).
	"""
	params = {
		"company": ctx.company,
		"from_date": from_date,
		"to_date": to_date,
		"docstatus": docstatus,
	}
	conditions = _so_conditions(ctx, params)

	rows = frappe.db.sql(
		"""
		SELECT so.currency,
		       COUNT(*) AS orders,
		       SUM(so.grand_total) AS amount,
		       SUM(so.base_grand_total) AS base_amount,
		       SUM(IFNULL(so.custom_jami_kvadrat, 0)) AS sqm,
		       COUNT(DISTINCT so.customer) AS customers
		FROM `tabSales Order` so
		WHERE so.company = %(company)s
		  AND so.docstatus = %(docstatus)s
		  AND so.transaction_date BETWEEN %(from_date)s AND %(to_date)s
		  {conditions}
		GROUP BY so.currency
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)

	total = frappe._dict({"amount": {}, "orders": 0, "sqm": 0.0, "customers": 0})
	for row in rows:
		_money_add(total.amount, row.currency, row.amount)
		total.orders += cint(row.orders)
		total.sqm += flt(row.sqm)
		total.customers = max(total.customers, cint(row.customers))
	return total


def _revenue(ctx, from_date, to_date):
	"""Tasdiqlangan sotuv — `Sales Invoice` ning O'Z valyutasida.

	Qaytadi: {"gross": sotuv, "returns": vozvrat, "net": sotuv − vozvrat}.
	Vozvrat — `is_return = 1` bo'lgan hisob-faktura (kredit-nota); ERPNext'da
	uning `grand_total` i manfiy bo'ladi, shuning uchun musbatga aylantiriladi.

	GL Income hisobi kompaniya valyutasida (USD) yuritilgani uchun undan olsak
	so'mdagi savdo dollarda ko'rinardi — shuning uchun summa hujjatdan olinadi.
	"""
	params = {"company": ctx.company, "from_date": from_date, "to_date": to_date}
	conditions = ""
	if ctx.customer:
		params["customer"] = ctx.customer
		conditions += " AND si.customer = %(customer)s"

	rows = frappe.db.sql(
		"""
		SELECT si.currency, si.is_return, SUM(si.grand_total) AS amount
		FROM `tabSales Invoice` si
		WHERE si.company = %(company)s
		  AND si.docstatus = 1
		  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  {conditions}
		GROUP BY si.currency, si.is_return
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)

	gross, returns = {}, {}
	for row in rows:
		if cint(row.is_return):
			_money_add(returns, row.currency, -flt(row.amount))
		else:
			_money_add(gross, row.currency, row.amount)

	return frappe._dict(
		{"gross": gross, "returns": returns, "net": _money_diff(gross, returns)}
	)


def _pnl_net_profit(ctx, from_date, to_date):
	"""ERPNext «Profit and Loss Statement» dagi «Profit for the year».

	Hisobot `presentation_currency = UZS` bilan chaqiriladi — konvertatsiyani
	ERPNext'ning o'zi bajaradi, shuning uchun karta hisobot bilan AYNAN bir xil
	raqamni ko'rsatadi. Kompaniya doim `ctx.company` (Oyna sex) bo'ladi.
	"""
	from erpnext.accounts.report.profit_and_loss_statement.profit_and_loss_statement import (
		execute as pnl_execute,
	)

	filters = frappe._dict(
		{
			"company": ctx.company,
			"filter_based_on": "Date Range",
			"period_start_date": from_date,
			"period_end_date": to_date,
			"periodicity": "Monthly",
			"accumulated_values": 0,
			"presentation_currency": PNL_PRESENTATION_CURRENCY,
		}
	)

	for row in pnl_execute(filters)[1] or []:
		if not isinstance(row, dict):
			continue
		label = (row.get("account_name") or str(row.get("account") or "")).strip("'")
		if label == "Profit for the year":
			return _money_add({}, PNL_PRESENTATION_CURRENCY, row.get("total"))

	return {}


def _cogs(ctx, from_date, to_date):
	"""Tannarx — GL COGS / Stock Adjustment aylanmasi (hisob valyutasida)."""
	return _gl_balance(ctx, get_cogs_accounts(ctx.company), to_date, from_date=from_date)


def _invoice_stats(ctx, from_date, to_date):
	"""Davrdagi tasdiqlangan hisob-fakturalar soni va mijozlar soni."""
	params = {"company": ctx.company, "from_date": from_date, "to_date": to_date}
	conditions = ""
	if ctx.customer:
		params["customer"] = ctx.customer
		conditions += " AND si.customer = %(customer)s"

	row = frappe.db.sql(
		"""
		SELECT SUM(IF(si.is_return = 1, 0, 1)) AS invoices,
		       SUM(IF(si.is_return = 1, 1, 0)) AS returns,
		       COUNT(DISTINCT si.customer) AS customers,
		       SUM(IFNULL(si.custom_jami_kvadrat, 0)) AS sqm
		FROM `tabSales Invoice` si
		WHERE si.company = %(company)s
		  AND si.docstatus = 1
		  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  {conditions}
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)
	row = row[0] if row else frappe._dict()
	return frappe._dict(
		{
			"invoices": cint(row.get("invoices")),
			"returns": cint(row.get("returns")),
			"customers": cint(row.get("customers")),
			# Kvadrat zakazdan fakturaga ko'chiriladi (oyna_order.py), shuning
			# uchun sotuv bilan bir manbadan — fakturadan olinadi.
			"sqm": flt(row.get("sqm")),
		}
	)


def _material_cost(ctx, from_date, to_date, docstatus=DOCSTATUS_SUBMITTED):
	"""Sarflangan materiallarning joriy baholash bo'yicha tannarxi.

	`Oyna Sarflangan Tovar` × `Bin.valuation_rate` (bazaviy valyutada) →
	taqdimot valyutasiga o'giriladi. Faqat TASDIQLANGAN zakazlar hisobga olinadi;
	tasdiqlangan zakaz uchun ERPNext'da Material Issue ham yaratilgani sababli
	bu raqam GL'dagi COGS bilan yonma-yon tekshirilishi mumkin.
	"""
	params = {
		"company": ctx.company,
		"from_date": from_date,
		"to_date": to_date,
		"docstatus": docstatus,
	}
	conditions = _so_conditions(ctx, params)
	if ctx.warehouse:
		params["warehouse"] = ctx.warehouse
		conditions += " AND ost.warehouse = %(warehouse)s"

	rows = frappe.db.sql(
		"""
		SELECT SUM(ost.qty * IFNULL(bin.valuation_rate, IFNULL(item.valuation_rate, 0))) AS cost
		FROM `tabOyna Sarflangan Tovar` ost
		INNER JOIN `tabSales Order` so ON so.name = ost.parent
		LEFT JOIN `tabBin` bin ON bin.item_code = ost.item_code AND bin.warehouse = ost.warehouse
		LEFT JOIN `tabItem` item ON item.name = ost.item_code
		WHERE so.company = %(company)s
		  AND so.docstatus = %(docstatus)s
		  AND so.transaction_date BETWEEN %(from_date)s AND %(to_date)s
		  {conditions}
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)

	# Bin.valuation_rate kompaniya valyutasida yuritiladi.
	return _money_add({}, ctx.company_currency, flt(rows[0].cost) if rows else 0.0)


# --------------------------------------------------------------------------
# Zaxira (Stock) yordamchilari
# --------------------------------------------------------------------------


def _stock_snapshot_sql(ctx, params, to_date_key="to_date"):
	"""Sana holatiga oxirgi Stock Ledger Entry (ERPNext Stock Balance mantiqi)."""
	conditions = ""
	if ctx.warehouse:
		params["warehouse"] = ctx.warehouse
		conditions += " AND sle.warehouse = %(warehouse)s"

	return """
		SELECT snap.item_code, snap.warehouse, snap.qty, snap.value, snap.rate
		FROM (
			SELECT sle.item_code,
			       sle.warehouse,
			       sle.qty_after_transaction AS qty,
			       sle.stock_value AS value,
			       sle.valuation_rate AS rate,
			       ROW_NUMBER() OVER (
			           PARTITION BY sle.item_code, sle.warehouse
			           ORDER BY sle.posting_datetime DESC, sle.creation DESC
			       ) AS rn
			FROM `tabStock Ledger Entry` sle
			WHERE sle.company = %(company)s
			  AND sle.is_cancelled = 0
			  AND sle.posting_date <= %({to_date_key})s
			  {conditions}
		) snap
		WHERE snap.rn = 1
	""".format(conditions=conditions, to_date_key=to_date_key)


def _stock_totals(ctx, to_date):
	params = {"company": ctx.company, "to_date": to_date}
	sql = _stock_snapshot_sql(ctx, params)
	rows = frappe.db.sql(
		"""
		SELECT COUNT(*) AS items, SUM(qty) AS qty, SUM(value) AS value
		FROM ({sql}) t
		WHERE t.qty <> 0
		""".format(sql=sql),
		params,
		as_dict=True,
	)
	row = rows[0] if rows else frappe._dict()
	return frappe._dict(
		{
			"items": cint(row.get("items")),
			"qty": flt(row.get("qty")),
			# Zaxira qiymati kompaniya valyutasida (Stock hisobi shu valyutada).
			"value": _money_add({}, ctx.company_currency, flt(row.get("value"))),
		}
	)


# --------------------------------------------------------------------------
# Trend intervali
# --------------------------------------------------------------------------


def _interval(from_date, to_date):
	days = date_diff(to_date, from_date) + 1
	if days <= 31:
		return "day"
	if days <= 120:
		return "week"
	return "month"


def _period_buckets(from_date, to_date, interval):
	"""Davrdagi barcha bucket kalitlari — grafikda bo'sh kunlar uzilib qolmasligi uchun."""
	buckets = []
	if interval == "day":
		current = getdate(from_date)
		while current <= getdate(to_date):
			buckets.append(str(current))
			current = getdate(add_days(current, 1))
	elif interval == "week":
		start = getdate(from_date)
		current = getdate(add_days(start, -start.weekday()))
		while current <= getdate(to_date):
			buckets.append(str(current))
			current = getdate(add_days(current, 7))
	else:
		current = getdate(from_date).replace(day=1)
		last = getdate(to_date).replace(day=1)
		while current <= last:
			buckets.append(str(current))
			current = getdate(add_months(current, 1))
	return buckets


def _fill_series(series_map, from_date, to_date, interval, template):
	"""Bo'sh oraliqlarni nol qiymat bilan to'ldiradi va tartiblab qaytaradi."""
	filled = []
	for key in _period_buckets(from_date, to_date, interval):
		row = series_map.get(key)
		if row is None:
			row = dict(template)
			row["label"] = key
		filled.append(row)
	return filled


def _date_group_sql(field, interval):
	if interval == "day":
		return "DATE({0})".format(field)
	if interval == "week":
		return "DATE(DATE_SUB({0}, INTERVAL WEEKDAY({0}) DAY))".format(field)
	return "DATE(DATE_FORMAT({0}, '%%Y-%%m-01'))".format(field)


# ==========================================================================
# ENDPOINT: meta
# ==========================================================================


@frappe.whitelist()
def get_meta():
	"""Filtrlar uchun ma'lumot: kompaniyalar, omborlar, kassa hisoblari, kurs."""
	allowed = get_allowed_companies()
	company_filters = {"name": ("in", allowed)} if allowed else {}
	companies = frappe.get_all(
		"Company",
		filters=company_filters,
		fields=["name", "default_currency"],
		order_by="name",
	)

	company = _resolve_company(frappe._dict())

	return {
		"company": company,
		"company_currency": frappe.get_cached_value("Company", company, "default_currency"),
		"companies": companies,
		"locked_company": bool(allowed),
		"warehouses": frappe.get_all(
			"Warehouse",
			filters={"company": company, "is_group": 0},
			fields=["name"],
			order_by="name",
			pluck="name",
		),
		"cost_centers": frappe.get_all(
			"Cost Center",
			filters={"company": company, "is_group": 0},
			fields=["name"],
			order_by="name",
			pluck="name",
		),
		"cash_accounts": [
			{"name": a.name, "label": a.account_name, "currency": a.currency, "kind": a.kind}
			for a in get_cash_accounts(company)
		],
		"permissions": {
			"gl": _can("GL Entry"),
			"sales_order": _can("Sales Order"),
			"stock": _can("Item", "Warehouse"),
			"kassa": _can("Kassa"),
		},
		"overdue_after_days": OVERDUE_AFTER_DAYS,
	}


# ==========================================================================
# ENDPOINT: overview (KPI kartalari)
# ==========================================================================


@frappe.whitelist()
def get_overview(filters=None):
	ctx = build_context(filters)
	return _get_overview(ctx)


def _get_overview(ctx):
	cards = []
	can_gl = _can("GL Entry")
	can_so = _can("Sales Order")
	can_stock = _can("Item", "Warehouse")

	# --- 1. Sotuv (tasdiqlangan tushum) -----------------------------------
	if can_gl:
		now_revenue = _revenue(ctx, ctx.from_date, ctx.to_date)
		prev_revenue = _revenue(ctx, ctx.prev_from, ctx.prev_to)
		invoices = _invoice_stats(ctx, ctx.from_date, ctx.to_date)
		cards.append(
			{
				"key": "sales",
				"label": _("Sotuv"),
				"value": _money_out(now_revenue.net),
				"format": "currency",
				"icon": "sell",
				"tone": "primary",
				"delta": _delta(now_revenue.net, prev_revenue.net),
				"details": [
					{"label": _("Hisob-faktura"), "value": invoices.invoices, "format": "int"},
					{"label": _("Mijozlar"), "value": invoices.customers, "format": "int"},
					{"label": _("Kvadrat"), "value": invoices.sqm, "format": "sqm"},
				],
				"route": ["List", "Sales Invoice"],
				"route_options": {"company": ctx.company, "docstatus": 1},
			}
		)

		# --- 2. Sof foyda (P&L «Profit for the year» bilan aynan) ---------
		# Yalpi foyda = tushum − tannarx; sof foyda = yalpi foyda − xarajatlar.
		# Kartada SOF foyda ko'rsatiladi, chunki P&L hisobotining yakuniy satri
		# ham shu; yalpi foyda pastki qatorda qo'shimcha ma'lumot sifatida turadi.
		# Xarajat = P&L «Total Expense (Debit)» — tannarx (COGS) ham ichida.
		# Shunda "tushum − xarajat = sof foyda" tengligi ko'z bilan tekshiriladi.
		# Xarajat hisoblari USD (hisob valyutasi) da yuritiladi, tushum esa
		# hujjat valyutasida (UZS) — shuning uchun xarajat P&L bilan bir xil
		# kursda UZS ga keltiriladi, aks holda ayirish ma'noga ega bo'lmaydi.
		expense_accounts = [a.name for a in get_expense_accounts(ctx.company, include_cogs=True)]
		now_opex = _money_to_presentation(
			_gl_balance(ctx, expense_accounts, ctx.to_date, from_date=ctx.from_date), ctx.to_date
		)
		prev_opex = _money_to_presentation(
			_gl_balance(ctx, expense_accounts, ctx.prev_to, from_date=ctx.prev_from), ctx.prev_to
		)

		# Sof foyda ERPNext P&L hisobotidan (presentation_currency = UZS) olinadi,
		# shunda karta hisobotdagi «Profit for the year» bilan aynan mos keladi.
		# Hisobot ishlamay qolsa — GL bo'yicha hisoblab, dashboard yiqilmaydi.
		try:
			now_profit = _pnl_net_profit(ctx, ctx.from_date, ctx.to_date)
			prev_profit = _pnl_net_profit(ctx, ctx.prev_from, ctx.prev_to)
		except Exception:
			frappe.log_error(
				title="Oyna dashboard: P&L sof foyda", message=frappe.get_traceback()
			)
			now_profit = _money_diff(now_revenue.net, now_opex)
			prev_profit = _money_diff(prev_revenue.net, prev_opex)

		margin = _margin(now_profit, now_revenue.net, PNL_PRESENTATION_CURRENCY)
		cards.append(
			{
				"key": "net_profit",
				"label": _("Sof foyda"),
				"value": _money_out(now_profit),
				"format": "currency",
				"icon": "trending-up",
				"tone": "success" if all(v >= 0 for v in now_profit.values()) else "danger",
				"delta": _delta(now_profit, prev_profit),
				# Pul summalari kartaning pastki qatorida ko'rsatilmaydi.
				"details": (
					[{"label": _("Sof marja"), "value": margin, "format": "percent"}]
					if margin is not None
					else []
				),
			}
		)

	# --- 3. Yetkazib beruvchilarga qarz (haqdorlar) ------------------------
	if can_gl:
		now_payable = _party_balance_summary(ctx, ctx.to_date, "Supplier")
		prev_payable = _party_balance_summary(ctx, ctx.prev_to, "Supplier")
		cards.append(
			{
				"key": "payables",
				"label": _("Yetkazib beruvchilarga qarz"),
				# Sof qoldiq — GL (party_type = Supplier) bilan aynan bir xil.
				"value": _money_out(now_payable.net),
				"format": "currency",
				"icon": "buying",
				"tone": "warning",
				"invert_delta": True,
				"delta": _delta(now_payable.net, prev_payable.net),
				"details": [
					{"label": _("Haqdorlar"), "value": now_payable.debtors, "format": "int"},
				],
				"as_of": True,
				"section": "payables",
			}
		)


		# --- 4. Mijozlar qarzi ---------------------------------------------
		now_debt = _receivable_summary(ctx, ctx.to_date)
		prev_debt = _receivable_summary(ctx, ctx.prev_to)
		cards.append(
			{
				"key": "receivables",
				"label": _("Mijozlar qarzi"),
				# SOF qoldiq — General Ledger'dagi (party_type = Customer) yakuniy
				# qoldiq bilan aynan bir xil: qarzdorlar summasidan avanslar ayrilgan.
				# Tarkibi pastki qatorda ko'rsatiladi.
				"value": _money_out(now_debt.net),
				"format": "currency",
				"icon": "users",
				"tone": "warning",
				# Qarzning o'sishi — yaxshi emas, shuning uchun signal teskari.
				"invert_delta": True,
				"delta": _delta(now_debt.net, prev_debt.net),
				"details": [
					{"label": _("Qarzdorlar"), "value": now_debt.debtors, "format": "int"},
				],
				"as_of": True,
				"section": "receivables",
			}
		)

		# --- 4 & 5. Kassa va bank ------------------------------------------
		accounts = get_cash_accounts(ctx.company, only=ctx.cash_account)
		cash_accounts = [a for a in accounts if a.kind == "cash"]
		bank_accounts = [a for a in accounts if a.kind == "bank"]

		for key, group, label, icon in (
			("cash", cash_accounts, _("Naqd kassa"), "wallet"),
			("bank", bank_accounts, _("Bank / plastik"), "bank"),
		):
			names = [a.name for a in group]
			now_balance = _gl_balance(ctx, names, ctx.to_date)
			prev_balance = _gl_balance(ctx, names, ctx.prev_to)
			cards.append(
				{
					"key": key,
					"label": label,
					"value": _money_out(now_balance),
					"format": "currency",
					"icon": icon,
					"tone": "info",
					"delta": _delta(now_balance, prev_balance),
					"details": [
						{"label": _("Hisoblar"), "value": len(group), "format": "int"},
					],
					"as_of": True,
					"section": "cash",
					"empty": not group,
				}
			)

	# --- 6. Zaxira ---------------------------------------------------------
	if can_stock:
		now_stock = _stock_totals(ctx, ctx.to_date)
		prev_stock = _stock_totals(ctx, ctx.prev_to)
		cards.append(
			{
				"key": "inventory",
				"label": _("Ombor zaxirasi"),
				"value": _money_out(now_stock.value),
				"format": "currency",
				"icon": "package",
				"tone": "info",
				"delta": _delta(now_stock.value, prev_stock.value),
				"details": [
					{"label": _("Tovar Turi"), "value": now_stock["items"], "format": "int"},
					{"label": _("Miqdor"), "value": now_stock.qty, "format": "qty"},
				],
				"as_of": True,
				"section": "inventory",
			}
		)

	# --- 8. Xarajatlar -----------------------------------------------------
	# Eslatma: "Sof pul oqimi" kartasi ataylab yo'q — kirim/chiqim/sof oqim
	# "Kassa va pul oqimi" bo'limining o'zida to'liq ko'rsatiladi.
	if can_gl:
		# Sof foyda kartasi uchun allaqachon hisoblangan — qayta so'ramaymiz.
		now_expense, prev_expense = now_opex, prev_opex
		cards.append(
			{
				"key": "expenses",
				"label": _("Xarajatlar"),
				"value": _money_out(now_expense),
				"format": "currency",
				"icon": "arrow-down",
				"tone": "danger",
				"invert_delta": True,
				"delta": _delta(now_expense, prev_expense),
				"section": "expenses",
			}
		)

	return {
		"permitted": True,
		"cards": _split_by_currency(cards),
		"context": _context_info(ctx),
	}


def _split_by_currency(cards):
	"""Ko'p valyutali kartani har bir valyuta uchun ALOHIDA kartaga ajratadi.

	Konvertatsiya yo'q, shuning uchun ikki valyutani bitta kartada ustma-ust
	ko'rsatishdan ko'ra alohida blok qilgan tushunarli: «Naqd kassa (UZS)» va
	«Naqd kassa (USD)».

	`details` (masalan «2 Hisoblar») ajratilgan kartalarda ko'rsatilmaydi —
	ular umumiy sanoq bo'lib, valyutalar bo'yicha bo'linmaydi.
	"""
	result = []
	for card in cards:
		values = card.get("value")
		if card.get("format") != "currency" or not isinstance(values, list) or len(values) < 2:
			result.append(card)
			continue

		deltas = card.get("delta") or {}
		for entry in values:
			currency = entry["currency"]
			clone = dict(card)
			clone["key"] = "{0}_{1}".format(card["key"], currency.lower())
			clone["label"] = "{0} ({1})".format(card["label"], currency)
			clone["value"] = [entry]
			clone["delta"] = {currency: deltas[currency]} if currency in deltas else {}
			clone["details"] = []
			# Foyda kabi kartalarda ishora har bir valyuta uchun alohida
			if card.get("tone") in ("success", "danger"):
				clone["tone"] = "success" if flt(entry["amount"]) >= 0 else "danger"
			result.append(clone)

	return result


def _party_accounts(ctx, party_type):
	"""(hisoblar, ishora) — Customer → Receivable (+), Supplier → Payable (−).

	Ishora shu tarzda tanlanadiki, natija HAR DOIM "kontragent bizga qancha
	qarzdor" (Customer) yoki "biz kontragentga qancha qarzdormiz" (Supplier)
	ma'nosini bersin: musbat = qarz, manfiy = avans.
	"""
	if party_type == "Supplier":
		return get_payable_accounts(ctx.company), -1.0
	return get_receivable_accounts(ctx.company), 1.0


def _party_balance_summary(ctx, to_date, party_type="Customer"):
	"""Kontragentlar qoldig'i: qarz (gross), avans, sof qoldiq va kontragent soni.

	KPI kartasi ham, tegishli bo'lim ham shu funksiyaga tayanadi — shuning uchun
	sarlavhadagi raqam jadval yig'indisi bilan doim mos keladi. Manba — GL,
	ya'ni faqat tasdiqlangan hujjatlar (Akt Sverka va General Ledger bilan bir xil).
	"""
	accounts, sign = _party_accounts(ctx, party_type)
	empty = frappe._dict({"gross": 0.0, "advance": 0.0, "net": 0.0, "debtors": 0})
	if not accounts:
		return empty

	params = {
		"company": ctx.company,
		"accounts": tuple(accounts),
		"to_date": to_date,
		"party_type": party_type,
	}
	conditions = _gl_extra_conditions(ctx, params)
	if ctx.customer and party_type == "Customer":
		params["customer"] = ctx.customer
		conditions += " AND gle.party = %(customer)s"

	rows = frappe.db.sql(
		"""
		SELECT gle.party, gle.account_currency,
		       SUM(gle.debit - gle.credit) AS base_amount,
		       SUM(gle.debit_in_account_currency - gle.credit_in_account_currency) AS amount
		FROM `tabGL Entry` gle
		WHERE gle.company = %(company)s
		  AND gle.is_cancelled = 0
		  AND gle.account IN %(accounts)s
		  AND gle.party_type = %(party_type)s
		  AND IFNULL(gle.party, '') != ''
		  AND gle.posting_date <= %(to_date)s
		  {conditions}
		GROUP BY gle.party, gle.account_currency
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)

	# Kontragent + valyuta kesimida qoldiq. Bir kontragent ikki valyutada
	# qarzdor bo'lishi mumkin, shuning uchun juftlik bo'yicha guruhlanadi.
	balances = {}
	for row in rows:
		key = (row.party, row.account_currency)
		balances[key] = balances.get(key, 0.0) + sign * flt(row.amount)

	gross, advance = {}, {}
	debtors = set()
	for (party, currency), value in balances.items():
		if value > 0.005:
			_money_add(gross, currency, value)
			debtors.add(party)
		elif value < -0.005:
			_money_add(advance, currency, -value)

	return frappe._dict(
		{
			"gross": gross,
			"advance": advance,
			"net": _money_diff(gross, advance),
			"debtors": len(debtors),
		}
	)


def _receivable_summary(ctx, to_date):
	return _party_balance_summary(ctx, to_date, "Customer")


def _context_info(ctx):
	return {
		"company": ctx.company,
		"company_currency": ctx.company_currency,
		"from_date": str(ctx.from_date),
		"to_date": str(ctx.to_date),
		"prev_from": str(ctx.prev_from),
		"prev_to": str(ctx.prev_to),
		"period": ctx.period,
	}


# ==========================================================================
# ENDPOINT: sales
# ==========================================================================


@frappe.whitelist()
def get_sales(filters=None):
	ctx = build_context(filters)
	return _get_sales(ctx)


def _get_sales(ctx):
	"""Sotuv tahlili — FAQAT tasdiqlangan hujjatlar asosida.

	• summa/trend — GL Income (P&L «Total Income» bilan aynan bir xil);
	• hisob-faktura va mijozlar soni — tasdiqlangan `Sales Invoice`;
	• kvadrat (m²) va material — tasdiqlangan `Sales Order`.
	Rasmiylashtirilmagan zakazlar alohida «order_book» blokida qaytariladi va
	hech qanday sotuv ko'rsatkichiga qo'shilmaydi.
	"""
	if not _can("GL Entry"):
		return _denied("GL Entry")

	# --- Tushum trendi: TANLANGAN DAVR EMAS, to'liq YIL (12 oy) -----------
	# Grafik doim yanvardan dekabrgacha ko'rsatadi, shunda oylar bir-biri bilan
	# taqqoslanadi. Manba o'sha GL Income hisoblari — ya'ni P&L bilan bir xil.
	trend_year = getdate(ctx.to_date).year
	trend = _year_revenue_trend(ctx, trend_year)

	revenue = _revenue(ctx, ctx.from_date, ctx.to_date)
	invoices = _invoice_stats(ctx, ctx.from_date, ctx.to_date)
	avg = {c: v / invoices.invoices for c, v in revenue.net.items()} if invoices.invoices else {}
	per_sqm = {c: v / invoices.sqm for c, v in revenue.net.items()} if invoices.sqm else {}

	return {
		"permitted": True,
		"interval": "month",
		"trend": [
			{
				"label": r["label"],
				"amount": _money_out(r["amount"]),
				"returns": _money_out(r["returns"]),
			}
			for r in trend
		],
		"trend_year": trend_year,
		"order_book": _order_book(ctx),
		"summary": {
			"amount": _money_out(revenue.net),
			"gross": _money_out(revenue.gross),
			"returns": _money_out(revenue.returns),
			"return_count": invoices.returns,
			"orders": invoices.invoices,
			"sqm": invoices.sqm,
			"customers": invoices.customers,
			"avg_order": _money_out(avg),
			"price_per_sqm": _money_out(per_sqm),
		},
		"context": _context_info(ctx),
	}


def _year_revenue_trend(ctx, year):
	"""Yilning 12 oyi bo'yicha sotuv va vozvrat (hujjat valyutasida).

	Davr filtri qanday bo'lishidan qat'i nazar butun kalendar yil qaytariladi —
	grafikda oylarni taqqoslash uchun.
	"""
	months = ["{0}-{1:02d}-01".format(year, m) for m in range(1, 13)]
	trend = {key: {"label": key, "amount": {}, "returns": {}} for key in months}

	params = {
		"company": ctx.company,
		"from_date": "{0}-01-01".format(year),
		"to_date": "{0}-12-31".format(year),
	}
	conditions = ""
	if ctx.customer:
		params["customer"] = ctx.customer
		conditions += " AND si.customer = %(customer)s"

	bucket = _date_group_sql("si.posting_date", "month")
	for row in frappe.db.sql(
		"""
		SELECT {bucket} AS bucket, si.currency, si.is_return, SUM(si.grand_total) AS amount
		FROM `tabSales Invoice` si
		WHERE si.company = %(company)s
		  AND si.docstatus = 1
		  AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  {conditions}
		GROUP BY bucket, si.currency, si.is_return
		""".format(bucket=bucket, conditions=conditions),
		params,
		as_dict=True,
	):
		entry = trend.get(str(row.bucket))
		if not entry:
			continue
		if cint(row.is_return):
			_money_add(entry["returns"], row.currency, -flt(row.amount))
		else:
			_money_add(entry["amount"], row.currency, row.amount)

	return [trend[key] for key in months]


def _order_book(ctx):
	"""Rasmiylashtirilmagan zakazlar portfeli (docstatus = 0).

	Bu SOTUV EMAS — hali hech qanday buxgalteriya yozuvi yo'q. Faqat sexdagi
	ish hajmini ko'rsatish uchun, alohida blokda va shunday belgilanib beriladi.
	"""
	if not _can("Sales Order"):
		return None

	params = {
		"company": ctx.company,
		"from_date": ctx.from_date,
		"to_date": ctx.to_date,
		"docstatus": DOCSTATUS_DRAFT,
	}
	conditions = _so_conditions(ctx, params)

	states = {}
	for row in frappe.db.sql(
		"""
		SELECT IFNULL(so.workflow_state, so.status) AS state, so.currency,
		       COUNT(*) AS orders,
		       SUM(so.grand_total) AS amount,
		       SUM(so.base_grand_total) AS base_amount,
		       SUM(IFNULL(so.custom_jami_kvadrat, 0)) AS sqm
		FROM `tabSales Order` so
		WHERE so.company = %(company)s
		  AND so.docstatus = %(docstatus)s
		  AND so.transaction_date BETWEEN %(from_date)s AND %(to_date)s
		  {conditions}
		GROUP BY state, so.currency
		""".format(conditions=conditions),
		params,
		as_dict=True,
	):
		key = row.state or _("Noma'lum")
		entry = states.setdefault(key, {"state": key, "orders": 0, "amount": {}, "sqm": 0.0})
		entry["orders"] += cint(row.orders)
		_money_add(entry["amount"], row.currency, row.amount)
		entry["sqm"] += flt(row.sqm)

	rows = sorted(states.values(), key=lambda r: -sum(abs(v) for v in r["amount"].values()))
	for row in rows:
		row["amount"] = _money_out(row["amount"])
	return {
		"states": rows,
		"total": _money_out(_money_sum(*[dict((e["currency"], e["amount"]) for e in r["amount"]) for r in rows])),
		"orders": sum(r["orders"] for r in rows),
		"sqm": sum(r["sqm"] for r in rows),
	}


# ==========================================================================
# ENDPOINT: orders — davrdagi barcha zakazlar, holati bilan
# ==========================================================================


def _workflow_state_styles():
	"""Workflow State -> rang uslubi. Ranglar workflow sozlamasidan olinadi,
	shuning uchun yangi holat qo'shilsa dashboard o'zi moslashadi."""
	rows = frappe.get_all("Workflow State", fields=["name", "style"], ignore_permissions=True)
	return {r.name: (r.style or "").lower() for r in rows}


@frappe.whitelist()
def get_orders(filters=None):
	ctx = build_context(filters)
	return _get_orders(ctx)


def _get_orders(ctx):
	"""Davrdagi BARCHA zakazlar (bekor qilinganlar ham) — holati bilan."""
	if not _can("Sales Order"):
		return _denied("Sales Order")

	params = {"company": ctx.company, "from_date": ctx.from_date, "to_date": ctx.to_date}
	conditions = _so_conditions(ctx, params)

	rows = frappe.db.sql(
		"""
		SELECT so.name, so.transaction_date, so.delivery_date,
		       so.customer, so.customer_name, so.currency, so.grand_total,
		       IFNULL(so.custom_jami_kvadrat, 0) AS sqm,
		       so.workflow_state, so.status, so.docstatus
		FROM `tabSales Order` so
		WHERE so.company = %(company)s
		  AND so.transaction_date BETWEEN %(from_date)s AND %(to_date)s
		  {conditions}
		ORDER BY so.transaction_date DESC, so.name DESC
		LIMIT 500
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)

	styles = _workflow_state_styles()
	orders = []
	states = {}

	for row in rows:
		if row.docstatus == 2:
			state, style = _("Bekor qilingan"), "danger"
		else:
			state = row.workflow_state or row.status
			style = styles.get(row.workflow_state or "", "")

		orders.append(
			{
				"name": row.name,
				"date": str(row.transaction_date) if row.transaction_date else None,
				"delivery_date": str(row.delivery_date) if row.delivery_date else None,
				"customer": row.customer,
				"customer_name": row.customer_name or row.customer,
				"sqm": flt(row.sqm),
				"amount": _money_out(_money_add({}, row.currency, row.grand_total)),
				"state": state,
				"style": style,
				"docstatus": cint(row.docstatus),
				# Submit qilingan zakaz buxgalteriyaga tushgan bo'ladi
				"confirmed": cint(row.docstatus) == DOCSTATUS_SUBMITTED,
			}
		)

		bucket = states.setdefault(
			state, {"state": state, "style": style, "orders": 0, "amount": {}, "sqm": 0.0}
		)
		bucket["orders"] += 1
		bucket["sqm"] += flt(row.sqm)
		_money_add(bucket["amount"], row.currency, row.grand_total)

	summary = sorted(states.values(), key=lambda r: -r["orders"])
	for row in summary:
		row["amount"] = _money_out(row["amount"])

	return {
		"permitted": True,
		"orders": orders,
		"states": summary,
		"total": len(orders),
		"truncated": len(rows) >= 500,
		"context": _context_info(ctx),
	}


# ==========================================================================
# ENDPOINT: inventory
# ==========================================================================


@frappe.whitelist()
def get_inventory(filters=None):
	ctx = build_context(filters)
	return _cached(ctx, "inventory", lambda: _get_inventory(ctx))


def _get_inventory(ctx):
	if not _can("Item", "Warehouse"):
		return _denied("Item", "Warehouse")

	params = {"company": ctx.company, "to_date": ctx.to_date}
	snapshot_sql = _stock_snapshot_sql(ctx, params)

	rows = frappe.db.sql(
		"""
		SELECT t.item_code, t.warehouse, t.qty, t.value, t.rate,
		       item.item_name, item.item_group, item.stock_uom
		FROM ({sql}) t
		LEFT JOIN `tabItem` item ON item.name = t.item_code
		ORDER BY t.value DESC
		""".format(sql=snapshot_sql),
		params,
		as_dict=True,
	)

	# Davr ichidagi haqiqiy material sarfi (Oyna sex buyurtmalari bo'yicha).
	consumption = _material_consumption(ctx)
	days = max(date_diff(ctx.to_date, ctx.from_date) + 1, 1)

	items = []
	total_value = {}
	total_qty = 0.0
	out_of_stock = 0
	low_stock = 0

	for row in rows:
		qty = flt(row.qty)
		value = _money_add({}, ctx.company_currency, flt(row.value))
		used = flt(consumption.get(row.item_code, {}).get("qty"))
		daily = used / days if used else 0.0
		cover = (qty / daily) if daily > 0 else None

		if qty <= 0:
			status = "out"
			out_of_stock += 1
		elif cover is not None and cover < LOW_STOCK_DAYS_COVER:
			status = "low"
			low_stock += 1
		else:
			status = "healthy"

		total_value = _money_sum(total_value, value)
		total_qty += qty

		items.append(
			{
				"item_code": row.item_code,
				"item_name": row.item_name or row.item_code,
				"item_group": row.item_group,
				"warehouse": row.warehouse,
				"qty": qty,
				"uom": row.stock_uom,
				"rate": _money_out(_money_add({}, ctx.company_currency, flt(row.rate))),
				"value": _money_out(value),
				"consumed": used,
				"days_cover": flt(cover, 1) if cover is not None else None,
				"status": status,
			}
		)

	# Guruh kesimi (Oynalar / Aksessuarlar / ...)
	groups = {}
	for item in items:
		group = groups.setdefault(
			item["item_group"] or _("Guruhsiz"), {"item_group": item["item_group"], "value": {}, "items": 0}
		)
		for entry in item["value"]:
			_money_add(group["value"], entry["currency"], entry["amount"])
		group["items"] += 1

	top_consumed = sorted(consumption.values(), key=lambda r: r["qty"], reverse=True)[:10]

	return {
		"permitted": True,
		"summary": {
			"value": _money_out(total_value),
			"qty": total_qty,
			"items": len(items),
			"out_of_stock": out_of_stock,
			"low_stock": low_stock,
			"days": days,
		},
		"items": items[:50],
		"groups": [
			dict(g, value=_money_out(g["value"]))
			for g in sorted(
				groups.values(), key=lambda r: -sum(abs(v) for v in r["value"].values())
			)
		],
		"top_consumed": top_consumed,
		"context": _context_info(ctx),
	}


def _material_consumption(ctx, docstatus=DOCSTATUS_SUBMITTED):
	"""Davrda TASDIQLANGAN zakazlarga sarflangan materiallar (miqdor + tannarx)."""
	params = {
		"company": ctx.company,
		"from_date": ctx.from_date,
		"to_date": ctx.to_date,
		"docstatus": docstatus,
	}
	conditions = _so_conditions(ctx, params)
	if ctx.warehouse:
		params["warehouse"] = ctx.warehouse
		conditions += " AND ost.warehouse = %(warehouse)s"

	rows = frappe.db.sql(
		"""
		SELECT ost.item_code,
		       ost.uom,
		       item.item_name,
		       COUNT(DISTINCT so.name) AS orders,
		       SUM(ost.qty) AS qty,
		       SUM(ost.qty * IFNULL(bin.valuation_rate, IFNULL(item.valuation_rate, 0))) AS cost
		FROM `tabOyna Sarflangan Tovar` ost
		INNER JOIN `tabSales Order` so ON so.name = ost.parent
		LEFT JOIN `tabBin` bin ON bin.item_code = ost.item_code AND bin.warehouse = ost.warehouse
		LEFT JOIN `tabItem` item ON item.name = ost.item_code
		WHERE so.company = %(company)s
		  AND so.docstatus = %(docstatus)s
		  AND so.transaction_date BETWEEN %(from_date)s AND %(to_date)s
		  {conditions}
		GROUP BY ost.item_code, ost.uom, item.item_name
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)

	return {
		row.item_code: {
			"item_code": row.item_code,
			"item_name": row.item_name or row.item_code,
			"uom": row.uom,
			"orders": cint(row.orders),
			"qty": flt(row.qty),
			"cost": _money_out(_money_add({}, ctx.company_currency, flt(row.cost))),
		}
		for row in rows
	}


# ==========================================================================
# ENDPOINT: receivables
# ==========================================================================


@frappe.whitelist()
def get_receivables(filters=None):
	ctx = build_context(filters)
	return _cached(ctx, "receivables", lambda: _get_party_ledger(ctx, "Customer"))


@frappe.whitelist()
def get_payables(filters=None):
	ctx = build_context(filters)
	return _cached(ctx, "payables", lambda: _get_party_ledger(ctx, "Supplier"))


def _get_party_ledger(ctx, party_type="Customer"):
	"""Kontragentlar qarzi + yosh (aging) tahlili — faqat GL asosida.

	Manba Akt Sverka va General Ledger bilan bir xil: o'sha hisoblar, o'sha
	yozuvlar. Farq faqat ishorada — bu yerda MUSBAT = kontragent qarzdor
	(Customer) yoki biz qarzdormiz (Supplier).
	"""
	if not _can("GL Entry"):
		return _denied("GL Entry")

	accounts, sign = _party_accounts(ctx, party_type)
	if not accounts:
		return {
			"permitted": True,
			"party_type": party_type,
			"empty_reason": _("Kompaniyada mos hisob turi sozlanmagan ({0}).").format(party_type),
			"summary": {},
			"aging": [],
			"customers": [],
			"context": _context_info(ctx),
		}

	params = {
		"company": ctx.company,
		"accounts": tuple(accounts),
		"to_date": ctx.to_date,
		"party_type": party_type,
	}
	conditions = _gl_extra_conditions(ctx, params)
	if ctx.customer and party_type == "Customer":
		params["customer"] = ctx.customer
		conditions += " AND gle.party = %(customer)s"

	rows = frappe.db.sql(
		"""
		SELECT gle.party, gle.posting_date, gle.account_currency,
		       gle.debit - gle.credit AS base_amount,
		       gle.debit_in_account_currency - gle.credit_in_account_currency AS amount
		FROM `tabGL Entry` gle
		WHERE gle.company = %(company)s
		  AND gle.is_cancelled = 0
		  AND gle.account IN %(accounts)s
		  AND gle.party_type = %(party_type)s
		  AND IFNULL(gle.party, '') != ''
		  AND gle.posting_date <= %(to_date)s
		  {conditions}
		ORDER BY gle.party, gle.posting_date, gle.creation
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)

	buckets = [
		("current", _("0–30 kun"), 0, OVERDUE_AFTER_DAYS),
		("b31_60", _("31–60 kun"), OVERDUE_AFTER_DAYS + 1, 60),
		("b61_90", _("61–90 kun"), 61, 90),
		("b90_plus", _("90+ kun"), 91, None),
	]

	# FIFO har bir (kontragent, valyuta) juftligi uchun alohida yuritiladi —
	# konvertatsiya yo'q, shuning uchun valyutalarni aralashtirib bo'lmaydi.
	by_party = {}
	for row in rows:
		amount = sign * flt(row.amount)
		entry = by_party.setdefault(
			(row.party, row.account_currency), {"charges": 0.0, "payments": 0.0, "queue": []}
		)
		if amount > 0:
			entry["charges"] += amount
			entry["queue"].append([getdate(row.posting_date), amount])
		elif amount < 0:
			entry["payments"] += -amount
			_apply_fifo(entry["queue"], -amount)

	party_names = _party_names(list({p for p, _c in by_party}), party_type)
	to_date = getdate(ctx.to_date)

	parties = {}
	totals = {"balance": {}, "overdue": {}, "current": {}, "advance": {}}
	bucket_totals = {key: {} for key, *_rest in buckets}

	for (party, currency), entry in by_party.items():
		balance = entry["charges"] - entry["payments"]
		if abs(balance) < 0.005:
			continue
		if balance < 0:
			_money_add(totals["advance"], currency, -balance)
			continue

		row_buckets = {key: 0.0 for key, *_rest in buckets}
		oldest_days = 0
		for posting_date, amount in entry["queue"]:
			if amount <= 0:
				continue
			age = date_diff(to_date, posting_date)
			oldest_days = max(oldest_days, age)
			for key, _label, start, end in buckets:
				if age >= start and (end is None or age <= end):
					row_buckets[key] += amount
					_money_add(bucket_totals[key], currency, amount)
					break

		overdue = sum(v for k, v in row_buckets.items() if k != "current")
		_money_add(totals["balance"], currency, balance)
		_money_add(totals["overdue"], currency, overdue)
		_money_add(totals["current"], currency, row_buckets["current"])

		card = parties.setdefault(
			party,
			{
				"customer": party,
				"customer_name": party_names.get(party, party),
				"charges": {},
				"payments": {},
				"balance": {},
				"overdue": {},
				"oldest_days": 0,
			},
		)
		_money_add(card["charges"], currency, entry["charges"])
		_money_add(card["payments"], currency, entry["payments"])
		_money_add(card["balance"], currency, balance)
		_money_add(card["overdue"], currency, overdue)
		card["oldest_days"] = max(card["oldest_days"], oldest_days)

	def weight(bag):
		return sum(abs(v) for v in (bag or {}).values())

	customers = sorted(parties.values(), key=lambda r: -weight(r["balance"]))
	debtors = len(customers)

	# O'rtacha va eng katta qarz — har bir valyuta ichida alohida.
	average = {c: v / debtors for c, v in totals["balance"].items()} if debtors else {}
	largest = {}
	for card in customers:
		for currency, amount in card["balance"].items():
			if amount > largest.get(currency, 0.0):
				largest[currency] = amount

	return {
		"permitted": True,
		"party_type": party_type,
		"summary": {
			"balance": _money_out(totals["balance"]),
			"net": _money_out(_money_diff(totals["balance"], totals["advance"])),
			"debtors": debtors,
			"overdue": _money_out(totals["overdue"]),
			"current": _money_out(totals["current"]),
			"advance": _money_out(totals["advance"]),
			"average": _money_out(average),
			"max": _money_out(largest),
			"overdue_after_days": OVERDUE_AFTER_DAYS,
		},
		"aging": [
			{"key": key, "label": label, "amount": _money_out(bucket_totals[key])}
			for key, label, *_rest in buckets
		],
		"customers": [
			{
				"customer": c["customer"],
				"customer_name": c["customer_name"],
				"oldest_days": c["oldest_days"],
				"charges": _money_out(c["charges"]),
				"payments": _money_out(c["payments"]),
				"balance": _money_out(c["balance"]),
				"overdue": _money_out(c["overdue"]),
			}
			for c in customers[:25]
		],
		"context": _context_info(ctx),
	}


def _apply_fifo(queue, payment):
	"""To'lovni eng eski ochiq qarzdan boshlab yopadi (FIFO)."""
	remaining = payment
	for item in queue:
		if remaining <= 0:
			break
		if item[1] <= 0:
			continue
		applied = min(item[1], remaining)
		item[1] -= applied
		remaining -= applied


def _party_names(parties, party_type="Customer"):
	if not parties:
		return {}
	field = {"Customer": "customer_name", "Supplier": "supplier_name", "Employee": "employee_name"}[
		party_type
	]
	rows = frappe.get_all(
		party_type,
		filters={"name": ("in", parties)},
		fields=["name", "{} as party_name".format(field)],
		ignore_permissions=True,
	)
	return {r.name: r.party_name or r.name for r in rows}


# ==========================================================================
# ENDPOINT: cash (kassa)
# ==========================================================================


@frappe.whitelist()
def get_cash(filters=None):
	ctx = build_context(filters)
	return _get_cash(ctx)


def _get_cash(ctx):
	if not _can("GL Entry"):
		return _denied("GL Entry")

	accounts = get_cash_accounts(ctx.company, only=ctx.cash_account)
	if not accounts:
		return {
			"permitted": True,
			"empty_reason": _("Kassa hisoblari topilmadi (Mode of Payment Account sozlanmagan)."),
			"accounts": [],
			"summary": {},
			"trend": [],
			"categories": [],
			"context": _context_info(ctx),
		}

	names = [a.name for a in accounts]
	params = {
		"company": ctx.company,
		"accounts": tuple(names),
		"from_date": ctx.from_date,
		"to_date": ctx.to_date,
	}
	conditions = _gl_extra_conditions(ctx, params)

	# Ochilish qoldig'i — davr boshigacha
	opening_rows = frappe.db.sql(
		"""
		SELECT gle.account, gle.account_currency,
		       SUM(gle.debit - gle.credit) AS base_amount,
		       SUM(gle.debit_in_account_currency - gle.credit_in_account_currency) AS amount
		FROM `tabGL Entry` gle
		WHERE gle.company = %(company)s
		  AND gle.is_cancelled = 0
		  AND gle.account IN %(accounts)s
		  AND gle.posting_date < %(from_date)s
		  {conditions}
		GROUP BY gle.account, gle.account_currency
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)

	movement_rows = frappe.db.sql(
		"""
		SELECT gle.account, gle.account_currency,
		       SUM(gle.debit) AS base_in, SUM(gle.credit) AS base_out,
		       SUM(gle.debit_in_account_currency) AS amount_in,
		       SUM(gle.credit_in_account_currency) AS amount_out
		FROM `tabGL Entry` gle
		WHERE gle.company = %(company)s
		  AND gle.is_cancelled = 0
		  AND gle.account IN %(accounts)s
		  AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  {conditions}
		GROUP BY gle.account, gle.account_currency
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)

	opening = {r.account: r for r in opening_rows}
	movement = {r.account: r for r in movement_rows}

	registers = []
	summary = frappe._dict({"opening": {}, "inflow": {}, "outflow": {}, "closing": {}})
	for account in accounts:
		op = opening.get(account.name)
		mv = movement.get(account.name)
		# Hisobning o'z valyutasidagi aniq summalar — konvertatsiyasiz.
		opening_amount = flt(op.amount) if op else 0.0
		inflow = flt(mv.amount_in) if mv else 0.0
		outflow = flt(mv.amount_out) if mv else 0.0
		closing = opening_amount + inflow - outflow

		registers.append(
			{
				"account": account.name,
				"label": account.account_name,
				"mode_of_payment": account.mode_of_payment,
				"kind": account.kind,
				"account_currency": account.currency,
				"opening": opening_amount,
				"inflow": inflow,
				"outflow": outflow,
				"closing": closing,
			}
		)

		_money_add(summary.opening, account.currency, opening_amount)
		_money_add(summary.inflow, account.currency, inflow)
		_money_add(summary.outflow, account.currency, outflow)
		_money_add(summary.closing, account.currency, closing)

	summary = frappe._dict(
		{
			"opening": _money_out(summary.opening),
			"inflow": _money_out(summary.inflow),
			"outflow": _money_out(summary.outflow),
			"closing": _money_out(summary.closing),
			"net": _money_out(_money_diff(summary.inflow, summary.outflow)),
		}
	)

	return {
		"permitted": True,
		"accounts": registers,
		"summary": summary,
		# `interval` grafik sana yorliqlari uchun kerak (kun / hafta / oy).
		"interval": _interval(ctx.from_date, ctx.to_date),
		"trend": _cash_trend(ctx, names),
		"categories": _cash_categories(ctx, names),
		"context": _context_info(ctx),
	}


def _cash_trend(ctx, accounts):
	interval = _interval(ctx.from_date, ctx.to_date)
	bucket = _date_group_sql("gle.posting_date", interval)
	params = {
		"company": ctx.company,
		"accounts": tuple(accounts),
		"from_date": ctx.from_date,
		"to_date": ctx.to_date,
	}
	conditions = _gl_extra_conditions(ctx, params)

	rows = frappe.db.sql(
		"""
		SELECT {bucket} AS bucket, gle.account_currency,
		       SUM(gle.debit) AS base_in, SUM(gle.credit) AS base_out,
		       SUM(gle.debit_in_account_currency) AS amount_in,
		       SUM(gle.credit_in_account_currency) AS amount_out
		FROM `tabGL Entry` gle
		WHERE gle.company = %(company)s
		  AND gle.is_cancelled = 0
		  AND gle.account IN %(accounts)s
		  AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  {conditions}
		GROUP BY bucket, gle.account_currency
		ORDER BY bucket
		""".format(bucket=bucket, conditions=conditions),
		params,
		as_dict=True,
	)

	trend = {}
	for row in rows:
		key = str(row.bucket)
		entry = trend.setdefault(key, {"label": key, "inflow": {}, "outflow": {}})
		_money_add(entry["inflow"], row.account_currency, row.amount_in)
		_money_add(entry["outflow"], row.account_currency, row.amount_out)

	result = _fill_series(trend, ctx.from_date, ctx.to_date, interval, {"inflow": {}, "outflow": {}})
	return [
		{
			"label": row["label"],
			"inflow": _money_out(row["inflow"]),
			"outflow": _money_out(row["outflow"]),
			"net": _money_out(_money_diff(row["inflow"], row["outflow"])),
		}
		for row in result
	]


def _cash_categories(ctx, accounts):
	"""Pul oqimini toifalarga ajratish — DDS hisoboti mantiqidan foydalanadi.

	Shu tufayli dashboarddagi toifalar DDS bilan bir xil bo'ladi.
	"""
	from akfa_diller.akfa_diller.report.dds.dds import (
		CATEGORY_LABELS,
		get_journal_entry_info_batch,
		get_payment_entry_info_batch,
		resolve_transaction_info,
	)

	params = {
		"company": ctx.company,
		"accounts": tuple(accounts),
		"from_date": ctx.from_date,
		"to_date": ctx.to_date,
	}
	conditions = _gl_extra_conditions(ctx, params)

	rows = frappe.db.sql(
		"""
		SELECT gle.posting_date, gle.voucher_type, gle.voucher_no, gle.party_type, gle.party,
		       gle.against, gle.account, gle.account_currency,
		       gle.debit, gle.credit,
		       gle.debit_in_account_currency, gle.credit_in_account_currency
		FROM `tabGL Entry` gle
		WHERE gle.company = %(company)s
		  AND gle.is_cancelled = 0
		  AND gle.account IN %(accounts)s
		  AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  {conditions}
		ORDER BY gle.posting_date, gle.creation
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)

	if not rows:
		return []

	pe_vouchers = [r.voucher_no for r in rows if r.voucher_type == "Payment Entry"]
	je_vouchers = [r.voucher_no for r in rows if r.voucher_type == "Journal Entry"]
	pe_info = get_payment_entry_info_batch(pe_vouchers)
	je_info = get_journal_entry_info_batch(je_vouchers)

	categories = {}
	for row in rows:
		info = resolve_transaction_info(row, pe_info, je_info, accounts)
		key = info.get("category") or "other"
		entry = categories.setdefault(
			key, {"key": key, "label": CATEGORY_LABELS.get(key, key), "inflow": {}, "outflow": {}}
		)
		_money_add(entry["inflow"], row.account_currency, row.debit_in_account_currency)
		_money_add(entry["outflow"], row.account_currency, row.credit_in_account_currency)

	def weight(row):
		return sum(abs(v) for v in _money_sum(row["inflow"], row["outflow"]).values())

	return [
		{
			"key": row["key"],
			"label": row["label"],
			"inflow": _money_out(row["inflow"]),
			"outflow": _money_out(row["outflow"]),
			"net": _money_out(_money_diff(row["inflow"], row["outflow"])),
		}
		for row in sorted(categories.values(), key=weight, reverse=True)
	]


# ==========================================================================
# ENDPOINT: expenses
# ==========================================================================


@frappe.whitelist()
def get_expenses(filters=None):
	ctx = build_context(filters)
	return _get_expenses(ctx)


def _get_expenses(ctx):
	if not _can("GL Entry"):
		return _denied("GL Entry")

	accounts = get_expense_accounts(ctx.company, include_cogs=True)
	if not accounts:
		return {
			"permitted": True,
			"empty_reason": _("Kompaniyada xarajat hisoblari topilmadi."),
			"categories": [],
			"trend": [],
			"summary": {},
			"context": _context_info(ctx),
		}

	names = [a.name for a in accounts]
	labels = {a.name: a.account_name for a in accounts}
	params = {
		"company": ctx.company,
		"accounts": tuple(names),
		"from_date": ctx.from_date,
		"to_date": ctx.to_date,
	}
	conditions = _gl_extra_conditions(ctx, params)

	rows = frappe.db.sql(
		"""
		SELECT gle.account, gle.account_currency AS currency,
		       SUM(gle.debit_in_account_currency - gle.credit_in_account_currency) AS amount,
		       COUNT(*) AS entries
		FROM `tabGL Entry` gle
		WHERE gle.company = %(company)s
		  AND gle.is_cancelled = 0
		  AND gle.account IN %(accounts)s
		  AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  {conditions}
		GROUP BY gle.account, gle.account_currency
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)

	grouped = {}
	total = {}
	for row in rows:
		entry = grouped.setdefault(row.account, {"amount": {}, "entries": 0})
		_money_add(entry["amount"], row.currency, row.amount)
		_money_add(total, row.currency, row.amount)
		entry["entries"] += cint(row.entries)

	# Xarajatlar KPI kartasi kabi bu yerda ham hammasi UZS ga keltiriladi.
	total = _money_to_presentation(total, ctx.to_date)
	for entry in grouped.values():
		entry["amount"] = _money_to_presentation(entry["amount"], ctx.to_date)

	def weight(bag):
		return sum(abs(v) for v in (bag or {}).values())

	categories = []
	for account, entry in grouped.items():
		if weight(entry["amount"]) < 0.005:
			continue
		# Ulush — har bir modda o'z valyutasidagi jamiga nisbatan.
		share = 0.0
		for currency, amount in entry["amount"].items():
			base = _money_get(total, currency)
			if base:
				share += amount / base * 100.0
		categories.append(
			{
				"account": account,
				"label": labels.get(account, account),
				"amount": _money_out(entry["amount"]),
				"entries": entry["entries"],
				"share": flt(share, 1),
			}
		)
	categories.sort(key=lambda r: -sum(abs(e["amount"]) for e in r["amount"]))

	prev_total = _money_to_presentation(
		_gl_balance(ctx, names, ctx.prev_to, from_date=ctx.prev_from), ctx.prev_to
	)

	# Trend
	interval = _interval(ctx.from_date, ctx.to_date)
	bucket = _date_group_sql("gle.posting_date", interval)
	trend = {}
	for row in frappe.db.sql(
		"""
		SELECT {bucket} AS bucket, gle.account_currency AS currency,
		       SUM(gle.debit_in_account_currency - gle.credit_in_account_currency) AS amount
		FROM `tabGL Entry` gle
		WHERE gle.company = %(company)s
		  AND gle.is_cancelled = 0
		  AND gle.account IN %(accounts)s
		  AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  {conditions}
		GROUP BY bucket, gle.account_currency
		ORDER BY bucket
		""".format(bucket=bucket, conditions=conditions),
		params,
		as_dict=True,
	):
		key = str(row.bucket)
		entry = trend.setdefault(key, {"label": key, "amount": {}})
		_money_add(entry["amount"], row.currency, row.amount)

	# Kurs davr oxiriga qarab olinadi — ERPNext hisobotlari ham davr ustunlarini
	# bitta (yakuniy) kursda ko'rsatadi, shuning uchun trend jami bilan mos keladi.
	for entry in trend.values():
		entry["amount"] = _money_to_presentation(entry["amount"], ctx.to_date)

	filled = _fill_series(trend, ctx.from_date, ctx.to_date, interval, {"amount": {}})

	return {
		"permitted": True,
		"summary": {
			"total": _money_out(total),
			"delta": _delta(total, prev_total),
			"categories": len(categories),
		},
		"categories": categories,
		"trend": [{"label": r["label"], "amount": _money_out(r["amount"])} for r in filled],
		"interval": interval,
		"context": _context_info(ctx),
	}


# ==========================================================================
# ENDPOINT: bitta chaqiruvda bir nechta bo'lim (kamroq HTTP so'rov)
# ==========================================================================


_BUILDERS = {
	"overview": _get_overview,
	"sales": _get_sales,
	"orders": _get_orders,
	"inventory": _get_inventory,
	"receivables": lambda ctx: _get_party_ledger(ctx, "Customer"),
	"payables": lambda ctx: _get_party_ledger(ctx, "Supplier"),
	"cash": _get_cash,
	"expenses": _get_expenses,
}


@frappe.whitelist()
def get_dashboard(filters=None, sections=None):
	"""Bir nechta bo'limni bitta so'rovda qaytaradi.

	Frontend avval `overview`ni (tez chizish uchun), so'ng qolganini oladi.
	Bitta bo'limdagi xato butun dashboardni yiqitmaydi — u `error` bilan qaytadi.
	"""
	ctx = build_context(filters)

	if isinstance(sections, str):
		try:
			sections = json.loads(sections)
		except (ValueError, TypeError):
			sections = [s.strip() for s in sections.split(",") if s.strip()]
	sections = [s for s in (sections or SECTIONS) if s in _BUILDERS]

	result = {"context": _context_info(ctx)}
	for section in sections:
		try:
			result[section] = _BUILDERS[section](ctx)
		except Exception:
			frappe.log_error(
				title="Oyna dashboard: {0}".format(section),
				message=frappe.get_traceback(),
			)
			result[section] = {
				"permitted": True,
				"error": _("Ushbu bo'limni yuklashda xatolik yuz berdi. Jurnalga yozildi."),
			}
	return result
