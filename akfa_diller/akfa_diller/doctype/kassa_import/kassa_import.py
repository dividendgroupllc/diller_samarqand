import frappe
from frappe import _
from frappe.model.document import Document


class KassaImport(Document):
    """
    Kassa (naqd to'lovlar) Excel importi -- boshqa dastur eksport qilgan
    "kassa" hisobotini akfa_diller'dagi mavjud Kassa doctype'iga aylantiradi.

    Workflow:
    1. Foydalanuvchi Excel fayl yuklaydi (company/mode_of_payment sozlangan).
    2. "Load Preview" -- manfiy summali qatorlarni review_rows'ga chiqaradi
       (musbat qatorlar har doim to'g'ridan-to'g'ri mijozga Приход bo'ladi,
       tekshirish shart emas).
    3. Foydalanuvchi har bir review_rows qatoriga "Turi"ni tanlaydi.
    4. "Process Import" -- har bir Excel qatori uchun ALOHIDA Kassa hujjati
       yaratadi va submit qiladi (guruhlash yo'q -- Sales Import'dan farqli,
       manba ma'lumotida bitta qator = bitta real to'lov hodisasi).
    """

    def validate(self):
        self._validate_not_mid_resume()

    def _validate_not_mid_resume(self):
        """
        Ba'zi qatorlar allaqachon Kassa hujjatiga aylangandan keyin
        company/mode_of_payment o'zgarishi -- qolgan qatorlar boshqa
        sozlamada yaratilib, bitta import ikki xil konfiguratsiyaga
        bo'linib ketishiga olib keladi (ogohlantirishsiz).
        """
        if not self.processed_rows or self.is_new():
            return

        locked_fields = {
            "company": _("Компания"),
            "mode_of_payment": _("To'lov usuli"),
        }
        for fieldname, label in locked_fields.items():
            if self.has_value_changed(fieldname):
                frappe.throw(
                    _("Import qisman bajarilgan (ba'zi qatorlar allaqachon Kassa hujjatiga aylangan) -- "
                      "{0}ni endi o'zgartirib bo'lmaydi. Avval 'Cancel Import' qiling.").format(label)
                )

    def before_submit(self):
        frappe.throw(_("Bu hujjatni submit qilib bo'lmaydi. 'Process Import' tugmasidan foydalaning."))

    def on_trash(self):
        if self.status == "Processed":
            frappe.throw(_("Bajarilgan importni o'chirib bo'lmaydi. Avval uni bekor qiling."))


# =============================================================================
# WHITELISTED METHODS (API modulida)
# =============================================================================
#
# akfa_diller/akfa_diller/api/daily_kassa_import.py
#
# =============================================================================
