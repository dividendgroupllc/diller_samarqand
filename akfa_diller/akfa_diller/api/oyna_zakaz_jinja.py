# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""Jinja (Print Format) uchun metodlar — hooks.jinja.methods."""

from akfa_diller.akfa_diller.api import oyna_konfigurator as k
from akfa_diller.akfa_diller.api.oyna_zakaz import build_print_ctx
from akfa_diller.akfa_diller.services.oyna_chizma import pozitsiya_svg


def oyna_zakaz_print_ctx(doc, variant="taklif"):
	return build_print_ctx(doc, variant)


def oyna_zakaz_svg(doc, poz, opts=None):
	nr = int(poz.get("nr") or 0)
	tesh = [t.as_dict() for t in (doc.get("teshiklar") or []) if int(t.pozitsiya_nr or 0) == nr]
	kes = [x.as_dict() for x in (doc.get("kesimlar") or []) if int(x.pozitsiya_nr or 0) == nr]
	o = dict(opts or {})
	o.setdefault("prefix", f"pf{nr}")
	o.setdefault("stil", k.ishlov_stillari(k.sozlama()))
	return pozitsiya_svg(poz.as_dict() if hasattr(poz, "as_dict") else dict(poz), tesh, kes, o)
