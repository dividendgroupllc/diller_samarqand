# -*- coding: utf-8 -*-
"""Raskroy-dvigatel testlari (siteсиз ham ishlaydi):
  /home/sardorbek/armada-bench/env/bin/python -c "import sys; sys.path.insert(0,'/home/sardorbek/armada-bench/apps/akfa_diller');
    from akfa_diller.akfa_diller.services.test_oyna_raskroy import run; run()"
  yoki: bench --site akfa.diler execute akfa_diller.akfa_diller.services.test_oyna_raskroy.run
"""
import json
import random
import time

from akfa_diller.akfa_diller.services.oyna_raskroy import raskroy

N = {"pass": 0, "fail": 0}


def tek(nom, ok, info=""):
	N["pass" if ok else "fail"] += 1
	print(("PASS " if ok else "FAIL ") + nom + ("" if ok else f"  → {info}"))


def yaqin(a, b, tol=1e-4):
	return abs(float(a) - float(b)) <= tol


def P(nr, w, h, dona=1, item="10 mm oq", rot=1, shakl="To'rtburchak"):
	return dict(nr=nr, w=w, h=h, dona=dona, item=item, burish_mumkin=rot, shakl=shakl)


L = {"10 mm oq": (3600, 2600)}


def _kesishadi(a, b):
	return not (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"] or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])


def geom_ok(reja):
	"""Bo'laklar list foydali maydoni ichida, o'zaro kesishmaydi, qoldiqlar bo'laklar bilan kesishmaydi."""
	for s in reja["listlar"]:
		f = s["foydali"]
		for b in s["bolaklar"]:
			if not (f["x"] <= b["x"] and f["y"] <= b["y"] and b["x"] + b["w"] <= f["x"] + f["w"] and b["y"] + b["h"] <= f["y"] + f["h"]):
				return False, f"bo'lak {b} foydali {f} tashqarisida"
		for i, a in enumerate(s["bolaklar"]):
			for b in s["bolaklar"][i + 1:]:
				if _kesishadi(a, b):
					return False, f"kesishadi {a} / {b}"
			for q in s["qoldiqlar"]:
				if _kesishadi(a, q):
					return False, f"qoldiq bo'lak bilan kesishadi {a} / {q}"
	return True, ""


def run():
	# --- T1: 2000×800, 2000×600, 1200×700 @ 3600×2600
	r = raskroy([P(1, 2000, 800), P(2, 2000, 600), P(3, 1200, 700)], L)
	j, it = r["jami"], r["itemlar"]["10 mm oq"]
	tek("T1 xato yo'q", r["xato"] == [], r["xato"])
	tek("T1 1 list", j["list_soni"] == 1, j)
	tek("T1 bolaklar 3.64", yaqin(j["bolaklar_m2"], 3.64), j)
	tek("T1 qoldiq 5.3304", yaqin(j["qoldiq_m2"], 5.3304), j)
	tek("T1 ishlatilgan 4.0296", yaqin(j["ishlatilgan_m2"], 4.0296), j)
	tek("T1 chiqindi 0.3896", yaqin(j["chiqindi_m2"], 0.3896), j)
	tek("T1 chiqindi 10.7 %", yaqin(j["chiqindi_foiz"], 10.7, 0.05), j)
	tek("T1 foydalanish 38.9 %", yaqin(j["foydalanish_foiz"], 38.9, 0.05), j)
	tek("T1 list = bolaklar + qoldiq + chiqindi", yaqin(9.36, j["bolaklar_m2"] + j["qoldiq_m2"] + j["chiqindi_m2"]), j)
	s = r["listlar"][0]
	joy = {(b["nr"]): (b["x"], b["y"], b["w"], b["h"], b["burilgan"]) for b in s["bolaklar"]}
	tek("T1 P1 (10,10,2000,800)", joy[1] == (10, 10, 2000, 800, 0), joy)
	tek("T1 P2 (10,810,2000,600)", joy[2] == (10, 810, 2000, 600, 0), joy)
	tek("T1 P3 (2010,10,1200,700)", joy[3] == (2010, 10, 1200, 700, 0), joy)
	q = sorted((x["w"], x["h"], x["x"], x["y"], x["m2"]) for x in s["qoldiqlar"])
	tek("T1 qoldiqlar 1580×1880 va 2000×1180", q == [(1580, 1880, 2010, 710, 2.9704), (2000, 1180, 10, 1410, 2.36)], q)
	tek("T1 9 chiziq (4 chekka + 5 kesish)", len(s["chiziqlar"]) == 9 and sum(1 for z in s["chiziqlar"] if z["tur"] == "chekka") == 4, len(s["chiziqlar"]))
	tek("T1 tartib 1..9", [z["tartib"] for z in s["chiziqlar"]] == list(range(1, 10)))
	tek("T1 determinizm", json.dumps(r, sort_keys=True) == json.dumps(raskroy([P(1, 2000, 800), P(2, 2000, 600), P(3, 1200, 700)], L), sort_keys=True))
	ok, info = geom_ok(r); tek("T1 geometriya", ok, info)
	print("   T1 usul:", it["usul"])
	# --- T2: 1800×1200 ×4 → 2 list
	r = raskroy([P(1, 1800, 1200, dona=4)], L)
	j = r["jami"]
	tek("T2 2 list", j["list_soni"] == 2, j)
	tek("T2 idx 1..4", sorted(b["idx"] for s in r["listlar"] for b in s["bolaklar"]) == [1, 2, 3, 4])
	tek("T2 har listda 2 bo'lak", [len(s["bolaklar"]) for s in r["listlar"]] == [2, 2])
	tek("T2 qoldiq 9.8328", yaqin(j["qoldiq_m2"], 9.8328), j)
	tek("T2 chiqindi 2.9 %", yaqin(j["chiqindi_foiz"], 2.9, 0.05), j)
	ok, info = geom_ok(r); tek("T2 geometriya", ok, info)
	# --- T3: 2500×3000 faqat burilsa sig'adi
	r = raskroy([P(1, 2500, 3000)], L)
	b = r["listlar"][0]["bolaklar"][0]
	tek("T3 burilgan 3000×2500", (b["w"], b["h"], b["burilgan"]) == (3000, 2500, 1), b)
	qq = r["listlar"][0]["qoldiqlar"]
	tek("T3 qoldiq 580×2580 = 1.4964", len(qq) == 1 and (qq[0]["w"], qq[0]["h"]) == (580, 2580) and yaqin(qq[0]["m2"], 1.4964), qq)
	tek("T3 chiqindi 0.3636", yaqin(r["jami"]["chiqindi_m2"], 0.3636), r["jami"])
	r = raskroy([P(1, 2500, 3000, rot=0)], L)
	tek("T3b burilmasa → xato", len(r["xato"]) == 1 and "P1" in r["xato"][0] and "sig'maydi" in r["xato"][0] and "burish taqiqlangan" in r["xato"][0], r["xato"])
	# --- T4: listsiz item
	r = raskroy([P(1, 1000, 800, item="6 mm oq")], L)
	tek("T4 list yo'q → xato", r["xato"] == ["6 mm oq: list o'lchami kiritilmagan (Raskroy → Listlar)"], r["xato"])
	tek("T4 list_soni 0", r["jami"]["list_soni"] == 0)
	# --- T5: doira + erkin, zaxira 20
	r = raskroy([P(1, 600, 600, shakl="Doira"), P(2, 900, 1400, shakl="Erkin shakl")], L, {"shakl_zaxira_mm": 20})
	bb = {b["nr"]: (b["w"], b["h"]) for b in r["listlar"][0]["bolaklar"]}
	tek("T5 bounding + zaxira 620×620 / 920×1420", bb[1] == (620, 620) and bb[2] in ((920, 1420), (1420, 920)), bb)
	tek("T5 bolaklar 1.62 (zaxira chiqindida)", yaqin(r["jami"]["bolaklar_m2"], 1.62), r["jami"])
	tek("T5 ishlatilgan 2.0004 / chiqindi 0.3804", yaqin(r["jami"]["ishlatilgan_m2"], 2.0004) and yaqin(r["jami"]["chiqindi_m2"], 0.3804), r["jami"])
	# --- T6: 3600×1300 ×2, chekka 0 → chiqindi 0
	r = raskroy([P(1, 3600, 1300, dona=2)], L, {"chekka_mm": 0})
	tek("T6 chiqindi 0, foydalanish 100", yaqin(r["jami"]["chiqindi_m2"], 0) and yaqin(r["jami"]["foydalanish_foiz"], 100), r["jami"])
	tek("T6 1 kesish chiziq", len(r["listlar"][0]["chiziqlar"]) == 1 and r["listlar"][0]["chiziqlar"][0]["tur"] == "kesish", r["listlar"][0]["chiziqlar"])
	# --- T7: butun list qoidasi
	r = raskroy([P(1, 3000, 2000)], L)
	tek("T7 3000×2000 → butun_list 0, 2 qoldiq", r["itemlar"]["10 mm oq"]["butun_list"] == 0 and len(r["listlar"][0]["qoldiqlar"]) == 2, r["listlar"][0]["qoldiqlar"])
	r = raskroy([P(1, 3300, 2300)], L)
	tek("T7b 3300×2300 → butun_list 1, qoldiq 0", r["itemlar"]["10 mm oq"]["butun_list"] == 1 and yaqin(r["jami"]["qoldiq_m2"], 0), r["jami"])
	tek("T7b ishlatilgan 9.36, chiqindi 23.3 %", yaqin(r["jami"]["ishlatilgan_m2"], 9.36) and yaqin(r["jami"]["chiqindi_foiz"], 23.3, 0.05), r["jami"])
	# --- T8: noto'g'ri list
	r = raskroy([P(1, 300, 200, item="6 mm oq"), P(2, 1000, 800)], {"6 mm oq": (400, 300), "10 mm oq": (3600, 2600)})
	tek("T8 400×300 list → xato", any("noto'g'ri (500–10000 mm)" in x for x in r["xato"]), r["xato"])
	tek("T8 boshqa item rejalandi", "10 mm oq" in r["itemlar"] and "6 mm oq" not in r["itemlar"])
	# --- T9: kerf 4
	r = raskroy([P(1, 2000, 800), P(2, 2000, 600), P(3, 1200, 700)], L, {"kerf_mm": 4})
	joy = {b["nr"]: (b["x"], b["y"]) for b in r["listlar"][0]["bolaklar"]}
	tek("T9 kerf: P2 y=814, P3 x=2014", joy[2] == (10, 814) and joy[3] == (2014, 10), joy)
	tek("T9 qoldiq 5.3006", yaqin(r["jami"]["qoldiq_m2"], 5.3006), r["jami"])
	ok, info = geom_ok(r); tek("T9 geometriya", ok, info)
	# --- T10: tezlik + geometriya (26 birlik)
	random.seed(7)
	sizes = [(1200, 700), (2000, 800), (600, 400), (1500, 1000), (900, 1400), (2500, 1750), (300, 300), (1000, 620)]
	pcs = [P(i + 1, *random.choice(sizes), dona=random.choice([1, 1, 2, 3])) for i in range(12)]
	t0 = time.time()
	r = raskroy(pcs, L)
	dt = time.time() - t0
	n = sum(p["dona"] for p in pcs)
	tek(f"T10 {n} birlik < 1 s ({dt:.3f} s)", dt < 1.0 and r["xato"] == [], (dt, r["xato"]))
	tek("T10 hamma bo'lak joylashdi", sum(len(s["bolaklar"]) for s in r["listlar"]) == n)
	ok, info = geom_ok(r); tek("T10 geometriya", ok, info)
	tek("T10 balans", all(yaqin(s["list_m2"], s["bolaklar_m2"] + s["qoldiq_m2"] + s["chiqindi_m2"]) for s in r["listlar"]))
	# --- T12: stress-audit (2026-09-05) tuzatishlari
	r = raskroy([P(1, 1200, 1700, dona=4)], L)
	tek("T12 portret 1200×1700 ×4 → 1 list (landshaft)", r["jami"]["list_soni"] == 1, r["jami"])
	tek("T12 hammasi burilgan", all(b["burilgan"] == 1 for b in r["listlar"][0]["bolaklar"]))
	r = raskroy([P(1, 400, 300, dona=70)], L)
	tek("T12 400×300 ×70 → 1 list (shelf)", r["jami"]["list_soni"] == 1 and r["itemlar"]["10 mm oq"]["usul"].startswith("shelf"), r["jami"])
	ok, info = geom_ok(r); tek("T12 shelf geometriya", ok, info)
	tek("T12 shelf chiziqlar bor", sum(1 for z in r["listlar"][0]["chiziqlar"] if z["tur"] == "kesish") > 0)
	r = raskroy([P(1, 900, 1500), P(2, 1100, 2000)], L)
	tek("T12 qoldiq 5.6144 (landshaft yotqizish)", yaqin(r["jami"]["qoldiq_m2"], 5.6144), r["jami"])
	r = raskroy([P(1, 900, 2000), P(2, 1100, 2000), P(3, 900, 900)], L)
	tek("T12 kesish-tartibi to'liq qidiruv: qoldiq 3.8144", yaqin(r["jami"]["qoldiq_m2"], 3.8144), r["jami"])
	r = raskroy([P(1, 3600, 2500, dona=3)], L)
	tek("T12 dona=3 sig'maydi → 1 xabar", len(r["xato"]) == 1, r["xato"])
	r = raskroy([P(1, 3570, 2570, shakl="Doira")], L, {"shakl_zaxira_mm": 20})
	tek("T12 doira xabarida asl o'lcham + zaxira", "3570×2570" in r["xato"][0] and "+20 mm" in r["xato"][0], r["xato"])
	r = raskroy([P(1, 2000, 800)], L, {"kerf_mm": -1, "chekka_mm": "-10", "qoldiq_min_tomon_mm": "300", "qoldiq_min_m2": "0.25", "min_polosa_mm": None})
	tek("T12 konst normalizatsiya (manfiy → 0, str → son, None → default)", r["konst"]["kerf_mm"] == 0 and r["konst"]["chekka_mm"] == 0 and r["konst"]["qoldiq_min_tomon_mm"] == 300 and r["konst"]["min_polosa_mm"] == 40 and r["jami"]["list_soni"] == 1, r["konst"])
	r = raskroy([P(1, 0, 800, item="6 mm oq"), P(2, 1000, 800), P(3, 500, 400, item="")], {"10 mm oq": (3600, 2600), "6 mm oq": (3600, 2600)})
	tek("T12 0-o'lcham va itemsiz → xato, qolgani rejalanadi", len(r["xato"]) == 2 and "o'lcham noto'g'ri" in r["xato"][0] and "oyna-item" in r["xato"][1] and r["jami"]["list_soni"] == 1, r["xato"])
	r = raskroy([P(1, 2500, 3000, rot=None)], L)
	tek("T12 burish None → ruxsat (burilgan)", r["listlar"] and r["listlar"][0]["bolaklar"][0]["burilgan"] == 1, r["xato"])
	r = raskroy([P(1, 500, 400, item="Ойна"), P(2, 500, 400, item="abc"), P(3, 500, 400, item="Zebra")], {"Ойна": (3600, 2600), "abc": (3600, 2600), "Zebra": (3600, 2600)})
	tek("T12 listlar tartibi = pozitsiya tartibi", [x["item"] for x in r["listlar"]] == ["Ойна", "abc", "Zebra"])
	r = raskroy([P(1, 1000.7, 799.6)], L)
	tek("T12 kasr mm yuqoriga (1001×800)", (r["listlar"][0]["bolaklar"][0]["w"], r["listlar"][0]["bolaklar"][0]["h"]) in ((1001, 800), (800, 1001)), r["listlar"][0]["bolaklar"][0])
	r = raskroy([P(1, 1000, 800), P(1, 700, 500)], L)
	tek("T12 takroriy nr → idx 1,2", sorted(b["idx"] for b in r["listlar"][0]["bolaklar"]) == [1, 2])
	t0 = time.time(); r = raskroy([P(i, 100, 100, dona=6) for i in range(1, 41)], L); dt = time.time() - t0
	tek(f"T12 240 mayda bo'lak < 3 s ({dt:.2f} s), 1 list", dt < 3 and r["jami"]["list_soni"] == 1)
	# --- T11: chizma (list_svg mavjud bo'lsa)
	try:
		from akfa_diller.akfa_diller.services.oyna_chizma import list_svg
	except ImportError:
		list_svg = None
	if list_svg:
		import re
		from xml.dom import minidom
		r = raskroy([P(1, 2000, 800), P(2, 2000, 600), P(3, 1200, 700)], L)
		s = dict(r["listlar"][0], oyna_item="10 mm oq", n=1, N=1)
		svg = list_svg(s, {"prefix": "rk1", "for_print": True, "legend": True, "tartib": True})
		tek("T11 svg", svg.startswith("<svg") and svg.rstrip().endswith("</svg>"))
		try:
			minidom.parseString(svg); tek("T11 XML parse", True)
		except Exception as e:
			tek("T11 XML parse", False, str(e)[:120])
		tek("T11 hatchq/hatch2", "url(#rk1-hatchq)" in svg and "url(#rk1-hatch2)" in svg)
		tek("T11 yorliqlar", "№1" in svg and "2000×800" in svg and "QOLDIQ" in svg)
		tek("T11 5 tartib-aylana", svg.count("<circle") == 5, svg.count("<circle"))
		tek("T11 o'lchamlar", ">3600<" in svg and ">2600<" in svg)
		tek("T11 nan yo'q", "nan" not in svg.lower())
		ids = re.findall(r' id="([^"]+)"', svg)
		tek("T11 id prefiks/unikal", all(i.startswith("rk1-") for i in ids) and len(ids) == len(set(ids)))
	print(f"\nJAMI: {N['pass']} PASS, {N['fail']} FAIL")
	return N


if __name__ == "__main__":
	run()
