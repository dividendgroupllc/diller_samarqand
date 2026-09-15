# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""Oyna sex konfigurator («Oyna Zakaz») seed: xizmat-itemlar, custom-fieldlar,
narx ro'yxati, «Oyna Narx Jadvali» defaultlari/tariflari, menedjer-rol ruxsatlari.

Idempotent; after_migrate'da ham chaqiriladi (ensure_setup), shuning uchun
prod `--skip-failing` patch'ni tashlab ketsa ham sozlama tushadi.
"""

from akfa_diller.akfa_diller.api.oyna_konfigurator import ensure_setup


def execute():
	ensure_setup()
