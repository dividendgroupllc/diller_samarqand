# -*- coding: utf-8 -*-
"""Zakaz Import (Klaes XML) — Imzo franshiza.

Oqim: XML biriktiriladi -> "Ko'rib chiqish" (hech narsa yozmaydi) ->
"Import qilish" -> long-navbatda: infratuzilma (idempotent) -> material va
tayyor-mahsulot Item'lari -> BITTA Stock Entry (Repack): materiallar ombordan
chiqadi, tayyor mahsulot pozitsiya-tannarxida (UZS -> USD kursda) kirim bo'ladi.

Dublikat-himoya: external_ref = "ZAKAZ:<raqam>" (unique ustun).
Pul manbasi faqat item_prime_cost (UZS); material-darajadagi narxlar ishlatilmaydi.
"""
import frappe
from frappe import _
from frappe.utils import flt

from akfa_diller.akfa_diller.services.klaes_xml_service import parse_zakaz
from akfa_diller.akfa_diller.utils.helpers import get_file_path
from akfa_diller.akfa_diller.utils.validators import check_duplicate_import

DOCTYPE = "Zakaz Import"
KOMPANIYA = "Imzo franshiza"
SINF_GURUH = {
    "profil": "Imzo Profillar",
    "furnitura": "Imzo Furnitura",
    "oyna": "Imzo Oyna",
    "aksessuar": "Imzo Aksessuarlar",
}
TAYYOR_GURUH = "Imzo Tayyor mahsulot"


def _rt(event, doc_name, payload):
    frappe.publish_realtime(f"akfa_zakaz_import_{event}",
                            dict(payload, doc_name=doc_name),
                            doctype=DOCTYPE, docname=doc_name)


def _parse_doc(doc):
    if not doc.xml_file:
        frappe.throw(_("Avval XML faylni biriktiring"))
    yol = get_file_path(doc.xml_file)
    n = parse_zakaz(yol)
    n["tarjimasiz"] = _tarjima_qollash(n)
    return n


def _tarjima_qollash(n):
    """Klaes kodlarini "Klaes Tarjima" lug'ati orqali Akfa savdo nomiga
    o'tkazadi (foydalanuvchi 2026-08-31: itemlar Akfa nomida yuritiladi).
    Lug'atda yo'q kod o'zicha qoladi. Qaytaradi: tarjimasiz kodlar ro'yxati."""
    yoq = set()
    kesh = {}

    def t(m):
        # avval aniq (order_nr) kod, keyin qisqa katalog-kod bilan izlanadi
        topilgan = None
        for kod in (m["kod"], m.get("kod2") or ""):
            if not kod:
                continue
            if kod not in kesh:
                kesh[kod] = frappe.db.get_value("Klaes Tarjima", kod, "akfa_nom")
            if kesh[kod]:
                topilgan = kesh[kod]
                break
        # tarjimasiz qolsa: item sifatida QISQA katalog-kod qulayroq
        m["item_kod"] = (topilgan or m.get("kod2") or m["kod"])[:140]
        if not topilgan:
            yoq.add(m["kod"])

    for m in n["materiallar"]:
        t(m)
    for p in n["pozitsiyalar"]:
        for m in p.get("materiallar") or []:
            t(m)
    return sorted(yoq)


@frappe.whitelist()
def get_preview_data(doc_name):
    """Faylni o'qib, hujjat maydonlarini/pozitsiyalar jadvalini to'ldiradi.
    Ombor hujjatlari YARATILMAYDI."""
    doc = frappe.get_doc(DOCTYPE, doc_name)
    try:
        n = _parse_doc(doc)
    except Exception as e:
        return {"success": False, "message": str(e)}

    h = n["hujjat"]
    doc.zakaz_raqami = h["raqam"]
    doc.zakaz_sanasi = h["sana"]
    doc.loyiha = h["loyiha"]
    doc.netto_uzs = h["netto_uzs"]
    doc.materiallar_soni = len(n["materiallar"])
    doc.aksessuar_summa_uzs = n["aksessuar_summa_uzs"]
    doc.set("pozitsiyalar", [])
    for p in n["pozitsiyalar"]:
        doc.append("pozitsiyalar", {
            "nr": p["nr"],
            "nom": p["nom"],
            "olcham": f"{p['eni_mm']:g} x {p['boyi_mm']:g}" if p["eni_mm"] else "",
            "dona": p["dona"],
            "tannarx_uzs": p["tannarx_uzs"],
            "tavsif": p["tavsif"],
        })

    # Tarjimasiz kodlar jadvali (foydalanuvchi 2026-09-03): kod -> Item
    # bog'lashni USER qiladi. Qoidalar: (1) lug'atda tarjimasi BOR kod bu
    # yerda CHIQMAYDI; (2) oldin kiritilgan bog'lovlar qayta-preview'da
    # yo'qolmaydi; (3) import paytida bog'lovlar Klaes Tarjima'ga muhrlanadi.
    kod_info = {}
    for m in list(n["materiallar"]) + [
        m for p in n["pozitsiyalar"] for m in (p.get("materiallar") or [])
    ]:
        kod_info.setdefault(m["kod"], {"nom": m.get("nom") or "",
                                       "birlik": m.get("birlik") or ""})
    eski = {r.klaes_kod: r.item for r in (doc.get("tarjimasizlar") or [])}
    doc.set("tarjimasizlar", [])
    for kod in n.get("tarjimasiz") or []:
        info = kod_info.get(kod) or {}
        doc.append("tarjimasizlar", {
            "klaes_kod": kod,
            "klaes_nom": (info.get("nom") or "")[:140],
            "birlik": info.get("birlik") or "",
            "item": eski.get(kod),
        })
    doc.save(ignore_permissions=True)
    frappe.db.commit()

    sinf_soni = {}
    for m in n["materiallar"]:
        sinf_soni[m["sinf"]] = sinf_soni.get(m["sinf"], 0) + 1
    return {
        "success": True,
        "hujjat": h,
        "pozitsiyalar": n["pozitsiyalar"],
        "materiallar": n["materiallar"],
        "sinf_soni": sinf_soni,
        "aksessuar_summa_uzs": n["aksessuar_summa_uzs"],
        "ogohlantirishlar": n["ogohlantirishlar"],
        "tarjimasiz": n.get("tarjimasiz") or [],
    }


@frappe.whitelist()
def process_import(doc_name):
    doc = frappe.get_doc(DOCTYPE, doc_name)
    if doc.status == "Processing":
        frappe.throw(_("Import allaqachon ishlayapti"))
    if doc.status == "Processed":
        frappe.throw(_("Bu zakaz allaqachon import qilingan"))

    n = _parse_doc(doc)

    # Itemsiz ishlab chiqarish YO'Q (foydalanuvchi 2026-09-03): lug'atda ham,
    # hujjat-jadvalidagi bog'lovlarda ham topilmagan kod qolsa -- import
    # boshlanmaydi. User "Tarjimasiz Klaes kodlari" jadvalida bog'lab,
    # saqlab, keyin qayta bosadi.
    boglangan = {
        (r.klaes_kod or "").strip()
        for r in (doc.get("tarjimasizlar") or [])
        if (r.item or "").strip()
    }
    ochiq = [k for k in (n.get("tarjimasiz") or []) if k not in boglangan]
    if ochiq:
        frappe.throw(_(
            "Import to'xtatildi: {0} ta Klaes kodi hali Akfa itemiga "
            "bog'lanmagan. Avval «Tarjimasiz Klaes kodlari» jadvalida har "
            "biriga item tanlab, hujjatni saqlang. Ochiq kodlar: {1}{2}"
        ).format(
            len(ochiq),
            ", ".join(ochiq[:10]),
            " ..." if len(ochiq) > 10 else "",
        ))

    ref = f"ZAKAZ:{n['hujjat']['raqam']}"

    dup = check_duplicate_import(ref, doc.name, import_doctype=DOCTYPE,
                                 invoice_fieldname="created_docs")
    if not dup.get("success", True):
        frappe.throw(dup.get("message") or _("Bu zakaz raqami allaqachon import qilingan"))

    doc.db_set("external_ref", ref)
    doc.db_set("status", "Processing")
    doc.db_set("error_log", "")
    frappe.db.commit()

    frappe.enqueue(_process_import_job, queue="long", timeout=3600,
                   doc_name=doc.name)
    return {"success": True, "message": _("Import navbatga qo'yildi")}


def _process_import_job(doc_name):
    try:
        _process_import_sync(doc_name)
    except Exception:
        frappe.db.rollback()
        xato = frappe.get_traceback()
        frappe.db.set_value(DOCTYPE, doc_name, {
            "status": "Failed", "error_log": xato[-3000:]}, update_modified=False)
        frappe.db.commit()
        frappe.log_error("Zakaz Import", xato)
        _rt("failed", doc_name, {"msg": xato[-500:]})


def _process_import_sync(doc_name):
    doc = frappe.get_doc(DOCTYPE, doc_name)
    # idempotentlik: hujjat allaqachon yaratilgan bo'lsa (parallel worker,
    # qayta chaqiruv) -- ikkinchi marta yozmaymiz
    if (doc.created_docs or "").strip():
        frappe.logger("zakaz_import").info(
            f"{doc_name}: created_docs bor ({doc.created_docs}) -- qayta ishlanmadi")
        return
    loglar = []

    def log(msg):
        loglar.append(msg)
        frappe.db.set_value(DOCTYPE, doc_name, "import_log",
                            "\n".join(loglar), update_modified=False)
        _rt("log", doc_name, {"msg": msg, "full_log": "\n".join(loglar)})

    # 0) USER bog'lagan tarjimalarni Klaes Tarjima lug'atiga MUHRLAYMIZ --
    # quyidagi parse allaqachon yangi lug'at bilan o'tadi (item to'g'ri nomda
    # ochiladi), keyingi importlarda esa bu kodlar "tarjimasiz" chiqmaydi.
    muhr = 0
    for r in list(doc.get("tarjimasizlar") or []):
        item = (r.item or "").strip()
        if not item:
            continue
        # akfa_nom = Item DOCNAME: import item_kod'ni docname sifatida ishlatadi
        if frappe.db.exists("Klaes Tarjima", r.klaes_kod):
            frappe.db.set_value("Klaes Tarjima", r.klaes_kod, "akfa_nom", item,
                                update_modified=False)
        else:
            kt = frappe.new_doc("Klaes Tarjima")
            kt.klaes_kod = r.klaes_kod
            kt.akfa_nom = item
            kt.flags.ignore_permissions = True
            kt.insert()
        muhr += 1
    if muhr:
        # bog'langan qatorlar jadvaldan olib tashlanadi (endi lug'atda)
        frappe.db.sql(
            """DELETE FROM `tabZakaz Import Tarjima`
               WHERE parent = %s AND COALESCE(item, '') != ''""", (doc.name,))
        frappe.db.commit()
        doc.reload()
        log(f"{muhr} ta kod-bog'lov Klaes Tarjima lug'atiga muhrlandi")

    n = _parse_doc(doc)
    if n.get("tarjimasiz"):
        # muhrlashdan KEYIN ham ochiq kod qolgan bo'lsa -- to'xtaymiz
        raise frappe.ValidationError(
            "Bog'lanmagan Klaes kodlari bor: " + ", ".join(n["tarjimasiz"][:10]))
    h = n["hujjat"]
    log(f"Zakaz {h['raqam']} ({h['sana']}): {len(n['pozitsiyalar'])} pozitsiya, "
        f"{len(n['materiallar'])} xil material")
    for o in n["ogohlantirishlar"]:
        log(f"OGOHLANTIRISH: {o}")
    wh, cc = _infra(doc, log)
    kurs = _kurs(doc.company)
    log(f"UZS->USD kurs: 1 USD = {1/kurs:,.0f} UZS" if kurs else "kurs topilmadi")

    # 1) material item'lari
    yangi_mat = 0
    for m in n["materiallar"]:
        if not frappe.db.exists("Item", m["item_kod"]):
            _item_yarat(m["item_kod"], m["item_kod"], SINF_GURUH[m["sinf"]],
                        m["birlik"],
                        tavsif=f"{m['nom']} | KLAES: {m['kod']}"
                               + (f" ({m['kod2']})" if m.get("kod2") else ""))
            yangi_mat += 1
        else:
            uom = frappe.db.get_value("Item", m["item_kod"], "stock_uom")
            if uom and uom != m["birlik"]:
                log(f"OGOHLANTIRISH: {m['item_kod']} birligi {uom}, "
                    f"faylda esa {m['birlik']} -- tekshiring")
    if yangi_mat:
        frappe.db.commit()
    log(f"Materiallar: {len(n['materiallar'])} xil ({yangi_mat} ta yangi karta ochildi)")

    # 2) tayyor mahsulot item'lari
    tayyor = []
    for p in n["pozitsiyalar"]:
        kod = f"ZKZ-{h['raqam']}-{p['nr']}"
        if not frappe.db.exists("Item", kod):
            # foydalanuvchi 2026-08-30: item nomi = item kodi (bir xil);
            # mahsulot tavsifi (nomi, o'lchami) description'da saqlanadi
            olcham = f" {p['eni_mm']:g}x{p['boyi_mm']:g}" if p["eni_mm"] else ""
            _item_yarat(kod, kod, TAYYOR_GURUH, "Nos",
                        tavsif=(p["nom"] + olcham + " | " + p["tavsif"])[:500])
        tayyor.append((kod, p))
    frappe.db.commit()
    log(f"Tayyor mahsulot kartalari: {len(tayyor)} ta")

    # 3) BITTA Manufacture Stock Entry (foydalanuvchi qarori 2026-08-30):
    # tayyor mahsulot tannarxi QO'LDA QO'YILMAYDI -- sarflangan materiallarning
    # daftar-qiymatidan o'zi hisoblanadi (materiallar oldindan narxlangan
    # bo'lishi kerak). Zavod prime_cost faqat ma'lumot uchun remarkda qoladi.
    # ERPNext qoidasi: bitta Manufacture SE'da faqat BITTA tayyor mahsulot --
    # shuning uchun HAR POZITSIYAGA alohida SE (o'z material-ro'yxati bilan;
    # parser materiallarni pozitsiya kesimida ham beradi).
    yaratilgan = []
    for kod, p in tayyor:
        mats = p.get("materiallar") or []
        zavod_prime = flt(p["tannarx_uzs"]) * flt(p["dona"])
        se = frappe.new_doc("Stock Entry")
        se.company = doc.company
        se.purpose = se.stock_entry_type = "Manufacture"
        se.posting_date = h["sana"]
        se.set_posting_time = 1
        se.posting_time = "23:59:59"
        se.remarks = (f"Zakaz {h['raqam']} pozitsiya {p['nr']} ({p['nom']}): "
                      f"Klaes XML import. Zavod prime {zavod_prime:,.0f} UZS "
                      f"(~${zavod_prime * kurs:,.2f}) -- faqat ma'lumot uchun; "
                      f"tannarx materiallardan hisoblanadi.")
        for m in mats:
            se.append("items", {
                "item_code": m["item_kod"],
                "qty": round(m["miqdor"], 3),
                "s_warehouse": wh,
                "cost_center": cc,
                "allow_zero_valuation_rate": 1,
            })
        se.append("items", {
            "item_code": kod,
            "qty": p["dona"],
            "t_warehouse": wh,
            "is_finished_item": 1,
            "cost_center": cc,
        })
        se.flags.ignore_permissions = True
        se.insert()
        se.submit()
        yaratilgan.append(se.name)
        for qator in doc.pozitsiyalar:
            if str(qator.nr) == str(p["nr"]):
                frappe.db.set_value("Zakaz Import Pozitsiya", qator.name,
                                    "tayyor_item", kod, update_modified=False)
                break
        log(f"Pozitsiya {p['nr']}: {se.name} ({len(mats)} material -> "
            f"{kod} x{p['dona']:g})")
    frappe.db.commit()

    doc.db_set("created_docs", ", ".join(yaratilgan))
    doc.db_set("status", "Processed")
    frappe.db.commit()
    log("IMPORT TUGADI")
    _rt("success", doc_name, {"result": ", ".join(yaratilgan),
                              "full_log": "\n".join(loglar)})


def _item_yarat(kod, nom, guruh, uom, tavsif=""):
    it = frappe.new_doc("Item")
    it.item_code = kod
    it.item_name = (nom or kod)[:140]
    it.item_group = guruh
    it.stock_uom = uom
    it.is_stock_item = 1
    if tavsif:
        it.description = tavsif
    it.flags.ignore_permissions = True
    it.insert()


def _kurs(company):
    """UZS -> USD ko'paytiruvchi (kompaniya kurs-jadvalidan)."""
    r = frappe.db.get_value("Company Currency Exchange",
                            {"company": company, "from_currency": "UZS",
                             "to_currency": "USD"}, "exchange_rate")
    if r:
        return flt(r)
    r = frappe.db.get_value("Company Currency Exchange",
                            {"company": company, "from_currency": "USD",
                             "to_currency": "UZS"}, "exchange_rate")
    if r:
        return 1.0 / flt(r)
    return 1.0 / 12000.0


def _infra(doc, log):
    """Idempotent: ombor, item-guruhlar, kompaniya default-schotlari.
    Faqat yetishmaganини yaratadi."""
    company = doc.company
    abbr = frappe.db.get_value("Company", company, "abbr")

    # kompaniya default-schotlari (SE uchun shart)
    komp = frappe.get_doc("Company", company)
    ozgardi = False
    if not komp.stock_adjustment_account:
        a = frappe.db.get_value("Account", {"company": company,
                                            "account_type": "Stock Adjustment"})
        if a:
            komp.stock_adjustment_account = a
            ozgardi = True
    if not komp.default_inventory_account:
        a = frappe.db.get_value("Account", {"company": company,
                                            "account_type": "Stock", "is_group": 0})
        if a:
            komp.default_inventory_account = a
            ozgardi = True
    if not komp.cost_center:
        cc_leaf = frappe.db.get_value("Cost Center", {"company": company, "is_group": 0})
        if cc_leaf:
            komp.cost_center = cc_leaf
            ozgardi = True
    if not komp.default_expense_account:
        a = frappe.db.get_value("Account", {"company": company,
                                            "account_type": "Cost of Goods Sold"})
        if a:
            komp.default_expense_account = a
            ozgardi = True
    if ozgardi:
        komp.flags.ignore_permissions = True
        komp.save()
        log("Kompaniya default-schotlari to'ldirildi")

    # ombor
    wh = doc.warehouse
    if not wh:
        wh = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0})
    if not wh:
        w = frappe.new_doc("Warehouse")
        w.warehouse_name = "Imzo Asosiy"
        w.company = company
        w.parent_warehouse = frappe.db.get_value(
            "Warehouse", {"company": company, "is_group": 1})
        w.flags.ignore_permissions = True
        w.insert()
        wh = w.name
        log(f"Yangi ombor: {wh}")
    if not doc.warehouse:
        doc.db_set("warehouse", wh)

    # item-guruhlar
    ota = "Imzo"
    if not frappe.db.exists("Item Group", ota):
        g = frappe.new_doc("Item Group")
        g.item_group_name = ota
        g.parent_item_group = "All Item Groups"
        g.is_group = 1
        g.flags.ignore_permissions = True
        g.insert()
        log("Item-guruh ochildi: Imzo")
    for guruh in list(SINF_GURUH.values()) + [TAYYOR_GURUH]:
        if not frappe.db.exists("Item Group", guruh):
            g = frappe.new_doc("Item Group")
            g.item_group_name = guruh
            g.parent_item_group = ota
            g.is_group = 0
            g.flags.ignore_permissions = True
            g.insert()
    frappe.db.commit()

    # cost center
    cc = doc.cost_center or frappe.db.get_value(
        "Cost Center", {"company": company, "is_group": 0})
    if not doc.cost_center and cc:
        doc.db_set("cost_center", cc)

    return wh, cc


@frappe.whitelist()
def cancel_import(doc_name):
    doc = frappe.get_doc(DOCTYPE, doc_name)
    if doc.status not in ("Processed", "Failed", "Processing"):
        frappe.throw(_("Faqat Processed/Failed importni bekor qilish mumkin"))
    bekor = []
    for nom in [x.strip() for x in (doc.created_docs or "").split(",") if x.strip()]:
        if frappe.db.exists("Stock Entry", nom):
            se = frappe.get_doc("Stock Entry", nom)
            if se.docstatus == 1:
                se.flags.ignore_permissions = True
                se.cancel()
            frappe.delete_doc("Stock Entry", nom, force=1, ignore_permissions=True)
            bekor.append(nom)
    doc.db_set("status", "Draft")
    # unique ustun: bo'sh satr emas, NULL (aks holda ikkinchi bekor-qilish
    # "Duplicate entry ''" bilan yiqiladi)
    doc.db_set("external_ref", None)
    doc.db_set("created_docs", "")
    doc.db_set("import_log", f"Bekor qilindi: {', '.join(bekor) or '-'}")
    frappe.db.commit()
    return {"success": True, "message": _("Bekor qilindi: {0}").format(", ".join(bekor) or "-")}
