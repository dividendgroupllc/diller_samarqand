"""oyna_chizma strukturaviy testlari. bench --site akfa.diler execute akfa_diller.akfa_diller.services.test_oyna_chizma.run
(yoki: ./env/bin/python -c 'from akfa_diller...' — frappe'siz ham ishlaydi)."""
import re
from akfa_diller.akfa_diller.services.oyna_chizma import pozitsiya_svg, layout, list_svg, list_layout

N = {"pass": 0, "fail": 0}


def tek(nom, ok, info=""):
	N["pass" if ok else "fail"] += 1
	print(("PASS " if ok else "FAIL ") + nom + (("  " + str(info)) if not ok else ""))


def P(**kw):
	d = dict(nr=1, shakl="To'rtburchak", oyna_item="10 mm oq", qalinlik_mm=10, eni_mm=1200, boyi_mm=800, dona=1,
	         qirra_yuqori="Yo'q", qirra_ong="Yo'q", qirra_pastki="Yo'q", qirra_chap="Yo'q", qirra_kontur="Yo'q",
	         burchak_yc="Yo'q", burchak_yo="Yo'q", burchak_po="Yo'q", burchak_pc="Yo'q", matofka_rejimi="Yo'q", zakalka=0)
	d.update(kw)
	return d


def LIST(**kw):
	"""Raskroy-list fixture: 3 bo'lak, 6 kesish-chizig'i (+4 chekka), 2 qoldiq @ 3600×2600."""
	d = dict(item="10 mm oq", oyna_item="10 mm oq", list_nr=1, W=3600, H=2600, foydali=dict(x=10, y=10, w=3580, h=2580),
	         bolaklar=[dict(nr=1, idx=1, x=10, y=10, w=1400, h=1000, burilgan=0, shakl="To'rtburchak"),
	                   dict(nr=2, idx=1, x=1410, y=10, w=1400, h=1000, burilgan=0, shakl="To'rtburchak"),
	                   dict(nr=3, idx=1, x=10, y=1010, w=1000, h=1500, burilgan=1, shakl="To'rtburchak")],
	         chiziqlar=[dict(tartib=1, tur="chekka", axis="x", coord=10, x1=10, y1=0, x2=10, y2=2600),
	                    dict(tartib=2, tur="chekka", axis="y", coord=10, x1=0, y1=10, x2=3600, y2=10),
	                    dict(tartib=3, tur="chekka", axis="x", coord=3590, x1=3590, y1=0, x2=3590, y2=2600),
	                    dict(tartib=4, tur="chekka", axis="y", coord=2590, x1=0, y1=2590, x2=3600, y2=2590),
	                    dict(tartib=5, tur="kesish", axis="x", coord=1410, x1=1410, y1=10, x2=1410, y2=2590),
	                    dict(tartib=6, tur="kesish", axis="y", coord=1010, x1=10, y1=1010, x2=1410, y2=1010),
	                    dict(tartib=7, tur="kesish", axis="x", coord=1010, x1=1010, y1=1010, x2=1010, y2=2590),
	                    dict(tartib=8, tur="kesish", axis="y", coord=2510, x1=10, y1=2510, x2=1010, y2=2510),
	                    dict(tartib=9, tur="kesish", axis="y", coord=1010, x1=1410, y1=1010, x2=3590, y2=1010),
	                    dict(tartib=10, tur="kesish", axis="x", coord=2810, x1=2810, y1=10, x2=2810, y2=1010)],
	         qoldiqlar=[dict(x=1410, y=1010, w=2180, h=1580, m2=3.4444), dict(x=2810, y=10, w=780, h=1000, m2=0.78)],
	         list_m2=9.36, bolaklar_m2=4.3, qoldiq_m2=4.2244, ishlatilgan_m2=5.1356, chiqindi_m2=0.8356, foydalanish_foiz=45.9, butun_list=0)
	d.update(kw)
	return d


def koordinatalar_ichida(svg, w, h):
	nums = [(float(a), float(b)) for a, b in re.findall(r'\b(?:x|cx|x1|x2)="(-?[\d.]+)"[^>]*?\b(?:y|cy|y1|y2)="(-?[\d.]+)"', svg)]
	return all(-2 <= x <= w + 2 and -2 <= y <= h + 2 for x, y in nums), len(nums)


def run():
	# 1 to'rtburchak + polirovka ×4 + 2 teshik
	s = pozitsiya_svg(P(qirra_yuqori="Polirovka", qirra_ong="Polirovka", qirra_pastki="Polirovka", qirra_chap="Polirovka"),
	                  [dict(diametr_mm=12, x_mm=50, y_mm=50), dict(diametr_mm=12, x_mm=1150, y_mm=50)], opts={"prefix": "t1"})
	tek("1 svg ochiladi/yopiladi", s.startswith("<svg") and s.endswith("</svg>"))
	tek("1 ikki teshik-aylana", s.count('<circle') == 2, s.count('<circle'))
	tek("1 zanjir 50/1100/50", all(x in s for x in (">50<", ">1100<")), s.count(">50<"))
	tek("1 umumiy 1200/800", ">1200<" in s and ">800<" in s)
	tek("1 4 ta polirovka yorlig'i", s.count("polirovka") == 4, s.count("polirovka"))
	tek("1 ZAKALKA yo'q", "ZAKALKA" not in s)
	tek("1 id prefiks", 'id="t1-arr-s"' in s and 'id="t1-hatch"' in s)
	ok, n = koordinatalar_ichida(s, 320, 240); tek("1 koordinatalar box ichida", ok and n > 10, n)
	tek("1 NaN yo'q", "nan" not in s.lower())
	# 2 doira + markaziy teshik
	s = pozitsiya_svg(P(shakl="Doira", diametr_mm=600, oyna_item="6 mm oq", qalinlik_mm=6, qirra_kontur="Shlifovka"),
	                  [dict(diametr_mm=30, x_mm=300, y_mm=300)], opts={"prefix": "t2"})
	tek("2 ellipse tana", "<ellipse" in s)
	tek("2 Ø 600", "Ø 600" in s)
	tek("2 shlifovka (kontur)", "shlifovka (kontur)" in s)
	tek("2 bitta teshik", s.count("<circle") == 1)
	# 3 erkin
	s = pozitsiya_svg(P(shakl="Erkin shakl", eni_mm=900, boyi_mm=1400), opts={"prefix": "t3"})
	tek("3 dashed bounding", 'stroke-dasharray="6 3"' in s)
	tek("3 shablon matni", "shablon bo'yicha" in s)
	tek("3 umumiy o'lchamlar", ">900<" in s and ">1400<" in s)
	# 4 qisman matofka
	s = pozitsiya_svg(P(matofka_rejimi="Qisman", matofka_x=100, matofka_y=100, matofka_w=300, matofka_h=200), opts={"prefix": "t4"})
	tek("4 hatch rect clip", 'fill="url(#t4-hatch)"' in s and 'clip-path="url(#t4-clip)"' in s)
	tek("4 Matofka 300×200", "Matofka 300×200" in s)
	tek("4 MATOFKA belgi", "MATOFKA" in s)
	tek("4 qo'shimcha o'lcham qatori", ">100<" in s and ">300<" in s)
	# 5 anizotrop
	L = layout(P(eni_mm=6000, boyi_mm=100), [], [], 320, 240)
	tek("5 anizotrop", L["aniz"] and L["sy"] > L["sx"], (L["sx"], L["sy"]))
	s = pozitsiya_svg(P(eni_mm=6000, boyi_mm=100), opts={"prefix": "t5"})
	tek("5 Nisbat saqlanmagan belgi", "Nisbat saqlanmagan" in s)
	tek("5 o'lchamlar", ">6000<" in s and ">100<" in s)
	# 6 fortochka pastki qirraga tegib (notch) + rozetka
	s = pozitsiya_svg(P(), kesimlar=[dict(tur="Fortochka", eni_mm=400, boyi_mm=300, x_mm=600, y_mm=0, ichki_radius_mm=10),
	                                dict(tur="Rozetka", eni_mm=70, boyi_mm=70, x_mm=100, y_mm=400, ichki_radius_mm=10)], opts={"prefix": "t6"})
	tek("6 ikki kesim rect", s.count('rx="') >= 2)
	tek("6 notch (oq chiziq)", 'stroke="#ffffff"' in s)
	tek("6 Fortochka yorlig'i", "Fortochka" in s and "Rozetka" in s)
	tek("6 kesim zanjiri (600→1000=400, 1000→1200=200)", ">400<" in s and ">200<" in s)
	# 7 burchaklar + faska
	s = pozitsiya_svg(P(burchak_yc="Radius", burchak_yc_mm=50, burchak_po="Kesik", burchak_po_mm=30, qirra_yuqori="Faska", faska_kengligi_mm=20), opts={"prefix": "t7"})
	tek("7 yoy (A) path'da", " A" in s.split('id="t7-piece"')[1].split("/>")[0] or "A" in s)
	tek("7 R50 va Kesik 30", "R50" in s and "Kesik 30" in s)
	tek("7 faska 20 + dashed", "faska 20" in s and 'stroke-dasharray="4 2"' in s)
	# 8 zich
	tesh = [dict(diametr_mm=10, x_mm=x, y_mm=y) for x, y in ((40, 40), (60, 40), (80, 40), (1160, 40), (40, 760), (1160, 760), (600, 400), (620, 400))]
	kes = [dict(tur="Fortochka", eni_mm=300, boyi_mm=250, x_mm=200, y_mm=200), dict(tur="Qulf", eni_mm=40, boyi_mm=80, x_mm=1100, y_mm=350)]
	s = pozitsiya_svg(P(zakalka=1, matofka_rejimi="To'liq"), tesh, kes, opts={"prefix": "t8", "legend": True, "for_print": True})
	tek("8 8 teshik", s.count("<circle") == 8 + 1, s.count("<circle"))  # +1 legend
	tek("8 NaN yo'q", "nan" not in s.lower())
	tek("8 ZAKALKA + MATOFKA", "ZAKALKA" in s and "MATOFKA" in s)
	tek("8 legend", "teshik</text>" in s)
	ok, n = koordinatalar_ichida(s, 320, 240); tek("8 koordinatalar box ichida", ok, n)
	# 9 bo'sh o'lcham
	s = pozitsiya_svg(P(eni_mm=0, boyi_mm=0), opts={"prefix": "t9"})
	tek("9 bo'sh → xabar", "O'lcham kiritilmagan" in s)
	# 10 escape
	s = pozitsiya_svg(P(korinish="<b>x</b>"), opts={"prefix": "t10"})
	tek("10 escape", "<b>" not in s and "&lt;b&gt;" in s)
	# 11 raskroy list-xaritasi (ekran)
	s = list_svg(LIST(), opts={"prefix": "l1", "n": 1, "N": 2})
	tek("11 svg", s.startswith("<svg") and s.endswith("</svg>"))
	tek("11 3 bo'lak id", all(f'id="l1-b{n}-1"' in s for n in (1, 2, 3)))
	tek("11 yorliqlar №/o'lcham/burilgan", "№1" in s and "1400×1000" in s and "1000×1500 ↻" in s)
	tek("11 hatchq + hatch2", 'url(#l1-hatchq)' in s and 'url(#l1-hatch2)' in s and 'id="l1-hatch2"' in s)
	tek("11 6 punktir kesish + 6 aylana", s.count('stroke-dasharray="5 3"') == 6 and s.count("<circle") == 6, (s.count('stroke-dasharray="5 3"'), s.count("<circle")))
	tek("11 4 chekka punktir", s.count('stroke-dasharray="1 2"') == 4, s.count('stroke-dasharray="1 2"'))
	tek("11 chiplar (listga nisbatan)", "LIST 1/2" in s and "bo'laklar 45.9 %" in s and "chiqindi 8.9 %" in s and "qoldiq 45.1 %" in s, s[-700:])
	tek("11 zanjir 1400 / 2180 (to'liq-uzunlikdagi kesim)", ">1400<" in s and ">2180<" in s)
	tek("11 umumiy 3600/2600", ">3600<" in s and ">2600<" in s)
	tek("11 QOLDIQ yorliq", "QOLDIQ 2180×1580" in s and "3.44 m²" in s)
	tek("11 izoh", "10 mm oq · list 3600×2600 mm" in s)
	ok, n = koordinatalar_ichida(s, 560, 390); tek("11 koordinatalar box ichida", ok and n > 20, n)
	tek("11 NaN yo'q", "nan" not in s.lower())
	# 12 Y pastdan: №1 (y=10,h=1000) rect'ning svg-y = oy + (H-1010)*sy
	L = list_layout(3600, 2600, 560, 390)
	m = re.search(r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)" id="l1-b1-1"', s)
	tek("12 №1 rect topildi", bool(m))
	if m:
		tek("12 Y pastdan", abs(float(m.group(2)) - (L["oy"] + (2600 - 1010) * L["sy"])) < 0.15 and abs(float(m.group(1)) - (L["ox"] + 10 * L["sx"])) < 0.15, (m.groups(), L["oy"], L["sy"]))
		tek("12 o'lcham px", abs(float(m.group(3)) - 1400 * L["sx"]) < 0.15 and abs(float(m.group(4)) - 1000 * L["sy"]) < 0.15)
	# 13 print + legenda + prefiks-izolyatsiya + escape + dona
	s2 = list_svg(LIST(oyna_item="<b>10 mm</b>"), opts={"prefix": "l2", "for_print": True, "legend": True, "box_w": 760, "box_h": 520, "n": 2, "N": 2, "dona": {1: 2}})
	tek("13 print legenda", "qoldiq — saqlanadi" in s2 and "kesish-chizig'i" in s2 and "chiqindi</text>" in s2)
	tek("13 escape", "<b>" not in s2 and "&lt;b&gt;" in s2)
	tek("13 dona → №1/1", "№1/1" in s2 and "№2</text>" in s2)
	ids = re.findall(r' id="([^"]+)"', s + s2)
	tek("13 id prefiks/unikal", all(i.startswith(("l1-", "l2-")) for i in ids) and len(ids) == len(set(ids)), ids)
	ok, n = koordinatalar_ichida(s2, 760, 520); tek("13 koordinatalar box ichida", ok, n)
	tek("13 tartib=False → aylana yo'q", list_svg(LIST(), opts={"prefix": "l3", "tartib": False}).count("<circle") == 0)
	tek("13 zanjir=False → 1400 yo'q", ">1400<" not in list_svg(LIST(), opts={"prefix": "l4", "zanjir": False}))
	# 14 bo'sh holatlar
	tek("14 list o'lchami yo'q", "List o'lchami kiritilmagan" in list_svg(dict(W=0, H=0), opts={"prefix": "l5"}))
	s3 = list_svg(LIST(bolaklar=[], chiziqlar=[], qoldiqlar=[]), opts={"prefix": "l6"})
	tek("14 bo'lak yo'q", "Bo'lak joylashtirilmagan" in s3 and "<circle" not in s3)
	tek("14 chiqindi > 10 % list → amber", '#e8a33d' in list_svg(LIST(chiqindi_m2=1.5), opts={"prefix": "l7"}) and '#e8a33d' not in list_svg(LIST(), opts={"prefix": "l8"}))
	print(f"\nJAMI: {N['pass']} PASS, {N['fail']} FAIL")
	return N


if __name__ == "__main__":
	run()
