# Copyright (c) 2025, abdulloh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ExpenseCostCenter(Document):
    def validate(self):
        if self.expense_account:
            acc = frappe.get_cached_value(
                "Account",
                self.expense_account,
                ["root_type", "is_group"],
                as_dict=True
            )
            if not acc:
                frappe.throw(_("Account {0} topilmadi").format(self.expense_account))
            if acc.root_type != "Expense":
                frappe.throw(_("Account Expense turiga tegishli bo'lishi kerak"))
            if acc.is_group:
                frappe.throw(_("Guruh account tanlab bo'lmaydi"))
