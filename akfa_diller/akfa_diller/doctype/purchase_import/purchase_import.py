import frappe
from frappe import _
from frappe.model.document import Document

from akfa_diller.akfa_diller.utils.validators import validate_warehouse_company, ValidationError


class PurchaseImport(Document):
    """
    Kunlik xarid (Purchase) Excel importi.

    Workflow:
    1. Foydalanuvchi Excel fayl yuklaydi. Har bir qatorda o'z "Ta'minotchi"si
       va "Sana"si MAJBURIY (bu import shaklidagi sana faqat ma'lumot uchun --
       Excel qatorlariga o'rnini bosmaydi va bir faylda turli kunlar/
       ta'minotchilar bo'lishi mumkin). Manba ma'lumotida alohida "Chek №" yo'q.
    2. Preview har bir "Ta'minotchi" + "Sana" birikmasi bo'yicha tovarlarni va
       topilgan ta'minotchini ko'rsatadi.
    3. Process Import har bir (Ta'minotchi, Sana) juftligi uchun ALOHIDA
       Purchase Invoice yaratadi -- bir xil ta'minotchidan boshqa kunda qayta
       xarid qilinsa, u ham alohida Purchase Invoice bo'ladi (boshqa hech
       narsa yaratilmaydi — Stock Entry yo'q).
    """

    def validate(self):
        self._validate_warehouse()
        self._validate_not_mid_resume()

    def _validate_warehouse(self):
        if self.warehouse and self.company:
            try:
                validate_warehouse_company(self.warehouse, self.company)
            except ValidationError as e:
                frappe.throw(str(e))

    def _validate_not_mid_resume(self):
        """
        Once some groups have already been turned into Purchase Invoices
        (a partial/resumable run), company/warehouse/cost_center must not
        change -- otherwise a Retry would create the remaining invoices
        under a different company/warehouse/cost_center than the ones
        already committed, silently splitting one import across two
        configurations with no warning.
        """
        if not self.processed_groups or self.is_new():
            return

        locked_fields = {
            "company": _("Компания"),
            "warehouse": _("Ombor"),
            "cost_center": _("Cost Center"),
        }
        for fieldname, label in locked_fields.items():
            if self.has_value_changed(fieldname):
                frappe.throw(
                    _("Import qisman bajarilgan (ba'zi guruhlar allaqachon Purchase Invoice'ga aylangan) -- "
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
# akfa_diller/akfa_diller/api/daily_purchase_import.py
#
# Client Script:
#   frappe.call({
#       method: 'akfa_diller.akfa_diller.api.daily_purchase_import.process_import',
#       args: { doc_name: frm.doc.name }
#   });
#
# =============================================================================
