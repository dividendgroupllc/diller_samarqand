import frappe
from frappe import _
from frappe.model.document import Document


class ReportServiceIchkiKarta(Document):
	def validate(self):
		self.client_name = (self.client_name or "").strip()
		dup = frappe.db.exists("Report Service Ichki Karta",
		                       {"client_name": self.client_name, "name": ["!=", self.name]})
		if dup:
			frappe.throw(_("Bu klient nomi allaqachon xaritalangan: {0}").format(dup))
		if self.rol == "Filial" and not self.naqd_account:
			frappe.throw(_("«Filial» roli uchun naqd hisob majburiy"))
		for f in ("naqd_account", "bank_account", "karta_account"):
			acc = self.get(f)
			if acc and frappe.db.get_value("Account", acc, "company") != self.company:
				frappe.throw(_("{0}: hisob {1} boshqa kompaniyaga tegishli").format(self.meta.get_label(f), acc))
