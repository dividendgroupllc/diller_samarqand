# Copyright (c) 2026, abdulloh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ZakazImport(Document):
    def validate(self):
        if self.warehouse and self.company:
            wh_company = frappe.db.get_value("Warehouse", self.warehouse, "company")
            if wh_company != self.company:
                frappe.throw(_(
                    "Ombor {0} kompaniyasi ({1}) hujjat kompaniyasiga ({2}) mos emas"
                ).format(self.warehouse, wh_company, self.company))

    def before_submit(self):
        frappe.throw(_("Zakaz Import submit qilinmaydi — 'Import qilish' tugmasidan foydalaning"))

    def on_trash(self):
        if self.status == "Processed":
            frappe.throw(_(
                "Processed holatdagi importni o'chirib bo'lmaydi. Avval 'Bekor qilish'ni bosing."
            ))
