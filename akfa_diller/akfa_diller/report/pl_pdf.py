# -*- coding: utf-8 -*-
# PL hisobotlari uchun PDF chiqaruvchi (akfa_diller).
#
# Dvigatel jazira_app/report/pl_pdf.py dan olingan — u yerda hal qilingan
# asosiy muammolar shu yerda ham saqlanadi:
#   1) TOR JADVAL, UZOQ BO'SHLIQ: sahifa o'lchami ustun soniga qarab eng
#      kichigi tanlanadi, ustun kengliklari aniq belgilanadi.
#   2) OYLIK FILTRDA OXIRGI OYLAR SAHIFADAN CHIQIB KETISHI (foydalanuvchi
#      shikoyati): ko'p ustunda sahifa KENGLIGI o'zi hisoblanadi ("1608pt
#      842pt" ko'rinishida — "landscape" so'zi bilan birga yozilmaydi, aks
#      holda qoida tashlanib A4 tikka qaytadi va o'ng tomoni kesiladi).
#   3) SARLAVHA HAR SAHIFADA (thead: table-header-group).
#
# Dizayn akfa Biznes paneli palitrasida (bosiq ko'k, ortiqcha bezaksiz).
# PDF-motor: weasyprint bo'lsa u (aniqroq), bo'lmasa frappe'ning wkhtmltopdf
# yo'li (prodda weasyprint o'rnatilmagan bo'lishi mumkin).

import json

import frappe
from frappe import _
from frappe.utils import flt

# ── Ranglar (Biznes paneli uslubi) ──────────────────────────────────────────
C_HEADER_BG = "#14304a"
C_SECTION_BG = "#3b7ddd"
C_RESULT_BG = "#1f5cb0"
C_SUB_BG = "#e8f0fb"
C_NEG = "#c0392b"

# ── O'lchamlar (punktda) ────────────────────────────────────────────────────
MARGIN = 26
MIN_LABEL_W = 260
MAX_LABEL_W = 340
MIN_VALUE_W = 100
MAX_VALUE_W = 145

PAGE_SIZES = [
	("A4 landscape", 842, 595),
	("A3 landscape", 1191, 842),
]
MAX_PAGE_W = 14000


def _pick_page(n_cols):
	"""Ustunlar soniga mos eng KICHIK sahifa; juda ko'p ustunda kenglik
	o'zi hisoblanadi — oxirgi oylar hech qachon kesilmaydi."""
	needed = MIN_LABEL_W + n_cols * MIN_VALUE_W + 2 * MARGIN
	for name, w, h in PAGE_SIZES:
		if needed <= w:
			return name, w, h
	width = min(MAX_PAGE_W, MIN_LABEL_W + n_cols * (MIN_VALUE_W + 8) + 2 * MARGIN)
	return f"{width}pt 842pt", width, 842


def _layout(n_cols):
	page_size, page_w, page_h = _pick_page(n_cols)
	content_w = page_w - 2 * MARGIN

	value_w = (content_w - MIN_LABEL_W) / n_cols if n_cols else content_w
	value_w = max(MIN_VALUE_W, min(MAX_VALUE_W, value_w))

	label_w = min(MAX_LABEL_W, content_w - n_cols * value_w)
	if label_w < MIN_LABEL_W:
		label_w = MIN_LABEL_W
		value_w = (content_w - label_w) / n_cols if n_cols else content_w

	base_font = 9.5 if n_cols <= 4 else (8.5 if n_cols <= 8 else 7.5)
	return {
		"page_size": page_size,
		"page_w": page_w,
		"page_h": page_h,
		"label_w": round(label_w, 1),
		"value_w": round(value_w, 1),
		"table_w": round(label_w + n_cols * value_w, 1),
		"font": base_font,
	}


# ── Raqam formati ───────────────────────────────────────────────────────────

def _num(v):
	n = round(flt(v))
	txt = f"{abs(n):,.0f}".replace(",", " ")
	return f"({txt})" if n < 0 else txt


def _cell_text(row, value):
	rt = row.get("row_type")
	if rt == "percent" or row.get("is_percent"):
		return f"{round(flt(value))}%"
	if rt == "ratio" or row.get("is_ratio"):
		return f"{flt(value):,.2f}".replace(",", " ").replace(".", ",")
	if row.get("is_qty"):
		return f"{round(flt(value)):,.0f}".replace(",", " ")
	return _num(value)


def _row_html(row, fkeys):
	rt = row.get("row_type") or "plain"
	if rt == "divider":
		return f'<tr class="r-divider"><td colspan="{len(fkeys) + 1}"></td></tr>'

	level = int(row.get("indent_level") or row.get("indent") or 0)
	label = frappe.utils.escape_html(row.get("label") or "")
	guides = "".join('<span class="g"></span>' for _ in range(level))
	cells = [f'<td class="lbl lv{min(level, 4)}">{guides}<span class="t">{label}</span></td>']

	for fk in fkeys:
		v = row.get(fk)
		if v is None or v == "":
			cells.append('<td class="val"></td>')
			continue
		txt = _cell_text(row, v)
		neg = "neg" if txt.startswith("(") else ""
		cells.append(f'<td class="val {neg}">{txt}</td>')
	return f'<tr class="r-{rt}">{"".join(cells)}</tr>'


# ── Sahifa qurish ───────────────────────────────────────────────────────────

def build_html(title, subtitle, filters, columns, data, meta_lines=None):
	if not columns:
		return "<html><body><p>Ma'lumot topilmadi.</p></body></html>", _layout(1)

	period_cols = columns[1:]
	fkeys = [c["fieldname"] for c in period_cols]
	L = _layout(len(fkeys)) if fkeys else _layout(1)

	head_cells = "".join(
		f'<th class="val">{frappe.utils.escape_html(str(c["label"]))}</th>'
		for c in period_cols)
	body_rows = "".join(_row_html(r, fkeys) for r in data)
	cols_html = (f'<col style="width:{L["label_w"]}pt">'
	             + f'<col style="width:{L["value_w"]}pt">' * len(fkeys))
	meta = "".join(f"<div>{frappe.utils.escape_html(m)}</div>" for m in (meta_lines or []))
	f = L["font"]

	html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  @page {{
    size: {L["page_size"]}; margin: {MARGIN}pt;
    @bottom-right {{ content: counter(page) " / " counter(pages);
      font-family: 'DejaVu Sans'; font-size: 7pt; color: #94a3b8; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'DejaVu Sans', Arial, sans-serif; font-size: {f}pt;
          color: #111; margin: 0; }}

  .hdr {{ background: {C_HEADER_BG}; color: #fff; padding: 11pt 14pt;
          border-radius: 5pt; margin-bottom: 10pt; display: flex;
          justify-content: space-between; align-items: center; }}
  .hdr .kick {{ font-size: {f - 2}pt; letter-spacing: 1.6pt;
                text-transform: uppercase; opacity: .55; }}
  .hdr .ttl {{ font-size: {f + 5}pt; font-weight: 700; margin-top: 2pt; }}
  .hdr .rgt {{ text-align: right; font-size: {f - 1}pt; opacity: .8;
               line-height: 1.7; }}

  table {{ width: {L["table_w"]}pt; border-collapse: collapse;
           table-layout: fixed; }}
  thead {{ display: table-header-group; }}
  thead th {{ background: #eef1f4; color: {C_HEADER_BG}; font-weight: 700;
              text-align: right; padding: 5pt 7pt;
              border-bottom: 1.5pt solid #cfd6dd; font-size: {f - .5}pt; }}
  thead th.lbl {{ text-align: left; }}

  td {{ padding: 3.4pt 7pt; }}
  td.lbl {{ text-align: left; overflow: hidden; text-overflow: ellipsis;
            white-space: nowrap; }}
  td.val {{ text-align: right; white-space: nowrap;
            font-variant-numeric: tabular-nums; }}
  td.neg {{ color: {C_NEG}; }}

  td.lbl .g {{ display: inline-block; width: 11pt; height: {f}pt;
               vertical-align: middle; border-left: .6pt solid #cbd5e1;
               margin-right: 3pt; }}
  td.lbl .t {{ vertical-align: middle; }}

  tr.r-root td {{ background: {C_SECTION_BG}; color: #fff; font-weight: 700;
                  text-transform: uppercase; letter-spacing: .2pt; }}
  tr.r-root td.neg {{ color: #ffe0db; }}
  tr.r-root td.lbl .g {{ border-left-color: rgba(255,255,255,.4); }}

  tr.r-result td {{ background: {C_RESULT_BG}; color: #fff; font-weight: 800; }}
  tr.r-result td.neg {{ color: #ffe0db; }}
  tr.r-result td.lbl .g {{ border-left-color: rgba(255,255,255,.4); }}

  tr.r-sub td {{ background: {C_SUB_BG}; color: #111; font-weight: 700; }}

  tr.r-detail td {{ background: #fff; color: #333; font-size: {f - .8}pt;
                    border-bottom: .4pt solid #f1f5f9; }}
  tr.r-detail:nth-child(even) td {{ background: #fafbfc; }}

  tr.r-percent td, tr.r-ratio td {{ background: #fff; color: #64748b;
                                    font-style: italic; font-size: {f - 1}pt;
                                    border-bottom: .4pt solid #f1f5f9; }}

  tr.r-divider td {{ padding: 3pt 0; border: none; background: #fff; }}
</style></head>
<body>
  <div class="hdr">
    <div>
      <div class="kick">{frappe.utils.escape_html(subtitle)}</div>
      <div class="ttl">{frappe.utils.escape_html(title)}</div>
    </div>
    <div class="rgt">{meta}</div>
  </div>
  <table>
    <colgroup>{cols_html}</colgroup>
    <thead><tr><th class="lbl">Ko'rsatkich</th>{head_cells}</tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
</body></html>"""
	return html, L


def _pdf_bytes(html, L):
	"""weasyprint bo'lsa — u (aniq sahifa-o'lchami); bo'lmasa wkhtmltopdf
	(frappe standarti) — sahifa o'lchami mm'da uzatiladi."""
	try:
		from weasyprint import HTML  # noqa
		return HTML(string=html).write_pdf()
	except ImportError:
		from frappe.utils.pdf import get_pdf
		pt2mm = 0.352778
		return get_pdf(html, options={
			"page-width": f"{round(L['page_w'] * pt2mm, 1)}mm",
			"page-height": f"{round(L['page_h'] * pt2mm, 1)}mm",
			"margin-top": "9mm", "margin-bottom": "9mm",
			"margin-left": "9mm", "margin-right": "9mm",
		})


def meta_from_filters(filters, extra_keys=None):
	lines = []
	fd, td = filters.get("from_date"), filters.get("to_date")
	if fd or td:
		lines.append(f"{fd or ''} — {td or ''}")
	if filters.get("periodicity"):
		lines.append(str(filters["periodicity"]))
	for key in (extra_keys or []):
		if filters.get(key):
			lines.append(str(filters[key]))
	return lines


def check_report_permission(report_name):
	"""PDF endpoint hisobotning O'ZI bilan bir xil huquq talab qilsin."""
	if not frappe.db.exists("Report", report_name):
		frappe.throw(_("Hisobot topilmadi: {0}").format(report_name))
	if not frappe.get_doc("Report", report_name).is_permitted():
		raise frappe.PermissionError(
			_("Sizda «{0}» hisobotini ko'rish huquqi yo'q").format(report_name))
	frappe.has_permission("Report", "read", doc=report_name, throw=True)


def send(filters, execute_fn, title, subtitle, filename_prefix,
         extra_meta_keys=None, report_name=None):
	"""Hisobotni PDF qilib brauzerga yuboradi (serverda saqlanmaydi)."""
	if report_name:
		check_report_permission(report_name)

	if isinstance(filters, str):
		filters = json.loads(filters)
	filters = frappe._dict(filters or {})

	columns, data = execute_fn(filters)
	html, L = build_html(title, subtitle, filters, columns, data,
	                     meta_from_filters(filters, extra_meta_keys))

	frappe.local.response.filename = "%s_%s_%s.pdf" % (
		filename_prefix,
		str(filters.get("from_date") or "")[:10],
		str(filters.get("to_date") or "")[:10],
	)
	frappe.local.response.filecontent = _pdf_bytes(html, L)
	frappe.local.response.type = "download"
