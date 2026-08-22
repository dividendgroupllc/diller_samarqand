"""Kompaniya bo'yicha valyuta kursi: qidirish va hujjatlarga avtomatik qo'yish.

Muammo (foydalanuvchi, 2026-08-22): ERPNext'ning `Currency Exchange` jadvali
BUTUN tizim uchun umumiy -- unda `company` maydoni yo'q. Bizda 4 kompaniya bor
va ular bir kunda har xil kurs ishlatishi mumkin. Ikkinchi muammo: har hujjatga
kursni qo'lda kiritib bo'lmaydi.

Yechim: `Company Currency Exchange` doctype (kompaniya kesimida kurs) + shu
moduldagi hook'lar. Hook'lar `before_validate`da ishlaydi, ERPNext esa kursni
faqat BO'SH bo'lganda o'zi qo'yadi (tekshirilgan: accounts_controller.py
"elif not self.conversion_rate", payment_entry.py "if not self.source_exchange_rate")
-- shuning uchun bizning qiymat saqlanib qoladi.

Kurs topilmasa hech narsa qilinmaydi -- ERPNext o'zining umumiy jadvalidan
oladi (orqaga moslik buzilmaydi).
"""
import frappe
from frappe.utils import flt, nowdate

# Sinxron yaratgan hujjatlar kursni API'dan (currencyRate) o'zi qo'yadi --
# ularga tegmaymiz, aks holda tiyingacha hisoblangan summalar buziladi.
_SYNC_FIELD = "custom_rs_dealer"
# Buxgalter qo'lda kurs kiritsa shu belgini qo'yadi -- keyingi saqlashda
# hook uni bosib ketmaydi.
_MANUAL_FIELD = "custom_manual_exchange_rate"

CUSTOM_FIELDS = [
    ("Sales Invoice", "insert_after", "conversion_rate"),
    ("Purchase Invoice", "insert_after", "conversion_rate"),
    ("Sales Order", "insert_after", "conversion_rate"),
    ("Purchase Order", "insert_after", "conversion_rate"),
    ("Payment Entry", "insert_after", "target_exchange_rate"),
    ("Journal Entry", "insert_after", "multi_currency"),
]


def ensure_custom_fields():
    """`custom_manual_exchange_rate` belgisini kerakli doctype'larga qo'shadi."""
    for dt, _pos, after in CUSTOM_FIELDS:
        if frappe.db.exists("Custom Field", {"dt": dt, "fieldname": _MANUAL_FIELD}):
            continue
        cf = frappe.new_doc("Custom Field")
        cf.dt = dt
        cf.fieldname = _MANUAL_FIELD
        cf.fieldtype = "Check"
        cf.label = "Kurs qo'lda kiritildi"
        cf.description = "Belgilansa, avtomatik kurs qo'yilmaydi"
        cf.insert_after = after
        cf.flags.ignore_permissions = True
        cf.insert()
    ensure_revaluation_field()
    frappe.db.commit()


def get_company_rate(company, from_currency, to_currency, date=None, purpose="for_selling"):
    """Kompaniya uchun kursni topadi.

    Ikkala yo'nalish ham bitta so'rovda ko'riladi va ENG YANGI sanadagi yozuv
    olinadi (bir xil sanada aynan yo'nalish ustun). Teskari yozuv bo'lsa 1/kurs
    qaytariladi -- shuning uchun har kunga bitta yozuv yetadi.

    MUHIM: yo'nalish bo'yicha emas, SANA bo'yicha saralanadi. Aks holda eski
    "UZS->USD" yozuvi yangi "USD->UZS" yozuvini to'sib qo'yardi (lokal sinovda
    aynan shu xato chiqdi, 2026-08-22).

    Sana bo'yicha yozuv topilmasa (hujjat eng birinchi kursdan ham oldin
    bo'lsa) -- eng erta mavjud kurs olinadi. Ya'ni jadval BUTUNLAY o'zimizniki:
    ERPNext'ning umumiy jadvaliga tushib qolish amalda qolmaydi.
    """
    if not company or not from_currency or not to_currency:
        return None
    if from_currency == to_currency:
        return 1.0
    if purpose not in ("for_buying", "for_selling"):
        purpose = "for_selling"
    date = date or nowdate()

    def _qidir(sana_sharti, tartib):
        return frappe.db.sql(
            f"""select from_currency, exchange_rate
                from `tabCompany Currency Exchange`
                where company = %s and {purpose} = 1
                  and ((from_currency = %s and to_currency = %s)
                    or (from_currency = %s and to_currency = %s))
                  {sana_sharti}
                order by {tartib}, (from_currency = %s) desc
                limit 1""",
            (company, from_currency, to_currency, to_currency, from_currency,
             *( (date,) if sana_sharti else () ), from_currency),
            as_dict=True,
        )

    qator = _qidir("and date <= %s", "date desc") or _qidir("", "date asc")
    if not qator:
        return None
    q = qator[0]
    kurs = flt(q.exchange_rate)
    if not kurs:
        return None
    return kurs if q.from_currency == from_currency else (1.0 / kurs)


def upsert_rate(company, date, from_currency, to_currency, rate, source="API", note=None):
    """Kursni yozadi/yangilaydi (idempotent). Sinxron shuni chaqiradi."""
    if not (company and date and from_currency and to_currency) or not rate:
        return None
    if from_currency == to_currency:
        return None
    mavjud = frappe.db.get_value(
        "Company Currency Exchange",
        {"company": company, "date": date, "from_currency": from_currency,
         "to_currency": to_currency},
        ["name", "exchange_rate"],
    )
    if mavjud:
        nom, eski = mavjud
        if flt(eski) != flt(rate):
            frappe.db.set_value("Company Currency Exchange", nom, "exchange_rate", flt(rate))
        return nom
    doc = frappe.new_doc("Company Currency Exchange")
    doc.company = company
    doc.date = date
    doc.from_currency = from_currency
    doc.to_currency = to_currency
    doc.exchange_rate = flt(rate)
    doc.for_buying = 1
    doc.for_selling = 1
    doc.rate_source = source
    doc.note = note
    doc.flags.ignore_permissions = True
    doc.insert()
    return doc.name


def _skip(doc):
    """Sinxron hujjatlari va qo'lda kurs kiritilganlar chetlab o'tiladi."""
    if doc.get(_SYNC_FIELD):
        return True
    if doc.get(_MANUAL_FIELD):
        return True
    return False


def _company_currency(company):
    return frappe.get_cached_value("Company", company, "default_currency") if company else None


def set_conversion_rate(doc, method=None):
    """`currency` + `conversion_rate` maydonli hujjatlar uchun (SI/PI/SO/PO...)."""
    if _skip(doc) or not doc.get("company"):
        return
    kompaniya_valyutasi = _company_currency(doc.company)
    valyuta = doc.get("currency")
    if not valyuta or not kompaniya_valyutasi or valyuta == kompaniya_valyutasi:
        return
    maqsad = "for_buying" if doc.doctype.startswith("Purchase") else "for_selling"
    sana = doc.get("posting_date") or doc.get("transaction_date")
    kurs = get_company_rate(doc.company, valyuta, kompaniya_valyutasi, sana, maqsad)
    if kurs:
        doc.conversion_rate = kurs


def set_payment_entry_rates(doc, method=None):
    """Payment Entry uchun ALOHIDA: unda `currency` maydoni yo'q, kurs ikkita
    maydonda -- source_exchange_rate (paid_from tomoni) va target_exchange_rate
    (paid_to tomoni). Ikkalasi ham o'z hisob raqami valyutasidan hisoblanadi."""
    if _skip(doc) or not doc.get("company"):
        return
    kompaniya_valyutasi = _company_currency(doc.company)
    if not kompaniya_valyutasi:
        return
    sana = doc.get("posting_date")

    manba_valyuta = doc.get("paid_from_account_currency")
    if manba_valyuta and manba_valyuta != kompaniya_valyutasi and not doc.get("source_exchange_rate"):
        kurs = get_company_rate(doc.company, manba_valyuta, kompaniya_valyutasi, sana, "for_buying")
        if kurs:
            doc.source_exchange_rate = kurs

    maqsad_valyuta = doc.get("paid_to_account_currency")
    if maqsad_valyuta and maqsad_valyuta != kompaniya_valyutasi and not doc.get("target_exchange_rate"):
        kurs = get_company_rate(doc.company, maqsad_valyuta, kompaniya_valyutasi, sana, "for_selling")
        if kurs:
            doc.target_exchange_rate = kurs


def set_journal_entry_rates(doc, method=None):
    """Journal Entry: kurs har SATRDA alohida (Journal Entry Account.exchange_rate)."""
    if _skip(doc) or not doc.get("company"):
        return
    kompaniya_valyutasi = _company_currency(doc.company)
    if not kompaniya_valyutasi:
        return
    for satr in doc.get("accounts") or []:
        if satr.get("exchange_rate") or not satr.get("account"):
            continue
        hisob_valyutasi = frappe.get_cached_value("Account", satr.account, "account_currency")
        if not hisob_valyutasi or hisob_valyutasi == kompaniya_valyutasi:
            continue
        kurs = get_company_rate(doc.company, hisob_valyutasi, kompaniya_valyutasi,
                                doc.get("posting_date"), "for_selling")
        if kurs:
            satr.exchange_rate = kurs


def set_revaluation_rates(doc, method=None):
    """Oy oxiridagi valyuta qayta baholash (Exchange Rate Revaluation).

    ERPNext bu hujjatda kursni O'ZINING umumiy `Currency Exchange` jadvalidan
    oladi (erpnext/accounts/doctype/exchange_rate_revaluation.py:289 --
    `get_exchange_rate(...)`), ya'ni kompaniya kursini bilmaydi. Shu sabab
    kursni va unga bog'liq summalarni shu yerda qayta hisoblaymiz.

    Kompaniyaning o'z kursi bo'lmasa -- HECH NARSA qilinmaydi (boshqa
    kompaniyalar, masalan Oyna sex, o'z holicha ishlayveradi).
    """
    if not doc.get("company") or not doc.get("posting_date"):
        return
    if doc.get(_MANUAL_FIELD):
        return
    company_currency = _company_currency(doc.company)
    if not company_currency:
        return

    for satr in doc.get("accounts") or []:
        acc_cur = satr.get("account_currency")
        if not acc_cur or acc_cur == company_currency:
            continue
        if satr.get("zero_balance"):
            continue  # nol qoldiqli satrlarda ERPNext o'z mantig'ini ishlatadi
        kurs = get_company_rate(doc.company, acc_cur, company_currency,
                                doc.get("posting_date"), "for_selling")
        if not kurs:
            continue
        satr.new_exchange_rate = kurs
        # ERPNext'ning o'z formulasi (calculate_new_account_balance) bilan bir xil:
        yangi_baza = flt(flt(satr.get("balance_in_account_currency")) * kurs)
        satr.new_balance_in_base_currency = yangi_baza
        satr.gain_loss = yangi_baza - flt(satr.get("balance_in_base_currency"))


def ensure_revaluation_field():
    """Qayta baholash hujjatiga ham "kurs qo'lda kiritildi" belgisi."""
    if frappe.db.exists("Custom Field",
                        {"dt": "Exchange Rate Revaluation", "fieldname": _MANUAL_FIELD}):
        return
    cf = frappe.new_doc("Custom Field")
    cf.dt = "Exchange Rate Revaluation"
    cf.fieldname = _MANUAL_FIELD
    cf.fieldtype = "Check"
    cf.label = "Kurs qo'lda kiritildi"
    cf.description = "Belgilansa, kompaniya kursi avtomatik qo'yilmaydi"
    cf.insert_after = "posting_date"
    cf.flags.ignore_permissions = True
    cf.insert()
    frappe.db.commit()


def seed_from_global(dry_run=False):
    """ERPNext'ning UMUMIY `Currency Exchange` jadvalidagi kurslarni HAR
    KOMPANIYAGA ko'chiradi (bir martalik, idempotent).

    Sabab (foydalanuvchi qarori 2026-08-22): endi barcha kompaniyalar --
    Oyna sex va Imzo franshiza ham -- bizning `Company Currency Exchange`
    jadvalidan foydalanadi. Ko'chirilmasa, ularning hujjatlari kurssiz qolardi.

    bench --site <sayt> execute akfa_diller.akfa_diller.api.exchange.seed_from_global
    """
    umumiy = frappe.get_all(
        "Currency Exchange",
        fields=["date", "from_currency", "to_currency", "exchange_rate", "for_buying", "for_selling"],
        order_by="date",
    )
    kompaniyalar = frappe.get_all("Company", pluck="name")
    print(f"Umumiy jadvalda {len(umumiy)} kurs, {len(kompaniyalar)} kompaniya")
    yaratildi = mavjud = 0
    for komp in kompaniyalar:
        for k in umumiy:
            bor = frappe.db.exists(
                "Company Currency Exchange",
                {"company": komp, "date": k.date, "from_currency": k.from_currency,
                 "to_currency": k.to_currency},
            )
            if bor:
                mavjud += 1
                continue
            if dry_run:
                print(f"  [dry] {komp} | {k.date} | {k.from_currency}->{k.to_currency} = {k.exchange_rate}")
                yaratildi += 1
                continue
            upsert_rate(komp, k.date, k.from_currency, k.to_currency, k.exchange_rate,
                        source="Qo'lda", note="ERPNext umumiy jadvalidan ko'chirildi")
            yaratildi += 1
    if not dry_run:
        frappe.db.commit()
    print(f"Yaratildi={yaratildi}, allaqachon bor={mavjud}")


def rate_coverage():
    """Har kompaniyada kurs bormi -- nazorat hisoboti.

    bench --site <sayt> execute akfa_diller.akfa_diller.api.exchange.rate_coverage
    """
    print(f"{'Kompaniya':<22} {'Kitob':<6} {'Kurs yozuvi':>12}  {'Eng erta':<12} {'Eng oxirgi':<12}")
    for komp in frappe.get_all("Company", fields=["name", "default_currency"]):
        qatorlar = frappe.db.sql(
            """select count(*), min(date), max(date) from `tabCompany Currency Exchange`
               where company = %s""", (komp.name,))[0]
        print(f"{komp.name:<22} {komp.default_currency:<6} {qatorlar[0]:>12}  "
              f"{str(qatorlar[1] or '-'):<12} {str(qatorlar[2] or '-'):<12}")
    yoq = frappe.db.sql(
        """select a.company, a.account_currency, count(*) from tabAccount a
           where a.is_group = 0 and a.account_currency != (
               select default_currency from tabCompany c where c.name = a.company)
           group by 1, 2""")
    print("\nValyutali hisoblar (kurs kerak bo'ladigan joylar):")
    for komp, cur, n in yoq:
        bor = frappe.db.exists("Company Currency Exchange", {"company": komp})
        print(f"  {komp:<22} {cur:<5} {n:>3} hisob   kurs jadvalida: {'BOR' if bor else 'YO`Q'}")
