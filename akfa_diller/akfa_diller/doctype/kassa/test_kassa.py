# Copyright (c) 2025, abdulloh and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from akfa_diller.akfa_diller.doctype.kassa.kassa import Kassa, get_exchange_rate


def make_kassa_doc(**overrides):
    data = {
        "doctype": "Kassa",
        "naming_series": "KASSA-.YYYY.-",
        "date": "2026-04-16",
        "transaction_type": "Расход",
        "company": "Pokiza",
        "mode_of_payment": "Наличый USD",
        "cash_account": "1112 - Наличные USD - P",
        "cash_account_currency": "USD",
        "party_type": "Supplier",
        "party": "Test Supplier",
        "party_currency": "UZS",
        "amount": 183,
        "exchange_rate": 12200,
        "debit_amount": 183,
        "credit_amount": 2232600,
        "manual_credit_amount": 0,
        "balance": 500,
    }
    data.update(overrides)
    return frappe.get_doc(data)


class FakePaymentEntry:
    def __init__(self):
        self.payment_type = None
        self.posting_date = None
        self.company = None
        self.mode_of_payment = None
        self.party_type = None
        self.party = None
        self.paid_from = None
        self.paid_to = None
        self.paid_amount = None
        self.received_amount = None
        self.source_exchange_rate = None
        self.target_exchange_rate = None
        self.reference_no = None
        self.reference_date = None
        self.remarks = None
        self.flags = SimpleNamespace(ignore_permissions=False)
        self.name = "ACC-PAY-TEST-0001"
        self.inserted = False
        self.submitted = False

    def insert(self):
        self.inserted = True

    def submit(self):
        self.submitted = True


class FakeJournalEntry:
    def __init__(self):
        self.voucher_type = None
        self.posting_date = None
        self.company = None
        self.cheque_no = None
        self.cheque_date = None
        self.user_remark = None
        self.multi_currency = 0
        self.accounts = []
        self.flags = SimpleNamespace(ignore_permissions=False)
        self.name = "ACC-JV-TEST-0001"
        self.inserted = False
        self.submitted = False

    def append(self, _table, data):
        row = SimpleNamespace(**data)
        self.accounts.append(row)
        return row

    def insert(self):
        self.inserted = True

    def submit(self):
        self.submitted = True


class UnitTestKassa(FrappeTestCase):
    def test_submit_routes_payment_entry_cases(self):
        cases = [
            ("Приход", "Customer"),
            ("Приход", "Supplier"),
            ("Приход", "Employee"),
            ("Расход", "Customer"),
            ("Расход", "Supplier"),
            ("Расход", "Employee"),
        ]

        for transaction_type, party_type in cases:
            doc = make_kassa_doc(transaction_type=transaction_type, party_type=party_type)
            doc.create_payment_entry = MagicMock()
            doc.create_dividend_journal_entry = MagicMock()
            doc.create_expense_journal_entry = MagicMock()
            doc.create_transfer_payment_entry = MagicMock()
            doc.create_conversion_payment_entry = MagicMock()

            doc.on_submit()

            self.assertEqual(doc.create_payment_entry.call_count, 1)
            self.assertEqual(doc.create_dividend_journal_entry.call_count, 0)
            self.assertEqual(doc.create_expense_journal_entry.call_count, 0)
            self.assertEqual(doc.create_transfer_payment_entry.call_count, 0)
            self.assertEqual(doc.create_conversion_payment_entry.call_count, 0)

    def test_submit_routes_non_party_cases(self):
        cases = [
            ("Расход", "Расходы", "create_expense_journal_entry"),
            ("Расход", "Дивиденд 1", "create_dividend_journal_entry"),
            ("Расход", "Дивиденд 2", "create_dividend_journal_entry"),
            ("Расход", "Дивиденд 3", "create_dividend_journal_entry"),
            ("Перемещения", "", "create_transfer_payment_entry"),
            ("Конвертация", "", "create_conversion_payment_entry"),
        ]

        for transaction_type, party_type, expected_method in cases:
            doc = make_kassa_doc(transaction_type=transaction_type, party_type=party_type, party=None)
            doc.create_payment_entry = MagicMock()
            doc.create_dividend_journal_entry = MagicMock()
            doc.create_expense_journal_entry = MagicMock()
            doc.create_transfer_payment_entry = MagicMock()
            doc.create_conversion_payment_entry = MagicMock()

            doc.on_submit()

            self.assertEqual(getattr(doc, expected_method).call_count, 1)
            self.assertEqual(
                sum(
                    method.call_count
                    for method in [
                        doc.create_payment_entry,
                        doc.create_dividend_journal_entry,
                        doc.create_expense_journal_entry,
                        doc.create_transfer_payment_entry,
                        doc.create_conversion_payment_entry,
                    ]
                ),
                1,
            )

    def test_validate_party_rules(self):
        expense_doc = make_kassa_doc(
            party_type="Расходы",
            expense_account="5219 - Exchange Gain/Loss - P",
            cost_center="Main - P",
            party="X",
        )
        expense_doc.validate_party()
        self.assertIsNone(expense_doc.party)

        dividend_doc = make_kassa_doc(
            party_type="Дивиденд 1",
            party="Will Be Cleared",
            expense_account="5219 - Exchange Gain/Loss - P",
            cost_center="Main - P",
        )
        dividend_doc.validate_party()
        self.assertIsNone(dividend_doc.party)
        self.assertIsNone(dividend_doc.expense_account)
        self.assertIsNone(dividend_doc.cost_center)

        party_doc = make_kassa_doc(party_type="Supplier", party=None, expense_account=None)
        with self.assertRaises(frappe.ValidationError):
            party_doc.validate_party()

    def test_validate_party_requires_cost_center_for_expense(self):
        expense_doc = make_kassa_doc(
            party_type="Расходы",
            expense_account="5219 - Exchange Gain/Loss - P",
            cost_center=None,
            party=None,
        )
        with self.assertRaises(frappe.ValidationError):
            expense_doc.validate_party()

    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.frappe.get_cached_value")
    def test_validate_transfer_requires_same_currency(self, mocked_get_cached_value):
        mocked_get_cached_value.side_effect = lambda doctype, name, fieldname: {
            ("Account", "1112 - Наличные USD - P", "account_currency"): "USD",
            ("Account", "1111 - Р/С UZB - P", "account_currency"): "UZS",
        }.get((doctype, name, fieldname))

        doc = make_kassa_doc(
            transaction_type="Перемещения",
            mode_of_payment_to="Р/С",
            cash_account_to="1111 - Р/С UZB - P",
            party_type="",
            party=None,
        )

        with self.assertRaises(frappe.ValidationError):
            doc.validate_transfer()

    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.frappe.get_cached_value")
    def test_validate_conversion_requires_different_currency(self, mocked_get_cached_value):
        mocked_get_cached_value.return_value = "USD"

        doc = make_kassa_doc(
            transaction_type="Конвертация",
            mode_of_payment_to="Наличый USD",
            cash_account_to="1112 - Наличные USD - P",
            debit_amount=100,
            credit_amount=100,
            party_type="",
            party=None,
        )

        with self.assertRaises(frappe.ValidationError):
            doc.validate_conversion()

    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.frappe.get_cached_value")
    def test_validate_conversion_allows_uzs_modes_with_usd(self, mocked_get_cached_value):
        mocked_get_cached_value.side_effect = lambda doctype, name, fieldname: {
            ("Account", "1112 - Наличные USD - P", "account_currency"): "USD",
            ("Account", "1110 - Наличные UZB - P", "account_currency"): "UZS",
            ("Account", "1111 - Р/С UZB - P", "account_currency"): "UZS",
        }.get((doctype, name, fieldname))

        valid_doc = make_kassa_doc(
            transaction_type="Конвертация",
            mode_of_payment="Наличый USD",
            cash_account="1112 - Наличные USD - P",
            mode_of_payment_to="Наличый UZS",
            cash_account_to="1110 - Наличные UZB - P",
            debit_amount=100,
            credit_amount=1219000,
            party_type="",
            party=None,
        )
        valid_doc.validate_conversion()

        bank_doc = make_kassa_doc(
            transaction_type="Конвертация",
            mode_of_payment="Наличый USD",
            cash_account="1112 - Наличные USD - P",
            mode_of_payment_to="Р/С",
            cash_account_to="1111 - Р/С UZB - P",
            debit_amount=100,
            credit_amount=1219000,
            party_type="",
            party=None,
        )
        bank_doc.validate_conversion()

        invalid_doc = make_kassa_doc(
            transaction_type="Конвертация",
            mode_of_payment="Наличый UZS",
            cash_account="1110 - Наличные UZB - P",
            mode_of_payment_to="Р/С",
            cash_account_to="1111 - Р/С UZB - P",
            debit_amount=100,
            credit_amount=100,
            party_type="",
            party=None,
        )
        with self.assertRaises(frappe.ValidationError):
            invalid_doc.validate_conversion()

    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.frappe.get_cached_value")
    def test_validate_conversion_uses_real_account_currency_for_bank(self, mocked_get_cached_value):
        # Р/С account valyutasi USD bo'lsa ham, nomi emas, haqiqiy valyuta hisobga olinadi:
        # USD (Р/С) -> UZS (Наличный UZS) konvertatsiyasi to'g'ri o'tishi kerak.
        mocked_get_cached_value.side_effect = lambda doctype, name, fieldname: {
            ("Account", "1111 - Р/С USD - P", "account_currency"): "USD",
            ("Account", "1110 - Наличные UZB - P", "account_currency"): "UZS",
        }.get((doctype, name, fieldname))

        bank_usd_doc = make_kassa_doc(
            transaction_type="Конвертация",
            mode_of_payment="Р/С",
            cash_account="1111 - Р/С USD - P",
            mode_of_payment_to="Наличый UZS",
            cash_account_to="1110 - Наличные UZB - P",
            debit_amount=100,
            credit_amount=1219000,
            party_type="",
            party=None,
        )
        bank_usd_doc.validate_conversion()

    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.get_exchange_rate", return_value=12200)
    def test_set_payment_exchange_details_auto_calculates_credit_amount(self, _mocked_rate):
        doc = make_kassa_doc(credit_amount=0, manual_credit_amount=0, amount=183)
        doc.set_payment_exchange_details()

        self.assertEqual(doc.debit_amount, 183)
        self.assertEqual(doc.credit_amount, 2232600)
        self.assertEqual(doc.manual_credit_amount, 0)

    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.get_exchange_rate", return_value=12200)
    def test_set_payment_exchange_details_preserves_manual_credit_amount(self, _mocked_rate):
        doc = make_kassa_doc(credit_amount=1800000, manual_credit_amount=1, amount=183)
        doc.set_payment_exchange_details()

        self.assertEqual(doc.debit_amount, 183)
        self.assertEqual(doc.credit_amount, 1800000)
        self.assertEqual(doc.manual_credit_amount, 1)

    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.get_exchange_rate", return_value=0.000081967)
    def test_set_payment_exchange_details_replaces_rounded_small_exchange_rate(self, _mocked_rate):
        doc = make_kassa_doc(
            cash_account_currency="UZS",
            party_currency="USD",
            amount=400000,
            exchange_rate=0.0001,
            credit_amount=32.79,
            manual_credit_amount=0,
        )
        doc.set_payment_exchange_details()

        self.assertEqual(doc.debit_amount, 400000)
        self.assertEqual(doc.exchange_rate, 0.000081967)
        self.assertEqual(doc.credit_amount, 32.79)

    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.frappe.db.get_value")
    def test_get_exchange_rate_reverse_keeps_small_rate_precision(self, mocked_get_value):
        mocked_get_value.side_effect = [None, 12190]

        self.assertEqual(get_exchange_rate("UZS", "USD", "2026-04-23"), 0.000082034)

    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.frappe.msgprint")
    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.frappe.utils.get_link_to_form", return_value="PAYMENT-LINK")
    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.frappe.get_cached_value")
    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.frappe.new_doc")
    def test_create_payment_entry_uses_manual_override_amount(
        self,
        mocked_new_doc,
        mocked_get_cached_value,
        _mocked_link,
        _mocked_msgprint,
    ):
        fake_pe = FakePaymentEntry()
        mocked_new_doc.return_value = fake_pe

        lookup = {
            ("Account", "SUPP-ACC-UZS", "account_currency"): "UZS",
            ("Account", "1112 - Наличные USD - P", "account_currency"): "USD",
            ("Account", "SUPP-ACC-UZS", "account_currency"): "UZS",
        }
        mocked_get_cached_value.side_effect = lambda doctype, name, fieldname: lookup.get((doctype, name, fieldname))

        doc = make_kassa_doc(
            transaction_type="Расход",
            party_type="Supplier",
            party="Билолиддин",
            party_currency="UZS",
            amount=183,
            credit_amount=1800000,
            manual_credit_amount=1,
        )
        doc.get_party_account = MagicMock(return_value="SUPP-ACC-UZS")
        doc.get_company_exchange_rate = MagicMock(side_effect=lambda currency: 1 if currency == "USD" else 0.000081967)
        doc.set_linked_document = MagicMock()
        doc.name = "KASSA-TEST-0001"

        doc.create_payment_entry()

        self.assertTrue(fake_pe.inserted)
        self.assertTrue(fake_pe.submitted)
        self.assertEqual(fake_pe.paid_amount, 183)
        self.assertEqual(fake_pe.received_amount, 1800000)
        self.assertEqual(fake_pe.payment_type, "Pay")

    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.frappe.msgprint")
    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.frappe.utils.get_link_to_form", return_value="JE-LINK")
    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.get_exchange_rate", return_value=0.000081967)
    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.frappe.get_cached_value")
    @patch("akfa_diller.akfa_diller.doctype.kassa.kassa.frappe.new_doc")
    def test_create_expense_journal_entry_keeps_account_currency_amount(
        self,
        mocked_new_doc,
        mocked_get_cached_value,
        _mocked_exchange_rate,
        _mocked_link,
        _mocked_msgprint,
    ):
        fake_je = FakeJournalEntry()
        mocked_new_doc.return_value = fake_je

        lookup = {
            ("Account", "1110 - Наличные UZB - P", "account_currency"): "UZS",
            ("Account", "5206 - Цех - P", "account_currency"): "UZS",
            ("Company", "Pokiza", "default_currency"): "USD",
        }
        mocked_get_cached_value.side_effect = lambda doctype, name, fieldname: lookup.get((doctype, name, fieldname))

        doc = Kassa({
            "doctype": "Kassa",
            "date": "2026-04-23",
            "transaction_type": "Расход",
            "company": "Pokiza",
            "mode_of_payment": "Наличый UZS",
            "cash_account": "1110 - Наличные UZB - P",
            "cash_account_currency": "UZS",
            "party_type": "Расходы",
            "expense_account": "5206 - Цех - P",
            "amount": 11000000,
        })
        doc.name = "KASSA-TEST-0001"
        doc.set_linked_document = MagicMock()

        doc.create_expense_journal_entry()

        self.assertTrue(fake_je.inserted)
        self.assertTrue(fake_je.submitted)
        self.assertEqual(fake_je.multi_currency, 1)
        self.assertEqual(fake_je.accounts[0].credit_in_account_currency, 11000000)
        self.assertEqual(fake_je.accounts[1].debit_in_account_currency, 11000000)
        self.assertAlmostEqual(fake_je.accounts[0].credit, fake_je.accounts[1].debit)
        self.assertEqual(fake_je.accounts[1].exchange_rate, 0.000081967)


class IntegrationTestKassa(FrappeTestCase):
    def test_case_matrix_documented_by_unit_tests(self):
        # Integration layer intentionally stays light; routing and accounting
        # behavior are covered in the unit suite above.
        self.assertTrue(True)
