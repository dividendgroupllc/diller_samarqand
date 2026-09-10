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
from frappe.utils import flt, getdate, today

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
            if r.status_code == 401:
                # token eskirdi (uzoq backfill'da ~15 daqiqada o'ladi) --
                # yangisini olib shu sahifani qayta so'raymiz (2026-08-28 saboq:
                # 5 filial "fetch xatosi 401" bilan tortilmay qolgan edi)
                base_url, token = report_service_client.get_token()
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
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


# ---------------------------------------------------------------- ichki kartalar
# Zavod POS'ida filiallar bir-biriga "klient" bo'lib ko'rinadi (foydalanuvchi
# jadvali 2026-09-10): asosiy ofis lentasida "Самарканд-1 (Акмал ака )(Ш) Булунгур",
# filial lentasida esa asosiy ofis "Sam baza"/"Самарканд-1" nomi bilan. Bunday
# qatorlar mijoz/ta'minotchi emas — bitta kompaniya ichidagi PUL KO'CHIRMASI.

def _ichki_kartalar():
	"""{klient nomi: qator} — Report Service Ichki Karta jadvalidan."""
	out = {}
	for r in frappe.get_all("Report Service Ichki Karta", filters={"enabled": 1},
	                        fields=["client_name", "rol", "label", "dealer_id", "company",
	                                "naqd_account", "bank_account", "karta_account"]):
		out[(r.client_name or "").strip()] = r
	return out


_KANAL_BANK = ("перечисл", "пречесл", "perechis", "perechesl", "bank", "банк")
_KANAL_KARTA = ("пластик", "plastik", "karta", "карта", "click", "клик", "terminal", "терминал")


def _kanal(kassa_label):
	"""Asosiy registr nomidan pul kanalini aniqlash: bank / karta / naqd."""
	s = (kassa_label or "").lower()
	if any(x in s for x in _KANAL_BANK):
		return "bank"
	if any(x in s for x in _KANAL_KARTA):
		return "karta"
	return "naqd"


def _qarshi_nom(r, ptype):
	"""Qator bo'yicha QARSHI tomon nomi (kassa registri emas)."""
	nom = r.get("fromClient") if ptype == "RECEIPT" else r.get("toClient")
	return (nom or "").strip()


def _qarshi_hisob(karta, kassa_label):
	"""Ichki karta + kanal -> qarshi tomon hisobi (bo'sh bo'lsa naqd hisob)."""
	k = _kanal(kassa_label)
	return karta.get(k + "_account") or karta.get("naqd_account")


def _bucket_sum_sql(did, kassa, bcur, account, sana=None, dan=None, gacha=None):
	"""ERPNext bucket summasi SCHOT nuqtai nazaridan: Receive/Pay/Internal Transfer —
	uchalasi ham to'g'ri sanaladi (paid_to = +, paid_from = -)."""
	shart = "and custom_rs_date = %(sana)s" if sana else "and custom_rs_date between %(dan)s and %(gacha)s"
	return frappe.db.sql(
		f"""
		select coalesce(sum(case when paid_to = %(acc)s then received_amount
		                         when paid_from = %(acc)s then -paid_amount else 0 end), 0)
		from `tabPayment Entry`
		where docstatus = 1 and custom_rs_dealer = %(did)s and custom_rs_kassa = %(kassa)s
		  and coalesce(custom_rs_currency, '') = %(cur)s {shart}
		""",
		{"acc": account, "did": did, "kassa": kassa, "cur": bcur or "",
		 "sana": sana, "dan": dan, "gacha": gacha},
	)[0][0]


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


def closed_until(settings=None):
    """Oy yopilgan sana: shu kundan OLDINGI davrga sinxron tegmaydi
    (foydalanuvchi talabi 2026-08-22). Bo'sh bo'lsa cheklov yo'q."""
    settings = settings or frappe.get_single("Report Service Settings")
    qiymat = getattr(settings, "closed_until", None)
    return getdate(qiymat) if qiymat else None


def _is_closed(pdate, chegara):
    """pdate yopilgan davrgami? (chegara kunining O'ZI ham yopiq hisoblanadi)"""
    if not chegara:
        return False
    return getdate(pdate) <= chegara


def _log_once(title, message):
    if not frappe.db.exists("Error Log", {"method": title}):
        frappe.log_error(title=title, message=message)


def _row_hash(r):
    key = "|".join(str(r.get(k) or "") for k in ("date", "amount", "fromClient", "toClient", "note", "created", "creator"))
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _kun_kursi(company, pdate):
    """Kompaniya kurs-jadvalidan (API feed'i avto-to'ldiradi) USD->UZS kun-kursi."""
    try:
        from akfa_diller.akfa_diller.api.exchange import get_company_rate
        return float(get_company_rate(company, "USD", "UZS", pdate) or 0)
    except Exception:
        return 0


def _parse_pay_date(s):
    return datetime.strptime(s, "%d.%m.%Y").strftime("%Y-%m-%d")


def _account_amount(r, account, fallback_rate=0):
    """Qator summasini SCHOT valyutasiga keltiradi.

    Foydalanuvchi qarori 2026-08-28: UZS schotlar YO'Q -- so'mdagi to'lov ham
    o'sha kassa-labelning USD schotiga, qatorning O'Z kursi (currencyRate)
    bilan dollarga o'girilib tushadi.

    Foydalanuvchi qarori 2026-09-02 ("bironta ham qolib ketmasin"): qator
    KURSSIZ kelsa (currencyRate=null -- jonli misol: 10.07 S1 Перечислении
    125 mlrd so'm BAZA-postavchikka), `fallback_rate` -- O'SHA KUNNING kursi
    (kurs-jadvali API feed'idan avto-to'ladi) bilan o'giriladi. Kun-kursi ham
    topilmasa None -- qator sinxron qilinmadi, log qoladi (jimgina noto'g'ri
    summa yozishdan ko'ra to'xtagan afzal)."""
    amount = round(r.get("amount") or 0, 2)
    cur = _norm_currency((r.get("currency") or "").strip() or None)
    acc_cur = frappe.get_cached_value("Account", account, "account_currency")
    try:
        rate = float(r.get("currencyRate") or 0)
    except (TypeError, ValueError):
        rate = 0
    # MANBA XATOSI qoidasi (foydalanuvchi 2026-08-28): "USD" deb kelgan qator
    # $500k dan katta bo'lsa -- bu aslida SO'M summa dollar maydoniga yozilgani
    # (jonli misol: 10.07 S1 Перечислении $41,169,628 = 41.17 mln so'm ~ $3,474).
    # Qatorning o'z kursida o'giriladi; bitta naqd/bank to'lovi $500k bo'lishi
    # bu biznesda real emas.
    if acc_cur == "USD" and abs(amount) >= 500000 and rate > 1000:
        _log_once(
            "Payments: so'm-xato qator kursda o'girildi",
            f"amount={amount} kurs={rate} -> {round(amount / rate, 2)}; "
            f"qator: {json.dumps(r, ensure_ascii=False)[:400]}")
        return round(amount / rate, 2)
    if not cur or cur == acc_cur:
        return amount
    if cur == "UZS" and acc_cur == "USD":
        if rate <= 0:
            if fallback_rate and float(fallback_rate) > 0:
                _log_once(
                    "Payments: kurssiz UZS qator KUN-KURSIDA o'girildi",
                    f"amount={amount} kun-kursi={fallback_rate} -> "
                    f"{round(amount / float(fallback_rate), 2)}; "
                    f"qator: {json.dumps(r, ensure_ascii=False)[:400]}")
                return round(amount / float(fallback_rate), 2)
            return None
        return round(amount / rate, 2)
    return None


def sync_payments(days=None, dealer_ids=None):
    """Soatlik: oxirgi PAYMENTS_WINDOW_DAYS kun. Qo'lda: days/dealer_ids bilan."""
    settings = frappe.get_single("Report Service Settings")
    if not settings.sync_enabled:
        return
    _ensure_custom_fields()
    kassa_map = _kassa_map()
    ichki = _ichki_kartalar()
    if not kassa_map:
        _log_once("Payments sync: xarita bo'sh",
                  "Report Service Kassa Account'da birorta yozuv yo'q -- sinxron ishlamaydi.")
        return

    chegara = closed_until(settings)
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

        # API bergan HAQIQIY kunlik kurslarni (currencyRate) Company Currency
        # Exchange'ga yozamiz -- shundan keyin hech kim hech qayerga kursni
        # qo'lda kiritmaydi, ERPNext har hujjatga sanaga qarab o'zi qo'yadi
        # (foydalanuvchi talabi 2026-08-22).
        _store_api_rates(branch, rows)

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
            # Filial lentasidagi ASOSIY OFIS kartasi (egizak): bu harakat asosiy
            # tomonda allaqachon yozilgan -- ikki marta yozilmasin. Qator bucket'ga
            # umuman kirmaydi, shuning uchun 0=0 taqqoslovi buzilmaydi.
            qarshi = _qarshi_nom(r, ptype)
            ik = ichki.get(qarshi)
            if ik and ik.rol == "Asosiy (egizak)":
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

        rebuilt = skipped = yopiq = 0
        for (pdate, kassa, bcur), brows in buckets.items():
            # summa SCHOT valyutasida yig'iladi (UZS qator -> USD schot: qatorning
            # o'z kursi bilan; kurssiz qator yig'indidan chiqadi va sinxron ham
            # uni yozmaydi -- ikkala tomon bir xil o'lchovda, 0=0 buzilmaydi)
            b_account = _resolve_account(kassa_map, did, kassa,
                                         (brows[0].get("currency") or "").strip() or None)
            kun_kursi = _kun_kursi(branch.company, pdate)
            api_sum = 0.0
            for r in brows:
                amt = _account_amount(r, b_account, fallback_rate=kun_kursi)
                if amt is None:
                    _log_once(
                        f"Payments sync: kurssiz UZS qator o'tkazildi ({branch.label} {kassa} {pdate})",
                        f"qator: {json.dumps(r, ensure_ascii=False)[:400]}")
                    continue
                api_sum += amt * (-1 if (r.get("paymentType") or "").upper() == "PAYMENT" else 1)
            api_sum = round(api_sum, 2)

            erp = _bucket_sum_sql(did, kassa, bcur, b_account, sana=pdate)
            erp_sum = round(float(erp or 0), 2)

            if abs(api_sum - erp_sum) < 0.01:
                skipped += 1
                continue

            if _is_closed(pdate, chegara):
                # YOPILGAN DAVR: hujjatlarni o'zgartirmaymiz -- oy yopilgandan
                # keyin manbada tahrir bo'lsa, buni buxgalter qo'lda hal qiladi.
                _log_once(
                    f"Payments: YOPILGAN davrda farq {branch.label} {kassa} {pdate}",
                    f"API={api_sum}, ERPNext={erp_sum}, farq={round(api_sum - erp_sum, 2)}. "
                    f"Yopilgan sana={chegara} -- hujjat o'zgartirilmadi, qo'lda ko'rib chiqing.",
                )
                yopiq += 1
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
            if _is_closed(odate, chegara):
                _log_once(
                    f"Payments: YOPILGAN davrda yetim yozuv {branch.label} {okassa} {odate}",
                    f"API'da bu kun uchun qator yo'q, lekin davr yopilgan ({chegara}) -- bekor qilinmadi.",
                )
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

        if rebuilt or removed or yopiq:
            summary.append(
                f"{branch.label}: {rebuilt} bucket qayta qurildi, {skipped} teng, "
                f"yetim={removed}, yopilgan davr={yopiq}")
        time.sleep(2)

    if summary:
        frappe.logger("payments_sync").info("; ".join(summary))


def _apply_row_rate(pe, row, company, fallback_rate=0):
    """Qatorning o'z kursini (currencyRate) Payment Entry'ga qo'yadi.

    API kursni doim USD->UZS ko'rinishida beradi (masalan 11 850). Kitob
    valyutasi USD bo'lgani uchun UZS tomoniga 1/kurs yoziladi. USD tomoniga
    tegilmaydi (u yerda kurs 1).
    """
    try:
        rate = float(row.get("currencyRate") or 0)
    except (TypeError, ValueError):
        rate = 0
    if rate <= 0:
        # kurssiz qator -- kun-kursi (2026-09-02 qarori bilan izchil)
        rate = float(fallback_rate or 0)
    if rate <= 0:
        return
    kitob = frappe.get_cached_value("Company", company, "default_currency")
    if kitob != "USD":
        return  # kitob dollarda bo'lmasa bu soddalashtirish o'rinli emas
    teskari = 1.0 / rate
    if (pe.paid_from_account_currency or "") == "UZS":
        pe.source_exchange_rate = teskari
    if (pe.paid_to_account_currency or "") == "UZS":
        pe.target_exchange_rate = teskari


def _store_api_rates(branch, rows):
    """API qatorlaridagi currencyRate/currencyRateDate -> Company Currency Exchange.

    Manba dastur kursni USD->UZS ko'rinishida beradi (masalan 11 850). Har
    (kompaniya, kurs sanasi) uchun bitta yozuv yetadi -- teskari yo'nalish
    (UZS->USD) kerak bo'lsa get_company_rate() 1/kurs qilib hisoblaydi.
    """
    from akfa_diller.akfa_diller.api.exchange import upsert_rate

    company = branch.company
    if not company:
        return
    kurslar = {}
    for r in rows:
        rate = r.get("currencyRate")
        rdate = r.get("currencyRateDate") or r.get("date")
        if not rate or not rdate:
            continue
        try:
            iso = _parse_pay_date(rdate)
        except Exception:
            continue
        # bir kunda bir necha qiymat kelsa oxirgisi (eng ko'p uchraydigani emas,
        # aynan oxirgi qator) qoladi -- manba shu kunga shuni ishlatgan
        kurslar[iso] = float(rate)
    for iso, rate in sorted(kurslar.items()):
        try:
            upsert_rate(company, iso, "USD", "UZS", rate, source="API",
                        note=f"{branch.label} feed'idan avtomatik")
        except Exception as e:
            frappe.log_error(
                title=f"Kurs yozilmadi ({company} {iso})",
                message=f"{e}\n\nkurs={rate}",
            )
    if kurslar:
        frappe.db.commit()


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

    ichki = _ichki_kartalar()
    kun_kursi = _kun_kursi(branch.company, pdate)
    for r in brows:
        currency = (r.get("currency") or "").strip() or None
        account = _resolve_account(kassa_map, did, kassa, currency)
        # summa SCHOT valyutasida (UZS -> USD: qator kursi, kurssiz bo'lsa
        # kun-kursi -- taqqoslov yig'indisi bilan BIR XIL qoida, 0=0 saqlanadi)
        amount = _account_amount(r, account, fallback_rate=kun_kursi)
        if amount is None or abs(amount) < 0.005:
            continue
        company = frappe.db.get_value("Account", account, "company")
        ptype = (r.get("paymentType") or "").upper()
        # filiallararo ichki ko'chirma (zavod POS'ida filial "klient" bo'lib ko'rinadi)
        karta = ichki.get(_qarshi_nom(r, ptype))
        if karta and karta.rol == "Filial":
            qarshi = _qarshi_hisob(karta, kassa)
            if not qarshi:
                _log_once(f"Payments sync: ichki karta hisobsiz ({karta.client_name})",
                          "Report Service Ichki Karta'da naqd hisob to'ldirilmagan.")
                karta = None
            elif frappe.db.get_value("Account", qarshi, "company") != company:
                # kompaniyalararo (masalan S1 -> Ishtixon): ERPNext ichki ko'chirmasi
                # bir kompaniya ichida bo'ladi -- eski usul (mijoz/ta'minotchi) qoladi
                _log_once(f"Payments sync: kompaniyalararo ichki karta ({karta.client_name})",
                          f"{qarshi} ({frappe.db.get_value('Account', qarshi, 'company')}) != {company} -- mijoz sifatida yozildi.")
                karta = None
        else:
            karta = None
        try:
            pe = frappe.new_doc("Payment Entry")
            pe.company = company
            pe.posting_date = pdate
            if karta:
                # PUL KO'CHIRMASI: party yo'q, ikkala tomon ham o'z kassa hisobi
                pe.payment_type = "Internal Transfer"
                qarshi = _qarshi_hisob(karta, kassa)
                if amount >= 0:      # pul shu registrga KIRDI -> filialdan keladi
                    pe.paid_from, pe.paid_to = qarshi, account
                else:                # pul shu registrdan CHIQDI -> filialga ketadi
                    pe.paid_from, pe.paid_to = account, qarshi
                pe.paid_amount = pe.received_amount = abs(amount)
            elif ptype == "PAYMENT":
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
            # API bergan HAQIQIY kurs (currencyRate) ustun: manba dastur shu
            # qator uchun aynan shu kursni ishlatgan, biz ham shuni yozamiz.
            # ERPNext o'zining kurs jadvalidan olgan qiymat bosib o'tiladi --
            # aks holda so'mdagi yozuv boshqa kursda kitobga tushib, manba bilan
            # farq berardi (foydalanuvchi talabi 2026-08-22).
            _apply_row_rate(pe, r, company, fallback_rate=kun_kursi)
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


_ICHKI_SEED = [
	# (klient nomi, rol, filial nomi, dealer_id, kompaniya, naqd, bank, karta)
	("Самарканд-1 (Акмал ака )(Ш) Булунгур  8811", "Filial", "Bulungur", "56", "Samarqand Diller",
	 "Bulungur NAQT USD - SD", None, None),
	("Самарканд-1 (Акмал ака )(Ш) Жомбой 6060", "Filial", "Jomboy", "57", "Samarqand Diller",
	 "Jomboy NAQT USD - SD", "Jomboy Perechesleniya USD - SD", None),
	("Самарканд-1 (Акмал ака )(Ш)Чупон ота 0667", "Filial", "Cho'pon ota", "58", "Samarqand Diller",
	 "Chopon Ota NAQT USD - SD", "Chopon Ota Perechesleniya USD - SD", "Chopon ota Plastik USD - SD"),
	("Самарканд-1 (Акмал ака )(Ш) Ургут 2200", "Filial", "Urgut 1", "63", "Samarqand Diller",
	 "Urgur1 NAQT USD - SD", "Urgur1 Perechesleniya USD - SD", None),
	("Самарканд-1 Ургут 2 KOMIL (+998 99 054 3020)", "Filial", "Urgut 2", "234", "Samarqand Diller",
	 "Urgut2  NAQT USD - SD", "Urgut2 Perechesleniya USD - SD", "Urgut2 Plastik USD - SD"),
	("Самарканд-1 (Акмал ака )(Ш) Нуробод 2877", "Filial", "Nurobod", "161", "Samarqand Diller",
	 "Nurobod NAQT USD - SD", None, None),
	("Ishtixon Filial (+998 97 915 0006)", "Filial", "Ishtixon", "97", "Kattaqo'rg'on",
	 "Cash Usd Ishtixon - K", "Ishtixon Perechesleniya USD - K", "Karta Ishtixon - K"),
	("Метан (ш)(+998 91 553 5685)", "Filial", "Mitan", "59", "Kattaqo'rg'on",
	 "Cash usd Mitan - K", None, None),
	("Каттако`рг`он (Акмал ака) (Ш) Мирбозор", "Filial", "Mirbozor", "160", "Kattaqo'rg'on",
	 "Cash usd Mirbozor - K", None, None),
	# filial lentasidagi ASOSIY ofis kartalari -- egizak, yozilmaydi
	("Sam baza (+998 98 273 4232)", "Asosiy (egizak)", "Samarqand-1", "36", "Samarqand Diller", None, None, None),
	# DIQQAT: "Самарканд-1  (+998 98 273 4232)" (Cho'pon ota lentasi) EGIZAK EMAS —
	# uning 25 ta Sales Invoice'i bor (haqiqiy savdo kontragenti), shuning uchun
	# ro'yxatga kiritilmaydi: to'lovlari odatdagidek mijoz-qabuli bo'lib qoladi.
]


def bootstrap_ichki_kartalar():
	"""Filial-kartalar ro'yxatini yaratish (idempotent). Yangi nomlarni foydalanuvchi
	«Report Service Ichki Karta» ro'yxatidan o'zi qo'shadi."""
	yangi = mavjud = 0
	for nom, rol, label, did, comp, naqd, bank, karta in _ICHKI_SEED:
		if frappe.db.exists("Report Service Ichki Karta", {"client_name": nom}):
			mavjud += 1
			continue
		if not frappe.db.exists("Company", comp):
			continue
		for acc in (naqd, bank, karta):
			if acc and not frappe.db.exists("Account", acc):
				frappe.log_error(title="Ichki karta: hisob topilmadi", message=f"{nom} -> {acc}")
		d = frappe.new_doc("Report Service Ichki Karta")
		d.update({"client_name": nom, "rol": rol, "label": label, "dealer_id": did, "company": comp,
		          "naqd_account": naqd if naqd and frappe.db.exists("Account", naqd) else None,
		          "bank_account": bank if bank and frappe.db.exists("Account", bank) else None,
		          "karta_account": karta if karta and frappe.db.exists("Account", karta) else None,
		          "enabled": 1})
		d.flags.ignore_permissions = True
		d.insert()
		yangi += 1
	frappe.db.commit()
	print(f"Ichki kartalar: {yangi} yangi, {mavjud} allaqachon bor")
	return yangi


def ichki_transfer_tahlil():
	"""QURUQ HISOB (hech narsa o'zgarmaydi): mavjud PE'lardan qanchasi ichki
	ko'chirmaga aylanadi va kassa qoldiqlari qanday bo'ladi."""
	ichki = _ichki_kartalar()
	filial = {k: v for k, v in ichki.items() if v.rol == "Filial"}
	egizak = {k: v for k, v in ichki.items() if v.rol != "Filial"}
	print("=== 1. Ichki ko'chirmaga aylanadigan PE'lar (asosiy ofis lentasi)")
	jami = 0.0
	tafsil = {}
	for nom, karta in filial.items():
		rows = frappe.db.sql("""select name, custom_rs_dealer did, custom_rs_kassa kassa, payment_type pt,
			paid_amount pa, received_amount ra, company from `tabPayment Entry`
			where docstatus=1 and party=%s and party_type in ('Customer','Supplier')""", (nom,), as_dict=True)
		if not rows:
			print(f"  {karta.label:<12} {nom[:46]:<46} — qator yo'q")
			continue
		s_in = sum(r.ra for r in rows if r.pt == "Receive")
		s_out = sum(r.pa for r in rows if r.pt == "Pay")
		neto = s_in - s_out
		jami += neto
		for r in rows:
			acc = _qarshi_hisob(karta, r.kassa)
			tafsil[acc] = tafsil.get(acc, 0.0) - (r.ra if r.pt == "Receive" else -r.pa)
		print(f"  {karta.label:<12} {len(rows):>4} PE | kirim {s_in:>12,.2f} | chiqim {s_out:>10,.2f} | sof {neto:>12,.2f}")
	print(f"  JAMI ichki ko'chirmaga aylanadi: {jami:,.2f} $ (soxta mijoz-krediti shuncha yo'qoladi)")
	print("\n=== 2. Egizak (filial lentasidagi asosiy ofis) — o'chiriladi")
	for nom, karta in egizak.items():
		rows = frappe.db.sql("""select account, count(*) n,
			coalesce(sum(case when paid_to=account then received_amount when paid_from=account then -paid_amount else 0 end),0) s
			from (select pe.name, pe.payment_type, pe.paid_amount, pe.received_amount, pe.paid_from, pe.paid_to,
			      case when pe.payment_type='Receive' then pe.paid_to else pe.paid_from end account
			      from `tabPayment Entry` pe where pe.docstatus=1 and pe.party=%s) t group by account""", (nom,), as_dict=True)
		for r in rows:
			print(f"  {nom[:44]:<44} {r.n:>4} PE | {r.account:<32} {r.s:>+12,.2f} qaytadi")
			tafsil[r.account] = tafsil.get(r.account, 0.0) - r.s
	print("\n=== 3. Filial kassa hisoblari: hozir -> keyin")
	for acc, ozgarish in sorted(tafsil.items(), key=lambda x: x[1]):
		bal = frappe.db.sql("""select coalesce(sum(debit-credit),0) from `tabGL Entry`
			where account=%s and is_cancelled=0""", (acc,))[0][0]
		print(f"  {acc:<38} {float(bal):>12,.2f} -> {float(bal)+ozgarish:>12,.2f}  ({ozgarish:+,.2f})")
	return jami


def ichki_transfer_migratsiya(dry_run=1, limit=0, client_name=None):
	"""Bir martalik: mavjud PE'larni ichki ko'chirmaga aylantirish.

	«Filial» kartasidagi to'lov  -> Internal Transfer (filial kassasi <-> asosiy registr)
	«Asosiy (egizak)» kartasidagi -> bekor qilinadi (harakat asosiy tomonda yozilgan)

	bench --site <sayt> execute akfa_diller.akfa_diller.api.payments_sync.ichki_transfer_migratsiya \
	    --kwargs "{'dry_run': 0}"
	"""
	dry_run, limit = int(dry_run), int(limit)
	ichki = _ichki_kartalar()
	if not ichki:
		print("Ichki karta ro'yxati bo'sh — avval bootstrap_ichki_kartalar()")
		return
	stat = {"transfer": 0, "bekor": 0, "otkazildi": 0, "xato": 0}
	summa = 0.0
	for nom, karta in ichki.items():
		if client_name and nom != client_name:
			continue
		names = frappe.db.sql("""select name from `tabPayment Entry`
			where docstatus = 1 and party = %s order by posting_date, name""", (nom,), pluck=True)
		for name in names:
			if limit and (stat["transfer"] + stat["bekor"]) >= limit:
				break
			pe = frappe.get_doc("Payment Entry", name)
			if pe.references:
				stat["otkazildi"] += 1
				print(f"  ! {name}: fakturaga taqsimlangan — tegilmadi")
				continue
			if karta.rol != "Filial":
				if not dry_run:
					pe.flags.ignore_permissions = True
					pe.cancel()
				stat["bekor"] += 1
				continue
			registr = pe.paid_to if pe.payment_type == "Receive" else pe.paid_from
			qarshi = _qarshi_hisob(karta, pe.custom_rs_kassa)
			if not qarshi or frappe.db.get_value("Account", qarshi, "company") != pe.company:
				stat["otkazildi"] += 1
				continue
			miqdor = flt(pe.received_amount if pe.payment_type == "Receive" else pe.paid_amount, 2)
			kirim = pe.payment_type == "Receive"   # pul registrga kirdi -> filialdan keladi
			if dry_run:
				stat["transfer"] += 1
				summa += miqdor
				continue
			eski = pe.as_dict()
			try:
				pe.flags.ignore_permissions = True
				pe.cancel()
				yangi = frappe.new_doc("Payment Entry")
				yangi.update({
					"company": eski.company, "posting_date": eski.posting_date,
					"payment_type": "Internal Transfer",
					"paid_from": qarshi if kirim else registr,
					"paid_to": registr if kirim else qarshi,
					"paid_amount": miqdor, "received_amount": miqdor,
					"mode_of_payment": eski.mode_of_payment, "cost_center": eski.cost_center,
					"reference_no": eski.reference_no or eski.custom_rs_row_hash,
					"reference_date": eski.reference_date or eski.posting_date,
					"remarks": eski.remarks,
					"custom_rs_dealer": eski.custom_rs_dealer, "custom_rs_kassa": eski.custom_rs_kassa,
					"custom_rs_date": eski.custom_rs_date, "custom_rs_currency": eski.custom_rs_currency,
					"custom_rs_row_hash": eski.custom_rs_row_hash,
				})
				yangi.flags.ignore_permissions = True
				yangi.insert()
				yangi.submit()
				stat["transfer"] += 1
				summa += miqdor
				if stat["transfer"] % 100 == 0:
					frappe.db.commit()
					print(f"  ... {stat['transfer']} ta ko'chirma")
			except Exception as e:
				frappe.db.rollback()
				stat["xato"] += 1
				frappe.log_error(title=f"Ichki transfer migratsiya: {name}", message=str(e)[:900])
				print(f"  XATO {name}: {str(e)[:120]}")
	if not dry_run:
		frappe.db.commit()
	print(f"\n{'QURUQ HISOB' if dry_run else 'BAJARILDI'}: ichki ko'chirma {stat['transfer']} ({summa:,.2f} $), "
	      f"egizak bekor {stat['bekor']}, o'tkazildi {stat['otkazildi']}, xato {stat['xato']}")
	return stat


def verify_payments(from_date, to_date):
    """0=0 tasdiq hisoboti: har (filial, kassa, valyuta) bo'yicha API vs ERPNext.

    bench --site <sayt> execute akfa_diller.akfa_diller.api.payments_sync.verify_payments \
        --kwargs "{'from_date': '2026-07-01', 'to_date': '2026-08-17'}"
    """
    settings = frappe.get_single("Report Service Settings")
    kassa_map = _kassa_map()
    ichki = _ichki_kartalar()
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
        kurs_kesh = {}
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
            ik = ichki.get(_qarshi_nom(r, ptype))
            if ik and ik.rol == "Asosiy (egizak)":
                continue  # sinxron ham yozmaydi (harakat asosiy tomonda)
            cur = (r.get("currency") or "").strip() or None
            account = _resolve_account(kassa_map, did, kassa, cur)
            if not account:
                key = (branch.label, kassa, cur)
                unmapped[key] = unmapped.get(key, 0) + (r.get("amount") or 0)
                continue
            # 2026-09-02: sinxron bilan BIR XIL o'lchov — qator summasi schot
            # valyutasiga keltiriladi (so'm-xato qoidasi + kun-kursi fallback).
            # Ilgari xom qiymat solishtirilib, yolg'on-farqlar chiqardi
            # (iyulda +42.3M "farq" — aslida ERPNext to'g'ri edi).
            try:
                pdate = _parse_pay_date(r.get("date"))
            except Exception:
                continue
            if pdate not in kurs_kesh:
                kurs_kesh[pdate] = _kun_kursi(branch.company, pdate)
            amt = _account_amount(r, account, fallback_rate=kurs_kesh[pdate])
            if amt is None:
                key = (branch.label, kassa, cur)
                unmapped[key] = unmapped.get(key, 0) + (r.get("amount") or 0)
                continue
            bkey = (kassa, _norm_currency(cur))
            api_by_bucket[bkey] = api_by_bucket.get(bkey, 0) + sign * amt

        for (kassa, bcur), api_sum in api_by_bucket.items():
            v_account = _resolve_account(kassa_map, did, kassa, bcur)
            erp = _bucket_sum_sql(did, kassa, bcur, v_account, dan=str(from_date), gacha=str(to_date))
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
