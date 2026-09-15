// Oyna Zakaz — konfigurator formasi: pozitsiya-kartalari (SVG), dialog orqali tahrir,
// jonli jami, holat-tugmalari (Tayyor / Topshirildi → Sales Invoice + Material Issue), PDF.

frappe.provide("akfa_diller.oz");

frappe.ui.form.on("Oyna Zakaz", {
	setup(frm) {
		frm.set_query("customer", () => ({ query: "akfa_diller.akfa_diller.api.oyna_zakaz.oyna_sex_customers" }));
		frm.set_query("item", "aksessuarlar", () => ({ query: "akfa_diller.akfa_diller.api.oyna_zakaz.aksessuar_items_query" }));
		frm.set_query("oyna_item", "pozitsiyalar", () => ({ query: "akfa_diller.akfa_diller.api.oyna_zakaz.oyna_items_query" }));
		frm.set_query("oyna_item", "listlar", () => ({ query: "akfa_diller.akfa_diller.api.oyna_zakaz.oyna_items_query" }));
	},
	onload(frm) {
		if (frm.is_new() && frm.doc.company !== "Oyna sex") {
			frm.doc.company = "Oyna sex"; // user-default kompaniyasi maydon-default'ini bosib ketadi
			frm.refresh_field("company");
		}
		if (frm.is_new() && !frm.doc.yetkazish_sanasi && frm.doc.sana) {
			frm.set_value("yetkazish_sanasi", frappe.datetime.add_days(frm.doc.sana, 7));
		}
	},
	sana(frm) {
		// qo'lda kiritilgan yetkazish sanasi saqlanadi; faqat bo'sh yoki zakaz sanasidan oldin bo'lsa +7 qo'yiladi
		if (frm.doc.docstatus === 0 && frm.doc.sana && (!frm.doc.yetkazish_sanasi || frm.doc.yetkazish_sanasi < frm.doc.sana)) {
			frm.set_value("yetkazish_sanasi", frappe.datetime.add_days(frm.doc.sana, 7));
		}
	},
	refresh(frm) {
		akfa_diller.oz.bind_grid(frm);
		akfa_diller.oz.render_cards(frm);
		akfa_diller.oz.add_buttons(frm);
		akfa_diller.oz.headline(frm);
		akfa_diller.oz.render_raskroy(frm);
		akfa_diller.oz.render_chiqindi_note(frm);
		if (frm.doc.docstatus === 0) {
			if (!frm._oz_intro) { frm._oz_intro = true; frm.set_intro(__("Pozitsiyalarni «+ Pozitsiya qo'shish» orqali kiriting. «Submit» = zakaz qabul qilindi (Zakaz olindi); keyin «Tayyor» va «Mijozga topshirildi» — faktura va tannarx avtomatik."), "blue"); }
		}
	},
	oldindan_tolov(frm) { akfa_diller.oz.jami(frm); },
	validate(frm) {
		if (!(frm.doc.pozitsiyalar || []).length && !(frm.doc.aksessuarlar || []).length) {
			frappe.msgprint(__("Kamida bitta pozitsiya yoki aksessuar kiriting"));
			frappe.validated = false;
		}
	},
});

// Har list bo'yicha mijozga % («Listlar rejasi» qatorlari) — narxga ta'sir qiladi, rejani ESKIRTIRMAYDI
frappe.ui.form.on("Oyna Zakaz List Reja", {
	mijoz_foiz(frm, cdt, cdn) {
		if (akfa_diller.oz._rk_lock) return;
		const r = locals[cdt][cdn];
		const max = Math.max(0, 100 - flt(r.foydalanish_foiz));
		akfa_diller.oz._rk_lock = true;
		try {
			if (flt(r.mijoz_foiz) > max + 0.05) {
				frappe.show_alert({ message: __("List {0}: eng ko'pi bilan {1} % (chiqindi {2} % + qoldiq {3} %)", [r.list_nr, max.toFixed(1), flt(r.chiqindi_foiz).toFixed(1), flt(r.qoldiq_foiz).toFixed(1)]), indicator: "orange" });
				frappe.model.set_value(cdt, cdn, "mijoz_foiz", max);
			}
			if (!cint(r.mijoz_foiz_qolda)) frappe.model.set_value(cdt, cdn, "mijoz_foiz_qolda", 1);
			frappe.model.set_value(cdt, cdn, "mijoz_jami_foiz", flt(r.foydalanish_foiz) + flt(r.mijoz_foiz));
		} finally { akfa_diller.oz._rk_lock = false; }
		frappe.show_alert({ message: __("Saqlang — narx qayta hisoblanadi"), indicator: "blue" }, 4);
	},
	qoldiq_mijozga(frm, cdt, cdn) {
		if (akfa_diller.oz._rk_lock) return;
		const r = locals[cdt][cdn];
		akfa_diller.oz._rk_lock = true;
		try {
			if (cint(r.qoldiq_mijozga)) {
				frappe.model.set_value(cdt, cdn, "mijoz_foiz", Math.max(0, 100 - flt(r.foydalanish_foiz)));
				frappe.model.set_value(cdt, cdn, "mijoz_foiz_qolda", 1);
				frappe.show_alert({ message: __("List {0}: qoldiq mijozga — butun list mijozga yoziladi va ombordan chiqadi. Saqlang", [r.list_nr]), indicator: "blue" }, 5);
			} else {  // belgi olib tashlandi → avtomatik foizga qaytadi
				frappe.model.set_value(cdt, cdn, "mijoz_foiz", flt(r.mijoz_foiz_avto));
				frappe.model.set_value(cdt, cdn, "mijoz_foiz_qolda", 0);
			}
			frappe.model.set_value(cdt, cdn, "mijoz_jami_foiz", flt(r.foydalanish_foiz) + flt(r.mijoz_foiz));
		} finally { akfa_diller.oz._rk_lock = false; }
	},
	mijoz_foiz_qolda(frm, cdt, cdn) {
		if (akfa_diller.oz._rk_lock) return;
		const r = locals[cdt][cdn];
		if (!cint(r.mijoz_foiz_qolda)) {  // «Qo'lda» olib tashlandi → avto
			akfa_diller.oz._rk_lock = true;
			try {
				frappe.model.set_value(cdt, cdn, "qoldiq_mijozga", 0);
				frappe.model.set_value(cdt, cdn, "mijoz_foiz", flt(r.mijoz_foiz_avto));
				frappe.model.set_value(cdt, cdn, "mijoz_jami_foiz", flt(r.foydalanish_foiz) + flt(r.mijoz_foiz_avto));
			} finally { akfa_diller.oz._rk_lock = false; }
		}
	},
});

frappe.ui.form.on("Oyna Zakaz Pozitsiya", {
	pozitsiyalar_remove(frm) { akfa_diller.oz.after_remove(frm); },
});

frappe.ui.form.on("Oyna Zakaz List", {
	oyna_item(frm) { akfa_diller.oz.raskroy_eskirdi(frm); },
	list_eni_mm(frm) { akfa_diller.oz.raskroy_eskirdi(frm); },
	list_boyi_mm(frm) { akfa_diller.oz.raskroy_eskirdi(frm); },
	listlar_remove(frm) { akfa_diller.oz.raskroy_eskirdi(frm); },
});

frappe.ui.form.on("Oyna Zakaz Aksessuar", {
	qty(frm, cdt, cdn) { akfa_diller.oz.aks_row(frm, cdt, cdn); },
	narx(frm, cdt, cdn) { akfa_diller.oz.aks_row(frm, cdt, cdn); },
	item(frm, cdt, cdn) {
		const r = locals[cdt][cdn];
		if (!r.item) return;
		// Item Price'ni whitelisted metod orqali (menejer rolida Item Price o'qish huquqi yo'q); ro'yxat nomi sozlamadan
		frappe.xcall("akfa_diller.akfa_diller.api.oyna_zakaz.aksessuar_narx", { item: r.item }).then((narx) => {
			if (flt(narx) && !flt(r.narx)) frappe.model.set_value(cdt, cdn, "narx", flt(narx));
		});
	},
	aksessuarlar_remove(frm) { akfa_diller.oz.jami(frm); },
});

akfa_diller.oz.aks_row = function (frm, cdt, cdn) {
	const r = locals[cdt][cdn];
	frappe.model.set_value(cdt, cdn, "summa", flt(r.qty) * flt(r.narx));
	akfa_diller.oz.jami(frm);
};

// jonli jami (server validate'da qayta hisoblaydi — bu faqat ko'rsatish uchun)
akfa_diller.oz.jami = function (frm) {
	const d = frm.doc;
	const poz = (d.pozitsiyalar || []).reduce((s, r) => s + flt(r.summa), 0);
	const aks = (d.aksessuarlar || []).reduce((s, r) => s + flt(r.summa), 0);
	d.pozitsiyalar_jami = poz;
	d.aksessuar_jami = aks;
	d.jami_summa = poz + aks;
	d.qoldiq = d.jami_summa - flt(d.oldindan_tolov);
	d.pozitsiya_soni = (d.pozitsiyalar || []).length;
	d.dona_jami = (d.pozitsiyalar || []).reduce((s, r) => s + cint(r.dona), 0);
	d.jami_kvadrat = (d.pozitsiyalar || []).reduce((s, r) => s + flt(r.jami_maydon_m2), 0);
	d.ogirlik_kg = (d.pozitsiyalar || []).reduce((s, r) => s + flt(r.ogirlik_kg), 0);
	["pozitsiyalar_jami", "aksessuar_jami", "jami_summa", "qoldiq", "pozitsiya_soni", "dona_jami", "jami_kvadrat", "ogirlik_kg"].forEach((f) => frm.refresh_field(f));
};

// grid: qatorga bosish → dialog (capture-fazada, Frappe'ning row-form'ini ochirmaymiz)
akfa_diller.oz.bind_grid = function (frm) {
	const grid = frm.fields_dict.pozitsiyalar.grid;
	if (!grid || grid._oz_bound) return;
	grid._oz_bound = true;
	grid.wrapper[0].addEventListener("click", function (e) {
		const $row = $(e.target).closest(".grid-row[data-idx]");
		if (!$row.length || $(e.target).closest(".grid-row-check, .row-check, input[type=checkbox]").length) return;
		e.stopPropagation();
		e.preventDefault();
		if (frm.doc.docstatus !== 0) return;
		const idx = cint($row.attr("data-idx"));
		const row = (frm.doc.pozitsiyalar || []).find((r) => r.idx === idx);
		if (row) akfa_diller.oz.open_dialog(frm, row);
	}, true);
	grid.wrapper.find(".grid-footer .grid-add-row, .grid-footer .grid-add-multiple-rows").hide();
	grid.wrapper.on("click", ".grid-remove-rows", () => setTimeout(() => akfa_diller.oz.after_remove(frm), 300));
};

// pozitsiya o'chirilganda bog'liq teshik/kesim qatorlari ham ketsin
akfa_diller.oz.after_remove = function (frm) {
	const nrs = (frm.doc.pozitsiyalar || []).map((r) => cint(r.nr));
	let changed = false;
	["teshiklar", "kesimlar"].forEach((f) => {
		(frm.doc[f] || []).filter((r) => !nrs.includes(cint(r.pozitsiya_nr))).forEach((r) => { frappe.model.clear_doc(r.doctype, r.name); changed = true; });
		if (changed) frm.refresh_field(f);
	});
	(frm.doc.aksessuarlar || []).forEach((r) => { if (cint(r.pozitsiya_nr) && !nrs.includes(cint(r.pozitsiya_nr))) r.pozitsiya_nr = 0; });
	akfa_diller.oz.pozitsiya_ozgardi(frm);
};

// pozitsiya qo'shildi/o'zgardi/o'chdi: jami → kartalar → listlar jadvali → raskroy eskirdi
akfa_diller.oz.pozitsiya_ozgardi = function (frm) {
	akfa_diller.oz.jami(frm);
	akfa_diller.oz.render_cards(frm);
	akfa_diller.oz.listlarni_toldir(frm).then(() => akfa_diller.oz.raskroy_eskirdi(frm));
};

akfa_diller.oz.open_dialog = function (frm, row) {
	new akfa_diller.oz.PozitsiyaDialog({
		frm, row,
		on_save: () => akfa_diller.oz.pozitsiya_ozgardi(frm),
	}).show();
};

akfa_diller.oz.remove_position = function (frm, nr) {
	frappe.confirm(__("Pozitsiya №{0} o'chirilsinmi? (teshik/kesim qatorlari ham o'chadi)", [nr]), () => {
		(frm.doc.pozitsiyalar || []).filter((r) => cint(r.nr) === cint(nr)).forEach((r) => frappe.model.clear_doc(r.doctype, r.name));
		frm.refresh_field("pozitsiyalar");
		akfa_diller.oz.after_remove(frm);
		frm.dirty();
	});
};

akfa_diller.oz.duplicate_position = function (frm, nr) {
	const src = (frm.doc.pozitsiyalar || []).find((r) => cint(r.nr) === cint(nr));
	if (!src) return;
	const yangi = akfa_diller.oz.next_nr(frm);
	const row = frm.add_child("pozitsiyalar");
	Object.keys(src).forEach((k) => { if (!["name", "idx", "doctype", "parent", "parentfield", "parenttype", "nr", "__islocal", "__unsaved", "creation", "modified", "owner", "modified_by", "docstatus"].includes(k)) row[k] = src[k]; });
	row.nr = yangi;
	["teshiklar", "kesimlar"].forEach((f) => {
		(frm.doc[f] || []).filter((r) => cint(r.pozitsiya_nr) === cint(nr)).forEach((r) => {
			const c = frm.add_child(f);
			Object.keys(r).forEach((k) => { if (!["name", "idx", "doctype", "parent", "parentfield", "parenttype", "__islocal", "__unsaved", "creation", "modified", "owner", "modified_by", "docstatus"].includes(k)) c[k] = r[k]; });
			c.pozitsiya_nr = yangi;
		});
		frm.refresh_field(f);
	});
	frm.refresh_field("pozitsiyalar");
	frm.dirty();
	akfa_diller.oz.pozitsiya_ozgardi(frm);
};

akfa_diller.oz.render_cards = function (frm) {
	const $w = frm.fields_dict.chizmalar_html.$wrapper;
	const rows = frm.doc.pozitsiyalar || [];
	const edit = frm.doc.docstatus === 0;
	const head = `<div class="oz-cards__head">
		${edit ? `<button class="btn btn-primary btn-sm oz-add">+ ${__("Pozitsiya qo'shish")}</button>` : ""}
		<span class="oz-chip">${rows.length} ${__("pozitsiya")}</span>
		<span class="oz-chip">${cint(frm.doc.dona_jami)} ${__("dona")}</span>
		<span class="oz-chip">${flt(frm.doc.jami_kvadrat).toFixed(3)} m²</span>
		<span class="oz-chip">${flt(frm.doc.ogirlik_kg).toFixed(1)} kg</span>
	</div>`;
	if (!rows.length) {
		$w.html(head + `<div class="oz-empty">${__("Hali pozitsiya yo'q — «Pozitsiya qo'shish» tugmasini bosing")}</div>`);
	} else {
		$w.html(head + `<div class="oz-cards">${rows.map((r) => akfa_diller.oz.card_html(frm, r, edit)).join("")}</div>`);
	}
	$w.find(".oz-add").on("click", () => akfa_diller.oz.open_dialog(frm, null));
	$w.find("[data-act]").on("click", function () {
		const nr = cint($(this).attr("data-nr")), act = $(this).attr("data-act");
		const row = rows.find((r) => cint(r.nr) === nr);
		if (act === "edit") akfa_diller.oz.open_dialog(frm, row);
		if (act === "copy") akfa_diller.oz.duplicate_position(frm, nr);
		if (act === "del") akfa_diller.oz.remove_position(frm, nr);
	});
	if (rows.length) {
		frappe.call({
			method: "akfa_diller.akfa_diller.api.oyna_zakaz.render_zakaz_previews",
			args: { doc: frm.doc, box_w: 320, box_h: 240 },
			callback: (r) => {
				(r.message || []).forEach((x) => $w.find(`.oz-card[data-nr="${x.nr}"] .oz-card__svg`).html(x.svg));
			},
		});
	}
};

akfa_diller.oz.card_html = function (frm, r, edit) {
	const chips = [];
	const q = ["qirra_yuqori", "qirra_ong", "qirra_pastki", "qirra_chap"].filter((f) => r[f] && r[f] !== "Yo'q");
	if (r.shakl === "To'rtburchak" && q.length) chips.push(`${__("Qirra")} ×${q.length}`);
	if (r.shakl !== "To'rtburchak" && r.qirra_kontur && r.qirra_kontur !== "Yo'q") chips.push(r.qirra_kontur);
	if (cint(r.teshik_soni)) chips.push(`${__("Teshik")} ${r.teshik_soni}`);
	if (cint(r.kesim_soni)) chips.push(`${__("Kesim")} ${r.kesim_soni}`);
	if (r.matofka_rejimi && r.matofka_rejimi !== "Yo'q") chips.push(__("Matofka"));
	if (cint(r.zakalka)) chips.push("ZAKALKA");
	if (cint(r.mijoz_oynasi)) chips.push(__("mijoz oynasi"));
	if (cint(r.narx_qolda)) chips.push(__("narx qo'lda"));
	if (cint(r.taxminiy)) chips.push(`<span class="oz-tax">TAXMINIY</span>`);
	return `<div class="oz-card" data-nr="${r.nr}">
		<div class="oz-card__svg"><div class="oz-skeleton"></div></div>
		<div class="oz-card__cap"><b>№${r.nr}</b> · ${frappe.utils.escape_html(r.oyna_item || "")} · ${r.olcham_matn || ""} · ${cint(r.dona)} ${__("dona")} · ${flt(r.ogirlik_kg).toFixed(1)} kg</div>
		<div class="oz-card__chips">${chips.map((c) => `<span class="oz-chip oz-chip--sm">${c}</span>`).join("")}</div>
		<div class="oz-card__foot">
			<span class="oz-card__sum">${akfa_diller.oz.fmt(r.summa)}</span>
			${edit ? `<span class="oz-card__btns">
				<button class="btn btn-xs btn-default" data-act="edit" data-nr="${r.nr}">${__("Tahrirlash")}</button>
				<button class="btn btn-xs btn-default" data-act="copy" data-nr="${r.nr}">${__("Nusxa")}</button>
				<button class="btn btn-xs btn-default oz-danger" data-act="del" data-nr="${r.nr}">${__("O'chirish")}</button></span>` : ""}
		</div>
	</div>`;
};

akfa_diller.oz.add_buttons = function (frm) {
	const d = frm.doc;
	if (d.docstatus === 0 && !frm.is_new()) {
		frm.add_custom_button(__("Hisoblash"), () => {
			frappe.call({
				method: "akfa_diller.akfa_diller.api.oyna_konfigurator.hisobla_preview",
				args: { doc: frm.doc },
				callback: (r) => {
					const m = r.message;
					if (!m) return;
					["pozitsiya_soni", "dona_jami", "haqiqiy_kvadrat", "jami_kvadrat", "material_kvadrat", "ogirlik_kg", "oyna_jami", "qirra_jami", "burchak_jami", "teshik_jami", "kesim_jami", "matofka_jami", "zakalka_jami", "markirovka_jami", "aksessuar_jami", "pozitsiyalar_jami", "jami_summa", "qoldiq", "raskroy_holat", "raskroy_list_soni", "raskroy_bolaklar_m2", "raskroy_qoldiq_m2", "raskroy_chiqindi_m2", "raskroy_chiqindi_foiz", "raskroy_mijoz_foiz", "raskroy_foydalanish_foiz", "raskroy_chiqindi_summa"].forEach((f) => { frm.doc[f] = m[f]; frm.refresh_field(f); });
					(m.pozitsiyalar || []).forEach((p, i) => { const row = frm.doc.pozitsiyalar[i]; if (row) akfa_diller.oz.HISOB_FIELDS.forEach((f) => { if (p[f] !== undefined) row[f] = p[f]; }); });
					frm.refresh_field("pozitsiyalar");
					if ((m._xato || []).length) frappe.msgprint({ title: __("Tekshiruv"), indicator: "red", message: m._xato.join("<br>") });
					akfa_diller.oz.render_cards(frm);
					akfa_diller.oz.render_raskroy(frm);
					akfa_diller.oz.render_chiqindi_note(frm);
				},
			});
		});
	}
	if (d.docstatus === 0 && (d.pozitsiyalar || []).length) {
		frm.add_custom_button(__("Raskroy tuzish"), () => akfa_diller.oz.raskroy_tuz(frm));
	}
	if (!frm.is_new()) {
		frm.add_custom_button(__("Taklifnoma (PDF)"), () => akfa_diller.oz.pdf(frm, "taklif"), __("Chop etish"));
		frm.add_custom_button(__("Sex-talon (PDF)"), () => akfa_diller.oz.pdf(frm, "talon"), __("Chop etish"));
	}
	if (d.docstatus === 1) {
		const otish = (yangi, tasdiq) => {
			const go = () => frappe.xcall("akfa_diller.akfa_diller.api.oyna_konfigurator.holat_ozgartir", { name: d.name, yangi }).then(() => frm.reload_doc());
			return tasdiq ? frappe.confirm(tasdiq, go) : go();
		};
		if (d.holat === "Zakaz olindi") {
			frm.add_custom_button(__("Tayyor bo'ldi"), () => otish("Tayyor")).addClass("btn-primary");
			frm.add_custom_button(__("Mijozga topshirildi"), () => otish("Topshirildi", __("Zakaz mijozga topshirildi — Sales Invoice va Material Issue yaratiladi. Davom etilsinmi?")));
		} else if (d.holat === "Tayyor") {
			frm.add_custom_button(__("Mijozga topshirildi"), () => otish("Topshirildi", __("Zakaz mijozga topshirildi — Sales Invoice va Material Issue yaratiladi. Davom etilsinmi?"))).addClass("btn-primary");
			frm.add_custom_button(__("«Zakaz olindi»ga qaytarish"), () => otish("Zakaz olindi"));
		}
		if (d.sales_invoice) frm.add_custom_button(__("Sales Invoice: {0}", [d.sales_invoice]), () => frappe.set_route("Form", "Sales Invoice", d.sales_invoice), __("Hujjatlar"));
		if (d.material_issue) frm.add_custom_button(__("Material Issue: {0}", [d.material_issue]), () => frappe.set_route("Form", "Stock Entry", d.material_issue), __("Hujjatlar"));
	}
};

akfa_diller.oz.pdf = function (frm, variant) {
	if (frm.is_dirty()) return frappe.msgprint(__("Avval saqlang"));
	window.open(`/api/method/akfa_diller.akfa_diller.api.oyna_zakaz.download_pdf?name=${encodeURIComponent(frm.doc.name)}&variant=${variant}`);
};

akfa_diller.oz.headline = function (frm) {
	if (frm.doc.docstatus !== 1) return;
	const parts = [`<b>${__(frm.doc.holat)}</b>`];
	if (frm.doc.sales_invoice) parts.push(`${__("Sales Invoice")}: <a href="/app/sales-invoice/${frm.doc.sales_invoice}">${frm.doc.sales_invoice}</a>`);
	if (frm.doc.material_issue) parts.push(`${__("Material Issue")}: <a href="/app/stock-entry/${frm.doc.material_issue}">${frm.doc.material_issue}</a>`);
	frm.dashboard.set_headline(parts.join(" · "));
};

// ============================================================ RASKROY — listdan kesish rejasi

akfa_diller.oz.RASKROY_IZOH = "<b>Raskroy</b> — pozitsiyalarni ombordagi <b>list</b> (varaq) ustiga joylashtirib kesish rejasi. " +
	"Quyidagi jadvalda har oyna uchun list o'lchamini tekshiring (oxirgi ishlatilgan o'lcham o'zi tushadi), so'ng <b>«Raskroy tuzish»</b>ni bosing: " +
	"xarita, nechta list ketishi, foydalanish %, <b>chiqindi</b> (avtomatik oyna narxiga qo'shiladi) va <b>qoldiq</b> (saqlanadi, narxga kirmaydi) chiqadi.";

// listlar jadvali: pozitsiyadagi har yangi oyna uchun qator (oxirgi ishlatilgan o'lcham bilan); yetim qatorlar o'chadi
akfa_diller.oz.listlarni_toldir = function (frm) {
	const items = [];
	(frm.doc.pozitsiyalar || []).forEach((p) => { if (p.oyna_item && !cint(p.mijoz_oynasi) && !items.includes(p.oyna_item)) items.push(p.oyna_item); });
	const bor = (frm.doc.listlar || []).map((r) => r.oyna_item);
	let changed = false;
	(frm.doc.listlar || []).filter((r) => r.oyna_item && !items.includes(r.oyna_item)).forEach((r) => { frappe.model.clear_doc(r.doctype, r.name); changed = true; });
	const yangi = items.filter((it) => !bor.includes(it));
	if (!yangi.length) {
		if (changed) { frm.refresh_field("listlar"); frm.dirty(); }
		return Promise.resolve(changed);
	}
	return frappe.xcall("akfa_diller.akfa_diller.api.oyna_zakaz.oxirgi_list_olchamlari", { items: yangi }).then((o) => {
		yangi.forEach((it) => {
			const row = frm.add_child("listlar");
			row.oyna_item = it;
			row.list_eni_mm = (o[it] || [])[0] || 0;
			row.list_boyi_mm = (o[it] || [])[1] || 0;
		});
		frm.refresh_field("listlar");
		frm.dirty();
		return true;
	});
};

// pozitsiya/list o'zgardi → reja endi mos emas (server saqlashda tasdiqlaydi)
akfa_diller.oz.raskroy_eskirdi = function (frm) {
	if (frm.doc.raskroy_holat === "Dolzarb") {
		frm.doc.raskroy_holat = "Eskirgan";
		frm.refresh_field("raskroy_holat");
		frm.dirty();
	}
	akfa_diller.oz.render_raskroy(frm, true);
	akfa_diller.oz.render_chiqindi_note(frm);
};

akfa_diller.oz.raskroy_tuz = function (frm) {
	if (frm.doc.docstatus !== 0) return;
	if (!(frm.doc.pozitsiyalar || []).some((p) => p.oyna_item && !cint(p.mijoz_oynasi))) {
		return frappe.msgprint(__("Raskroy uchun oyna-pozitsiya yo'q"));
	}
	const go = () => frappe.xcall("akfa_diller.akfa_diller.api.oyna_konfigurator.raskroy_tuz", { name: frm.doc.name })
		.then((r) => { frappe.show_alert({ message: r.xabar, indicator: "green" }, 8); return frm.reload_doc(); });
	// saqlash rad etilsa (validate xatosi) raskroy tuzilmaydi va forma qayta yuklanmaydi
	akfa_diller.oz.listlarni_toldir(frm).then(() => (frm.is_new() || frm.is_dirty()) ? frm.save(undefined, (r) => { if (!(r && r.exc)) go(); }) : go());
};

akfa_diller.oz.raskroy_ochir = function (frm) {
	frappe.confirm(__("Raskroy rejasi o'chirilsinmi? Sarf yana jadval foizi bilan hisoblanadi."), () =>
		frappe.xcall("akfa_diller.akfa_diller.api.oyna_konfigurator.raskroy_ochir", { name: frm.doc.name }).then(() => frm.reload_doc()));
};

akfa_diller.oz.render_raskroy = function (frm, faqat_bosh) {
	const f = frm.fields_dict.raskroy_html;
	if (!f) return;
	if (frm.fields_dict.raskroy_izoh_html && !frm._oz_rk_izoh) { frm._oz_rk_izoh = true; frm.fields_dict.raskroy_izoh_html.$wrapper.html(`<div class="oz-hint">${akfa_diller.oz.RASKROY_IZOH}</div>`); }
	const d = frm.doc, $w = f.$wrapper;
	const holat = d.raskroy_holat || "Yo'q";
	const edit = d.docstatus === 0;
	const pill = { "Yo'q": "yoq", "Dolzarb": "dolzarb", "Eskirgan": "eskirgan" }[holat] || "yoq";
	const chips = [];
	if (holat !== "Yo'q") {
		chips.push(`${cint(d.raskroy_list_soni)} ${__("list")}`);
		chips.push(`${__("bo'laklar")} ${flt(d.raskroy_bolaklar_m2).toFixed(2)} m²`);
		chips.push(`${__("qoldiq")} ${flt(d.raskroy_qoldiq_m2).toFixed(2)} m²`);
		chips.push(`${__("chiqindi")} ${flt(d.raskroy_chiqindi_m2).toFixed(2)} m² (${flt(d.raskroy_chiqindi_foiz).toFixed(1)} % list)`);
		chips.push(`${__("bo'laklar")} ${flt(d.raskroy_foydalanish_foiz).toFixed(1)} % list`);
		chips.push(`<b>${__("mijozga")} ${flt(d.raskroy_mijoz_foiz).toFixed(1)} % = ${akfa_diller.oz.fmt(d.raskroy_chiqindi_summa)}</b>`);
	}
	const olchamsiz = (d.listlar || []).filter((r) => r.oyna_item && (!cint(r.list_eni_mm) || !cint(r.list_boyi_mm))).map((r) => r.oyna_item);
	const head = `<div class="oz-rk__head">
		${edit ? `<button class="btn btn-primary btn-sm oz-rk-tuz">${holat === "Yo'q" ? __("Raskroy tuzish") : __("Raskroy qayta tuzish")}</button>` : ""}
		<span class="oz-pill oz-pill--${pill}">${__("Raskroy")}: ${__(holat)}</span>
		${chips.map((c) => `<span class="oz-chip">${c}</span>`).join("")}
		${edit && holat !== "Yo'q" ? `<button class="btn btn-default btn-xs oz-rk-ochir">${__("Rejani o'chirish")}</button>` : ""}
		${olchamsiz.length ? `<span class="oz-rk__xato">${__("List o'lchami kiritilmagan")}: ${olchamsiz.join(", ")}</span>` : ""}
	</div>`;
	let body = "";
	if (holat !== "Yo'q") {
		const satr = (d.raskroy_reja || []).map((r) =>
			`<tr><td><b>${__("LIST")} ${r.list_nr}</b> · ${frappe.utils.escape_html(r.oyna_item || "")} ${r.olcham || ""}</td>
			<td><span class="oz-rk-seg oz-rk-seg--b" style="width:${flt(r.foydalanish_foiz)}%"></span><span class="oz-rk-seg oz-rk-seg--c" style="width:${flt(r.chiqindi_foiz)}%"></span><span class="oz-rk-seg oz-rk-seg--q" style="width:${flt(r.qoldiq_foiz)}%"></span></td>
			<td>${__("bo'laklar")} ${flt(r.foydalanish_foiz).toFixed(1)} % · ${__("chiqindi")} ${flt(r.chiqindi_foiz).toFixed(1)} % · ${__("qoldiq")} ${flt(r.qoldiq_foiz).toFixed(1)} %</td>
			<td class="oz-rk-mijoz">→ ${__("qo'shimcha")} <b>${flt(r.mijoz_foiz).toFixed(1)} %</b>${cint(r.qoldiq_mijozga) ? ` <span class="oz-chip oz-chip--sm">${__("qoldiq mijozga")}</span>` : cint(r.mijoz_foiz_qolda) ? ` <span class="oz-chip oz-chip--sm">${__("qo'lda")}</span>` : ""} = ${akfa_diller.oz.fmt(r.mijoz_summa)} · ${__("mijoz jami")} <b>${flt(r.mijoz_jami_foiz).toFixed(1)} %</b></td></tr>`).join("");
		if (satr) body += `<table class="oz-rk-tbl">${satr}</table><div class="oz-hint">${__("Bo'laklar (oyna narxi) mijozga har doim yoziladi; «Mijozga qo'shimcha %» = shu list ustiga qo'shiladigan foiz (default chiqindi). Har list uchun «Listlar rejasi» jadvalida o'zgartiring (chegara: chiqindi + qoldiq) yoki «Qoldiq mijozga»ni belgilang, so'ng saqlang.")}</div>`;
	}
	if (holat === "Yo'q") {
		body = (d.pozitsiyalar || []).length ? `<div class="oz-empty">${__("Raskroy hali tuzilmagan — listlar jadvalini tekshirib «Raskroy tuzish»ni bosing")}</div>` : "";
	} else {
		const rows = d.raskroy_reja || [];
		body = `<div class="oz-list-cards">${rows.map((r) => `<div class="oz-list-card" data-nr="${r.list_nr}">
			<div class="oz-list-card__cap"><b>${__("LIST")} ${r.list_nr}/${rows.length}</b> · ${frappe.utils.escape_html(r.oyna_item || "")} · ${r.olcham} mm
				<span class="oz-chip oz-chip--sm">${__("foydalanish")} ${flt(r.foydalanish_foiz).toFixed(1)} %</span>
				<span class="oz-chip oz-chip--sm">${r.bolaklar_matn || ""}</span></div>
			<div class="oz-list-card__svg"><div class="oz-skeleton"></div></div>
			<div class="oz-list-card__qoldiq">${__("Qoldiq")}: ${r.qoldiqlar_matn || "—"} · ${__("chiqindi")} ${flt(r.chiqindi_m2).toFixed(2)} m²</div>
		</div>`).join("")}</div>`;
	}
	$w.html(head + body);
	$w.find(".oz-rk-tuz").on("click", () => akfa_diller.oz.raskroy_tuz(frm));
	$w.find(".oz-rk-ochir").on("click", () => akfa_diller.oz.raskroy_ochir(frm));
	if (holat !== "Yo'q" && !frm.is_new() && !faqat_bosh) {
		frappe.call({
			method: "akfa_diller.akfa_diller.api.oyna_zakaz.render_raskroy_svgs",
			args: { name: d.name, box_w: 560, box_h: 390 },
			callback: (r) => { (r.message || []).forEach((x) => $w.find(`.oz-list-card[data-nr="${x.n}"] .oz-list-card__svg`).html(x.svg)); },
		});
	} else if (faqat_bosh) {
		$w.find(".oz-list-card__svg").html(`<div class="oz-empty">${__("Reja eskirgan — qayta tuzing")}</div>`);
	}
};

akfa_diller.oz.render_chiqindi_note = function (frm) {
	const f = frm.fields_dict.chiqindi_izoh_html;
	if (!f) return;
	const d = frm.doc, holat = d.raskroy_holat || "Yo'q";
	const oyna_bor = (d.pozitsiyalar || []).some((p) => p.oyna_item && !cint(p.mijoz_oynasi));
	let html = "";
	if (!oyna_bor) html = "";
	else if (holat === "Dolzarb") html = `<div class="oz-chiqindi-note">${__("List sarfi (raskroy)")}: ${__("chiqindi")} ${flt(d.raskroy_chiqindi_foiz).toFixed(1)} % → ${__("mijozga yozildi")} <b>${flt(d.raskroy_mijoz_foiz).toFixed(1)} %</b> = <b>${akfa_diller.oz.fmt(d.raskroy_chiqindi_summa)}</b> (oyna narxi ichida) · ${__("qoldiq")} ${flt(d.raskroy_qoldiq_m2).toFixed(2)} m² ${__("omborda qoladi")}</div>`;
	else if (holat === "Eskirgan") html = `<div class="oz-chiqindi-note oz-chiqindi-note--eskirgan">${__("Raskroy rejasi eskirgan — «Raskroy tuzish»ni qayta bosing; hozircha sarf jadval foizi bilan, chiqindi mijozga qo'shilmagan")}</div>`;
	else html = `<div class="oz-chiqindi-note oz-chiqindi-note--yoq">${__("Raskroy tuzilmagan — sarf jadval foizi bilan (taxminiy), haqiqiy chiqindi mijozga hisoblanmagan")}</div>`;
	f.$wrapper.html(html);
};
