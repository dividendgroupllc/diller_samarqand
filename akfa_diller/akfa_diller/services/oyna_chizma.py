# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""Oyna pozitsiyasi uchun texnik SVG-chizma (toza Python, DB'siz).

Yagona manba: dialog-preview, forma-kartalar, PDF (weasyprint/wkhtmltopdf) va
Print Format shu funksiyani ishlatadi. SVG 1.1 doirasida (foreignObject, CSS
o'zgaruvchi, auto-start-reverse YO'Q — wkhtmltopdf/QtWebKit uchun).

Koordinata-konvensiyasi: X chapdan, Y PASTDAN (mm). Tomonlar ①yuqori ②o'ng
③pastki ④chap (soat yo'nalishi). Burchaklar: yc/yo/po/pc.
"""

import math
from html import escape

ROW = 16
QIRRA_STIL = {  # (stroke-width, ichki chiziq, ichki dasharray, yorliq)
	"Pritupka": (1.4, False, None, "pritupka"),
	"Shlifovka": (2.0, False, None, "shlifovka"),
	"Polirovka": (2.4, True, None, "polirovka"),
	"Faska": (2.0, True, "4 2", "faska"),
}
QIRRA_CHIZMA = {  # Ishlov turlari jadvalidagi "Chizmada ko'rinishi" → (stroke, ichki chiziq, dasharray)
	"Ingichka chiziq": (1.4, False, None), "O'rta chiziq": (2.0, False, None), "Qalin chiziq": (2.4, False, None),
	"Qalin + ichki chiziq": (2.4, True, None), "Punktir ichki chiziq": (2.0, True, "4 2"),
}
TOMONLAR = (("qirra_yuqori", "①"), ("qirra_ong", "②"), ("qirra_pastki", "③"), ("qirra_chap", "④"))
RANG = dict(ink="#111111", dim="#334155", muted="#64748b", hatch="#64748b", glass="#eef4fb",
            glass_print="#f3f3f3", accent="#1e40af", amber="#e8a33d", white="#ffffff",
            qoldiq="#d9f99d", qoldiq_stroke="#4d7c0f", chiqindi="#fecaca", chiqindi_stroke="#b91c1c")
# Raskroy-xaritasi: pozitsiya raqami bo'yicha pastel ranglar (qora yozuv hammasida o'qiladi)
PALITRA = ("#dbeafe", "#fef3c7", "#e0e7ff", "#fce7f3", "#ccfbf1", "#ffedd5", "#ede9fe", "#f1f5f9")


def _i(v, default=0):
	try:
		return int(float(v)) if v not in (None, "") else default
	except (TypeError, ValueError):
		return default


def _f(v, default=0.0):
	try:
		return float(v) if v not in (None, "") else default
	except (TypeError, ValueError):
		return default


def _fmt(v):
	v = float(v)
	return str(int(round(v))) if abs(v - round(v)) < 1e-6 else f"{v:.1f}"


def bounding(poz):
	shakl = poz.get("shakl") or "To'rtburchak"
	if shakl == "Doira":
		d = _i(poz.get("diametr_mm")) or _i(poz.get("eni_mm"))
		return shakl, d, d
	return shakl, _i(poz.get("eni_mm")), _i(poz.get("boyi_mm"))


def hisob(poz, konst=None):
	"""m²/kg (chizma-yorliqlari uchun; dvigatel bilan bir xil formula)."""
	step = (konst or {}).get("yaxlitlash_mm", 10)
	minm = (konst or {}).get("min_maydon_m2", 0.25)
	shakl, W, H = bounding(poz)
	hw = int(math.ceil(round(W / step, 6)) * step) if W > 0 else 0
	hh = int(math.ceil(round(H / step, 6)) * step) if H > 0 else 0
	net = round(hw * hh / 1e6, 4)
	dona = max(_i(poz.get("dona"), 1), 1)
	t = _i(poz.get("qalinlik_mm"))
	return {"m2_net": net, "m2_hisob": max(net, minm) if net else 0.0,
	        "ogirlik_kg": round(W * H / 1e6 * t * 2.5 * dona, 2)}


def layout(poz, teshiklar, kesimlar, box_w=320, box_h=240, legend=False):
	shakl, W, H = bounding(poz)
	W, H = max(W, 1), max(H, 1)
	tesh_koord = [t for t in (teshiklar or []) if _i(t.get("x_mm")) and _i(t.get("y_mm"))]  # (0,0)/bo'sh = koordinatasiz
	kes_koord = [k for k in (kesimlar or []) if k.get("x_mm") not in (None, "") and k.get("y_mm") not in (None, "")]
	qisman = (poz.get("matofka_rejimi") == "Qisman" and _i(poz.get("matofka_w")) and _i(poz.get("matofka_h")))
	rows = 1 + (1 if tesh_koord else 0) + (1 if kes_koord else 0) + (1 if qisman else 0)
	ml = 34 + rows * ROW
	mb = 30 + rows * ROW + (14 if legend else 0)
	mt, mr = 30, 36
	aw, ah = box_w - ml - mr, box_h - mt - mb
	s = min(aw / W, ah / H)
	sx = sy = s
	aniz = False
	if min(W, H) * s < 14 or max(W, H) / max(min(W, H), 1) > 8:
		aniz = True
		if W < H:
			sx = min(max(s, 14.0 / W), aw / W)
		else:
			sy = min(max(s, 14.0 / H), ah / H)
	ox = ml + (aw - W * sx) / 2.0
	oy = mt + (ah - H * sy) / 2.0
	return dict(shakl=shakl, W=W, H=H, sx=sx, sy=sy, ox=ox, oy=oy, ml=ml, mb=mb, mt=mt, mr=mr,
	            box_w=box_w, box_h=box_h, aniz=aniz, tesh_koord=tesh_koord, kes_koord=kes_koord,
	            qisman=bool(qisman), rows=rows)


class _Chizuvchi:
	def __init__(self, L, opts):
		self.L = L
		self.o = opts
		self.p = opts.get("prefix", "p1")
		self.stil = opts.get("stil") or {}  # {"qirra": {nom: chizma}, "burchak": {nom: chizma}}
		self.out = []
		self.font = opts.get("font", 10)
		self.ink = RANG["ink"]
		self.acc = RANG["ink"] if opts.get("for_print") else RANG["accent"]
		self.glass = RANG["glass_print"] if opts.get("for_print") else RANG["glass"]

	# koordinata
	def X(self, x):
		return self.L["ox"] + x * self.L["sx"]

	def Y(self, y):
		return self.L["oy"] + (self.L["H"] - y) * self.L["sy"]

	def add(self, s):
		self.out.append(s)

	def text(self, x, y, s, size=None, anchor="middle", rotate=None, weight=None, fill=None, italic=False):
		size = size or self.font - 1
		attrs = [f'x="{x:.1f}"', f'y="{y + size * 0.35:.1f}"', f'font-size="{size}"', f'text-anchor="{anchor}"',
		         f'fill="{fill or self.ink}"', 'font-family="DejaVu Sans, Arial, sans-serif"']
		if weight:
			attrs.append(f'font-weight="{weight}"')
		if italic:
			attrs.append('font-style="italic"')
		if rotate is not None:
			attrs.append(f'transform="rotate({rotate} {x:.1f} {y:.1f})"')
		self.add(f"<text {' '.join(attrs)}>{escape(str(s), quote=False)}</text>")

	def line(self, x1, y1, x2, y2, w=0.8, color=None, dash=None, markers=False):
		a = [f'x1="{x1:.1f}"', f'y1="{y1:.1f}"', f'x2="{x2:.1f}"', f'y2="{y2:.1f}"',
		     f'stroke="{color or self.ink}"', f'stroke-width="{w}"']
		if dash:
			a.append(f'stroke-dasharray="{dash}"')
		if markers:
			a.append(f'marker-start="url(#{self.p}-arr-s)" marker-end="url(#{self.p}-arr-e)"')
		self.add(f"<line {' '.join(a)}/>")

	# ---------------------------------------------------------------- qismlar
	def defs(self, extra=""):
		p = self.p
		self.add(f'<defs><marker id="{p}-arr-s" markerWidth="8" markerHeight="8" refX="1" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M8,1 L1,4 L8,7 Z" fill="{RANG["dim"]}"/></marker>'
		         f'<marker id="{p}-arr-e" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,1 L7,4 L0,7 Z" fill="{RANG["dim"]}"/></marker>'
		         f'<pattern id="{p}-hatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="{RANG["hatch"]}" stroke-width="0.8"/></pattern>'
		         + (f'<clipPath id="{p}-clip"><path d="{self.piece_d}"/></clipPath>' if getattr(self, "piece_d", None) else "")
		         + extra + '</defs>')

	def prepare(self, poz):
		"""Kontur-path'ni oldindan hisoblash (clipPath defs ichida bo'lishi shart —
		weasyprint top-level clipPath'dan keyingi elementlarni chizmaydi)."""
		self.piece_d, self.corners = None, {}
		if self.L["shakl"] == "To'rtburchak":
			self.piece_d, self.corners = self.piece_path(poz)

	def piece_path(self, poz):
		"""To'rtburchak konturi (burchak-ishlovi bilan) — SVG path."""
		L = self.L
		W, H = L["W"], L["H"]
		X, Y = self.X, self.Y

		def cmm(k):
			tur = poz.get("burchak_" + k) or "Yo'q"
			mm = _i(poz.get(f"burchak_{k}_mm"))
			if tur == "Yo'q" or mm <= 0:
				return None, 0
			return tur, min(mm, min(W, H) // 2)

		yc, yo, po, pc = cmm("yc"), cmm("yo"), cmm("po"), cmm("pc")
		d = []
		# boshlanish: yuqori tomon, chap burchakdan keyin
		d.append(f"M{X(yc[1]):.1f},{Y(H):.1f}")
		d.append(f"L{X(W - yo[1]):.1f},{Y(H):.1f}")
		self._corner(d, yo, X(W), Y(H), X(W), Y(H - yo[1]), X(W - yo[1]), Y(H))
		d.append(f"L{X(W):.1f},{Y(po[1]):.1f}")
		self._corner(d, po, X(W), Y(0), X(W - po[1]), Y(0), X(W), Y(po[1]))
		d.append(f"L{X(pc[1]):.1f},{Y(0):.1f}")
		self._corner(d, pc, X(0), Y(0), X(0), Y(pc[1]), X(pc[1]), Y(0))
		d.append(f"L{X(0):.1f},{Y(H - yc[1]):.1f}")
		self._corner(d, yc, X(0), Y(H), X(yc[1]), Y(H), X(0), Y(H - yc[1]))
		d.append("Z")
		return " ".join(d), dict(yc=yc, yo=yo, po=po, pc=pc)

	def _qirra_stil(self, tur):
		ch = (self.stil.get("qirra") or {}).get(tur)
		if ch in QIRRA_CHIZMA:
			w, ichki, dash = QIRRA_CHIZMA[ch]
			return w, ichki, dash, tur.lower()
		return QIRRA_STIL.get(tur, (1.4, False, None, tur.lower()))

	def _kenglikli(self, tur):
		ch = (self.stil.get("qirra") or {}).get(tur)
		return ch == "Punktir ichki chiziq" if ch else tur == "Faska"

	def _yoy(self, tur):
		ch = (self.stil.get("burchak") or {}).get(tur)
		return ch == "Yoy (radius)" if ch else tur == "Radius"

	def _corner(self, d, c, cx, cy, ex, ey, sx_, sy_):
		tur, mm = c
		if not tur:
			return
		if self._yoy(tur):
			r = mm * self.L["sx"]
			ry = mm * self.L["sy"]
			d.append(f"A{r:.1f},{ry:.1f} 0 0 1 {ex:.1f},{ey:.1f}")
		else:
			d.append(f"L{ex:.1f},{ey:.1f}")

	def body(self, poz):
		L = self.L
		shakl = L["shakl"]
		if shakl == "Doira":
			cx, cy = self.X(L["W"] / 2.0), self.Y(L["H"] / 2.0)
			rx, ry = L["W"] / 2.0 * L["sx"], L["H"] / 2.0 * L["sy"]
			self.add(f'<ellipse id="{self.p}-piece" cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="{self.glass}" stroke="{self.ink}" stroke-width="0.8"/>')
			return
		if shakl == "Erkin shakl":
			self.add(f'<rect x="{self.X(0):.1f}" y="{self.Y(L["H"]):.1f}" width="{L["W"] * L["sx"]:.1f}" height="{L["H"] * L["sy"]:.1f}" fill="{self.glass}" stroke="{self.ink}" stroke-width="0.8" stroke-dasharray="6 3"/>')
			self.text(self.X(L["W"] / 2.0), self.Y(L["H"] / 2.0), "Erkin shakl — shablon bo'yicha", size=self.font - 1, italic=True, fill=RANG["muted"])
			return
		d = self.piece_d
		self.add(f'<path id="{self.p}-piece" d="{d}" fill="{self.glass}" stroke="{self.ink}" stroke-width="0.8"/>')

	def matofka(self, poz):
		L = self.L
		rej = poz.get("matofka_rejimi") or "Yo'q"
		if rej == "Yo'q":
			return
		if rej == "To'liq":
			if self.piece_d:
				self.add(f'<path d="{self.piece_d}" fill="url(#{self.p}-hatch)" stroke="none"/>')
			elif L["shakl"] == "Doira":
				cx, cy = self.X(L["W"] / 2.0), self.Y(L["H"] / 2.0)
				self.add(f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{L["W"] / 2.0 * L["sx"]:.1f}" ry="{L["H"] / 2.0 * L["sy"]:.1f}" fill="url(#{self.p}-hatch)" stroke="none"/>')
			else:
				self.add(f'<rect x="{self.X(0):.1f}" y="{self.Y(L["H"]):.1f}" width="{L["W"] * L["sx"]:.1f}" height="{L["H"] * L["sy"]:.1f}" fill="url(#{self.p}-hatch)" stroke="none"/>')
			return
		# Qisman
		x, y, w, h = _i(poz.get("matofka_x")), _i(poz.get("matofka_y")), _i(poz.get("matofka_w")), _i(poz.get("matofka_h"))
		if w and h:
			clip = f' clip-path="url(#{self.p}-clip)"' if self.piece_d else ""
			self.add(f'<rect x="{self.X(x):.1f}" y="{self.Y(y + h):.1f}" width="{w * L["sx"]:.1f}" height="{h * L["sy"]:.1f}" fill="url(#{self.p}-hatch)" stroke="{RANG["muted"]}" stroke-width="0.6" stroke-dasharray="3 2"{clip}/>')
			self.text(self.X(x + w / 2.0), self.Y(y + h / 2.0), f"Matofka {w}×{h}", size=self.font - 2, fill=RANG["muted"])
		else:
			m2 = _f(poz.get("matofka_maydoni_m2"))
			self.text(self.X(L["W"] / 2.0), self.Y(L["H"]) + 12, f"Matofka {m2:g} m² (joyi kelishiladi)", size=self.font - 2, fill=RANG["muted"], italic=True)

	def qirralar(self, poz):
		L = self.L
		W, H = L["W"], L["H"]
		X, Y = self.X, self.Y
		if L["shakl"] != "To'rtburchak":
			tur = poz.get("qirra_kontur") or "Yo'q"
			if tur == "Yo'q":
				return
			w, ichki, dash, nom = self._qirra_stil(tur)
			if L["shakl"] == "Doira":
				cx, cy = X(W / 2.0), Y(H / 2.0)
				self.add(f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{W / 2.0 * L["sx"]:.1f}" ry="{H / 2.0 * L["sy"]:.1f}" fill="none" stroke="{self.acc}" stroke-width="{w}"/>')
			lbl = nom + (f" {_i(poz.get('faska_kengligi_mm'))}" if self._kenglikli(tur) else "") + " (kontur)"
			self.text(X(W / 2.0), Y(0) + 9, lbl, size=self.font - 2, fill=self.acc)
			return
		c = self.corners
		segs = {
			"qirra_yuqori": ((X(c["yc"][1]), Y(H)), (X(W - c["yo"][1]), Y(H)), (0, -1)),
			"qirra_ong": ((X(W), Y(H - c["yo"][1])), (X(W), Y(c["po"][1])), (1, 0)),
			"qirra_pastki": ((X(W - c["po"][1]), Y(0)), (X(c["pc"][1]), Y(0)), (0, 1)),
			"qirra_chap": ((X(0), Y(c["pc"][1])), (X(0), Y(H - c["yc"][1])), (-1, 0)),
		}
		for fn, belgi in TOMONLAR:
			tur = poz.get(fn) or "Yo'q"
			(x1, y1), (x2, y2), (nx, ny) = segs[fn]
			# tomon raqami (doim)
			mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
			if tur == "Yo'q":
				continue
			w, ichki, dash, nom = self._qirra_stil(tur)
			self.line(x1, y1, x2, y2, w=w, color=self.acc)
			if ichki:
				off = max(4.0, _i(poz.get("faska_kengligi_mm")) * L["sx"]) if self._kenglikli(tur) else 3.0
				self.line(x1 - nx * off, y1 - ny * off, x2 - nx * off, y2 - ny * off, w=0.8, color=self.acc, dash=dash)
			lbl = f"{belgi} {nom}" + (f" {_i(poz.get('faska_kengligi_mm'))}" if self._kenglikli(tur) else "")
			uz = math.hypot(x2 - x1, y2 - y1)
			if uz < 40:
				lbl = belgi
			if fn == "qirra_yuqori":
				self.text(mx, my - 8, lbl, size=self.font - 2, fill=self.acc)
			elif fn == "qirra_pastki":
				self.text(mx, my + 9, lbl, size=self.font - 2, fill=self.acc)
			elif fn == "qirra_ong":
				self.text(mx + 9, my, lbl, size=self.font - 2, fill=self.acc, rotate=90)
			else:
				self.text(mx - 9, my, lbl, size=self.font - 2, fill=self.acc, rotate=-90)

	def burchaklar(self, poz):
		L = self.L
		W, H = L["W"], L["H"]
		X, Y = self.X, self.Y
		if L["shakl"] != "To'rtburchak":
			return
		nuq = {"yc": (X(0), Y(H), -1, -1), "yo": (X(W), Y(H), 1, -1), "po": (X(W), Y(0), 1, 1), "pc": (X(0), Y(0), -1, 1)}
		for k, (cx, cy, dx, dy) in nuq.items():
			tur, mm = self.corners.get(k, (None, 0))
			if not tur:
				continue
			ax, ay = cx + dx * 10, cy + dy * 10
			self.line(cx + dx * 2, cy + dy * 2, ax, ay, w=0.6, color=RANG["dim"])
			lbl = f"R{mm}" if self._yoy(tur) else f"{tur} {mm}"
			self.text(ax + dx * 2, ay + dy * 6, lbl, size=self.font - 2, anchor="end" if dx < 0 else "start", fill=RANG["dim"])

	def kesimlar(self, kesimlar):
		L = self.L
		W, H = L["W"], L["H"]
		for k in L["kes_koord"]:
			x, y, w, h = _i(k.get("x_mm")), _i(k.get("y_mm")), _i(k.get("eni_mm")), _i(k.get("boyi_mm"))
			r = _i(k.get("ichki_radius_mm")) * L["sx"]
			px, py = self.X(x), self.Y(y + h)
			pw, ph = w * L["sx"], h * L["sy"]
			self.add(f'<rect x="{px:.1f}" y="{py:.1f}" width="{pw:.1f}" height="{ph:.1f}" rx="{r:.1f}" ry="{r:.1f}" fill="{RANG["white"]}" stroke="{self.ink}" stroke-width="1"/>')
			# qirraga tegsa — konturni ochish (notch)
			er = 1.6
			if x == 0:
				self.line(px, py, px, py + ph, w=er, color=RANG["white"])
			if x + w == W:
				self.line(px + pw, py, px + pw, py + ph, w=er, color=RANG["white"])
			if y == 0:
				self.line(px, py + ph, px + pw, py + ph, w=er, color=RANG["white"])
			if y + h == H:
				self.line(px, py, px + pw, py, w=er, color=RANG["white"])
			tur = k.get("tur") or "Kesim"
			if pw > 44 and ph > 20:
				self.text(px + pw / 2.0, py + ph / 2.0 - (5 if ph > 26 else 0), tur, size=self.font - 2)
				if ph > 26:
					self.text(px + pw / 2.0, py + ph / 2.0 + 6, f"{w}×{h}", size=self.font - 3, fill=RANG["muted"])
			else:
				self.text(px + pw / 2.0, py - 5, f"{tur} {w}×{h}", size=self.font - 3, fill=RANG["muted"])
		# koordinatasiz kesimlar — izoh
		kk = [k for k in (kesimlar or []) if k not in L["kes_koord"]]
		if kk:
			self.text(self.X(W / 2.0), self.Y(H) - 20, "Kesim (joyi chizmada emas): " + ", ".join(f"{k.get('tur')} {_i(k.get('eni_mm'))}×{_i(k.get('boyi_mm'))}" for k in kk), size=self.font - 3, fill=RANG["muted"])

	def teshiklar(self, teshiklar):
		L = self.L
		for t in L["tesh_koord"]:
			x, y, d = _i(t.get("x_mm")), _i(t.get("y_mm")), _i(t.get("diametr_mm"))
			cx, cy = self.X(x), self.Y(y)
			r = max(3.0, d / 2.0 * L["sx"])
			self.add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{RANG["white"]}" stroke="{self.ink}" stroke-width="1"/>')
			self.line(cx - r - 2, cy, cx + r + 2, cy, w=0.5, color=RANG["dim"])
			self.line(cx, cy - r - 2, cx, cy + r + 2, w=0.5, color=RANG["dim"])
			lbl = f"Ø{d}" + (f" ×{_i(t.get('soni'))}" if _i(t.get("soni")) > 1 else "")
			self.text(cx + r + 3, cy - r - 3, lbl, size=self.font - 2, anchor="start", fill=RANG["dim"])
		kk = [t for t in (teshiklar or []) if t not in L["tesh_koord"]]
		if kk:
			self.text(self.X(L["W"] / 2.0), self.Y(L["H"]) - 10, "Teshik (joyi chizmada emas): " + ", ".join(f"Ø{_i(t.get('diametr_mm'))}×{max(_i(t.get('soni')), 1)}" for t in kk), size=self.font - 3, fill=RANG["muted"])

	# ---------------------------------------------------------------- o'lchamlar
	def dim_h(self, xs, y, weight=None, size=None):
		"""Gorizontal zanjir: xs — mm-nuqtalar, y — px. Qisqa segment matni chetga chiqariladi."""
		size = size or self.font - 1
		xs = sorted(set(xs))
		for x in xs:
			px = self.X(x)
			self.line(px, self.Y(0) + 3, px, y + 3, w=0.5, color=RANG["dim"])
		n = len(xs) - 1
		for i, (a, b) in enumerate(zip(xs, xs[1:])):
			xa, xb = self.X(a), self.X(b)
			self.line(xa, y, xb, y, w=0.7, color=RANG["dim"], markers=True)
			lbl = _fmt(b - a)
			tw = len(lbl) * size * 0.62 + 4
			if xb - xa >= tw:
				self.text((xa + xb) / 2.0, y - 5, lbl, size=size, weight=weight, fill=RANG["dim"])
			elif i == 0:
				self.text(xa - 3, y - 5, lbl, size=size - 1, anchor="end", fill=RANG["dim"])
			elif i == n - 1:
				self.text(xb + 3, y - 5, lbl, size=size - 1, anchor="start", fill=RANG["dim"])
			else:
				self.text((xa + xb) / 2.0, y - 5, lbl, size=max(size - 3, 6), fill=RANG["dim"])

	def dim_v(self, ys, x, weight=None, size=None):
		size = size or self.font - 1
		ys = sorted(set(ys))
		for y in ys:
			py = self.Y(y)
			self.line(self.X(0) - 3, py, x - 3, py, w=0.5, color=RANG["dim"])
		n = len(ys) - 1
		for i, (a, b) in enumerate(zip(ys, ys[1:])):
			ya, yb = self.Y(a), self.Y(b)  # ya pastda (katta px), yb yuqorida
			self.line(x, ya, x, yb, w=0.7, color=RANG["dim"], markers=True)
			lbl = _fmt(b - a)
			tw = len(lbl) * size * 0.62 + 4
			if ya - yb >= tw:
				self.text(x - 5, (ya + yb) / 2.0, lbl, size=size, weight=weight, fill=RANG["dim"], rotate=-90)
			elif i == 0:
				self.text(x - 5, ya + 3, lbl, size=size - 1, anchor="end", fill=RANG["dim"], rotate=-90)
			elif i == n - 1:
				self.text(x - 5, yb - 3, lbl, size=size - 1, anchor="start", fill=RANG["dim"], rotate=-90)
			else:
				self.text(x - 5, (ya + yb) / 2.0, lbl, size=max(size - 3, 6), fill=RANG["dim"], rotate=-90)

	def olchamlar(self, poz, teshiklar, kesimlar):
		L = self.L
		W, H = L["W"], L["H"]
		if L["shakl"] == "Doira":
			cx, cy = self.X(W / 2.0), self.Y(H / 2.0)
			rx = W / 2.0 * L["sx"]
			self.line(cx - rx, cy, cx + rx, cy, w=0.7, color=RANG["dim"], markers=True)
			self.text(cx - rx * 0.5, cy - 7, f"Ø {W}", size=self.font, weight="600", fill=RANG["dim"])
		else:
			self.dim_h([0, W], self.Y(0) + 26, weight="600", size=self.font)
			self.dim_v([0, H], self.X(0) - 26, weight="600", size=self.font)
		row = 2
		if L["tesh_koord"]:
			xs = [0, W] + [_i(t.get("x_mm")) for t in L["tesh_koord"]]
			ys = [0, H] + [_i(t.get("y_mm")) for t in L["tesh_koord"]]
			self.dim_h(xs, self.Y(0) + 26 + (row - 1) * ROW)
			self.dim_v(ys, self.X(0) - 26 - (row - 1) * ROW)
			row += 1
		if L["kes_koord"]:
			xs = [0, W]
			ys = [0, H]
			for k in L["kes_koord"]:
				xs += [_i(k.get("x_mm")), _i(k.get("x_mm")) + _i(k.get("eni_mm"))]
				ys += [_i(k.get("y_mm")), _i(k.get("y_mm")) + _i(k.get("boyi_mm"))]
			self.dim_h(xs, self.Y(0) + 26 + (row - 1) * ROW)
			self.dim_v(ys, self.X(0) - 26 - (row - 1) * ROW)
			row += 1
		if L["qisman"]:
			x, y, w, h = _i(poz.get("matofka_x")), _i(poz.get("matofka_y")), _i(poz.get("matofka_w")), _i(poz.get("matofka_h"))
			self.dim_h([0, x, x + w, W], self.Y(0) + 26 + (row - 1) * ROW)
			self.dim_v([0, y, y + h, H], self.X(0) - 26 - (row - 1) * ROW)

	def belgilar(self, poz):
		L = self.L
		x = L["box_w"] - 4
		y = 10
		chips = []
		if _i(poz.get("zakalka")):
			chips.append(("ZAKALKA", self.ink, RANG["white"]))
		if (poz.get("matofka_rejimi") or "Yo'q") != "Yo'q":
			chips.append(("MATOFKA", RANG["white"], self.ink))
		if _i(poz.get("mijoz_oynasi")):
			chips.append(("MIJOZ OYNASI", RANG["white"], self.ink))
		if _i(poz.get("markirovka")):
			chips.append(("MARKIROVKA", RANG["white"], self.ink))
		if L["aniz"]:
			chips.append(("Nisbat saqlanmagan", RANG["white"], RANG["amber"]))
		for lbl, bg, fg in chips:
			w = len(lbl) * 5.6 + 8
			self.add(f'<rect x="{x - w:.1f}" y="{y - 7}" width="{w:.1f}" height="13" rx="3" fill="{bg}" stroke="{fg if bg == RANG["white"] else "none"}" stroke-width="0.8"/>')
			self.text(x - w / 2.0, y, lbl, size=8, weight="bold", fill=fg)
			x -= w + 4
		kor = poz.get("korinish") or "Old tomondan"
		dona = _i(poz.get("dona"), 1)
		self.text(4, L["box_h"] - 6, f"Ko'rinish: {kor.lower()} · mm" + (f" · ×{dona} dona" if dona > 1 else ""), size=7, anchor="start", fill=RANG["muted"])

	def legend(self):
		L = self.L
		y = L["box_h"] - 18
		x = 6
		for nom, w, dash in (("pritupka", 1.4, None), ("shlifovka", 2.0, None), ("polirovka", 2.4, None), ("faska", 2.0, "4 2")):
			self.line(x, y, x + 14, y, w=w, color=self.acc, dash=dash)
			self.text(x + 17, y, nom, size=7, anchor="start", fill=RANG["muted"])
			x += 17 + len(nom) * 4.1 + 7
		self.add(f'<rect x="{x}" y="{y - 5}" width="14" height="10" fill="url(#{self.p}-hatch)" stroke="{RANG["muted"]}" stroke-width="0.5"/>')
		self.text(x + 17, y, "matofka", size=7, anchor="start", fill=RANG["muted"])
		x += 17 + 36
		self.add(f'<circle cx="{x + 5}" cy="{y}" r="4" fill="{RANG["white"]}" stroke="{self.ink}" stroke-width="0.8"/>')
		self.text(x + 13, y, "teshik", size=7, anchor="start", fill=RANG["muted"])


def pozitsiya_svg(poz, teshiklar=None, kesimlar=None, opts=None):
	"""Bitta pozitsiya uchun to'liq SVG-matn."""
	opts = dict(opts or {})
	box_w, box_h = int(opts.get("box_w", 320)), int(opts.get("box_h", 240))
	teshiklar = [dict(t) for t in (teshiklar or [])]
	kesimlar = [dict(k) for k in (kesimlar or [])]
	poz = dict(poz or {})
	L = layout(poz, teshiklar, kesimlar, box_w, box_h, legend=bool(opts.get("legend")))
	c = _Chizuvchi(L, opts)
	c.add(f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 {box_w} {box_h}" font-family="DejaVu Sans, Arial, sans-serif">')
	shakl, W, H = bounding(poz)
	if W > 0 and H > 0:
		c.prepare(poz)
	c.defs()
	c.add(f'<rect x="0" y="0" width="{box_w}" height="{box_h}" fill="{RANG["white"]}"/>')
	if W <= 0 or H <= 0:
		c.text(box_w / 2.0, box_h / 2.0, "O'lcham kiritilmagan", size=10, fill=RANG["muted"], italic=True)
		c.add("</svg>")
		return "".join(c.out)
	c.body(poz)
	c.matofka(poz)
	c.qirralar(poz)
	c.burchaklar(poz)
	c.kesimlar(kesimlar)
	c.teshiklar(teshiklar)
	c.olchamlar(poz, teshiklar, kesimlar)
	c.belgilar(poz)
	if opts.get("legend"):
		c.legend()
	c.add("</svg>")
	return "".join(c.out)


def zakaz_svgs(doc, opts=None):
	"""Hujjatning barcha pozitsiyalari uchun [(nr, svg)] (pozitsiya_nr bo'yicha bog'lab)."""
	def rows(field):
		return [r.as_dict() if hasattr(r, "as_dict") else dict(r) for r in (doc.get(field) or [])]
	out = []
	tesh, kes = rows("teshiklar"), rows("kesimlar")
	for i, p in enumerate(rows("pozitsiyalar"), 1):
		nr = _i(p.get("nr"))
		o = dict(opts or {})
		o.setdefault("prefix", f"oz{nr}")
		out.append((nr, pozitsiya_svg(p, [t for t in tesh if _i(t.get("pozitsiya_nr")) == nr],
		                              [k for k in kes if _i(k.get("pozitsiya_nr")) == nr], o)))
	return out


# ======================================================================= RASKROY — list xaritasi
def list_layout(W, H, box_w=560, box_h=390, legend=False, for_print=False):
	"""Listning izotrop masshtabi; `layout()` bilan bir xil kalitlar (ox/oy/sx/sy/W/H…)."""
	W, H = max(int(W), 1), max(int(H), 1)
	ml, mb, mt, mr = (62, 70 + (16 if legend else 0), 34, 26) if for_print else (46, 48 + (16 if legend else 0), 30, 22)
	aw, ah = box_w - ml - mr, box_h - mt - mb
	s = min(aw / W, ah / H)
	ox = ml + (aw - W * s) / 2.0
	oy = mt + (ah - H * s) / 2.0
	return dict(shakl="List", W=W, H=H, sx=s, sy=s, ox=ox, oy=oy, ml=ml, mb=mb, mt=mt, mr=mr, box_w=box_w, box_h=box_h,
	            aniz=False, tesh_koord=[], kes_koord=[], qisman=False, rows=1)


class _ListChizuvchi(_Chizuvchi):
	"""Raskroy-xaritasi: list (zich shtrix = chiqindi) → qoldiqlar (shtrix) → bo'laklar → kesish-chiziqlari →
	o'lchamlar → chiplar → izoh → legenda."""

	def rect_mm(self, x, y, w, h, **attrs):
		a = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
		self.add(f'<rect x="{self.X(x):.1f}" y="{self.Y(y + h):.1f}" width="{w * self.L["sx"]:.1f}" height="{h * self.L["sy"]:.1f}" {a}/>')

	def _yorliq(self, x, y, w, h, satrlar, weight_first=True, fill=None, pill=False):
		"""Katak markaziga 1–3 satr matn; kichik katakda faqat birinchi satr / hech narsa. pill — oq plashka (shtrix ustida o'qilishi uchun)."""
		pw, ph = w * self.L["sx"], h * self.L["sy"]
		cx, cy = self.X(x + w / 2.0), self.Y(y + h / 2.0)
		f = self.font
		if pw < 14 or ph < 9:
			return
		size = f + 1 if (pw > 90 and ph > 40) else max(f - 3, 6)
		rotate = None
		if ph > pw * 1.5 and pw < 60:  # tor-baland katak → matn 90° buriladi (pw/ph almashadi)
			rotate, pw, ph = -90, ph, pw
		if ph < 22 or pw < 44:
			satrlar = satrlar[:1]
		eng_uzun = max(len(t) for t in satrlar) * size * 0.68
		if eng_uzun > pw - 4:  # sig'masa: kichraytirish → qisqartirish
			size = max(6, int((pw - 4) / (max(len(t) for t in satrlar) * 0.68)))
			if len(satrlar[0]) * size * 0.68 > pw - 4:
				satrlar = [satrlar[0].replace("QOLDIQ ", "Q ")]
				if len(satrlar[0]) * size * 0.68 > pw - 4:
					return
		n = len(satrlar)
		if pill:
			tw = max(len(t) for t in satrlar) * size * 0.68 + 8
			th = n * (size + 2) + 4
			bw, bh = (th, tw) if rotate else (tw, th)
			self.add(f'<rect x="{cx - bw / 2:.1f}" y="{cy - bh / 2:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="3" fill="{RANG["white"]}" fill-opacity="0.92" stroke="{fill or self.ink}" stroke-width="0.5"/>')
		for i, s in enumerate(satrlar):
			d = (i - (n - 1) / 2.0) * (size + 2)
			xx, yy = (cx + d, cy) if rotate else (cx, cy + d)
			self.text(xx, yy, s, size=size if i == 0 else max(size - 1, 6), weight="bold" if (i == 0 and weight_first) else None,
			          fill=fill, rotate=rotate)

	def list_fon(self):
		"""List foni = chiqindi rangi (och qizil) + zich shtrix: bo'lak/qoldiq bilan yopilmagan joy chiqindi."""
		L = self.L
		self.rect_mm(0, 0, L["W"], L["H"], fill=RANG["chiqindi"], stroke="none")
		self.rect_mm(0, 0, L["W"], L["H"], fill=f"url(#{self.p}-hatch2)", stroke=self.ink, stroke_width=1.2)

	def foydali(self, f):
		if f:
			self.rect_mm(f["x"], f["y"], f["w"], f["h"], fill="none", stroke=RANG["muted"], stroke_width=0.6, stroke_dasharray="2 2")

	def qoldiqlar(self, qoldiqlar):
		for q in qoldiqlar or []:
			self.rect_mm(q["x"], q["y"], q["w"], q["h"], fill=RANG["qoldiq"], stroke="none")
			self.rect_mm(q["x"], q["y"], q["w"], q["h"], fill=f"url(#{self.p}-hatchq)", stroke=RANG["qoldiq_stroke"], stroke_width=0.9)
			m2 = q.get("m2") if q.get("m2") is not None else q["w"] * q["h"] / 1e6
			self._yorliq(q["x"], q["y"], q["w"], q["h"], [f"QOLDIQ {_fmt(q['w'])}×{_fmt(q['h'])}", f"{float(m2):.2f} m²"], fill=RANG["qoldiq_stroke"], pill=True)

	def bolaklar(self, bolaklar, dona=None):
		dona = dona or {}
		for b in bolaklar or []:
			nr, idx = _i(b.get("nr")), _i(b.get("idx"), 1)
			rang = PALITRA[(nr - 1) % len(PALITRA)] if nr > 0 else self.glass
			self.rect_mm(b["x"], b["y"], b["w"], b["h"], id=f"{self.p}-b{nr}-{idx}", fill=rang, stroke=self.ink, stroke_width=0.9)
			ko = f"№{nr}" + (f"/{idx}" if (idx > 1 or _i(dona.get(nr), 1) > 1) else "")
			olcham = f"{_fmt(b['w'])}×{_fmt(b['h'])}" + (" ↻" if _i(b.get("burilgan")) else "")
			satrlar = [ko, olcham] + ([str(b["olcham_matn"])] if b.get("olcham_matn") and str(b["olcham_matn"]) != olcham.replace(" ↻", "") else [])
			self._yorliq(b["x"], b["y"], b["w"], b["h"], satrlar)

	def chiziqlar(self, chiziqlar, tartib=True):
		for z in chiziqlar or []:
			x1, y1, x2, y2 = self.X(z["x1"]), self.Y(z["y1"]), self.X(z["x2"]), self.Y(z["y2"])
			if z.get("tur") == "chekka":
				self.line(x1, y1, x2, y2, w=0.5, color=RANG["muted"], dash="1 2")
				continue
			self.line(x1, y1, x2, y2, w=1.1, color=self.acc, dash="5 3")
			if tartib and z.get("tartib"):
				cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
				r = 6.5 if self.font >= 11 else 5.5
				self.add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{RANG["white"]}" stroke="{self.acc}" stroke-width="0.9"/>')
				self.text(cx, cy, str(z["tartib"]), size=r + 1, weight="bold", fill=self.acc)

	def olchamlar(self, sheet, zanjir=True):
		L = self.L
		W, H = L["W"], L["H"]
		f = sheet.get("foydali") or {}
		xs, ys = {0, W}, {0, H}
		if zanjir:
			if f:
				xs |= {f["x"], f["x"] + f["w"]}
				ys |= {f["y"], f["y"] + f["h"]}
			for z in sheet.get("chiziqlar") or []:
				if z.get("tur") == "chekka":
					continue
				if z["axis"] == "x" and f and z["y1"] <= f["y"] and z["y2"] >= f["y"] + f["h"]:
					xs.add(z["coord"])
				elif z["axis"] == "y" and f and z["x1"] <= f["x"] and z["x2"] >= f["x"] + f["w"]:
					ys.add(z["coord"])
		xs, ys = sorted(xs), sorted(ys)
		self.dim_h(xs, self.Y(0) + 16)
		self.dim_v(ys, self.X(0) - 16)
		if len(xs) > 2:  # umumiy o'lcham alohida qatorda
			self.dim_h([0, W], self.Y(0) + 32, weight="bold")
		if len(ys) > 2:
			self.dim_v([0, H], self.X(0) - 32, weight="bold")

	def chiplar(self, sheet, n=None, N=None):
		L = self.L
		x, y = L["box_w"] - 4, 11
		chips = []
		if n and N:
			chips.append((f"LIST {n}/{N}", self.ink, RANG["white"]))
		# Hamma foizlar LISTGA nisbatan (bo'laklar + chiqindi + qoldiq = 100 %)
		list_m2 = L["W"] * L["H"] / 1e6
		fp = sheet.get("foydalanish_foiz")
		if fp is not None:
			chips.append((f"bo'laklar {_fmt(fp)} %", RANG["white"], self.ink))
		ch, qol = sheet.get("chiqindi_m2"), sheet.get("qoldiq_m2")
		if ch is not None and list_m2:
			foiz = float(ch) / list_m2 * 100
			chips.append((f"chiqindi {_fmt(round(foiz, 1))} %", RANG["white"], RANG["amber"] if foiz > 10 else RANG["chiqindi_stroke"]))
		if qol is not None and list_m2:
			chips.append((f"qoldiq {_fmt(round(float(qol) / list_m2 * 100, 1))} %", RANG["white"], RANG["qoldiq_stroke"]))
		for lbl, bg, fg in chips:
			w = len(lbl) * 5.6 + 10
			self.add(f'<rect x="{x - w:.1f}" y="{y - 7}" width="{w:.1f}" height="14" rx="3" fill="{bg}" stroke="{fg if bg == RANG["white"] else "none"}" stroke-width="0.8"/>')
			self.text(x - w / 2.0, y, lbl, size=8, weight="bold", fill=fg)
			x -= w + 4

	def izoh(self, sheet):
		L = self.L
		item = sheet.get("oyna_item") or sheet.get("item") or ""
		parts = [str(item)] if item else []
		parts.append(f"list {_fmt(L['W'])}×{_fmt(L['H'])} mm")
		if sheet.get("qoldiq_m2") is not None:
			parts.append(f"qoldiq {float(sheet['qoldiq_m2']):.2f} m²")
		if sheet.get("chiqindi_m2") is not None:
			parts.append(f"chiqindi {float(sheet['chiqindi_m2']):.2f} m²")
		self.text(4, L["box_h"] - 6, " · ".join(parts), size=7, anchor="start", fill=RANG["muted"])

	def legend(self):
		L = self.L
		y, x = L["box_h"] - 19, 6
		for fill, stroke, nom in ((PALITRA[0], self.ink, "bo'laklar (rang = pozitsiya №)"), (RANG["qoldiq"], RANG["qoldiq_stroke"], "qoldiq — MIJOZGA beriladi" if self.o.get("qoldiq_mijozga") else "qoldiq — saqlanadi"),
		                          (RANG["chiqindi"], RANG["chiqindi_stroke"], "chiqindi")):
			self.add(f'<rect x="{x}" y="{y - 5}" width="14" height="10" fill="{fill}" stroke="{stroke}" stroke-width="0.8"/>')
			self.text(x + 17, y, nom, size=7, anchor="start", fill=RANG["muted"])
			x += 17 + len(nom) * 4.1 + 8
		self.line(x, y, x + 14, y, w=1.1, color=self.acc, dash="5 3")
		self.text(x + 17, y, "kesish-chizig'i (raqam = tartib; 1–4 chekka)", size=7, anchor="start", fill=RANG["muted"])


def list_svg(sheet, opts=None):
	"""Bitta list uchun raskroy-xaritasi SVG. sheet — reja['listlar'][i] (+ ixtiyoriy oyna_item, n, N,
	bolaklar[].olcham_matn); opts: box_w, box_h, prefix, for_print, legend, zanjir, tartib, n, N, dona{nr: dona}."""
	opts = dict(opts or {})
	sheet = dict(sheet or {})
	box_w, box_h = int(opts.get("box_w", 560)), int(opts.get("box_h", 390))
	W, H = _i(sheet.get("W")), _i(sheet.get("H"))
	legend = bool(opts.get("legend"))
	L = list_layout(W, H, box_w, box_h, legend=legend, for_print=bool(opts.get("for_print")))
	opts.setdefault("font", 11 if opts.get("for_print") else 10)
	c = _ListChizuvchi(L, opts)
	p = c.p
	c.add(f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 {box_w} {box_h}" font-family="DejaVu Sans, Arial, sans-serif">')
	c.defs(f'<pattern id="{p}-hatch2" patternUnits="userSpaceOnUse" width="4" height="4" patternTransform="rotate(45)">'
	       f'<line x1="0" y1="0" x2="0" y2="4" stroke="{RANG["chiqindi_stroke"]}" stroke-width="0.5" stroke-opacity="0.55"/>'
	       f'<line x1="0" y1="0" x2="4" y2="0" stroke="{RANG["chiqindi_stroke"]}" stroke-width="0.5" stroke-opacity="0.55"/></pattern>'
	       f'<pattern id="{p}-hatchq" patternUnits="userSpaceOnUse" width="7" height="7" patternTransform="rotate(45)">'
	       f'<line x1="0" y1="0" x2="0" y2="7" stroke="{RANG["qoldiq_stroke"]}" stroke-width="0.6" stroke-opacity="0.5"/></pattern>')
	c.add(f'<rect x="0" y="0" width="{box_w}" height="{box_h}" fill="{RANG["white"]}"/>')
	if W <= 0 or H <= 0:
		c.text(box_w / 2.0, box_h / 2.0, "List o'lchami kiritilmagan", size=11, fill=RANG["muted"], italic=True)
		c.add("</svg>")
		return "".join(c.out)
	c.list_fon()
	c.foydali(sheet.get("foydali"))
	c.qoldiqlar(sheet.get("qoldiqlar"))
	c.bolaklar(sheet.get("bolaklar"), dona=opts.get("dona"))
	if not sheet.get("bolaklar"):
		c.text(c.X(W / 2.0), c.Y(H / 2.0), "Bo'lak joylashtirilmagan", size=11, fill=RANG["muted"], italic=True)
	c.chiziqlar(sheet.get("chiziqlar"), tartib=opts.get("tartib", True))
	c.olchamlar(sheet, zanjir=opts.get("zanjir", True))
	c.chiplar(sheet, n=opts.get("n") or sheet.get("n"), N=opts.get("N") or sheet.get("N"))
	c.izoh(sheet)
	if legend:
		c.legend()
	c.add("</svg>")
	return "".join(c.out)
