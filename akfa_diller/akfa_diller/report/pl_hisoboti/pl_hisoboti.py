# -*- coding: utf-8 -*-
# PL Hisoboti — Akfa diller uchun P&L (foyda-zarar) hisoboti.
#
# Dvigatel (davr-bo'lish, GL asosidagi P&L, qator-dizayni) jazira_app'ning
# pl_hisoboti reportidan olingan. Akfa uchun moslashtirilgan qism:
#
#   - Jazira'da xarajat-guruhlar hisob-raqamga (52001/52002) qotirilgan edi;
#     Akfa kompaniyalarida (SD/K/Os/If) xarajat-daraxti nomli guruhlarda
#     (Avtotransport/Ijara/Kommunal/...). Shu sabab guruhlash DINAMIK:
#     har leaf Expense-hisob o'z OTA-GURUHI nomi ostida chiqadi, bo'sh
#     bo'limlar ko'rsatilmaydi — hisob qo'shilsa hisobot o'zi kengayadi.
#   - Себестоимость = account_type 'Cost of Goods Sold' + 'Stock Adjustment'
#     (Umumiy paneldagi "Sof natija" kartasi bilan bir xil ta'rif).
#   - "Sotuvlar soni (chek)" va "o'rtacha chek" saqlangan (SD/K aralash
#     assortiment — kg-hajm ma'nosiz).

import frappe
from frappe import _
from frappe.utils import flt, getdate
from datetime import date, timedelta

COGS_TURLARI = ("Cost of Goods Sold", "Stock Adjustment")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = filters.get("company")
	if not company:
		frappe.throw(_("Avval kompaniyani tanlang"))

	period_list = build_period_list(filters)
	if not period_list:
		return [], []

	from_date = str(period_list[0]["from_date"])
	to_date = str(period_list[-1]["to_date"])

	cc_list = _cost_center_subtree(filters.get("cost_center"))
	gl_rows = fetch_gl(company, from_date, to_date, cc_list)
	order_rows = fetch_order_counts(company, from_date, to_date, cc_list)
	abbr = frappe.get_cached_value("Company", company, "abbr") or ""

	pdata, cogs_hisoblar = aggregate(period_list, gl_rows, order_rows, abbr)
	daraxt = fetch_expense_tree(company)

	columns = get_columns(period_list)
	data = build_rows(period_list, pdata, daraxt, cogs_hisoblar)
	return columns, data


# ─── Davr ro'yxati (jazira bilan bir xil dvigatel) ──────────────────────────

def build_period_list(filters):
	today = date.today()
	from_date = getdate(filters.get("from_date") or date(today.year, 1, 1))
	to_date = getdate(filters.get("to_date") or today)
	periodicity = filters.get("periodicity") or "Monthly"

	periods = []
	current = from_date
	while current <= to_date:
		y, m = current.year, current.month
		if periodicity == "Monthly":
			p_start = date(y, m, 1)
			nm = m + 1
			p_end = (date(y, nm, 1) - timedelta(days=1)) if nm <= 12 else date(y, 12, 31)
			label = p_start.strftime("%b %Y")
			next_cur = date(y, nm, 1) if nm <= 12 else date(y + 1, 1, 1)
		elif periodicity == "Quarterly":
			q = (m - 1) // 3
			qs, qe = q * 3 + 1, q * 3 + 3
			p_start = date(y, qs, 1)
			p_end = (date(y, qe + 1, 1) - timedelta(days=1)) if qe < 12 else date(y, 12, 31)
			label = f"Q{q + 1} {y}"
			nqs = qe + 1
			next_cur = date(y, nqs, 1) if nqs <= 12 else date(y + 1, 1, 1)
		elif periodicity == "Half-Yearly":
			if m <= 6:
				p_start, p_end, label = date(y, 1, 1), date(y, 6, 30), f"H1 {y}"
				next_cur = date(y, 7, 1)
			else:
				p_start, p_end, label = date(y, 7, 1), date(y, 12, 31), f"H2 {y}"
				next_cur = date(y + 1, 1, 1)
		else:  # Yearly
			p_start, p_end, label = date(y, 1, 1), date(y, 12, 31), str(y)
			next_cur = date(y + 1, 1, 1)

		actual_start = max(p_start, from_date)
		actual_end = min(p_end, to_date)
		if actual_start <= actual_end:
			periods.append({"key": label, "label": label,
			                "from_date": actual_start, "to_date": actual_end})
		current = next_cur
	return periods


# ─── Ma'lumot ────────────────────────────────────────────────────────────────

def _cost_center_subtree(cost_center):
	"""Tanlangan cost-center (guruh bo'lsa — butun ostidagilari bilan).
	Bo'sh bo'lsa None — filtr qo'llanmaydi."""
	if not cost_center:
		return None
	lft, rgt = frappe.db.get_value("Cost Center", cost_center, ["lft", "rgt"]) or (None, None)
	if lft is None:
		return [cost_center]
	return [r[0] for r in frappe.db.sql(
		"""SELECT name FROM `tabCost Center` WHERE lft >= %s AND rgt <= %s""",
		(lft, rgt))] or [cost_center]


def fetch_gl(company, from_date, to_date, cc_list=None):
	cc_shart = ""
	params = [company, from_date, to_date]
	if cc_list:
		cc_shart = " AND gle.cost_center IN %s"
		params.append(tuple(cc_list))
	return frappe.db.sql("""
		SELECT
			gle.posting_date,
			gle.account,
			acc.parent_account,
			acc.account_name,
			acc.root_type,
			acc.account_type,
			SUM(gle.debit) AS debit,
			SUM(gle.credit) AS credit
		FROM `tabGL Entry` gle
		JOIN `tabAccount` acc ON acc.name = gle.account
		WHERE gle.company = %s
		  AND gle.is_cancelled = 0
		  AND gle.posting_date BETWEEN %s AND %s
		  -- Yil yopilganda Period Closing Voucher butun natijani teskari
		  -- yozadi; chiqarilmasa hisobot nolga tushib qolardi.
		  AND gle.voucher_type != 'Period Closing Voucher'
		  AND acc.root_type IN ('Income', 'Expense'){cc}
		GROUP BY gle.posting_date, gle.account
		ORDER BY gle.posting_date
	""".replace("{cc}", cc_shart), tuple(params), as_dict=True)


def fetch_expense_tree(company):
	"""Kompaniyaning BUTUN Expense-daraxti (guruhlar ham) — hisobot xarajatlarni
	daftardagi ierarxiyaning o'zidek (lft-tartibda) ko'rsatishi uchun."""
	return frappe.db.sql("""
		SELECT name, account_name, parent_account, is_group, lft, rgt, account_type
		FROM `tabAccount`
		WHERE company = %s AND root_type = 'Expense'
		ORDER BY lft
	""", (company,), as_dict=True)


def fetch_order_counts(company, from_date, to_date, cc_list=None):
	cc_shart = ""
	params = [company, from_date, to_date]
	if cc_list:
		# chek filial-kesimida: hujjat qatorlari shu cost-centerga tegishli
		cc_shart = """ AND EXISTS (SELECT 1 FROM `tabSales Invoice Item` sii
			WHERE sii.parent = si.name AND sii.cost_center IN %s)"""
		params.append(tuple(cc_list))
	return frappe.db.sql("""
		SELECT si.posting_date, COUNT(*) AS cnt
		FROM `tabSales Invoice` si
		WHERE si.company = %s AND si.docstatus = 1
		  AND si.posting_date BETWEEN %s AND %s{cc}
		GROUP BY si.posting_date
	""".replace("{cc}", cc_shart), tuple(params), as_dict=True)


# ─── Yig'ish ─────────────────────────────────────────────────────────────────

def _period_key(posting_date, period_list):
	for p in period_list:
		if p["from_date"] <= posting_date <= p["to_date"]:
			return p["key"]
	return None


def _fk(period_key):
	return "v_" + period_key.replace(" ", "_").replace("-", "_")


def _guruh_nomi(parent_account, abbr):
	nom = parent_account or _("Boshqa xarajatlar")
	suffix = " - " + abbr
	if abbr and nom.endswith(suffix):
		nom = nom[: -len(suffix)]
	return nom


def aggregate(period_list, gl_rows, order_rows, abbr):
	def empty():
		return dict(revenue=0, cogs=0, cogs_detail={}, leaf={}, orders=0)

	pdata = {p["key"]: empty() for p in period_list}
	cogs_hisoblar = set()

	for r in gl_rows:
		pk = _period_key(r.posting_date, period_list)
		if not pk:
			continue
		d = pdata[pk]
		net = flt(r.debit) - flt(r.credit)

		if r.root_type == "Income":
			d["revenue"] += flt(r.credit) - flt(r.debit)
			continue
		if r.account_type in COGS_TURLARI:
			d["cogs"] += net
			# hisob-kesimida ham (kompaniyada qanday yozilgan bo'lsa,
			# shunday ko'rinadi: CoGS va Stock Adjustment alohida qatorda)
			d["cogs_detail"][r.account_name] = (
				d["cogs_detail"].get(r.account_name, 0) + net)
			cogs_hisoblar.add(r.account_name)
			continue

		d["leaf"][r.account] = d["leaf"].get(r.account, 0) + net

	for r in order_rows:
		pk = _period_key(r.posting_date, period_list)
		if pk:
			pdata[pk]["orders"] += int(r.cnt or 0)

	return pdata, cogs_hisoblar


# ─── Ustunlar ────────────────────────────────────────────────────────────────

def get_columns(period_list):
	cols = [{"fieldname": "label", "label": _("Ko'rsatkich"),
	         "fieldtype": "Data", "width": 310}]
	for p in period_list:
		cols.append({"fieldname": _fk(p["key"]), "label": p["label"],
		             "fieldtype": "Currency", "options": "currency", "width": 150})
	return cols


# ─── Qatorlar ────────────────────────────────────────────────────────────────

def build_rows(period_list, pdata, daraxt, cogs_hisoblar=None):
	fkeys = [_fk(p["key"]) for p in period_list]

	def mk(label, getter=None, value_map=None, row_type="detail", level=0,
	       is_percent=False, is_ratio=False, is_qty=False, indent=None):
		r = {"label": label, "row_type": row_type, "indent_level": level,
		     "is_percent": 1 if is_percent else 0,
		     "is_ratio": 1 if is_ratio else 0,
		     "is_qty": 1 if is_qty else 0}
		if indent is not None:
			r["indent"] = indent
		if value_map is None and getter is not None:
			value_map = {_fk(p["key"]): getter(pdata[p["key"]]) for p in period_list}
		r.update(value_map or {})
		return r

	def divider():
		r = {"label": "", "row_type": "divider", "indent_level": 0}
		for fk in fkeys:
			r[fk] = None
		return r

	def per_period(fn):
		return {_fk(p["key"]): fn(pdata[p["key"]]) for p in period_list}

	rows = []

	rows.append(mk(_("Sotuvlar soni (chek)"), getter=lambda d: d["orders"],
	               row_type="root", is_qty=True))
	rows.append(divider())

	rows.append(mk(_("Tushum (savdo daromadi)"), getter=lambda d: d["revenue"],
	               row_type="root"))
	rows.append(mk(_("o'rtacha chek"),
		value_map=per_period(lambda d: d["revenue"] / d["orders"] if d["orders"] else 0),
		row_type="ratio", level=1, is_ratio=True))
	rows.append(divider())

	rows.append(mk(_("Sotilgan tovar tannarxi"), getter=lambda d: d["cogs"],
	               row_type="root"))
	# tannarx tarkibi — kompaniya daftaridagi hisob-nomlari bilan
	for acc in sorted(cogs_hisoblar or []):
		arow = {_fk(p["key"]): pdata[p["key"]]["cogs_detail"].get(acc, 0)
		        for p in period_list}
		if all(not v for v in arow.values()):
			continue
		rows.append(mk(acc, value_map=arow, row_type="detail", level=1, indent=1))
	rows.append(mk(_("Yalpi (marjinal) foyda"),
		value_map=per_period(lambda d: d["revenue"] - d["cogs"]), row_type="result"))
	rows.append(mk(_("marja"),
		value_map=per_period(lambda d: (d["revenue"] - d["cogs"]) / d["revenue"] * 100
		                     if d["revenue"] else 0),
		row_type="percent", level=1, is_percent=True))
	rows.append(divider())

	def _opex(d):
		return sum(d["leaf"].values())

	rows.append(mk(_("Xarajatlar"), value_map=per_period(_opex),
	               row_type="root"))

	# XARAJAT-DARAXTI daftardagi ierarxiyaning o'zidek (foydalanuvchi
	# 2026-09-02: "indirect ichidagi grouplar hamma-hammasi to'g'ri
	# ko'rinishi kerak"): guruh -> ichki guruh -> hisob, CoA (lft) tartibida.
	# COGS-turli hisoblar tannarx-blokida — bu yerda qatnashmaydi; harakati
	# bo'lmagan hisob/guruh ko'rsatilmaydi.
	tugunlar = {a.name: a for a in daraxt}
	bolalar = {}
	for a in daraxt:
		bolalar.setdefault(a.parent_account, []).append(a)  # lft-tartib saqlanadi

	def leaf_qiymat(nom):
		return {_fk(p["key"]): pdata[p["key"]]["leaf"].get(nom, 0)
		        for p in period_list}

	def shoxcha_jami(node):
		"""Guruh ostidagi barcha (COGS bo'lmagan) leaf-yig'indi, davr kesimida."""
		jami = {_fk(p["key"]): 0 for p in period_list}
		for a in daraxt:
			if a.is_group or a.account_type in COGS_TURLARI:
				continue
			if node.lft < a.lft < node.rgt:
				for p in period_list:
					fk = _fk(p["key"])
					jami[fk] += pdata[p["key"]]["leaf"].get(a.name, 0)
		return jami

	def chiz(node, level):
		if node.is_group:
			jami = shoxcha_jami(node)
			if all(not v for v in jami.values()):
				return
			rows.append(mk(node.account_name, value_map=jami,
			               row_type="sub", level=level, indent=max(level - 1, 0)))
			for bola in bolalar.get(node.name, []):
				chiz(bola, level + 1)
		else:
			if node.account_type in COGS_TURLARI:
				return
			vals = leaf_qiymat(node.name)
			if all(not v for v in vals.values()):
				return
			rows.append(mk(node.account_name, value_map=vals,
			               row_type="detail", level=level, indent=max(level - 1, 0)))

	# Expense-ildiz(lar)dan boshlaymiz — ildizning o'zi chizilmaydi
	for a in daraxt:
		if not a.parent_account or a.parent_account not in tugunlar:
			for bola in bolalar.get(a.name, []):
				chiz(bola, 1)
	rows.append(divider())

	def _net(d):
		return d["revenue"] - d["cogs"] - _opex(d)

	rows.append(mk(_("Sof foyda"), value_map=per_period(_net), row_type="result"))
	rows.append(mk(_("rentabellik"),
		value_map=per_period(lambda d: _net(d) / d["revenue"] * 100 if d["revenue"] else 0),
		row_type="percent", level=1, is_percent=True))
	return rows
