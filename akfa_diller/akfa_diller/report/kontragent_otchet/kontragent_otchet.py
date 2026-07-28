import frappe
from frappe.utils import flt

def execute(filters=None):
    if not filters:
        return [], []

    columns = get_columns(filters)
    data = get_data(filters)

    return columns, data


def get_columns(filters):
    currency = filters.get("currency", "")

    # Base columns
    columns = [
        {"label": "Контрагент тури", "fieldname": "party_type", "fieldtype": "Data", "width": 130},
        {"label": "Контрагент", "fieldname": "party", "fieldtype": "Dynamic Link", "options": "party_type", "width": 200},
        {"label": "Валюта", "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "width": 80},
        {"label": "Акт Сверка", "fieldname": "akt_sverka_link", "fieldtype": "Data", "width": 120},
    ]

    # If currency filter is empty, show all currencies
    if not currency:
        columns.extend([
            # Opening balances UZS
            {"label": "Кредит UZS (дан олдин)", "fieldname": "opening_credit_uzs", "fieldtype": "Currency", "width": 150},
            {"label": "Дебет UZS (дан олдин)", "fieldname": "opening_debit_uzs", "fieldtype": "Currency", "width": 150},
            # Opening balances USD
            {"label": "Кредит USD (дан олдин)", "fieldname": "opening_credit_usd", "fieldtype": "Currency", "width": 150},
            {"label": "Дебет USD (дан олдин)", "fieldname": "opening_debit_usd", "fieldtype": "Currency", "width": 150},
            # Period balances UZS
            {"label": "Кредит UZS (давр)", "fieldname": "period_credit_uzs", "fieldtype": "Currency", "width": 150},
            {"label": "Дебет UZS (давр)", "fieldname": "period_debit_uzs", "fieldtype": "Currency", "width": 150},
            # Period balances USD
            {"label": "Кредит USD (давр)", "fieldname": "period_credit_usd", "fieldtype": "Currency", "width": 150},
            {"label": "Дебет USD (давр)", "fieldname": "period_debit_usd", "fieldtype": "Currency", "width": 150},
            # Final balances UZS
            {"label": "Сўнгги Кредит UZS", "fieldname": "final_credit_uzs", "fieldtype": "Currency", "width": 150},
            {"label": "Сўнгги Дебет UZS", "fieldname": "final_debit_uzs", "fieldtype": "Currency", "width": 150},
            # Final balances USD
            {"label": "Сўнгги Кредит USD", "fieldname": "final_credit_usd", "fieldtype": "Currency", "width": 150},
            {"label": "Сўнгги Дебет USD", "fieldname": "final_debit_usd", "fieldtype": "Currency", "width": 150},
        ])
    elif currency == "UZS":
        columns.extend([
            # Opening balances UZS
            {"label": "Кредит (дан олдин)", "fieldname": "opening_credit_uzs", "fieldtype": "Currency", "width": 150},
            {"label": "Дебет (дан олдин)", "fieldname": "opening_debit_uzs", "fieldtype": "Currency", "width": 150},
            # Period balances UZS
            {"label": "Кредит (давр)", "fieldname": "period_credit_uzs", "fieldtype": "Currency", "width": 150},
            {"label": "Дебет (давр)", "fieldname": "period_debit_uzs", "fieldtype": "Currency", "width": 150},
            # Final balances UZS
            {"label": "Сўнгги Кредит", "fieldname": "final_credit_uzs", "fieldtype": "Currency", "width": 150},
            {"label": "Сўнгги Дебет", "fieldname": "final_debit_uzs", "fieldtype": "Currency", "width": 150},
        ])
    elif currency == "USD":
        columns.extend([
            # Opening balances USD
            {"label": "Кредит (дан олдин)", "fieldname": "opening_credit_usd", "fieldtype": "Currency", "width": 150},
            {"label": "Дебет (дан олдин)", "fieldname": "opening_debit_usd", "fieldtype": "Currency", "width": 150},
            # Period balances USD
            {"label": "Кредит (давр)", "fieldname": "period_credit_usd", "fieldtype": "Currency", "width": 150},
            {"label": "Дебет (давр)", "fieldname": "period_debit_usd", "fieldtype": "Currency", "width": 150},
            # Final balances USD
            {"label": "Сўнгги Кредит", "fieldname": "final_credit_usd", "fieldtype": "Currency", "width": 150},
            {"label": "Сўнгги Дебет", "fieldname": "final_debit_usd", "fieldtype": "Currency", "width": 150},
        ])

    return columns


def get_data(filters):
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    party_type = filters.get("party_type")
    party = filters.get("party")
    currency_filter = filters.get("currency")

    # Get list of parties (without currency filter in query)
    parties = get_parties(party_type, party)

    data = []

    # Initialize totals
    totals = {
        "opening_credit_uzs": 0,
        "opening_debit_uzs": 0,
        "opening_credit_usd": 0,
        "opening_debit_usd": 0,
        "period_credit_uzs": 0,
        "period_debit_uzs": 0,
        "period_credit_usd": 0,
        "period_debit_usd": 0,
        "final_credit_uzs": 0,
        "final_debit_uzs": 0,
        "final_credit_usd": 0,
        "final_debit_usd": 0,
    }

    for party_info in parties:
        row = calculate_party_balances(party_info, from_date, to_date)
        if row:
            # Filter by party's default currency if currency filter is set
            if currency_filter and row.get("currency") != currency_filter:
                continue

            data.append(row)

            # Add to totals
            for key in totals:
                totals[key] += row.get(key, 0)

    # Add total row at the top if there's data
    if data:
        total_row = {
            "party_type": "",
            "party": "ЖАМИ",
            "currency": "",
            "akt_sverka_link": "",
            "is_total_row": True
        }
        total_row.update(totals)
        data.insert(0, total_row)

    return data


def get_parties(party_type=None, party=None):
    """Get list of parties based on filters"""
    conditions = ["party IS NOT NULL", "party != ''", "party_type IS NOT NULL", "party_type != ''"]
    values = []

    if party:
        # Specific party
        conditions.append("party = %s")
        values.append(party)

    if party_type:
        conditions.append("party_type = %s")
        values.append(party_type)

    where_clause = "WHERE " + " AND ".join(conditions)

    query = f"""
        SELECT DISTINCT party_type, party
        FROM `tabGL Entry`
        {where_clause}
        ORDER BY party_type, party
    """

    result = frappe.db.sql(query, tuple(values), as_dict=True)
    return result


def calculate_party_balances(party_info, from_date, to_date):
    """Calculate all balances for a party"""
    party_type = party_info.get("party_type")
    party = party_info.get("party")

    # Get party currency from Party Financial Defaults
    currency = get_party_currency(party_type, party)

    # Calculate opening balances (before from_date)
    opening_uzs = calculate_opening_balance(party_type, party, from_date, "UZS")
    opening_usd = calculate_opening_balance(party_type, party, from_date, "USD")

    # Calculate period balances (from_date to to_date)
    period_uzs = calculate_period_balance(party_type, party, from_date, to_date, "UZS")
    period_usd = calculate_period_balance(party_type, party, from_date, to_date, "USD")

    # Calculate final balances
    final_uzs_net = (opening_uzs['credit'] - opening_uzs['debit']) + (period_uzs['credit'] - period_uzs['debit'])
    final_usd_net = (opening_usd['credit'] - opening_usd['debit']) + (period_usd['credit'] - period_usd['debit'])

    # Determine final credit/debit
    final_credit_uzs = final_uzs_net if final_uzs_net > 0 else 0
    final_debit_uzs = abs(final_uzs_net) if final_uzs_net < 0 else 0

    final_credit_usd = final_usd_net if final_usd_net > 0 else 0
    final_debit_usd = abs(final_usd_net) if final_usd_net < 0 else 0

    return {
        "party_type": party_type,
        "party": party,
        "currency": currency,
        "akt_sverka_link": "Акт Сверка",  # Will be formatted as link in JS
        "opening_credit_uzs": opening_uzs['credit'] if opening_uzs['credit'] > 0 else 0,
        "opening_debit_uzs": opening_uzs['debit'] if opening_uzs['debit'] > 0 else 0,
        "opening_credit_usd": opening_usd['credit'] if opening_usd['credit'] > 0 else 0,
        "opening_debit_usd": opening_usd['debit'] if opening_usd['debit'] > 0 else 0,
        "period_credit_uzs": period_uzs['credit'],
        "period_debit_uzs": period_uzs['debit'],
        "period_credit_usd": period_usd['credit'],
        "period_debit_usd": period_usd['debit'],
        "final_credit_uzs": final_credit_uzs,
        "final_debit_uzs": final_debit_uzs,
        "final_credit_usd": final_credit_usd,
        "final_debit_usd": final_debit_usd,
    }


def get_party_currency(party_type, party):
    """Get party currency with safe fallback when custom doctype is unavailable."""
    currency = None

    if frappe.db.exists("DocType", "Party Financial Defaults"):
        currency = frappe.db.get_value(
            "Party Financial Defaults",
            {"party_type": party_type, "party": party},
            "currency"
        )

    if not currency:
        currency = frappe.db.get_value(
            "GL Entry",
            {"party_type": party_type, "party": party, "is_cancelled": 0},
            "account_currency",
            order_by="posting_date desc, creation desc"
        )

    return currency or "UZS"


def calculate_opening_balance(party_type, party, from_date, currency):
    """
    Calculate opening balance before from_date for a specific currency

    Credit calculation:
    + Journal Entry (Opening Entry) Credit
    + Purchase Invoice
    + Payment Entry Receive
    + Journal Entry (Journal Entry) Credit
    + Salary Slip (only for UZS)

    Debit calculation:
    + Journal Entry (Opening Entry) Debit
    + Sales Invoice
    + Payment Entry Pay
    + Journal Entry (Journal Entry) Debit
    """

    # Journal Entry Opening Entry Credit
    je_opening_credit = frappe.db.sql("""
        SELECT IFNULL(SUM(credit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE posting_date < %s
          AND party_type = %s
          AND party = %s
          AND voucher_type = 'Journal Entry'
          AND account_currency = %s
          AND is_cancelled = 0
          AND voucher_no IN (
              SELECT name FROM `tabJournal Entry`
              WHERE voucher_type = 'Opening Entry'
          )
    """, (from_date, party_type, party, currency))[0][0] or 0

    # Journal Entry Journal Entry Credit
    je_journal_credit = frappe.db.sql("""
        SELECT IFNULL(SUM(credit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE posting_date < %s
          AND party_type = %s
          AND party = %s
          AND voucher_type = 'Journal Entry'
          AND account_currency = %s
          AND is_cancelled = 0
          AND voucher_no IN (
              SELECT name FROM `tabJournal Entry`
              WHERE voucher_type = 'Journal Entry'
          )
    """, (from_date, party_type, party, currency))[0][0] or 0

    # Purchase Invoice
    pi_credit = frappe.db.sql("""
        SELECT IFNULL(SUM(credit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE posting_date < %s
          AND party_type = %s
          AND party = %s
          AND voucher_type = 'Purchase Invoice'
          AND account_currency = %s
          AND is_cancelled = 0
    """, (from_date, party_type, party, currency))[0][0] or 0

    # Payment Entry Receive
    pe_receive_credit = frappe.db.sql("""
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
    """, (from_date, party_type, party, currency))[0][0] or 0

    # Salary Slip (only for UZS)
    salary_credit = 0
    if currency == "UZS":
        salary_credit = frappe.db.sql("""
            SELECT IFNULL(SUM(credit_in_account_currency), 0)
            FROM `tabGL Entry`
            WHERE posting_date < %s
              AND party_type = %s
              AND party = %s
              AND voucher_type = 'Salary Slip'
              AND account_currency = 'UZS'
              AND is_cancelled = 0
        """, (from_date, party_type, party))[0][0] or 0

    total_credit = je_opening_credit + je_journal_credit + pi_credit + pe_receive_credit + salary_credit

    # Debit calculations
    # Journal Entry Opening Entry Debit
    je_opening_debit = frappe.db.sql("""
        SELECT IFNULL(SUM(debit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE posting_date < %s
          AND party_type = %s
          AND party = %s
          AND voucher_type = 'Journal Entry'
          AND account_currency = %s
          AND is_cancelled = 0
          AND voucher_no IN (
              SELECT name FROM `tabJournal Entry`
              WHERE voucher_type = 'Opening Entry'
          )
    """, (from_date, party_type, party, currency))[0][0] or 0

    # Journal Entry Journal Entry Debit
    je_journal_debit = frappe.db.sql("""
        SELECT IFNULL(SUM(debit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE posting_date < %s
          AND party_type = %s
          AND party = %s
          AND voucher_type = 'Journal Entry'
          AND account_currency = %s
          AND is_cancelled = 0
          AND voucher_no IN (
              SELECT name FROM `tabJournal Entry`
              WHERE voucher_type = 'Journal Entry'
          )
    """, (from_date, party_type, party, currency))[0][0] or 0

    # Sales Invoice
    si_debit = frappe.db.sql("""
        SELECT IFNULL(SUM(debit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE posting_date < %s
          AND party_type = %s
          AND party = %s
          AND voucher_type = 'Sales Invoice'
          AND account_currency = %s
          AND is_cancelled = 0
    """, (from_date, party_type, party, currency))[0][0] or 0

    # Payment Entry Pay
    pe_pay_debit = frappe.db.sql("""
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
    """, (from_date, party_type, party, currency))[0][0] or 0

    total_debit = je_opening_debit + je_journal_debit + si_debit + pe_pay_debit

    # Calculate net and determine credit/debit
    net = total_credit - total_debit

    if net > 0:
        return {"credit": net, "debit": 0}
    else:
        return {"credit": 0, "debit": abs(net)}


def calculate_period_balance(party_type, party, from_date, to_date, currency):
    """
    Calculate period balance from from_date to to_date for a specific currency

    Credit calculation:
    + Opening Entry Credit
    + Journal Entry Credit
    + Purchase Invoice
    + Payment Entry Receive
    + Salary Slip (only for UZS)

    Debit calculation:
    + Opening Entry Debit
    + Journal Entry Debit
    + Payment Entry Pay
    + Sales Invoice
    """

    # Opening Entry Credit
    opening_credit = frappe.db.sql("""
        SELECT IFNULL(SUM(credit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE posting_date >= %s
          AND posting_date <= %s
          AND party_type = %s
          AND party = %s
          AND voucher_type = 'Journal Entry'
          AND account_currency = %s
          AND is_cancelled = 0
          AND voucher_no IN (
              SELECT name FROM `tabJournal Entry`
              WHERE voucher_type = 'Opening Entry'
          )
    """, (from_date, to_date, party_type, party, currency))[0][0] or 0

    # Journal Entry Credit
    je_credit = frappe.db.sql("""
        SELECT IFNULL(SUM(credit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE posting_date >= %s
          AND posting_date <= %s
          AND party_type = %s
          AND party = %s
          AND voucher_type = 'Journal Entry'
          AND account_currency = %s
          AND is_cancelled = 0
          AND voucher_no IN (
              SELECT name FROM `tabJournal Entry`
              WHERE voucher_type = 'Journal Entry'
          )
    """, (from_date, to_date, party_type, party, currency))[0][0] or 0

    # Purchase Invoice Credit
    pi_credit = frappe.db.sql("""
        SELECT IFNULL(SUM(credit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE posting_date >= %s
          AND posting_date <= %s
          AND party_type = %s
          AND party = %s
          AND voucher_type = 'Purchase Invoice'
          AND account_currency = %s
          AND is_cancelled = 0
    """, (from_date, to_date, party_type, party, currency))[0][0] or 0

    # Payment Entry Receive Credit
    pe_receive_credit = frappe.db.sql("""
        SELECT IFNULL(SUM(ge.credit_in_account_currency), 0)
        FROM `tabGL Entry` ge
        INNER JOIN `tabPayment Entry` pe ON ge.voucher_no = pe.name
        WHERE ge.posting_date >= %s
          AND ge.posting_date <= %s
          AND ge.party_type = %s
          AND ge.party = %s
          AND ge.voucher_type = 'Payment Entry'
          AND pe.payment_type = 'Receive'
          AND ge.account_currency = %s
          AND ge.is_cancelled = 0
    """, (from_date, to_date, party_type, party, currency))[0][0] or 0

    # Salary Slip (only for UZS)
    salary_credit = 0
    if currency == "UZS":
        salary_credit = frappe.db.sql("""
            SELECT IFNULL(SUM(credit_in_account_currency), 0)
            FROM `tabGL Entry`
            WHERE posting_date >= %s
              AND posting_date <= %s
              AND party_type = %s
              AND party = %s
              AND voucher_type = 'Salary Slip'
              AND account_currency = 'UZS'
              AND is_cancelled = 0
        """, (from_date, to_date, party_type, party))[0][0] or 0

    total_credit = opening_credit + je_credit + pi_credit + pe_receive_credit + salary_credit

    # Debit calculations
    # Opening Entry Debit
    opening_debit = frappe.db.sql("""
        SELECT IFNULL(SUM(debit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE posting_date >= %s
          AND posting_date <= %s
          AND party_type = %s
          AND party = %s
          AND voucher_type = 'Journal Entry'
          AND account_currency = %s
          AND is_cancelled = 0
          AND voucher_no IN (
              SELECT name FROM `tabJournal Entry`
              WHERE voucher_type = 'Opening Entry'
          )
    """, (from_date, to_date, party_type, party, currency))[0][0] or 0

    # Journal Entry Debit
    je_debit = frappe.db.sql("""
        SELECT IFNULL(SUM(debit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE posting_date >= %s
          AND posting_date <= %s
          AND party_type = %s
          AND party = %s
          AND voucher_type = 'Journal Entry'
          AND account_currency = %s
          AND is_cancelled = 0
          AND voucher_no IN (
              SELECT name FROM `tabJournal Entry`
              WHERE voucher_type = 'Journal Entry'
          )
    """, (from_date, to_date, party_type, party, currency))[0][0] or 0

    # Payment Entry Pay Debit
    pe_pay_debit = frappe.db.sql("""
        SELECT IFNULL(SUM(ge.debit_in_account_currency), 0)
        FROM `tabGL Entry` ge
        INNER JOIN `tabPayment Entry` pe ON ge.voucher_no = pe.name
        WHERE ge.posting_date >= %s
          AND ge.posting_date <= %s
          AND ge.party_type = %s
          AND ge.party = %s
          AND ge.voucher_type = 'Payment Entry'
          AND pe.payment_type = 'Pay'
          AND ge.account_currency = %s
          AND ge.is_cancelled = 0
    """, (from_date, to_date, party_type, party, currency))[0][0] or 0

    # Sales Invoice Debit
    si_debit = frappe.db.sql("""
        SELECT IFNULL(SUM(debit_in_account_currency), 0)
        FROM `tabGL Entry`
        WHERE posting_date >= %s
          AND posting_date <= %s
          AND party_type = %s
          AND party = %s
          AND voucher_type = 'Sales Invoice'
          AND account_currency = %s
          AND is_cancelled = 0
    """, (from_date, to_date, party_type, party, currency))[0][0] or 0

    total_debit = opening_debit + je_debit + pe_pay_debit + si_debit

    return {"credit": total_credit, "debit": total_debit}
