"""Kassa (payments) sinxroni — 0=0 tamoyili (foydalanuvchi talabi 2026-08-16).

Pul tovar emas: har (filial, kassa, kun) bo'yicha ERPNext'dagi summa API bilan
AYNAN teng bo'lishi shart. Shu sabab arxitektura kun-bo'lak (bucket) asosida:

  1. Har bucket uchun API netto summasi (DELETED'siz) hisoblanadi.
  2. ERPNext'dagi shu bucket'ga tegilgan Payment Entry'lar summasi bilan
     solishtiriladi. Teng bo'lsa -- tegilmaydi.
  3. Farq bo'lsa -- bucket'ning BARCHA PE'lari bekor qilinib, joriy API
     qatorlaridan qaytadan quriladi. Yangi qator, tahrir (UPDATED), o'chirish
     (DELETED) -- hammasi shu yagona mexanizm bilan 0=0 ga keladi.

Hujjatlar: RECEIPT musbat -> Payment Entry (Receive, mijozdan kassaga);
RECEIPT manfiy -> Payment Entry (Pay, kassadan mijozga -- qaytarim).
PAYMENT/CONTRA turlari va XARITALANMAGAN kassalar sinxron qilinmaydi --
bir marta log yoziladi (ular ikkala tomonda ham hisobdan tashqarida,
0=0 solishtiruvi buzilmaydi).

Kassa doctype ISHLATILMAYDI (foydalanuvchi qarori) -- faqat Payment Entry.
"""
import hashlib
import json
import time
from datetime import datetime, timedelta

import frappe
import requests
from frappe.utils import getdate, today

from akfa_diller.akfa_diller.api.report_service_sync import _get_or_create_customer
from akfa_diller.akfa_diller.services import report_service_client

PAYMENTS_WINDOW_DAYS = 3     # soatlik yurishda qamrov
PAYMENTS_DEEP_DAYS = 30      # kunlik chuqur tekshiruv qamrovi
PAGE = 100000

CUSTOM_FIELDS = [
    ("custom_rs_dealer", "Data", "RS Dealer"),
    ("custom_rs_kassa", "Data", "RS Kassa"),
    ("custom_rs_date", "Date", "RS Sana"),
    ("custom_rs_row_hash", "Data", "RS Row Hash"),
    ("custom_rs_currency", "Data", "RS Valyuta"),
]


def _ensure_custom_fields():
    for fieldname, fieldtype, label in CUSTOM_FIELDS:
        if frappe.db.exists("Custom Field", {"dt": "Payment Entry", "fieldname": fieldname}):
            continue
        cf = frappe.new_doc("Custom Field")
        cf.dt = "Payment Entry"
        cf.fieldname = fieldname
        cf.fieldtype = fieldtype
        cf.label = label
        cf.read_only = 1
        cf.no_copy = 1
        cf.insert_after = "remarks"
        cf.flags.ignore_permissions = True
        cf.insert()
    frappe.db.commit()


def _fetch_payments(base_url, token, dealer_id, from_date, to_date):
    rows = []
    start = 0
    while True:
        body = {
            "filter": {"fromDate": str(from_date), "toDate": str(to_date), "dealerId": int(dealer_id),
                       "paymentType": None, "fromClient": "", "toClient": "", "status": None},
            "length": PAGE,
            "order": [{"field": None, "sort": None}],
            "start": start,
        }
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        for attempt in range(8):
            r = requests.post(f"{base_url}/api/reports/payments-currency", json=body, headers=headers, timeout=90)
            if r.status_code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        payload = r.json()
        page_rows = payload.get("data") or []
        rows.extend(page_rows)
        expected = payload.get("recordsTotal")
        if len(page_rows) < PAGE:
            break
        start += PAGE
    # API band paytda 200 bilan CHALA javob qaytarishi kuzatildi (Bulungur
    # 226k -> 26k voqeasi) -- chala ma'lumot bilan bucket qayta qurilsa yaxshi
    # yozuvlar o'chib ketadi. recordsTotal bilan tekshiramiz: mos kelmasa xato.
    if expected is not None and len(rows) != expected:
        raise Exception(f"payments fetch chala: {len(rows)} != recordsTotal {expected}")
    return rows


def _kassa_map():
    rows = frappe.get_all(
        "Report Service Kassa Account",
        filters={"enabled": 1},
        fields=["dealer_id", "kassa_label", "account", "currency", "mode_of_payment"],
    )
    out = {}
    for r in rows:
        key = (str(r.dealer_id), (r.kassa_label or "").strip(), (r.currency or "").strip() or None)
        out[key] = r.account
        out[("__mop__",) + key] = r.mode_of_payment
    return out


def _resolve_mop(kassa_map, did, label, currency):
    row_cur = _norm_currency(currency)
    for key_cur in (currency, row_cur, None):
        mop = kassa_map.get(("__mop__", did, label, key_cur))
        if mop:
            return mop
    return None


# SHB -- biznes-kelishuv bo'yicha dollar (USD schotlarga yoziladi)
_CURRENCY_EQUIV = {"SHB": "USD"}


def _norm_currency(currency):
    return _CURRENCY_EQUIV.get(currency or "", currency)


def _resolve_account(kassa_map, did, label, currency):
    """Qidiruv tartibi: aynan qator valyutasi -> normallashgan (SHB->USD) ->
    umumiy. Umumiy xarita faqat schot valyutasi qator valyutasiga (normallashgan)
    mos kelsagina ishlatiladi -- aks holda None: 12 mln so'm "12 mln dollar"
    bo'lib yozilib ketmasin, yozilmay log'da ko'ringani ming marta yaxshi."""
    row_cur = _norm_currency(currency)
    for key_cur in (currency, row_cur):
        acc = kassa_map.get((did, label, key_cur))
        if acc:
            return acc
    acc = kassa_map.get((did, label, None))
    if not acc:
        return None
    acc_cur = frappe.get_cached_value("Account", acc, "account_currency")
    if row_cur and acc_cur and row_cur != acc_cur:
        return None
    return acc


def _log_once(title, message):
    if not frappe.db.exists("Error Log", {"method": title}):
        frappe.log_error(title=title, message=message)


def _row_hash(r):
    key = "|".join(str(r.get(k) or "") for k in ("date", "amount", "fromClient", "toClient", "note", "created", "creator"))
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _parse_pay_date(s):
    return datetime.strptime(s, "%d.%m.%Y").strftime("%Y-%m-%d")


def sync_payments(days=None, dealer_ids=None):
    """Soatlik: oxirgi PAYMENTS_WINDOW_DAYS kun. Qo'lda: days/dealer_ids bilan."""
    settings = frappe.get_single("Report Service Settings")
    if not settings.sync_enabled:
        return
    _ensure_custom_fields()
    kassa_map = _kassa_map()
    if not kassa_map:
        _log_once("Payments sync: xarita bo'sh",
                  "Report Service Kassa Account'da birorta yozuv yo'q -- sinxron ishlamaydi.")
        return

    base_url, token = report_service_client.get_token()
    to_date = today()
    from_date = str(getdate(today()) - timedelta(days=int(days) if days else PAYMENTS_WINDOW_DAYS))
    wanted = {str(d) for d in dealer_ids} if dealer_ids else None

    summary = []
    for branch in settings.dealer_branches:
        did = str(branch.dealer_id)
        if wanted is not None and did not in wanted:
            continue
        try:
            rows = _fetch_payments(base_url, token, branch.dealer_id, from_date, to_date)
        except Exception as e:
            frappe.log_error(title=f"Payments sync: fetch xatosi ({branch.label})", message=str(e)[:800])
            continue

        # bucket: (sana, kassa_label) -> qatorlar (DELETED chiqarib tashlangan)
        buckets = {}
        for r in rows:
            if (r.get("status") or "").upper() == "DELETED":
                continue
            ptype = (r.get("paymentType") or "").upper()
            # RECEIPT: kassa registri toClient'da (pul kassaga KIRADI);
            # PAYMENT: registri fromClient'da (pul kassadan CHIQIB toClient'ga --
            # odatda ta'minotchi BAZAga -- ketadi). CONTRA hali kuzatilmagan.
            if ptype == "RECEIPT":
                kassa = (r.get("toClient") or "").strip()
            elif ptype == "PAYMENT":
                kassa = (r.get("fromClient") or "").strip()
            else:
                _log_once(
                    f"Payments sync: {ptype or '?'} turi o'tkazildi, {branch.label}",
                    f"paymentType={ptype} -- bu tur hali sinxron qilinmaydi (qo'lda ko'rib chiqiladi).",
                )
                continue
            currency = (r.get("currency") or "").strip() or None
            if not _resolve_account(kassa_map, did, kassa, currency):
                _log_once(
                    f"Payments sync: xaritalanmagan kassa {kassa!r} ({branch.label})",
                    f"dealer={did}, kassa={kassa!r}, valyuta={currency} -- Report Service Kassa Account'ga qo'shilmaguncha sinxron qilinmaydi.",
                )
                continue
            try:
                pdate = _parse_pay_date(r.get("date"))
            except Exception:
                continue
            # bucket VALYUTA kesimida: UZS va USD summalari hech qachon bitta
            # taqqoslovga qo'shilmaydi (128 mlrd'lik saboq, 2026-08-16)
            buckets.setdefault((pdate, kassa, _norm_currency(currency)), []).append(r)

        # bucket ichida RECEIPT va PAYMENT aralash bo'lishi mumkin (bir registr)

        rebuilt = skipped = 0
        for (pdate, kassa, bcur), brows in buckets.items():
            api_sum = round(sum(
                (r.get("amount") or 0) * (-1 if (r.get("paymentType") or "").upper() == "PAYMENT" else 1)
                for r in brows
            ), 2)

            erp = frappe.db.sql(
                """
                select coalesce(sum(case when payment_type = 'Receive' then received_amount else -paid_amount end), 0)
                from `tabPayment Entry`
                where docstatus = 1 and custom_rs_dealer = %s and custom_rs_kassa = %s
                  and custom_rs_date = %s and coalesce(custom_rs_currency, '') = %s
                """,
                (did, kassa, pdate, bcur or ""),
            )[0][0]
            erp_sum = round(float(erp or 0), 2)

            if abs(api_sum - erp_sum) < 0.01:
                skipped += 1
                continue

            _rebuild_bucket(branch, did, kassa, pdate, bcur, kassa_map, brows, settings)
            rebuilt += 1

        # YETIM bucketlar: ERPNextda PE bor-u, joriy API javobida o'sha
        # (kassa, kun, valyuta) uchun BIRORTA qator yo'q (hammasi o'chirilgan
        # yoki avvalgi chala-fetchdan qolgan) -- bekor qilinadi, 0=0 tiklanadi.
        api_keys = {(kassa, pdate, bcur or "") for (pdate, kassa, bcur) in buckets.keys()}
        orphans = frappe.db.sql(
            """
            select distinct custom_rs_kassa, custom_rs_date, coalesce(custom_rs_currency, '')
            from `tabPayment Entry`
            where docstatus = 1 and custom_rs_dealer = %s
              and custom_rs_date between %s and %s
            """,
            (did, from_date, to_date),
        )
        removed = 0
        for okassa, odate, ocur in orphans:
            if (okassa, str(odate), ocur) in api_keys:
                continue
            for name in frappe.get_all(
                "Payment Entry",
                filters={"docstatus": 1, "custom_rs_dealer": did, "custom_rs_kassa": okassa,
                         "custom_rs_date": odate, "custom_rs_currency": ocur},
                pluck="name",
            ):
                try:
                    doc = frappe.get_doc("Payment Entry", name)
                    doc.flags.ignore_permissions = True
                    doc.cancel()
                    removed += 1
                except Exception as e:
                    frappe.log_error(title=f"Payments sync: yetim PE bekor bo'lmadi {name}", message=str(e)[:600])
            frappe.db.commit()

        if rebuilt or removed:
            summary.append(f"{branch.label}: {rebuilt} bucket qayta qurildi, {skipped} teng, yetim={removed}")
        time.sleep(2)

    if summary:
        frappe.logger("payments_sync").info("; ".join(summary))


def _rebuild_bucket(branch, did, kassa, pdate, bcur, kassa_map, brows, settings):
    """Bucket'ni 0=0 ga keltirish: eski PE'lar bekor, joriy qatorlardan qayta qurish."""
    old = frappe.get_all(
        "Payment Entry",
        filters={"docstatus": 1, "custom_rs_dealer": did, "custom_rs_kassa": kassa,
                 "custom_rs_date": pdate, "custom_rs_currency": bcur or ""},
        pluck="name",
    )
    for name in old:
        try:
            doc = frappe.get_doc("Payment Entry", name)
            doc.flags.ignore_permissions = True
            doc.cancel()
        except Exception as e:
            frappe.log_error(
                title=f"Payments sync: PE bekor bo'lmadi {name}",
                message=str(e)[:800],
            )
            return  # bucket'ni chala holatda qoldirmaymiz -- keyingi yurishda qayta uriniladi
    frappe.db.commit()

    from akfa_diller.akfa_diller.api.report_service_sync import _get_or_create_supplier

    for r in brows:
        amount = round(r.get("amount") or 0, 2)
        if abs(amount) < 0.005:
            continue
        currency = (r.get("currency") or "").strip() or None
        account = _resolve_account(kassa_map, did, kassa, currency)
        company = frappe.db.get_value("Account", account, "company")
        ptype = (r.get("paymentType") or "").upper()
        try:
            pe = frappe.new_doc("Payment Entry")
            pe.company = company
            pe.posting_date = pdate
            if ptype == "PAYMENT":
                # kassadan chiqim -- toClient odatda ta'minotchi (BAZA).
                # MINUS summali PAYMENT esa teskarisi: bazadan kassaga qaytim
                # (jonli misol: "narx farqi -1008$") -> Receive from Supplier.
                supplier = _get_or_create_supplier(None, r.get("toClient"), None, settings, branch)
                pe.party_type = "Supplier"
                pe.party = supplier
                if amount >= 0:
                    pe.payment_type = "Pay"
                    pe.paid_from = account
                    pe.paid_amount = amount
                    pe.received_amount = amount
                else:
                    pe.payment_type = "Receive"
                    pe.paid_to = account
                    pe.paid_amount = -amount
                    pe.received_amount = -amount
            else:
                raw_name = (r.get("fromClient") or "").strip()
                # bo'sh/'-'/juda qisqa nom -> bitta doimiy filial-kassa mijozi
                # (aks holda har safar "Report Service mijoz None - N" dublikatlar
                # tug'ilardi -- audit: 210 ta chiqindi mijoz)
                if len(raw_name.strip("-_. ")) < 2:
                    raw_name = f"{branch.label} kassa mijozi"
                customer = _get_or_create_customer(None, raw_name, None, settings, branch)
                pe.party_type = "Customer"
                pe.party = customer
                if amount >= 0:
                    pe.payment_type = "Receive"
                    pe.paid_to = account
                    pe.paid_amount = amount
                    pe.received_amount = amount
                else:
                    pe.payment_type = "Pay"
                    pe.paid_from = account
                    pe.paid_amount = -amount
                    pe.received_amount = -amount
            if branch.cost_center:
                pe.cost_center = branch.cost_center
            mop = _resolve_mop(kassa_map, did, kassa, currency)
            if mop:
                pe.mode_of_payment = mop
            pe.custom_rs_dealer = did
            pe.custom_rs_kassa = kassa
            pe.custom_rs_date = pdate
            pe.custom_rs_currency = bcur or ""
            pe.custom_rs_row_hash = _row_hash(r)
            pe.reference_no = _row_hash(r)
            pe.reference_date = pdate
            if r.get("note"):
                pe.remarks = str(r.get("note"))[:500]
            pe.flags.ignore_permissions = True
            pe.setup_party_account_field()
            pe.set_missing_values()
            pe.set_exchange_rate()
            # Ikki tomon valyutasi farq qilsa qarama-qarshi summa KURS bilan
            # hisoblanadi. LANGAR -- doim KASSA tomoni (qatorning o'z summasi):
            # Receive'da kassa = paid_to (received_amount), Pay'da kassa =
            # paid_from (paid_amount). Teskari qo'yilsa kichik summalar 0 ga
            # yaxlitlanib "Received Amount is mandatory" (K kompaniyasi UZS
            # kitobi) yoki 125 mlrd toshib ketishi (1264) chiqadi -- ikkala
            # jonli saboq 2026-08-17.
            if (pe.paid_from_account_currency or "") != (pe.paid_to_account_currency or ""):
                src_rate = pe.source_exchange_rate or 1
                tgt_rate = pe.target_exchange_rate or 1
                if pe.payment_type == "Receive":
                    pe.paid_amount = round(pe.received_amount * tgt_rate / src_rate, 2)
                else:
                    pe.received_amount = round(pe.paid_amount * src_rate / tgt_rate, 2)
            pe.insert()
            pe.submit()
        except Exception as e:
            frappe.db.rollback()
            frappe.log_error(
                title=f"Payments sync: PE yaratilmadi ({branch.label} {kassa} {pdate})",
                message=f"{e}\n\nqator: {json.dumps(r, ensure_ascii=False)[:600]}",
            )
            # bucket chala -- summa tengligi keyingi yurishda qayta tekshirilib,
            # yana qayta quriladi
            continue
    frappe.db.commit()


def deep_check_payments():
    """Kunlik: oxirgi PAYMENTS_DEEP_DAYS kunni 0=0 tekshiruvi."""
    sync_payments(days=PAYMENTS_DEEP_DAYS)


def backfill_payments(from_date, to_date, dealer_ids=None):
    """Bir martalik: berilgan oraliqni to'liq 0=0 ga keltirish.

    bench --site <sayt> execute akfa_diller.akfa_diller.api.payments_sync.backfill_payments \\
        --kwargs "{'from_date': '2026-07-01', 'to_date': '2026-08-16'}"
    """
    # +1 YO'Q: aynan from_date'dan boshlanadi (oldin +1 tufayli oynaga
    # bir kun oldingi (30-iyun) to'lovlari ham kirib qolgan edi -- audit topildi)
    days = (getdate(today()) - getdate(from_date)).days
    sync_payments(days=days, dealer_ids=dealer_ids)


# Standart xarita: (dealer, API kassa nomi) -> schot nomzodlari (PROD nomi, lokal nomi).
# Birinchi mavjudi olinadi. ❓ savollilar (Выручка, подоконник, vozvrat, Расход,
# Ishtixon "Перечислении") ATAYLAB yo'q -- foydalanuvchi javobidan keyin qo'shiladi.
_DEFAULT_KASSA_MAPPINGS = [
    ("36", "Накд", ["Samarqand Наличие usd - SD", "S1 Naqd (Накд) - SD"]),
    ("36", "Пластик Карта", ["Samarqand 1 plastik kard - SD", "S1 Plastik Karta - SD"]),
    ("36", "Click", ["Samarqand 1 click usd - SD", "S1 Click - SD"]),
    ("36", "Перечислении", ["Perechisleniya usd samarqand 1 - SD", "S1 Bank O'tkazma (Перечислении) - SD"]),
    ("38", "Naqt Pul", ["Cash Usd Kattaqo'rgon - K", "Kattaqo'rg'on Naqd (Naqt Pul) - KTQ"]),
    ("38", "Plastik Karta", ["Karta Kattaqo'rgon - K", "Kattaqo'rg'on Plastik Karta - KTQ"]),
    ("38", "perechislenie", ["Kattaqorgon Perechesleniya USD - K", "Kattaqo'rg'on Bank O'tkazma (perechislenie) - KTQ"]),
    ("38", "Bank", ["Kattaqorgon Perechesleniya USD - K", "Kattaqo'rg'on Bank O'tkazma (perechislenie) - KTQ"]),
    ("56", "Naqd", ["Bulungur NAQT USD - SD", "Bulungur Naqd - SD"]),
    ("57", "NAQT PUL", ["Jomboy NAQT USD - SD", "Jomboy Naqd - SD"]),
    ("57", "PERECHISLENI", ["Jomboy Perechesleniya USD - SD", "Jomboy Bank O'tkazma - SD"]),
    ("58", "NAQT", ["Chopon Ota NAQT USD - SD", "Cho'pon ota Naqd - SD"]),
    ("58", "пластик", ["Chopon ota Plastik USD - SD", "Cho'pon ota Plastik Karta - SD"]),
    ("58", "Пречесление", ["Chopon Ota Perechesleniya USD - SD", "Cho'pon ota Bank O'tkazma - SD"]),
    ("59", "Naqd pul", ["Cash usd Mitan - K", "Mitan Naqd - KTQ"]),
    ("63", "naqt pul", ["Urgur1 NAQT USD - SD", "Urgut 1 Naqd - SD"]),
    ("63", "bank", ["Urgur1 Perechesleniya USD - SD", "Urgut 1 Bank O'tkazma - SD"]),
    ("97", "NAXT", ["Cash Usd Ishtixon - K", "Ishtixon Naqd - KTQ"]),
    ("97", "$", ["Cash Usd Ishtixon - K", "Ishtixon Naqd - KTQ"]),
    ("97", "Plastik", ["Karta Ishtixon - K", "Ishtixon Plastik Karta - KTQ"]),
    ("160", "naxt", ["Cash usd Mirbozor - K", "Mirbozor Naqd - KTQ"]),
    ("161", "НАКТ", ["Nurobod NAQT USD - SD", "Nurobod Naqd - SD"]),
    ("234", "NAQD", ["Urgut2  NAQT USD - SD", "Urgut 2 Naqd - SD"]),
    ("234", "TERMINAL", ["Urgut2 Plastik USD - SD", "Urgut 2 Terminal (Karta) - SD"]),
    ("234", "PERECHISLENIYA", ["Urgut2 Perechesleniya USD - SD", "Urgut 2 Bank O'tkazma - SD"]),
]


def bootstrap_kassa_mappings():
    """Standart xaritani o'rnatish (idempotent; mavjud yozuvlarga tegilmaydi):

    bench --site <sayt> execute akfa_diller.akfa_diller.api.payments_sync.bootstrap_kassa_mappings
    """
    created = skipped = noacc = 0
    for dealer, label, candidates in _DEFAULT_KASSA_MAPPINGS:
        if frappe.db.exists("Report Service Kassa Account", {"dealer_id": dealer, "kassa_label": label}):
            skipped += 1
            continue
        # abbr-moslashuv: PROD'da Kattaqo'rg'on abbri "K", lokalda "KTQ" --
        # har nomzodning ikkala variantini ham sinaymiz
        expanded = []
        for a in candidates:
            expanded.append(a)
            if a.endswith(" - K"):
                expanded.append(a[:-4] + " - KTQ")
            elif a.endswith(" - KTQ"):
                expanded.append(a[:-6] + " - K")
        account = next((a for a in expanded if frappe.db.exists("Account", a)), None)
        if not account:
            print(f"  schot topilmadi: dealer={dealer} kassa={label!r} nomzodlar={candidates}")
            noacc += 1
            continue
        doc = frappe.new_doc("Report Service Kassa Account")
        doc.dealer_id = dealer
        doc.kassa_label = label
        doc.account = account
        doc.flags.ignore_permissions = True
        doc.insert()
        created += 1
        print(f"  {dealer} | {label!r} -> {account}")
    frappe.db.commit()
    print(f"Yaratildi={created}, mavjud={skipped}, schot-topilmadi={noacc}")


def verify_payments(from_date, to_date):
    """0=0 tasdiq hisoboti: har (filial, kassa, valyuta) bo'yicha API vs ERPNext.

    bench --site <sayt> execute akfa_diller.akfa_diller.api.payments_sync.verify_payments \
        --kwargs "{'from_date': '2026-07-01', 'to_date': '2026-08-17'}"
    """
    settings = frappe.get_single("Report Service Settings")
    kassa_map = _kassa_map()
    base_url, token = report_service_client.get_token()

    totals = {}  # valyuta -> [api, erp]
    bad = []
    unmapped = {}
    for branch in settings.dealer_branches:
        did = str(branch.dealer_id)
        rows = None
        for attempt in range(3):
            try:
                rows = _fetch_payments(base_url, token, branch.dealer_id, str(from_date), str(to_date))
                break
            except Exception as e:
                print(f"{branch.label}: fetch urinish {attempt+1} xato ({e})")
                time.sleep(60)
        if rows is None:
            print(f"{branch.label}: FETCH BO'LMADI -- bu filial tekshirilmadi!")
            continue

        api_by_bucket = {}
        for r in rows:
            if (r.get("status") or "").upper() == "DELETED":
                continue
            ptype = (r.get("paymentType") or "").upper()
            if ptype == "RECEIPT":
                kassa = (r.get("toClient") or "").strip()
                sign = 1
            elif ptype == "PAYMENT":
                kassa = (r.get("fromClient") or "").strip()
                sign = -1
            else:
                continue
            cur = (r.get("currency") or "").strip() or None
            if not _resolve_account(kassa_map, did, kassa, cur):
                key = (branch.label, kassa, cur)
                unmapped[key] = unmapped.get(key, 0) + (r.get("amount") or 0)
                continue
            bkey = (kassa, _norm_currency(cur))
            api_by_bucket[bkey] = api_by_bucket.get(bkey, 0) + sign * (r.get("amount") or 0)

        for (kassa, bcur), api_sum in api_by_bucket.items():
            erp = frappe.db.sql(
                """
                select coalesce(sum(case when payment_type = 'Receive' then received_amount else -paid_amount end), 0)
                from `tabPayment Entry`
                where docstatus = 1 and custom_rs_dealer = %s and custom_rs_kassa = %s
                  and coalesce(custom_rs_currency, '') = %s
                  and custom_rs_date between %s and %s
                """,
                (did, kassa, bcur or "", str(from_date), str(to_date)),
            )[0][0]
            api_sum = round(api_sum, 2)
            erp_sum = round(float(erp or 0), 2)
            diff = round(erp_sum - api_sum, 2)
            mark = "OK 0=0" if abs(diff) < 0.01 else f"FARQ {diff:+,.2f}"
            print(f"  {branch.label:<14} {kassa:<16} [{bcur}] API={api_sum:>18,.2f} ERP={erp_sum:>18,.2f}  {mark}")
            t = totals.setdefault(bcur, [0.0, 0.0])
            t[0] += api_sum
            t[1] += erp_sum
            if abs(diff) >= 0.01:
                bad.append((branch.label, kassa, bcur, diff))
        time.sleep(2)

    print()
    for cur, (a, e) in totals.items():
        print(f"JAMI [{cur}]: API={a:,.2f} ERP={e:,.2f} farq={e-a:+,.2f}")
    print(f"0=0 bo'lmagan bucketlar: {len(bad)}")
    for b in bad:
        print(f"  {b[0]} | {b[1]} [{b[2]}] | {b[3]:+,.2f}")
    print(f"Xaritalanmagan (YOZILMAGAN) qolganlar: {len(unmapped)}")
    for (label, kassa, cur), s in unmapped.items():
        print(f"  {label} | {kassa!r} [{cur}] | {s:,.2f}")
