"""Tasdiqlash skripti -- kim bo'lmasin, xohlagan vaqtda ishga tushirib, ma'lumot
haqiqatan Report Service API'sidan jonli olinayotganini o'z ko'zi bilan ko'rishi
uchun.

Ishga tushirish:
    cd /home/frappe/frappe-bench
    sudo -u frappe /home/frappe/.local/bin/bench --site akfa.local execute \
        akfa_diller.akfa_diller._verify_data_proof.run

Bu skript hech qanday hujjatga tegmaydi, faqat API'dan o'qiydi (xavfsiz)."""

from akfa_diller.akfa_diller.services import report_service_client

CHECKS = [
    ("Kattaqo'rg'on -> Ishtixon (03.07.2026)", 38, "52", "2026-07-03", "2026-07-03"),
    ("Ishtixon o'zi qabul qildim degan (03.07.2026)", 97, "164", "2026-07-03", "2026-07-03"),
    ("Samarqand-1 -> Jomboy (09.07.2026)", 36, "7", "2026-07-09", "2026-07-09"),
]


def run():
    base_url, token = report_service_client.get_token()
    print(f"Report Service API bilan bog'landi: {base_url}\n")

    for label, dealer_id, target_cid, from_date, to_date in CHECKS:
        rows = report_service_client.fetch_all_rows_for_dealer(base_url, token, dealer_id, from_date, to_date)
        rows = [r for r in rows if str(r.get("clientCid")) == target_cid]

        totals = {}
        for r in rows:
            item = r.get("productName")
            qty = r.get("qty") or 0
            totals[item] = totals.get(item, 0) + qty

        print(f"=== {label} ===")
        print(f"API'dan {len(rows)} ta qator, {len(totals)} ta tovar turi olindi:")
        for item, qty in sorted(totals.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {item}: {qty} dona")
        print()

    print("=== Bu ma'lumot HOZIR, jonli, to'g'ridan-to'g'ri API'dan olindi ===")
