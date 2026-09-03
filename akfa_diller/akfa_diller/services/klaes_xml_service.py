# -*- coding: utf-8 -*-
"""Klaes zakaz-XML parseri (Imzo franshiza).

11 ta namunaviy fayl tahlilidan chiqqan parse-shartnoma (2026-08-30):
- document_kind == "order", valyuta UZS, brutto = netto x 1.12 (QQS 12%).
- Haqiqiy konstruksiya soni <piece> da (item_quantity DOIM 1 -- ishonilmaydi!)
- Profillar: birlik "м" -> sum(cuttinglength*piece)/1000 metr;
  birlik "Штук" -> sum(piece) dona. Kesim-qatorlar butun pozitsiya uchun --
  piece ga QAYTA ko'paytirilmaydi.
- Furnitura: fit/item_measurement/measure/piece yig'indisi (dona).
- Oyna: g_rect va g_spec bir xil; dona=glasspiece, maydon=glassarea*glasspiece.
- Aksessuar: rekursiv (accessory_item, technical_accessory_item,
  follow-on_accessory_item); miqdor=item_quantity.
- Fiktiv kodlar tashlanadi: "~..." profillari, "~"/"" furnitura, "мс" oyna,
  "Кол-во"/"площадь" aksessuar-qatorlari, "$~ПАКЕТ"/"$~ТАЛОН".
- Material darajasidagi narxlar USD-simon kichik sonlar -- pulga ISHLATILMAYDI;
  pul manbasi faqat item_prime_cost (UZS, 1 dona uchun).
"""
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict

FIKTIV_PROFIL_PREFIKS = ("~", "$~")
FIKTIV_FURNITURA = ("~", "")
FIKTIV_OYNA = ("мс",)
FIKTIV_AKSESSUAR = ("Кол-во", "площадь", "Количество", "Общая площадь")

BIRLIK_XARITA = {"м": "Meter", "Штук": "Nos", "м²": "Square Meter"}


def _g(el, yol, default=""):
    e = el.find(yol) if el is not None else None
    if e is None or e.text is None:
        return default
    return e.text.strip()


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _sana(s):
    """Klaes sanalari nolsiz keladi: '2026-8-3' -> '2026-08-03'."""
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", (s or "").strip())
    if not m:
        return None
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def parse_zakaz(xml_path):
    """XML -> tuzilgan lug'at. Xatoda ValueError ko'taradi."""
    root = ET.parse(xml_path).getroot()
    ogoh = []

    if _g(root, "document_kind") != "order":
        raise ValueError(f"document_kind='{_g(root, 'document_kind')}' -- 'order' kutilgan edi")
    valyuta = _g(root, "document_currency_isocode")
    if valyuta != "UZS":
        raise ValueError(f"valyuta '{valyuta}' -- UZS kutilgan edi")

    netto = _f(_g(root, "document_amount_net"))
    brutto = _f(_g(root, "document_amount_gross"))
    if netto and abs(brutto - netto * 1.12) > max(1.0, netto * 0.001):
        ogoh.append(f"brutto ({brutto:,.2f}) != netto x 1.12 ({netto * 1.12:,.2f})")

    hujjat = {
        "raqam": _g(root, "document_number"),
        "sana": _sana(_g(root, "document_date")),
        "ozgargan_sana": _sana(_g(root, "document_date_of_change")),
        "mijoz_raqami": _g(root, "customer_number"),
        "mijoz_nomi": _g(root, "document_address/first_name"),
        "masul": _g(root, "authorised_person_name"),
        "loyiha": _g(root, "project_number"),
        "netto_uzs": netto,
        "brutto_uzs": brutto,
        "izoh": _g(root, "document_note").replace("|", " | ")[:500],
    }
    if not hujjat["raqam"]:
        raise ValueError("document_number bo'sh")
    if not hujjat["sana"]:
        raise ValueError("document_date o'qilmadi")

    pozitsiyalar = []
    # (sinf, kod) -> {"kod","nom","birlik","miqdor","sinf"}
    materiallar = OrderedDict()

    def _lugatga(lugat, sinf, kod, nom, birlik, miqdor, kod2=""):
        kalit = (sinf, kod)
        if kalit not in lugat:
            lugat[kalit] = {"sinf": sinf, "kod": kod, "nom": nom,
                            "birlik": birlik, "miqdor": 0.0, "kod2": kod2}
        lugat[kalit]["miqdor"] += miqdor

    items = root.find("document_items")
    for it in (list(items) if items is not None else []):
        poz_mat = OrderedDict()

        def mat_qosh(sinf, kod, nom, birlik, miqdor, kod2=""):
            if miqdor <= 0:
                return
            # Frappe hujjat-nomida < > taqiqlangan (masalan "МС>МССН")
            kod = kod.replace("<", "-").replace(">", "-").strip()
            kod2 = (kod2 or "").replace("<", "-").replace(">", "-").strip()
            _lugatga(poz_mat, sinf, kod, nom, birlik, miqdor, kod2)
            _lugatga(materiallar, sinf, kod, nom, birlik, miqdor, kod2)
        if _g(it, "item_type") != "konstruktion":
            ogoh.append(f"pozitsiya {_g(it, 'item_number')}: item_type="
                        f"'{_g(it, 'item_type')}' -- o'tkazib yuborildi")
            continue
        dona = _f(_g(it, "piece", "1"), 1.0)
        poz = {
            "nr": _g(it, "item_number"),
            "nom": _g(it, "item_short_description"),
            "dona": dona,
            "tannarx_uzs": _f(_g(it, "item_prime_cost")),
            "eni_mm": _f(_g(it, "item_measurement/breite/mass0")),
            "boyi_mm": _f(_g(it, "item_measurement/height/mass0")),
            "tavsif": _g(it, "item_text").replace("|", " | ")[:300],
            "oyna_soni": _f(_g(it, "matlist/quantity_glasses")),
        }
        pozitsiyalar.append(poz)

        matlist = it.find("matlist")
        if matlist is None:
            ogoh.append(f"pozitsiya {poz['nr']}: matlist yo'q")
            continue

        # --- PROFILLAR (prf_pvc/prf_alu/gask_pvc/gask_alu) ---
        profs = matlist.find("profiles")
        for p in (list(profs) if profs is not None else []):
            kod = _g(p, "profile_nr")
            if not kod or kod.startswith(FIKTIV_PROFIL_PREFIKS):
                continue
            birlik = _g(p, "profile_unit")
            nom = _g(p, "profile_desc") or kod
            # Rangigacha aniq buyurtma-kodi (order_nr) -- tarjima lug'ati
            # aynan shu kodda yuritiladi; topilsa asosiy kalit shu bo'ladi
            zakaz_kod = ""
            for e in p.iter("order_nr"):
                t = (e.text or "").strip()
                if t:
                    zakaz_kod = t
                    break
            asosiy = zakaz_kod or kod
            olchovlar = p.findall(".//measure")
            if birlik == "м":
                mm = sum(_f(_g(m, "cuttinglength")) * _f(_g(m, "piece"))
                         for m in olchovlar)
                mat_qosh("profil", asosiy, nom, "Meter", mm / 1000.0, kod2=kod)
            else:  # "Штук"
                soni = sum(_f(_g(m, "piece")) for m in olchovlar)
                mat_qosh("profil", asosiy, nom, "Nos", soni, kod2=kod)

        # --- FURNITURA ---
        fits = matlist.find("fittings")
        fit_rows = list(fits) if fits is not None else []
        for p in fit_rows:
            kod = _g(p, "fitting_nr")
            if kod in FIKTIV_FURNITURA:
                if _f(_g(p, ".//measure/piece")) > 10:
                    ogoh.append(f"pozitsiya {poz['nr']}: kodi bo'sh furnitura "
                                f"(soni katta) -- o'tkazildi")
                continue
            soni = sum(_f(_g(m, "piece"))
                       for m in p.findall("item_measurement/measure"))
            mat_qosh("furnitura", kod, _g(p, "fitting_desc") or kod, "Nos", soni)
        if not fit_rows and "окно" in poz["nom"].lower() and "глух" not in poz["nom"].lower():
            ogoh.append(f"pozitsiya {poz['nr']} ('{poz['nom']}'): ochiladigan "
                        f"oynada furnitura ro'yxati YO'Q -- manba-ma'lumot kamchiligi")

        # --- OYNA (g_rect + g_spec) ---
        gls = matlist.find("glasprodukte")
        oyna_dona = 0.0
        for p in (list(gls) if gls is not None else []):
            kod = _g(p, "glasprodukt")
            if not kod or kod in FIKTIV_OYNA:
                continue
            dona_g = _f(_g(p, "glasspiece"), 1.0)
            oyna_dona += dona_g
            nom = _g(p, "produkt_bez") or kod
            mat_qosh("oyna", kod, nom, "Nos", dona_g)
        if poz["oyna_soni"] and oyna_dona and abs(oyna_dona - poz["oyna_soni"]) > 0.001:
            ogoh.append(f"pozitsiya {poz['nr']}: oyna dona {oyna_dona:g} != "
                        f"quantity_glasses {poz['oyna_soni']:g}")

        # --- AKSESSUARLAR (rekursiv) ---
        acc = it.find("accessory_list")

        def acc_yur(el):
            for c in (list(el) if el is not None else []):
                if c.tag in ("accessory_item", "technical_accessory_item",
                             "follow-on_accessory_item"):
                    nom_a = _g(c, "item_short_description")
                    kod_a = _g(c, "article_number") or nom_a
                    if nom_a in FIKTIV_AKSESSUAR or kod_a in FIKTIV_AKSESSUAR:
                        continue
                    birlik_a = BIRLIK_XARITA.get(_g(c, "unit"), "Nos")
                    mat_qosh("aksessuar", kod_a, nom_a or kod_a, birlik_a,
                             _f(_g(c, "item_quantity")))
                acc_yur(c)

        acc_yur(acc)
        poz["materiallar"] = list(poz_mat.values())

    if not pozitsiyalar:
        raise ValueError("faylda birorta konstruksiya-pozitsiya topilmadi")

    poz_summa = sum(p["tannarx_uzs"] * p["dona"] for p in pozitsiyalar)
    if poz_summa - netto > max(1.0, netto * 0.001):
        ogoh.append(f"pozitsiyalar summasi ({poz_summa:,.2f}) netto'dan katta")
    aks_summa_uzs = max(netto - poz_summa, 0.0)

    return {
        "hujjat": hujjat,
        "pozitsiyalar": pozitsiyalar,
        "materiallar": list(materiallar.values()),
        "aksessuar_summa_uzs": aks_summa_uzs,
        "ogohlantirishlar": ogoh,
    }
