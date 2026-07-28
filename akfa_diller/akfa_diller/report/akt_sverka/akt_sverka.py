import base64
import json
import os

import frappe
from frappe.utils import flt

def execute(filters=None):
    if not filters:
        return [], []

    columns = get_columns()
    data = get_data(filters)
    
    # Summary HTML table yaratish
    summary_html = get_summary_html(data, filters)
    
    return columns, data, summary_html, None, None


def format_balance(value):
    """Balance qiymatini 2 decimal bilan format qilish"""
    return round(flt(value), 2) if value is not None else None


def format_qty(value):
    """Qty ni 2 decimal bilan format qilish"""
    return round(flt(value), 2) if value is not None else None


def voucher_type_matches(row, base_voucher_type):
    voucher_type = (row or {}).get("voucher_type") or ""
    return voucher_type == base_voucher_type or voucher_type.startswith(f"{base_voucher_type} ")


def make_invoice_item_row(gl, item):
    return {
        "posting_date": "",
        "voucher_type": "",
        "voucher_no": "",
        "item_name": item.get('item_name', ''),
        "qty": format_qty(item.get('qty')),
        "rate": item.get('rate'),
        "currency": item.get('currency', gl.currency),
        "credit": item.get('credit', 0),
        "debit": item.get('debit', 0),
        "balance": None,
        "indent": 1,
        "is_item_row": 1,
    }


def get_columns():
    return [
        {"label": "Сана", "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
        {"label": "Ҳужжат", "fieldname": "voucher_type", "fieldtype": "Data", "width": 150},
        {"label": "Ҳужжат №", "fieldname": "voucher_no", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 210},
        {"label": "Маҳсулот номи", "fieldname": "item_name", "fieldtype": "Data", "width": 200},
        {"label": "Миқдори", "fieldname": "qty", "fieldtype": "Float", "precision": 2, "width": 80},
        {"label": "Нархи", "fieldname": "rate", "fieldtype": "Currency", "width": 100},
        {"label": "Валюта", "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "width": 80},
        {"label": "Кредит", "fieldname": "credit", "fieldtype": "Currency", "width": 120},
        {"label": "Дебет", "fieldname": "debit", "fieldtype": "Currency", "width": 120},
        {"label": "Қолдиқ", "fieldname": "balance", "fieldtype": "Float", "precision": 2, "width": 120},
    ]


def get_data(filters):
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    party_type = filters.get("party_type")
    party = filters.get("party")

    # Party ning asosiy valyutasini aniqlash (birinchi tranzaksiya valyutasidan)
    party_currency = frappe.db.sql("""
        SELECT account_currency
        FROM `tabGL Entry`
        WHERE party_type = %s
          AND party = %s
          AND is_cancelled = 0
        ORDER BY posting_date ASC, creation ASC
        LIMIT 1
    """, (party_type, party))

    party_currency = party_currency[0][0] if party_currency else 'UZS'

    # Boshlang'ich qoldiq (до from_date) - party valyutasida, cancelled'siz.
    # Invoice returnlari GL'da teskari tomonga tushadi, shuning uchun invoice'lar net hisoblanadi.
    # Formula: PI net - SI net + PE receive - PE pay + JE credit - JE debit + Opening credit - Opening debit + Salary

    # Purchase Invoice (PI) - credit qarzni oshiradi, debit return qarzni kamaytiradi
    opening_pi = frappe.db.sql("""
        SELECT IFNULL(SUM(credit_in_account_currency - debit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE posting_date < %s
          AND party_type = %s
          AND party = %s
          AND voucher_type = 'Purchase Invoice'
          AND account_currency = %s
          AND is_cancelled = 0
    """, (from_date, party_type, party, party_currency))[0][0]

    # Sales Invoice (SI) - debit ular qarzini oshiradi, credit return qarzni kamaytiradi
    opening_si = frappe.db.sql("""
        SELECT IFNULL(SUM(debit_in_account_currency - credit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE posting_date < %s
          AND party_type = %s
          AND party = %s
          AND voucher_type = 'Sales Invoice'
          AND account_currency = %s
          AND is_cancelled = 0
    """, (from_date, party_type, party, party_currency))[0][0]

    # Payment Entry Receive - credit (biz pul oldik)
    opening_pe_receive = frappe.db.sql("""
        SELECT IFNULL(SUM(ge.credit_in_account_currency), 0)
        FROM `tabGL Entry` ge
        INNER JOIN `tabPayment Entry` pe ON ge.voucher_no = pe.name
        WHERE ge.posting_date < %s
          AND ge.party_type = %s
          AND ge.party = %s
          AND ge.voucher_type = 'Payment Entry'
          AND pe.payment_type = 'Receive'
          AND ge.account_currency = %s
          AND ge.is_cancelled = 0
    """, (from_date, party_type, party, party_currency))[0][0]

    # Payment Entry Pay - debit (biz pul to'ladik)
    opening_pe_pay = frappe.db.sql("""
        SELECT IFNULL(SUM(ge.debit_in_account_currency), 0)
        FROM `tabGL Entry` ge
        INNER JOIN `tabPayment Entry` pe ON ge.voucher_no = pe.name
        WHERE ge.posting_date < %s
          AND ge.party_type = %s
          AND ge.party = %s
          AND ge.voucher_type = 'Payment Entry'
          AND pe.payment_type = 'Pay'
          AND ge.account_currency = %s
          AND ge.is_cancelled = 0
    """, (from_date, party_type, party, party_currency))[0][0]

    # Journal Entry (JE) credit va debit - opening entry emas
    opening_je = frappe.db.sql("""
        SELECT
            IFNULL(SUM(ge.credit_in_account_currency), 0) as je_credit,
            IFNULL(SUM(ge.debit_in_account_currency), 0) as je_debit
        FROM `tabGL Entry` ge
        INNER JOIN `tabJournal Entry` je ON ge.voucher_no = je.name
        WHERE ge.posting_date < %s
          AND ge.party_type = %s
          AND ge.party = %s
          AND ge.voucher_type = 'Journal Entry'
          AND je.is_opening = 'No'
          AND ge.account_currency = %s
          AND ge.is_cancelled = 0
    """, (from_date, party_type, party, party_currency), as_dict=True)[0]

    opening_je_credit = opening_je.get('je_credit', 0)
    opening_je_debit = opening_je.get('je_debit', 0)

    # Opening Entry credit va debit
    opening_entry = frappe.db.sql("""
        SELECT
            IFNULL(SUM(ge.credit_in_account_currency), 0) as op_credit,
            IFNULL(SUM(ge.debit_in_account_currency), 0) as op_debit
        FROM `tabGL Entry` ge
        INNER JOIN `tabJournal Entry` je ON ge.voucher_no = je.name
        WHERE ge.posting_date < %s
          AND ge.party_type = %s
          AND ge.party = %s
          AND ge.voucher_type = 'Journal Entry'
          AND je.is_opening = 'Yes'
          AND ge.account_currency = %s
          AND ge.is_cancelled = 0
    """, (from_date, party_type, party, party_currency), as_dict=True)[0]

    opening_op_credit = opening_entry.get('op_credit', 0)
    opening_op_debit = opening_entry.get('op_debit', 0)

    # Salary Slip'lardan (faqat Employee uchun)
    opening_salary = 0
    if party_type == "Employee":
        opening_salary = frappe.db.sql("""
            SELECT
                IFNULL(SUM(gross_pay), 0)
            FROM `tabSalary Slip`
            WHERE posting_date < %s
              AND employee = %s
              AND docstatus = 1
        """, (from_date, party))[0][0]

    # Formula: PI - SI + PE receive - PE pay + JE credit - JE debit + OP credit - OP debit + Salary
    opening_balance = (
        flt(opening_pi) - flt(opening_si) +
        flt(opening_pe_receive) - flt(opening_pe_pay) +
        flt(opening_je_credit) - flt(opening_je_debit) +
        flt(opening_op_credit) - flt(opening_op_debit) +
        flt(opening_salary)
    )

    data = []

    # Opening qoldiq birinchi qator
    # Agar musbat -> Credit, manfiy -> Debit
    opening_credit = opening_balance if opening_balance > 0 else 0
    opening_debit = abs(opening_balance) if opening_balance < 0 else 0

    data.append({
        "posting_date": from_date,
        "voucher_type": "Boshlang'ich qoldiq",
        "voucher_no": "",
        "item_name": "",
        "qty": None,
        "rate": None,
        "currency": party_currency,
        "credit": opening_credit,
        "debit": opening_debit,
        "balance": format_balance(opening_balance)
    })

    # GL Entry'larni olish - faqat party valyutasida, cancelled'larni olib tashlash
    gl_entries = frappe.db.sql("""
        SELECT
            gl.posting_date,
            gl.voucher_type,
            gl.voucher_no,
            gl.debit_in_account_currency as debit,
            gl.credit_in_account_currency as credit,
            gl.account_currency AS currency
        FROM `tabGL Entry` gl
        WHERE gl.posting_date BETWEEN %s AND %s
          AND gl.party_type = %s
          AND gl.party = %s
          AND gl.account_currency = %s
          AND gl.is_cancelled = 0
        ORDER BY gl.posting_date ASC, gl.creation ASC
    """, (from_date, to_date, party_type, party, party_currency), as_dict=True)

    # Salary Slip'larni olish (Employee uchun)
    salary_slips = []
    if party_type == "Employee":
        salary_slips = frappe.db.sql("""
            SELECT 
                posting_date,
                name as voucher_no,
                employee_name,
                currency,
                gross_pay as credit,
                0 as debit
            FROM `tabSalary Slip`
            WHERE posting_date BETWEEN %s AND %s
              AND employee = %s
              AND docstatus = 1
            ORDER BY posting_date ASC
        """, (from_date, to_date, party), as_dict=True)
        
        # Salary Slip'larni GL entries bilan birlashtirish
        for ss in salary_slips:
            ss['voucher_type'] = 'Salary Slip'
    
    # GL Entry va Salary Slip'larni birlashtirish va sanaga qarab tartiblash
    all_entries = list(gl_entries) + list(salary_slips)
    all_entries.sort(key=lambda x: x['posting_date'])

    balance = opening_balance  # Balance party valyutasida

    # Har bir entry uchun detail ma'lumotlarni olish
    for entry in all_entries:
        voucher_type = entry.get('voucher_type')
        voucher_no = entry.get('voucher_no')
        
        # Salary Slip uchun
        if voucher_type == "Salary Slip":
            balance += flt(entry.get('credit', 0))  # Salary - credit (bizning qarzimiz oshadi)

            data.append({
                "posting_date": entry.posting_date,
                "voucher_type": voucher_type,
                "voucher_no": voucher_no,
                "item_name": entry.get('employee_name', ''),
                "qty": None,
                "rate": None,
                "currency": entry.get('currency', party_currency),
                "credit": entry.get('credit', 0),
                "debit": 0,
                "balance": format_balance(balance),
            })
            continue
        
        # GL Entry'lar uchun (eski kod)
        gl = entry
        
        # Purchase Invoice uchun item details
        if voucher_type == "Purchase Invoice":
            items = get_purchase_invoice_items(voucher_no)
            if items:
                total_credit = sum(flt(item.get('credit', 0)) for item in items)
                balance += total_credit  # Credit - bizning qarzimiz oshadi

                parent_row_id = f"{voucher_type}:{voucher_no}"
                data.append({
                    "posting_date": gl.posting_date,
                    "voucher_type": voucher_type,
                    "voucher_no": voucher_no,
                    "item_name": "",
                    "qty": None,
                    "rate": None,
                    "currency": gl.currency,
                    "credit": total_credit,
                    "debit": 0,
                    "balance": format_balance(balance),
                    "indent": 0,
                    "row_id": parent_row_id,
                    "has_item_details": 1,
                    "item_count": len(items),
                })

                for item in items:
                    child_row = make_invoice_item_row(gl, item)
                    child_row["parent_row_id"] = parent_row_id
                    data.append(child_row)
            else:
                # Agar item topilmasa, faqat GL entry ko'rsatish
                balance += flt(gl.credit)  # Credit oshadi
                data.append({
                    "posting_date": gl.posting_date,
                    "voucher_type": voucher_type,
                    "voucher_no": voucher_no,
                    "item_name": "",
                    "qty": None,
                    "rate": None,
                    "currency": gl.currency,
                    "credit": gl.credit,
                    "debit": 0,
                    "balance": format_balance(balance),
                })
        
        # Sales Invoice uchun item details
        elif voucher_type == "Sales Invoice":
            items = get_sales_invoice_items(voucher_no)
            if items:
                total_debit = sum(flt(item.get('debit', 0)) for item in items)
                balance -= total_debit  # Debit - to'lov qildik, qarz kamayadi

                parent_row_id = f"{voucher_type}:{voucher_no}"
                data.append({
                    "posting_date": gl.posting_date,
                    "voucher_type": voucher_type,
                    "voucher_no": voucher_no,
                    "item_name": "",
                    "qty": None,
                    "rate": None,
                    "currency": gl.currency,
                    "credit": 0,
                    "debit": total_debit,
                    "balance": format_balance(balance),
                    "indent": 0,
                    "row_id": parent_row_id,
                    "has_item_details": 1,
                    "item_count": len(items),
                })

                for item in items:
                    child_row = make_invoice_item_row(gl, item)
                    child_row["parent_row_id"] = parent_row_id
                    data.append(child_row)
            else:
                balance -= flt(gl.debit)  # Debit kamayadi
                data.append({
                    "posting_date": gl.posting_date,
                    "voucher_type": voucher_type,
                    "voucher_no": voucher_no,
                    "item_name": "",
                    "qty": None,
                    "rate": None,
                    "currency": gl.currency,
                    "credit": 0,
                    "debit": gl.debit,
                    "balance": format_balance(balance),
                })
        
        # Payment Entry uchun
        elif voucher_type == "Payment Entry":
            payment_info = get_payment_entry_info(voucher_no)

            # Payment Entry: Balance = Balance + Credit - Debit
            balance += flt(gl.credit) - flt(gl.debit)

            data.append({
                "posting_date": gl.posting_date,
                "voucher_type": voucher_type,
                "voucher_no": voucher_no,
                "item_name": payment_info.get('description', ''),
                "qty": None,
                "rate": None,
                "currency": gl.currency,
                "credit": gl.credit,
                "debit": gl.debit,
                "balance": format_balance(balance),
            })
        
        # Journal Entry uchun
        elif voucher_type == "Journal Entry":
            je_accounts = get_journal_entry_accounts(voucher_no, party_type, party)
            if je_accounts:
                # Har bir accounting entry uchun qator, balance faqat oxirida
                total_debit = sum(flt(acc.get('debit', 0)) for acc in je_accounts)
                total_credit = sum(flt(acc.get('credit', 0)) for acc in je_accounts)

                for idx, acc in enumerate(je_accounts):
                    is_last_item = (idx == len(je_accounts) - 1)
                    if is_last_item:
                        balance += total_credit - total_debit  # Credit - Debit

                    data.append({
                        "posting_date": gl.posting_date,
                        "voucher_type": voucher_type,
                        "voucher_no": voucher_no,
                        "item_name": acc.get('account', ''),
                        "qty": None,
                        "rate": None,
                        "currency": gl.currency,
                        "credit": acc.get('credit', 0),
                        "debit": acc.get('debit', 0),
                        "balance": format_balance(balance) if is_last_item else None,
                    })
            else:
                balance += flt(gl.credit) - flt(gl.debit)  # Credit - Debit
                data.append({
                    "posting_date": gl.posting_date,
                    "voucher_type": voucher_type,
                    "voucher_no": voucher_no,
                    "item_name": "",
                    "qty": None,
                    "rate": None,
                    "currency": gl.currency,
                    "credit": gl.credit,
                    "debit": gl.debit,
                    "balance": format_balance(balance),
                })
        
        # Boshqa document type'lar uchun
        else:
            balance += flt(gl.credit) - flt(gl.debit)  # Credit - Debit
            data.append({
                "posting_date": gl.posting_date,
                "voucher_type": voucher_type,
                "voucher_no": voucher_no,
                "item_name": "",
                "qty": None,
                "rate": None,
                "currency": gl.currency,
                "credit": gl.credit,
                "debit": gl.debit,
                "balance": format_balance(balance),
            })

    # Total qatorini qo'shish
    if len(data) > 1:  # Agar faqat opening balance dan ko'proq qator bo'lsa
        # Opening balance qatorini hisobga olmasdan total hisoblash
        total_credit = sum(
            flt(row.get('credit', 0))
            for row in data
            if row.get('voucher_type') != 'Boshlang\'ich qoldiq' and not row.get('is_item_row')
        )
        total_debit = sum(
            flt(row.get('debit', 0))
            for row in data
            if row.get('voucher_type') != 'Boshlang\'ich qoldiq' and not row.get('is_item_row')
        )

        data.append({
            "posting_date": to_date,
            "voucher_type": "Total",
            "voucher_no": "",
            "item_name": "",
            "qty": None,
            "rate": None,
            "currency": party_currency,
            "credit": total_credit,
            "debit": total_debit,
            "balance": format_balance(balance),  # Oxirgi balance party valyutasida
        })

    for row in data:
        row.setdefault("indent", 0)

    return data


def get_purchase_invoice_items(voucher_no):
    """Purchase Invoice item'larini olish"""
    items = frappe.db.sql("""
        SELECT 
            pii.item_name,
            pii.qty,
            pii.rate,
            pi.currency,
            pii.amount as credit,
            0 as debit
        FROM `tabPurchase Invoice Item` pii
        INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
        WHERE pii.parent = %s
        ORDER BY pii.idx
    """, voucher_no, as_dict=True)
    
    return items


def get_sales_invoice_items(voucher_no):
    """Sales Invoice item'larini olish"""
    items = frappe.db.sql("""
        SELECT 
            sii.item_name,
            sii.qty,
            sii.rate,
            si.currency,
            0 as credit,
            sii.amount as debit
        FROM `tabSales Invoice Item` sii
        INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
        WHERE sii.parent = %s
        ORDER BY sii.idx
    """, voucher_no, as_dict=True)
    
    return items


def get_payment_entry_info(voucher_no):
    """Payment Entry ma'lumotlarini olish"""
    payment = frappe.db.sql("""
        SELECT 
            payment_type,
            paid_from,
            paid_to
        FROM `tabPayment Entry`
        WHERE name = %s
    """, voucher_no, as_dict=True)
    
    if payment:
        p = payment[0]
        # Payment type ga qarab description va account
        if p.payment_type == 'Pay':
            return {
                'description': 'Pay',
                'account': p.paid_from,
            }
        elif p.payment_type == 'Receive':
            return {
                'description': 'Receive',
                'account': p.paid_to
            }
        else:
            return {
                'description': p.payment_type,
                'account': p.paid_from or p.paid_to
            }
    
    return {'description': '', 'account': ''}


def get_journal_entry_accounts(voucher_no, party_type, party):
    """Journal Entry account'larini olish - account currency da"""
    accounts = frappe.db.sql("""
        SELECT 
            account,
            debit_in_account_currency as debit,
            credit_in_account_currency as credit
        FROM `tabJournal Entry Account`
        WHERE parent = %s
          AND party_type = %s
          AND party = %s
        ORDER BY idx
    """, (voucher_no, party_type, party), as_dict=True)
    
    return accounts


def get_summary_html(data, filters):
    """
    Summary HTML table yaratish - 2 qatorda Credit va Debit parallel
    """
    if not data or len(data) <= 1:
        return ""
    
    # Opening balance
    opening_balance = data[0].get('balance', 0) if data else 0
    
    # Closing balance (Total qatoridan yoki oxirgi qatordan)
    closing_balance = 0
    total_row = [r for r in data if r.get('voucher_type') == 'Total']
    if total_row:
        closing_balance = total_row[0].get('balance', 0)
    elif data:
        closing_balance = data[-1].get('balance', 0)
    
    # Ostatok nachalo - Opening balance ni Credit/Debit ga bo'lish
    opening_credit = opening_balance if opening_balance > 0 else 0
    opening_debit = abs(opening_balance) if opening_balance < 0 else 0
    
    # Oborot po tovar (Goods turnover)
    goods_credit = sum(flt(r.get('credit', 0)) for r in data 
                       if r.get('voucher_type') == 'Purchase Invoice')
    goods_debit = sum(flt(r.get('debit', 0)) for r in data 
                      if r.get('voucher_type') == 'Sales Invoice')
    
    # Oborot po deneg (Money turnover)
    money_credit = sum(flt(r.get('credit', 0)) for r in data 
                       if r.get('voucher_type') == 'Payment Entry')
    money_debit = sum(flt(r.get('debit', 0)) for r in data 
                      if r.get('voucher_type') == 'Payment Entry')
    
    # Nachisleniya (Accruals) - Journal Entry + Salary Slip
    accruals_credit = sum(flt(r.get('credit', 0)) for r in data 
                          if r.get('voucher_type') in ['Journal Entry', 'Salary Slip'])
    accruals_debit = sum(flt(r.get('debit', 0)) for r in data 
                         if r.get('voucher_type') in ['Journal Entry', 'Salary Slip'])
    
    # Ostatok nakones
    closing_credit = closing_balance if closing_balance > 0 else 0
    closing_debit = abs(closing_balance) if closing_balance < 0 else 0
    
    # HTML table yaratish
    html = f"""
    <div style="margin-top: 20px; padding: 15px; background-color: #f9f9f9; border-radius: 5px;">
        <table style="width: 100%; border-collapse: collapse; background: white;">
            <thead>
                <tr style="background-color: #f0f0f0;">
                    <th style="padding: 10px; text-align: left; border: 1px solid #ddd; width: 40%;"></th>
                    <th style="padding: 10px; text-align: right; border: 1px solid #ddd; width: 30%; color: #d32f2f; font-weight: bold;">Кредит (Credit)</th>
                    <th style="padding: 10px; text-align: right; border: 1px solid #ddd; width: 30%; color: #388e3c; font-weight: bold;">Дебет (Debit)</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd; font-weight: 500;">Остаток на начало</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #d32f2f;">{opening_credit:,.2f}</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #388e3c;">{opening_debit:,.2f}</td>
                </tr>
                <tr style="background-color: #fafafa;">
                    <td style="padding: 10px; border: 1px solid #ddd; font-weight: 500;">Оборот по товарам</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #d32f2f;">{goods_credit:,.2f}</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #388e3c;">{goods_debit:,.2f}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd; font-weight: 500;">Оборот по деньгам</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #d32f2f;">{money_credit:,.2f}</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #388e3c;">{money_debit:,.2f}</td>
                </tr>
                <tr style="background-color: #fafafa;">
                    <td style="padding: 10px; border: 1px solid #ddd; font-weight: 500;">Начисления</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #d32f2f;">{accruals_credit:,.2f}</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #388e3c;">{accruals_debit:,.2f}</td>
                </tr>
                <tr style="background-color: #e3f2fd; font-weight: bold;">
                    <td style="padding: 12px; border: 1px solid #ddd; font-weight: bold;">Остаток на конец</td>
                    <td style="padding: 12px; border: 1px solid #ddd; text-align: right; color: #d32f2f; font-weight: bold;">{closing_credit:,.2f}</td>
                    <td style="padding: 12px; border: 1px solid #ddd; text-align: right; color: #388e3c; font-weight: bold;">{closing_debit:,.2f}</td>
                </tr>
            </tbody>
        </table>
    </div>
    """
    
    return html


@frappe.whitelist()
def generate_akt_sverka_pdf(filters):
    from frappe.utils.pdf import get_pdf

    if isinstance(filters, str):
        filters = json.loads(filters)

    data = get_data(filters)

    company = (
        frappe.defaults.get_user_default("Company")
        or frappe.db.get_single_value("Global Defaults", "default_company")
        or ""
    )

    summary = _build_pdf_summary(data)
    html = _render_pdf_html(data, filters, company, summary)

    pdf_options = {
        "page-size": "A4",
        "orientation": "Landscape",
        "margin-top": "12mm",
        "margin-right": "8mm",
        "margin-bottom": "12mm",
        "margin-left": "8mm",
        "encoding": "UTF-8",
        "no-outline": None,
    }

    try:
        pdf_content = get_pdf(html, options=pdf_options)
    except Exception as e:
        frappe.log_error(message=frappe.get_traceback(), title="Akt Sverka PDF Error")
        frappe.throw(
            f"PDF генерациясида хато юз берди: {str(e)}\n"
            "Лог: Site Logs > Error Log да кўринг."
        )

    return base64.b64encode(pdf_content).decode("utf-8")


def _fmt_num(value):
    try:
        return "{:,.2f}".format(float(value or 0))
    except (TypeError, ValueError):
        return "0.00"


def _build_pdf_summary(data):
    if not data:
        return {
            "opening_credit": 0.0,
            "opening_debit": 0.0,
            "goods_credit": 0.0,
            "goods_debit": 0.0,
            "money_credit": 0.0,
            "money_debit": 0.0,
            "accruals_credit": 0.0,
            "accruals_debit": 0.0,
            "closing_credit": 0.0,
            "closing_debit": 0.0,
        }

    opening_balance = flt(data[0].get("balance") or 0)

    closing_balance = 0.0
    total_rows = [r for r in data if r.get("voucher_type") == "Total"]
    if total_rows:
        closing_balance = flt(total_rows[0].get("balance") or 0)
    elif data:
        closing_balance = flt(data[-1].get("balance") or 0)

    def _credit(voucher_type):
        return sum(flt(r.get("credit", 0)) for r in data if voucher_type_matches(r, voucher_type))

    def _debit(voucher_type):
        return sum(flt(r.get("debit", 0)) for r in data if voucher_type_matches(r, voucher_type))

    return {
        "opening_credit": opening_balance if opening_balance > 0 else 0.0,
        "opening_debit": abs(opening_balance) if opening_balance < 0 else 0.0,
        "goods_credit": _credit("Purchase Invoice"),
        "goods_debit": _debit("Sales Invoice"),
        "money_credit": _credit("Payment Entry"),
        "money_debit": _debit("Payment Entry"),
        "accruals_credit": _credit("Journal Entry") + _credit("Salary Slip"),
        "accruals_debit": _debit("Journal Entry") + _debit("Salary Slip"),
        "closing_credit": closing_balance if closing_balance > 0 else 0.0,
        "closing_debit": abs(closing_balance) if closing_balance < 0 else 0.0,
    }


def _render_pdf_html(data, filters, company, summary):
    template_path = os.path.join(os.path.dirname(__file__), "akt_sverka_template.html")

    if not os.path.exists(template_path):
        frappe.throw(
            f"PDF шаблон топилмади: {template_path}\n"
            "akt_sverka_template.html файлини report папкасига қўйинг."
        )

    with open(template_path, "r", encoding="utf-8") as f:
        template_str = f.read()

    context = {
        "rows": data,
        "company": company,
        "summary": summary,
        "party": filters.get("party", ""),
        "party_type": filters.get("party_type", ""),
        "from_date": filters.get("from_date", ""),
        "to_date": filters.get("to_date", ""),
        "fmt": _fmt_num,
    }

    return frappe.render_template(template_str, context)
