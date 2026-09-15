"""Raskroy «Listlar rejasi» qatorlariga har-list foizlarni to'ldirish: Dolzarb qoralamalarni qayta saqlash
(validate → hisobla → _raskroy_yoz). Idempotent."""
import frappe


def execute():
	if not frappe.db.exists("DocType", "Oyna Zakaz") or not frappe.db.has_column("Oyna Zakaz", "raskroy_holat"):
		return
	for name in frappe.get_all("Oyna Zakaz", {"docstatus": 0, "raskroy_holat": ("in", ("Dolzarb", "Eskirgan"))}, pluck="name"):
		try:
			d = frappe.get_doc("Oyna Zakaz", name)
			d.flags.ignore_permissions = True
			d.save()
		except Exception:
			frappe.log_error("oyna_raskroy_reja_resave", f"{name}: {frappe.get_traceback()}"[:2000])
	frappe.db.commit()
