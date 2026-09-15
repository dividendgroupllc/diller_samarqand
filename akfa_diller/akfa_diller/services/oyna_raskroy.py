# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""Raskroy — oyna listidan kesish rejasi (nesting). TOZA Python, DB'siz, kutubxonasiz.

Kirish:  pieces  = [{nr, item, w, h, dona, burish_mumkin, shakl}]   (mm; w/h = hisob-o'lchamlar)
         listlar = {item: (W, H)}                                     (shu o'lchamdagi listlar cheksiz)
         konst   = DEF ustidan (chekka_mm, kerf_mm, min_polosa_mm, shakl_zaxira_mm,
                   qoldiq_min_tomon_mm, qoldiq_min_m2, qoldiq_max_soni)
Chiqish: reja (dict, JSON'ga tayyor):
         itemlar {item: {W,H,list_soni,bolaklar_m2,qoldiq_m2,ishlatilgan_m2,chiqindi_m2,chiqindi_foiz,
                         foydalanish_foiz,butun_list,usul}}
         listlar [{item,list_nr,W,H,foydali,bolaklar,chiziqlar,qoldiqlar,...}]   (list_nr global 1..N)
         jami, xato

Koordinata: listning chap-pastki burchagi (0,0), x o'ngga (eni), y yuqoriga (bo'yi), mm.
Bo'lak/qoldiq (x,y) — chap-pastki burchagi; w/h burilganda allaqachon almashgan.

Algoritm: har item MUSTAQIL (oq bo'lak qora listga tushmaydi). Ikki oila multi-start bilan:
 (1) gilyotin free-rectangle evristikasi — 5 saralash × BSSF/BAF × SAS/LAS/MAXAS × 4 burish-siyosati
     (eng_yaxshi / burilmasin / landshaft / portret) = 120 yugurish;
 (2) shelf (qator/ustun) joylashuvi — bir xil bo'laklar va «hammasini burish kerak» holatlari uchun (4 yugurish).
Har list uchun kesish-tartibi: packer'ning o'z daraxti VA joylashuvdan rekursiv gilyotin-ajratish (`_chiziqlar`;
≤12 bo'lakda eng ko'p qoldiq saqlaydigan tartib to'liq qidiriladi, undan ko'pida ochko'z) — ko'proq qoldiq
saqlagani tanlanadi. Maqsad (item bo'yicha): (list soni, −qoldiq maydoni, kesim-chiziqlar soni). Deterministik.

«kesim» so'zi tizimda fortochka/qulf o'yig'i uchun band — bu yerda kesim-chiziqlar = `chiziqlar`,
joylashuvlar = `bolaklar`.
"""

import math

DEF = dict(chekka_mm=10, kerf_mm=0, qoldiq_min_tomon_mm=300, qoldiq_min_m2=0.25, qoldiq_max_soni=2,
           min_polosa_mm=40, shakl_zaxira_mm=0)
INT_KEYS = ("chekka_mm", "kerf_mm", "qoldiq_min_tomon_mm", "qoldiq_max_soni", "min_polosa_mm", "shakl_zaxira_mm")
FLOAT_KEYS = ("qoldiq_min_m2",)
SORTS = (
	("maydon", lambda u: (-u["w"] * u["h"],)),
	("uzun", lambda u: (-max(u["w"], u["h"]), -min(u["w"], u["h"]))),
	("boyi", lambda u: (-u["h"], -u["w"])),
	("eni", lambda u: (-u["w"], -u["h"])),
	("perimetr", lambda u: (-(u["w"] + u["h"]),)),
)
TANLOV = ("BSSF", "BAF")
BOLISH = ("SAS", "LAS", "MAXAS")
BURISH = ("eng_yaxshi", "burilmasin", "landshaft", "portret")
SHELF = (("qator", "landshaft"), ("qator", "berilgan"), ("ustun", "landshaft"), ("ustun", "berilgan"))
CHIZIQ_LIMIT = 80      # listda shundan ko'p bo'lak — faqat packer daraxti (rederivatsiya qimmat)
TOLA_QIDIRUV_LIMIT = 12  # shundan kam bo'lak — kesish-tartibi to'liq qidiruv (maksimal qoldiq)


def _m2(a):
	return round(a / 1e6, 4)


def _son(v, default=None, yaxlit="ceil"):
	"""int (mm): kasr — yuqoriga (yaxlit="ceil") yoki pastga; noto'g'ri qiymat → default."""
	try:
		f = float(v)
	except (TypeError, ValueError):
		return default
	if f != f:  # nan
		return default
	return int(math.ceil(f)) if yaxlit == "ceil" else int(f)


def _konst(konst):
	"""DEF + foydalanuvchi konstantlari; son-tipga majburlash, manfiylar 0 ga."""
	out = dict(DEF)
	for k, v in (konst or {}).items():
		if k in INT_KEYS:
			n = _son(v, None, "floor")
			if n is not None:
				out[k] = max(n, 0)
		elif k in FLOAT_KEYS:
			try:
				out[k] = max(float(v), 0.0)
			except (TypeError, ValueError):
				pass
		else:
			out[k] = v
	return out


def _birliklar(pieces, konst, xato):
	"""dona → alohida birliklar (nr, idx); doira/erkin shaklga zaxira (chiqindi hisobiga). Noto'g'ri kirish → xato."""
	out, korilgan = [], {}
	for i, p in enumerate(pieces or []):
		nr = _son(p.get("nr"), None, "floor")
		tag = f"P{nr}" if nr is not None else f"#{i + 1}"
		item = p.get("item")
		w0, h0 = _son(p.get("w"), 0), _son(p.get("h"), 0)
		if not item:
			xato.append(f"{tag}: oyna-item ko'rsatilmagan")
			continue
		if w0 <= 0 or h0 <= 0:
			xato.append(f"{tag}: o'lcham noto'g'ri ({p.get('w')}×{p.get('h')} mm)")
			continue
		shakl = p.get("shakl") or "To'rtburchak"
		z = int(konst["shakl_zaxira_mm"]) if shakl in ("Doira", "Erkin shakl") else 0
		dona = max(_son(p.get("dona"), 1, "floor") or 1, 1)
		rv = p.get("burish_mumkin")
		rot = True if rv in (None, "") else bool(_son(rv, 1, "floor"))
		key = (str(item), nr)
		idx0 = korilgan.get(key, 0)  # takroriy nr → idx davom etadi (yorliq noyob qoladi)
		for j in range(dona):
			out.append(dict(nr=nr if nr is not None else 0, idx=idx0 + j + 1, item=str(item), w=w0 + z, h=h0 + z,
			                w0=w0, h0=h0, z=z, m2=w0 * h0 / 1e6, rot=rot, shakl=shakl))
		korilgan[key] = idx0 + dona
	return out


def _yonalishlar(u):
	o = [(u["w"], u["h"], 0)]
	if u["rot"] and u["w"] != u["h"]:
		o.append((u["h"], u["w"], 1))
	return o


def _baho(tanlov, fw, fh, w, h):
	dw, dh = fw - w, fh - h
	return (min(dw, dh), max(dw, dh)) if tanlov == "BSSF" else (fw * fh - w * h, min(dw, dh))


def _pref(burish, w, h, r):
	"""Burish-siyosati: eng_yaxshi — faqat sig'ish bahosi; burilmasin — asl yo'nalish afzal;
	landshaft — eni ≥ bo'yi afzal; portret — bo'yi ≥ eni afzal."""
	if burish == "burilmasin":
		return (r,)
	if burish == "landshaft":
		return (0 if w >= h else 1,)
	if burish == "portret":
		return (0 if h >= w else 1,)
	return ()


def _chiziq(axis, coord, dan, gacha):
	return dict(axis=axis, coord=coord,
	            x1=coord if axis == "x" else dan, y1=dan if axis == "x" else coord,
	            x2=coord if axis == "x" else gacha, y2=gacha if axis == "x" else coord)


def _bolak(u, x, y, w, h, r):
	return dict(nr=u["nr"], idx=u["idx"], x=x, y=y, w=w, h=h, burilgan=r, shakl=u["shakl"], m2=u["m2"])


def _joylashtir(units, W, H, konst, sort_key, tanlov, bolish, burish):
	"""Gilyotin free-rect: ochiq listlar bo'yicha first-fit, list ichida best-fit; bo'linish daraxtga yoziladi."""
	k, c, mp = int(konst["kerf_mm"]), int(konst["chekka_mm"]), int(konst["min_polosa_mm"])
	sheets = []
	for u in sorted(units, key=lambda u: (sort_key(u), u["nr"], u["idx"])):
		best, sheet = None, None
		for s in sheets:
			for fi, (fx, fy, fw, fh) in enumerate(s["free"]):
				for (w, h, r) in _yonalishlar(u):
					if w <= fw and h <= fh:
						sc = _pref(burish, w, h, r) + _baho(tanlov, fw, fh, w, h)
						if best is None or sc < best[0]:
							best = (sc, fi, w, h, r)
			if best:
				sheet = s
				break
		if best is None:
			sheet = dict(free=[(c, c, W - 2 * c, H - 2 * c)], bolaklar=[], daraxt=[])
			sheets.append(sheet)
			fx, fy, fw, fh = sheet["free"][0]
			best = min((_pref(burish, w, h, r) + _baho(tanlov, fw, fh, w, h), 0, w, h, r)
			           for (w, h, r) in _yonalishlar(u) if w <= fw and h <= fh)
		_, fi, w, h, r = best
		fx, fy, fw, fh = sheet["free"].pop(fi)
		sheet["bolaklar"].append(_bolak(u, fx, fy, w, h, r))
		rw, th = fw - w - k, fh - h - k
		if bolish == "SAS":
			gor = fw <= fh
		elif bolish == "LAS":
			gor = fw > fh
		else:  # MAXAS — eng katta bitta bo'sh rect qoladigan bo'linish
			gor = max(rw * h, fw * th) >= max(rw * fh, w * th)
		if gor:  # to'liq kenglikdagi gorizontal kesim, keyin bo'lak o'ngidagi qism
			a, b = (fx + w + k, fy, rw, h), (fx, fy + h + k, fw, th)
			if fh > h:
				sheet["daraxt"].append(_chiziq("y", fy + h, fx, fx + fw))
			if fw > w:
				sheet["daraxt"].append(_chiziq("x", fx + w, fy, fy + h))
		else:  # to'liq balandlikdagi vertikal kesim, keyin bo'lak ustidagi qism
			a, b = (fx + w + k, fy, rw, fh), (fx, fy + h + k, w, th)
			if fw > w:
				sheet["daraxt"].append(_chiziq("x", fx + w, fy, fy + fh))
			if fh > h:
				sheet["daraxt"].append(_chiziq("y", fy + h, fx, fx + w))
		for fr in (a, b):
			if fr[2] >= mp and fr[3] >= mp:
				sheet["free"].append(fr)
	return sheets


def _shelf(units, W, H, konst, yonalish, orient):
	"""Shelf (FFDH) joylashuvi: qatorlar (yoki ustunlar — transpozitsiya bilan). Bir xil bo'laklar va
	«hammasini burish kerak» holatlarida free-rect evristikasidan yaxshi. Chiqish formati _joylashtir bilan bir xil."""
	k, c, mp = int(konst["kerf_mm"]), int(konst["chekka_mm"]), int(konst["min_polosa_mm"])
	T = yonalish == "ustun"
	UW, UH = (H - 2 * c, W - 2 * c) if T else (W - 2 * c, H - 2 * c)

	def orientlar(u):
		w, h = (u["h"], u["w"]) if T else (u["w"], u["h"])
		o = [(w, h, 0)]
		if u["rot"] and w != h:
			o.append((h, w, 1))
		if orient == "landshaft":
			o.sort(key=lambda t: (0 if t[0] >= t[1] else 1, t[2]))
		return o

	order = sorted(units, key=lambda u: (-orientlar(u)[0][1], -orientlar(u)[0][0], u["nr"], u["idx"]))
	sheets = []
	for u in order:
		placed = False
		for s in sheets:
			for sh in s["shelves"]:
				for (w, h, r) in orientlar(u):
					if h <= sh["h"] and sh["x"] + w <= c + UW:
						sh["bolaklar"].append((u, sh["x"], sh["y"], w, h, r))
						sh["x"] += w + k
						placed = True
						break
				if placed:
					break
			if placed:
				break
			for (w, h, r) in orientlar(u):
				if w <= UW and s["y"] + h <= c + UH:
					sh = dict(y=s["y"], h=h, x=c + w + k, bolaklar=[(u, c, s["y"], w, h, r)])
					s["shelves"].append(sh)
					s["y"] += h + k
					placed = True
					break
			if placed:
				break
		if not placed:
			s = dict(shelves=[], y=c)
			sheets.append(s)
			for (w, h, r) in orientlar(u):
				if w <= UW and h <= UH:
					s["shelves"].append(dict(y=c, h=h, x=c + w + k, bolaklar=[(u, c, c, w, h, r)]))
					s["y"] = c + h + k
					placed = True
					break
			if not placed:
				return None  # sig'maydi (oldindan tekshirilgan — bo'lmasligi kerak)

	def tr_rect(x, y, w, h):
		return (y, x, h, w) if T else (x, y, w, h)

	def tr_line(z):
		if not T:
			return z
		return dict(axis="y" if z["axis"] == "x" else "x", coord=z["coord"], x1=z["y1"], y1=z["x1"], x2=z["y2"], y2=z["x2"])

	out = []
	for s in sheets:
		bol, daraxt, free = [], [], []
		for sh in s["shelves"]:
			ust = sh["y"] + sh["h"]
			if ust < c + UH:
				daraxt.append(_chiziq("y", ust, c, c + UW))
			for (u, x, y, w, h, r) in sh["bolaklar"]:
				bx, by, bw, bh = tr_rect(x, y, w, h)
				bol.append(_bolak(u, bx, by, bw, bh, r))
				if x + w < c + UW:
					daraxt.append(_chiziq("x", x + w, sh["y"], ust))
				if h < sh["h"]:
					daraxt.append(_chiziq("y", y + h, x, x + w))
					if w >= mp and sh["h"] - h >= mp:
						free.append(tr_rect(x, y + h, w, sh["h"] - h))
			qw = c + UW - sh["x"]
			if qw >= mp and sh["h"] >= mp:
				free.append(tr_rect(sh["x"], sh["y"], qw, sh["h"]))
		qh = c + UH - s["y"]
		if qh >= mp and UW >= mp:
			free.append(tr_rect(c, s["y"], UW, qh))
		out.append(dict(free=free, bolaklar=bol, daraxt=[tr_line(z) for z in daraxt]))
	return out


def _qoldiq_bahosi(leaves, konst):
	"""Bo'sh rectlar → saqlanadigan qoldiqlarning (mezon + eng katta N) yig'indi maydoni."""
	q = sorted((r[2] * r[3] for r in leaves
	            if min(r[2], r[3]) >= konst["qoldiq_min_tomon_mm"] and r[2] * r[3] / 1e6 >= konst["qoldiq_min_m2"]), reverse=True)
	return sum(q[:int(konst["qoldiq_max_soni"])])


def _chiziqlar(rect, pcs, k, memo, konst, tola):
	"""Joylashuvdan gilyotin kesish-chiziqlari: (chiziqlar, bo'sh rectlar) yoki None (memo + backtracking).
	tola=True: barcha nomzodlar sinaladi, eng ko'p qoldiq saqlagani olinadi; False: eng katta bo'sh hududni
	ajratadigan birinchi muvaffaqiyatli nomzod (ochko'z)."""
	x, y, w, h = rect
	if not pcs:
		return [], [rect]
	key = (rect, tuple(sorted((p["x"], p["y"], p["w"], p["h"]) for p in pcs)))
	if key in memo:
		return memo[key]
	cands = []
	for cx in sorted({p["x"] + p["w"] for p in pcs} | {p["x"] - k for p in pcs}):
		if x < cx and cx + k < x + w and not any(p["x"] < cx + k and p["x"] + p["w"] > cx for p in pcs):
			A = [p for p in pcs if p["x"] + p["w"] <= cx]
			ida = {id(p) for p in A}
			cands.append(("x", cx, (x, y, cx - x, h), (cx + k, y, x + w - cx - k, h), A, [p for p in pcs if id(p) not in ida]))
	for cy in sorted({p["y"] + p["h"] for p in pcs} | {p["y"] - k for p in pcs}):
		if y < cy and cy + k < y + h and not any(p["y"] < cy + k and p["y"] + p["h"] > cy for p in pcs):
			A = [p for p in pcs if p["y"] + p["h"] <= cy]
			ida = {id(p) for p in A}
			cands.append(("y", cy, (x, y, w, cy - y), (x, cy + k, w, y + h - cy - k), A, [p for p in pcs if id(p) not in ida]))
	res, res_sc = None, None
	for ax, co, ra, rb, A, B in sorted(cands, key=lambda c: (
			-max(c[2][2] * c[2][3] if not c[4] else 0, c[3][2] * c[3][3] if not c[5] else 0),
			min(len(c[4]), len(c[5])))):
		ra_ = _chiziqlar(ra, A, k, memo, konst, tola)
		if ra_ is None:
			continue
		rb_ = _chiziqlar(rb, B, k, memo, konst, tola)
		if rb_ is None:
			continue
		cand = ([_chiziq(ax, co, y if ax == "x" else x, y + h if ax == "x" else x + w)] + ra_[0] + rb_[0], ra_[1] + rb_[1])
		if not tola:
			res = cand
			break
		sc = (_qoldiq_bahosi(cand[1], konst), -len(cand[0]))
		if res is None or sc > res_sc:
			res, res_sc = cand, sc
	if res is None and len(pcs) == 1:
		p = pcs[0]
		if p["x"] == x and p["y"] == y and p["w"] == w and p["h"] == h:
			res = ([], [])  # bitta bo'lak rectni to'liq egallagan
	memo[key] = res
	return res


def _yakunla(sheet, item, nr, W, H, konst):
	"""Bitta list: kesim-siyosatini tanlash (daraxt vs joylashuvdan ajratish), qoldiq/chiqindi hisob."""
	c, k = int(konst["chekka_mm"]), int(konst["kerf_mm"])

	def saralash(bosh):
		q = [r for r in bosh if min(r[2], r[3]) >= konst["qoldiq_min_tomon_mm"] and r[2] * r[3] / 1e6 >= konst["qoldiq_min_m2"]]
		return sorted(q, key=lambda r: (-r[2] * r[3], r[0], r[1]))[:int(konst["qoldiq_max_soni"])]

	variantlar = [(sheet["daraxt"], saralash(sheet["free"]))]
	n = len(sheet["bolaklar"])
	if n <= CHIZIQ_LIMIT:
		g = _chiziqlar((c, c, W - 2 * c, H - 2 * c), sheet["bolaklar"], k, {}, konst, tola=n <= TOLA_QIDIRUV_LIMIT)
		if g is not None:
			variantlar.append((g[0], saralash(g[1])))
	chiz, q = min(variantlar, key=lambda v: (-sum(r[2] * r[3] for r in v[1]), len(v[0])))
	trim = [_chiziq("x", c, 0, H), _chiziq("y", c, 0, W), _chiziq("x", W - c, 0, H), _chiziq("y", H - c, 0, W)] if c > 0 else []
	chiz = [dict(z, tartib=i, tur="chekka" if i <= len(trim) else "kesish") for i, z in enumerate(trim + chiz, 1)]
	list_m2 = _m2(W * H)
	bol = round(sum(p["m2"] for p in sheet["bolaklar"]), 4)
	qoldiqlar = [dict(x=r[0], y=r[1], w=r[2], h=r[3], m2=_m2(r[2] * r[3])) for r in q]
	qol = round(sum(r["m2"] for r in qoldiqlar), 4)
	ish = round(list_m2 - qol, 4)
	katta = max(p["m2"] for p in sheet["bolaklar"])  # nominal (zaxirasiz) maydon
	return dict(item=item, list_nr=nr, W=W, H=H, foydali=dict(x=c, y=c, w=W - 2 * c, h=H - 2 * c),
	            bolaklar=[{kk: v for kk, v in p.items() if kk != "m2"} for p in sheet["bolaklar"]],
	            chiziqlar=chiz, qoldiqlar=qoldiqlar,
	            list_m2=list_m2, bolaklar_m2=bol, qoldiq_m2=qol, ishlatilgan_m2=ish, chiqindi_m2=round(ish - bol, 4),
	            foydalanish_foiz=round(bol / list_m2 * 100, 1) if list_m2 else 0.0,
	            butun_list=int(katta > 0.5 * W * H / 1e6 and not q))


def raskroy(pieces, listlar, konst=None):
	konst = _konst(konst)
	xato, itemlar, sheets_out = [], {}, []
	units = _birliklar(pieces, konst, xato)
	listlar = listlar or {}
	for item in dict.fromkeys(u["item"] for u in units):  # pozitsiyalarda birinchi uchrash tartibi
		us = [u for u in units if u["item"] == item]
		lst = listlar.get(item)
		try:
			W, H = _son(lst[0], 0, "floor"), _son(lst[1], 0, "floor")
		except (TypeError, IndexError, KeyError):
			W = H = 0
		if not W or not H:
			xato.append(f"{item}: list o'lchami kiritilmagan (Raskroy → Listlar)")
			continue
		if W < 500 or H < 500 or W > 10000 or H > 10000:
			xato.append(f"{item}: list o'lchami {W}×{H} mm noto'g'ri (500–10000 mm)")
			continue
		c = int(konst["chekka_mm"])
		UW, UH = W - 2 * c, H - 2 * c
		bad, korilgan = False, set()
		for u in us:
			if not any(w <= UW and h <= UH for (w, h, _r) in _yonalishlar(u)):
				bad = True
				if u["nr"] in korilgan:
					continue  # dona=N — xabar bir marta
				korilgan.add(u["nr"])
				if not u["rot"] and u["h"] <= UW and u["w"] <= UH:
					sabab = "burish taqiqlangan (naqshli oyna) — kattaroq list kiriting"
				else:
					sabab = "kattaroq list kiriting"
				olch = f"{u['w0']}×{u['h0']} mm" + (f" (+{u['z']} mm shakl-zaxira = {u['w']}×{u['h']})" if u["z"] else "")
				xato.append(f"P{u['nr']}: {olch} bo'lak {item} listiga ({W}×{H}, foydali {UW}×{UH}) sig'maydi — {sabab}")
		if bad:
			continue
		best = None

		def baholab(sheets, usul):
			nonlocal best
			if sheets is None:
				return
			fin = [_yakunla(s, item, i + 1, W, H, konst) for i, s in enumerate(sheets)]
			score = (len(fin), -sum(f["qoldiq_m2"] for f in fin), sum(len(f["chiziqlar"]) for f in fin))
			if best is None or score < best[0]:
				best = (score, fin, usul)

		for (snom, skey) in SORTS:
			for tanlov in TANLOV:
				for bolish in BOLISH:
					for burish in BURISH:
						baholab(_joylashtir(us, W, H, konst, skey, tanlov, bolish, burish), f"{snom}/{tanlov}/{bolish}/{burish}")
		for yon, ori in SHELF:
			baholab(_shelf(us, W, H, konst, yon, ori), f"shelf/{yon}/{ori}")
		fin = best[1]
		bol = round(sum(f["bolaklar_m2"] for f in fin), 4)
		qol = round(sum(f["qoldiq_m2"] for f in fin), 4)
		ish = round(sum(f["ishlatilgan_m2"] for f in fin), 4)
		ch = round(ish - bol, 4)
		itemlar[item] = dict(W=W, H=H, list_soni=len(fin), bolaklar_m2=bol, qoldiq_m2=qol, ishlatilgan_m2=ish,
		                     chiqindi_m2=ch, chiqindi_foiz=round(ch / bol * 100, 1) if bol else 0.0,
		                     foydalanish_foiz=round(bol / (len(fin) * W * H / 1e6) * 100, 1) if fin else 0.0,
		                     butun_list=int(any(f["butun_list"] for f in fin)), usul=best[2])
		sheets_out += fin
	for i, s in enumerate(sheets_out, 1):  # global list raqamlari (item tartibida)
		s["list_nr"] = i
	bol = round(sum(v["bolaklar_m2"] for v in itemlar.values()), 4)
	qol = round(sum(v["qoldiq_m2"] for v in itemlar.values()), 4)
	ish = round(sum(v["ishlatilgan_m2"] for v in itemlar.values()), 4)
	ch = round(ish - bol, 4)
	list_m2 = sum(v["list_soni"] * v["W"] * v["H"] / 1e6 for v in itemlar.values())
	jami = dict(list_soni=sum(v["list_soni"] for v in itemlar.values()), bolaklar_m2=bol, qoldiq_m2=qol,
	            ishlatilgan_m2=ish, chiqindi_m2=ch, chiqindi_foiz=round(ch / bol * 100, 1) if bol else 0.0,
	            foydalanish_foiz=round(bol / list_m2 * 100, 1) if list_m2 else 0.0)
	return dict(versiya=1, konst=konst, itemlar=itemlar, listlar=sheets_out, jami=jami, xato=xato)
