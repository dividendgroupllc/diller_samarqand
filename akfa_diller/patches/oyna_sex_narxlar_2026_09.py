# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""Sex narx ro'yxati (07.09.2026) — matofka turlari, bo'yash, paket, reska.

Foydalanuvchi bergan qo'lyozma ro'yxatdagi 12 xizmatni to'liq kiritadi va
matofka modelini sexning haqiqiy ishiga moslaydi:

  - eski «Matofka qisman» (maydon bo'yicha) → «Matofka polosa» (butun oynaga).
    Sex izohi: polosa va gulda ham BUTUN oyna matoviy bo'ladi, faqat lenta yoki
    naqsh ostidagi joy shaffof qoladi — shuning uchun maydon emas, narx farq qiladi;
  - «Matofka gul» 120 000, «Oyna bo'yash» 200 000, «Paket yig'ish» 50 000 (so'm/m²)
    va «Reska (kesish)» 3 000 (so'm/dona — har kesilgan oynaga) qatorlari qo'shiladi.

Idempotent: mavjud qatorning narxiga TEGILMAYDI (foydalanuvchi o'zgartirgan
bo'lishi mumkin), faqat yo'q qator qo'shiladi.
"""

import frappe

IZOH = "Sex narxi 07.09.2026"

#: xizmat -> (narx, birlik-izohi)
YANGI = (
	("Matofka gul", 120000),
	("Oyna bo'yash", 200000),
	("Paket yig'ish", 50000),
	("Reska (kesish)", 3000),   # 12.09.2026: sex aniqlashtirdi — 1 ta oyna 3 000
)


def execute():
	if not frappe.db.exists("DocType", "Oyna Narx Jadvali"):
		return

	# 1) pozitsiyalar: «Qisman» endi yo'q → «Polosa»
	n = frappe.db.sql("""update `tabOyna Zakaz Pozitsiya` set matofka_rejimi='Polosa'
	                     where matofka_rejimi='Qisman'""")
	frappe.db.sql("""update `tabOyna Zakaz Pozitsiya` set matofka_maydoni_m2=0
	                 where ifnull(matofka_maydoni_m2,0)!=0""")

	# 2) tarif-qatori: «Matofka qisman» → «Matofka polosa» (narx o'zgarmaydi)
	frappe.db.sql("""update `tabOyna Narx Ishlov`
	                 set xizmat='Matofka polosa',
	                     izoh=%s
	                 where xizmat='Matofka qisman'""", IZOH)

	# 3) yetishmayotgan xizmat-qatorlari
	s = frappe.get_single("Oyna Narx Jadvali")
	bor = {(r.xizmat or "").strip() for r in (s.get("narx_ishlov") or [])}
	qoshildi = []
	for xizmat, narx in YANGI:
		if xizmat in bor:
			continue
		s.append("narx_ishlov", {"xizmat": xizmat, "qalinlik_mm": 0, "narx": narx, "izoh": IZOH})
		qoshildi.append(xizmat)
	if qoshildi:
		s.flags.ignore_permissions = True
		s.save(ignore_permissions=True)

	frappe.db.commit()
	frappe.clear_cache(doctype="Oyna Zakaz Pozitsiya")
	print(f"  matofka «Qisman»→«Polosa»; yangi narx-qatorlari: {qoshildi or 'yo`q (hammasi bor edi)'}")
