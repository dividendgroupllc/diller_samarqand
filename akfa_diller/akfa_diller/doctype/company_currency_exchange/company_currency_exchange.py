"""Kompaniya bo'yicha valyuta kursi (foydalanuvchi talabi 2026-08-22).

ERPNext'ning o'z `Currency Exchange` jadvalida `company` maydoni YO'Q -- kurs
butun tizim uchun bitta. Bizda esa 4 kompaniya bor va ular bir kunda har xil
kurs ishlatishi mumkin. Shu sabab shu doctype: kurs KOMPANIYA kesimida.
"""
import frappe
from frappe.model.document import Document


class CompanyCurrencyExchange(Document):
    def autoname(self):
        abbr = frappe.get_cached_value("Company", self.company, "abbr") or self.company
        if self.for_buying and self.for_selling:
            tur = "BS"
        elif self.for_buying:
            tur = "B"
        elif self.for_selling:
            tur = "S"
        else:
            tur = "X"
        # kompaniya nomida apostrof bo'lishi mumkin (Kattaqo'rg'on) -- abbr ishlatamiz
        self.name = f"{abbr}-{self.date}-{self.from_currency}-{self.to_currency}-{tur}"

    def validate(self):
        # autoname insert'da validate'dan OLDIN ishlaydi -- shu sabab avval
        # aynan shu nom bandmi, shuni tekshiramiz (aks holda baza darajasidagi
        # tushunarsiz IntegrityError chiqadi)
        if self.is_new() and self.name and frappe.db.exists("Company Currency Exchange", self.name):
            frappe.throw(
                f"Bu kurs allaqachon kiritilgan: {self.name}. "
                "Yangisini qo'shish o'rniga mavjudini oching va kursni o'zgartiring."
            )
        if self.from_currency == self.to_currency:
            frappe.throw("Bir xil valyutaga kurs kerak emas")
        if not self.exchange_rate or self.exchange_rate <= 0:
            frappe.throw("Kurs 0 dan katta bo'lishi kerak")
        if not (self.for_buying or self.for_selling):
            frappe.throw("Kamida bittasi belgilanishi kerak: xaridlar yoki sotuvlar uchun")
        # bir kompaniya + kun + valyuta juftligi + maqsad uchun bitta yozuv
        dup = frappe.db.sql(
            """select name from `tabCompany Currency Exchange`
               where company=%s and date=%s and from_currency=%s and to_currency=%s
                 and name != %s
                 and ((for_buying=1 and %s=1) or (for_selling=1 and %s=1))
               limit 1""",
            (self.company, self.date, self.from_currency, self.to_currency,
             self.name or "", self.for_buying or 0, self.for_selling or 0),
        )
        if dup:
            frappe.throw(f"Bu kompaniya/sana/valyuta uchun kurs allaqachon bor: {dup[0][0]}")
