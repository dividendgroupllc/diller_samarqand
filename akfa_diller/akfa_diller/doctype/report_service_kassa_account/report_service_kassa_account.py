import frappe
from frappe.model.document import Document


class ReportServiceKassaAccount(Document):
    def validate(self):
        dup = frappe.db.exists(
            "Report Service Kassa Account",
            {
                "dealer_id": self.dealer_id,
                "kassa_label": self.kassa_label,
                "currency": self.currency or "",
                "name": ["!=", self.name],
            },
        )
        if dup:
            frappe.throw(f"Bu dealer+kassa+valyuta uchligi allaqachon xaritalangan: {dup}")
