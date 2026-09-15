// Oyna Zakaz — pozitsiya tahrirlash dialogi (jonli SVG-preview + narx + tekshiruv).
// Server (api/oyna_zakaz.preview_pozitsiya) — yagona hisob/chizma manbai.

frappe.provide("akfa_diller.oz");

akfa_diller.oz.QIRRA = "Yo'q\nPritupka\nShlifovka\nPolirovka\nFaska";
akfa_diller.oz.BURCHAK = "Yo'q\nRadius\nKesik";
akfa_diller.oz.POZ_FIELDS = [
	"shakl", "oyna_item", "eni_mm", "boyi_mm", "diametr_mm", "perimetr_mm", "chizma", "dona", "korinish", "mijoz_oynasi", "izoh",
	"qirra_yuqori", "qirra_ong", "qirra_pastki", "qirra_chap", "qirra_kontur", "faska_kengligi_mm",
	"burchak_yc", "burchak_yc_mm", "burchak_yo", "burchak_yo_mm", "burchak_po", "burchak_po_mm", "burchak_pc", "burchak_pc_mm",
	"matofka_rejimi", "boyash", "paket", "paket_oyna_item", "zakalka", "markirovka", "markirovka_matn",
	"narx_qolda", "narx_izoh", "oyna_summa", "qirra_summa", "burchak_summa", "teshik_summa", "kesim_summa", "matofka_summa", "zakalka_summa", "markirovka_summa",
	"reska_summa", "boyash_summa", "paket_summa",
];
akfa_diller.oz.HISOB_FIELDS = [
	"hisob_eni_mm", "hisob_boyi_mm", "haqiqiy_maydon_m2", "hisob_maydon_m2", "jami_maydon_m2", "perimetr_m", "ogirlik_kg",
	"teshik_soni", "kesim_soni", "olcham_matn", "xulosa", "tavsif", "ishlov_tartibi", "oyna_tarif", "summa", "taxminiy",
	"qalinlik_mm", "rang", "eni_mm", "boyi_mm", "chiqindi_foiz", "chiqindi_summa",
	"oyna_summa", "qirra_summa", "burchak_summa", "teshik_summa", "kesim_summa", "matofka_summa", "zakalka_summa", "markirovka_summa",
	"reska_summa", "boyash_summa", "paket_summa",
];
// Qirra/burchak tanlovlari — Narx Jadvalidagi «Ishlov turlari»dan (Property Setter → meta)
akfa_diller.oz.opts = function (fn, fallback, doctype) {
	const m = frappe.get_meta(doctype || "Oyna Zakaz Pozitsiya");
	const f = m && (m.fields || []).find((x) => x.fieldname === fn);
	return (f && f.options) || fallback;
};
akfa_diller.oz.yashirin = function (fn, doctype) {
	const m = frappe.get_meta(doctype || "Oyna Zakaz Pozitsiya");
	const f = m && (m.fields || []).find((x) => x.fieldname === fn);
	return !!(f && f.hidden);
};
akfa_diller.oz.KESIM_DEFAULT = { Fortochka: [300, 300], Qulf: [40, 80], Rozetka: [70, 70], Boshqa: [100, 100] };

akfa_diller.oz.fmt = function (v) {
	const n = Math.round(flt(v));
	return (n < 0 ? "−" : "") + String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " so'm";
};

akfa_diller.oz.PozitsiyaDialog = class {
	constructor({ frm, row, on_save }) {
		this.frm = frm;
		this.row = row || null;
		this.on_save = on_save;
		this._seq = 0;
		this._timer = null;
		this.last = null;
		this.nr = row ? row.nr : akfa_diller.oz.next_nr(frm);
	}

	tesh_rows() {
		return (this.frm.doc.teshiklar || []).filter((r) => cint(r.pozitsiya_nr) === cint(this.nr));
	}
	kes_rows() {
		return (this.frm.doc.kesimlar || []).filter((r) => cint(r.pozitsiya_nr) === cint(this.nr));
	}

	fields() {
		const r = this.row || {};
		const QIRRA = akfa_diller.oz.opts("qirra_yuqori", "Yo'q\nPritupka\nShlifovka\nPolirovka\nFaska");
		const BURCHAK = akfa_diller.oz.opts("burchak_yc", "Yo'q\nRadius\nKesik");
		const KESIM = akfa_diller.oz.opts("tur", "Fortochka\nQulf\nRozetka\nBoshqa", "Oyna Zakaz Kesim");
		// Narx Jadvalida tur bo'lmasa (masalan burchak ishlovi sexda yo'q) — bo'lim umuman ko'rsatilmaydi
		const BURCHAK_BOR = BURCHAK.split("\n").some((o) => o && o !== "Yo'q");
		const FASKA_BOR = QIRRA.split("\n").some((o) => /faska/i.test(o));
		// Markirovka — Narx Jadvalida qatori bo'lmasa server uni hidden qilib qo'yadi
		const MARKIROVKA_BOR = !akfa_diller.oz.yashirin("markirovka");
		const oldingi = akfa_diller.oz.last_position(this.frm);
		const v = (f, d) => (r[f] !== undefined && r[f] !== null && r[f] !== "" ? r[f] : d);
		const sel = (fn, label, options, d, extra) =>
			Object.assign({ fieldname: fn, fieldtype: "Select", label, options, default: v(fn, d) }, extra || {});
		const int = (fn, label, extra) => Object.assign({ fieldname: fn, fieldtype: "Int", label, default: v(fn, null) }, extra || {});
		const cur = (fn, label) => ({ fieldname: fn, fieldtype: "Currency", label, default: v(fn, 0), depends_on: "eval:doc.narx_qolda" });
		const ch = (fn, label, extra) => Object.assign({ fieldname: fn, fieldtype: "Check", label, default: v(fn, 0) }, extra || {});
		return [
			{ fieldtype: "Section Break", label: __("O'lcham"), fieldname: "sec_olcham" },
			sel("shakl", __("Shakl"), "To'rtburchak\nDoira\nErkin shakl", "To'rtburchak", { reqd: 1 }),
			{
				fieldname: "oyna_item", fieldtype: "Link", options: "Item", label: __("Oyna"), reqd: 1,
				default: v("oyna_item", oldingi ? oldingi.oyna_item : null),
				get_query: () => ({ query: "akfa_diller.akfa_diller.api.oyna_zakaz.oyna_items_query" }),
			},
			{ fieldname: "oyna_info", fieldtype: "HTML", options: "" },
			int("dona", __("Dona"), { reqd: 1, default: v("dona", 1) }),
			sel("korinish", __("Ko'rinish"), "Old tomondan\nOrqa tomondan", oldingi ? oldingi.korinish || "Old tomondan" : "Old tomondan"),
			ch("mijoz_oynasi", __("Mijozning o'z oynasi (faqat ishlovlar)")),
			{ fieldtype: "Column Break" },
			int("eni_mm", __("Eni (mm)"), { depends_on: "eval:doc.shakl!='Doira'" }),
			int("boyi_mm", __("Bo'yi (mm)"), { depends_on: "eval:doc.shakl!='Doira'" }),
			int("diametr_mm", __("Diametr (mm)"), { depends_on: "eval:doc.shakl=='Doira'" }),
			int("perimetr_mm", __("Perimetr (mm)"), { depends_on: "eval:doc.shakl=='Erkin shakl'", description: __("Bo'sh bo'lsa 2×(eni+bo'yi)") }),
			{ fieldname: "chizma", fieldtype: "Attach", label: __("Chizma / shablon (rasm yoki PDF)"), default: v("chizma", ""), depends_on: "eval:doc.shakl=='Erkin shakl'",
			  description: __("Mijoz shablonining surati yoki o'lchamli eskiz — ustaga ma'lumot uchun (narxga ta'sir qilmaydi)") },
			{ fieldname: "izoh", fieldtype: "Small Text", label: __("Izoh"), default: v("izoh", ""), description: __("Erkin shakl uchun shablon/chizma izohi shart") },

			{ fieldtype: "Section Break", label: BURCHAK_BOR ? __("Qirra va burchak") : __("Qirra (kromka)"), fieldname: "sec_qirra" },
			{ fieldname: "barcha_tomonlar", fieldtype: "Select", label: __("Barcha tomonlar"), options: QIRRA, default: "Yo'q", depends_on: "eval:doc.shakl=='To\\'rtburchak'" },
			{ fieldname: "qollash_btn", fieldtype: "Button", label: __("4 tomonga qo'llash"), depends_on: "eval:doc.shakl=='To\\'rtburchak'" },
			sel("qirra_yuqori", __("① Yuqori"), QIRRA, "Yo'q", { depends_on: "eval:doc.shakl=='To\\'rtburchak'" }),
			sel("qirra_chap", __("④ Chap"), QIRRA, "Yo'q", { depends_on: "eval:doc.shakl=='To\\'rtburchak'" }),
			sel("qirra_ong", __("② O'ng"), QIRRA, "Yo'q", { depends_on: "eval:doc.shakl=='To\\'rtburchak'" }),
			sel("qirra_pastki", __("③ Pastki"), QIRRA, "Yo'q", { depends_on: "eval:doc.shakl=='To\\'rtburchak'" }),
			sel("qirra_kontur", __("Kontur qirrasi"), QIRRA, "Yo'q", { depends_on: "eval:doc.shakl!='To\\'rtburchak'" }),
			...(FASKA_BOR ? [int("faska_kengligi_mm", __("Faska kengligi (mm)"))] : []),
			...(BURCHAK_BOR ? [
			{ fieldtype: "Column Break" },
			{ fieldtype: "HTML", options: `<div class="oz-hint">${__("Burchaklar (soat yo'nalishi): yuqori-chap, yuqori-o'ng, pastki-o'ng, pastki-chap")}</div>` },
			sel("burchak_yc", __("Yuqori-chap"), BURCHAK, "Yo'q", { depends_on: "eval:doc.shakl=='To\\'rtburchak'" }),
			int("burchak_yc_mm", __("R / kesik (mm)"), { depends_on: "eval:doc.burchak_yc && doc.burchak_yc!='Yo\\'q'" }),
			sel("burchak_yo", __("Yuqori-o'ng"), BURCHAK, "Yo'q", { depends_on: "eval:doc.shakl=='To\\'rtburchak'" }),
			int("burchak_yo_mm", __("R / kesik (mm)"), { depends_on: "eval:doc.burchak_yo && doc.burchak_yo!='Yo\\'q'" }),
			sel("burchak_po", __("Pastki-o'ng"), BURCHAK, "Yo'q", { depends_on: "eval:doc.shakl=='To\\'rtburchak'" }),
			int("burchak_po_mm", __("R / kesik (mm)"), { depends_on: "eval:doc.burchak_po && doc.burchak_po!='Yo\\'q'" }),
			sel("burchak_pc", __("Pastki-chap"), BURCHAK, "Yo'q", { depends_on: "eval:doc.shakl=='To\\'rtburchak'" }),
			int("burchak_pc_mm", __("R / kesik (mm)"), { depends_on: "eval:doc.burchak_pc && doc.burchak_pc!='Yo\\'q'" }),
			] : []),

			{ fieldtype: "Section Break", label: __("Teshik va kesim"), fieldname: "sec_teshik" },
			{ fieldtype: "HTML", options: `<div class="oz-hint">${__("Koordinatalar: X — chap qirradan, Y — pastki qirradan (mm), teshik MARKAZI / kesimning chap-pastki burchagi. Koordinatasiz qator = joyi chizmada emas (soni bo'yicha narxlanadi).")}</div>` },
			{
				fieldname: "teshiklar", fieldtype: "Table", label: __("Teshiklar"), in_place_edit: true, cannot_add_rows: false,
				data: this.tesh_rows().map((t) => ({ diametr_mm: t.diametr_mm, x_mm: t.x_mm, y_mm: t.y_mm, soni: t.soni || 1, izoh: t.izoh })),
				fields: [
					{ fieldname: "diametr_mm", fieldtype: "Int", label: __("Ø (mm)"), in_list_view: 1, columns: 2, reqd: 1 },
					{ fieldname: "x_mm", fieldtype: "Int", label: __("X (mm)"), in_list_view: 1, columns: 2 },
					{ fieldname: "y_mm", fieldtype: "Int", label: __("Y (mm)"), in_list_view: 1, columns: 2 },
					{ fieldname: "soni", fieldtype: "Int", label: __("Soni"), in_list_view: 1, columns: 1, default: 1 },
					{ fieldname: "izoh", fieldtype: "Data", label: __("Izoh"), in_list_view: 1, columns: 3 },
				],
			},
			{ fieldname: "kozgu_x_btn", fieldtype: "Button", label: __("Ko'zgu X (o'ngga nusxa)") },
			{ fieldname: "kozgu_y_btn", fieldtype: "Button", label: __("Ko'zgu Y (yuqoriga nusxa)") },
			{ fieldname: "tort_burchak_btn", fieldtype: "Button", label: __("4 burchakka teshik") },
			{ fieldtype: "Section Break" },
			{
				fieldname: "kesimlar", fieldtype: "Table", label: __("Kesimlar (fortochka, qulf, rozetka…)"), in_place_edit: true, cannot_add_rows: false,
				data: this.kes_rows().map((x) => ({ tur: x.tur, eni_mm: x.eni_mm, boyi_mm: x.boyi_mm, x_mm: x.x_mm, y_mm: x.y_mm, ichki_radius_mm: x.ichki_radius_mm, soni: x.soni || 1, izoh: x.izoh })),
				fields: [
					{ fieldname: "tur", fieldtype: "Select", label: __("Turi"), options: KESIM, in_list_view: 1, columns: 2, reqd: 1, default: KESIM.split("\n")[0] },
					{ fieldname: "eni_mm", fieldtype: "Int", label: __("Eni"), in_list_view: 1, columns: 1, reqd: 1 },
					{ fieldname: "boyi_mm", fieldtype: "Int", label: __("Bo'yi"), in_list_view: 1, columns: 1, reqd: 1 },
					{ fieldname: "x_mm", fieldtype: "Int", label: __("X"), in_list_view: 1, columns: 1 },
					{ fieldname: "y_mm", fieldtype: "Int", label: __("Y"), in_list_view: 1, columns: 1 },
					{ fieldname: "ichki_radius_mm", fieldtype: "Int", label: __("Ichki R"), in_list_view: 1, columns: 1 },
					{ fieldname: "soni", fieldtype: "Int", label: __("Soni"), in_list_view: 1, columns: 1, default: 1 },
					{ fieldname: "izoh", fieldtype: "Data", label: __("Izoh"), in_list_view: 1, columns: 2 },
				],
			},

			{ fieldtype: "Section Break", label: __("Ishlovlar"), fieldname: "sec_ishlov" },
			// Matofkaning uchala turi ham BUTUN oyna maydoniga hisoblanadi (sex izohi 07.09.2026):
			// polosa — lenta ostidagi joy shaffof qoladi, gul — naqsh/logo shaffof qoladi.
			sel("matofka_rejimi", __("Matofka"), "Yo'q\nTo'liq\nPolosa\nGul", "Yo'q"),
			{ fieldtype: "HTML", options: `<div class="oz-hint">${__("To'liq (splashnoy) — butun oyna matoviy · Polosa — lenta yopishtirilgan joy shaffof · Gul/logo — naqsh shaffof. Uchalasi ham butun oyna maydoniga hisoblanadi.")}</div>` },
			ch("boyash", __("Oyna bo'yash")),
			{ fieldtype: "Column Break" },
			ch("paket", __("Paket (2 qavat oyna)")),
			{
				fieldname: "paket_oyna_item", fieldtype: "Link", options: "Item", label: __("2-oyna (paket)"),
				default: v("paket_oyna_item", null),
				depends_on: "eval:doc.paket",
				get_query: () => ({ query: "akfa_diller.akfa_diller.api.oyna_zakaz.oyna_items_query" }),
				description: __("Paketning ikkinchi qavati — boshqa rang yoki qalinlik bo'lishi mumkin"),
			},
			{ fieldtype: "Section Break" },
			ch("zakalka", __("Zakalka (200×300 … 3000×2000 mm)")),
			...(MARKIROVKA_BOR ? [
				ch("markirovka", __("Markirovka / logo / yozuv")),
				{ fieldname: "markirovka_matn", fieldtype: "Data", label: __("Markirovka matni (nima, qayerda, o'lchami)"), default: v("markirovka_matn", ""), depends_on: "eval:doc.markirovka" },
			] : []),
			{ fieldtype: "HTML", options: `<div class="oz-hint">${__("Aksessuarlar asosiy formadagi «Aksessuarlar» jadvalida (pozitsiya № bilan) kiritiladi.")}</div>` },

			{ fieldtype: "Section Break", label: __("Narx"), fieldname: "sec_narx" },
			{ fieldname: "narx_html", fieldtype: "HTML", options: "" },
			ch("narx_qolda", __("Narx qo'lda (kategoriya-summalarni o'zim kiritaman)")),
			// mandatory_depends_on ISHLATILMAYDI: Frappe dialog har refresh_dependency'da bunday maydonni qayta chizib, yozilayotgan matnni o'chirib yuboradi (2026-09-05); majburiylik saqlashda tekshiriladi
			{ fieldname: "narx_izoh", fieldtype: "Data", label: __("Sabab (majburiy)"), default: v("narx_izoh", ""), depends_on: "eval:doc.narx_qolda", description: __("Nega kelishilgan narx — taklifnomada ko'rinadi") },
			{ fieldtype: "Column Break" },
			cur("oyna_summa", __("Oyna")), cur("qirra_summa", __("Qirra ishlovi")), cur("burchak_summa", __("Burchak ishlovi")),
			cur("teshik_summa", __("Teshiklar")), cur("kesim_summa", __("Kesimlar")), cur("matofka_summa", __("Matofka")), cur("zakalka_summa", __("Zakalka")),
			cur("markirovka_summa", __("Markirovka")), cur("reska_summa", __("Reska (kesish)")),
			cur("boyash_summa", __("Bo'yash")), cur("paket_summa", __("Paket yig'ish")),
		];
	}

	show() {
		const me = this;
		this.dialog = new frappe.ui.Dialog({
			title: this.row ? __("Pozitsiya №{0}", [this.nr]) : __("Yangi pozitsiya №{0}", [this.nr]),
			size: "extra-large",
			fields: this.fields(),
			primary_action_label: __("Saqlash"),
			primary_action: () => me.save(),
			secondary_action_label: __("Yopish"),
			secondary_action: () => me.close(),
		});
		const d = this.dialog;
		d.$wrapper.addClass("oz-dialog");
		d.$wrapper.find(".modal-dialog").css("max-width", "1320px");
		this.$preview = $(`<div class="oz-preview">
			<div class="oz-preview__svg"><div class="oz-preview__empty text-muted">${__("O'lcham kiriting…")}</div></div>
			<div class="oz-preview__hisob"></div>
			<div class="oz-preview__xato"></div>
			<div class="oz-preview__narx"></div>
		</div>`).appendTo(d.$wrapper.find(".modal-body"));

		// tugmalar
		d.fields_dict.qollash_btn.$input.on("click", () => {
			const t = d.get_value("barcha_tomonlar") || "Yo'q";
			["qirra_yuqori", "qirra_ong", "qirra_pastki", "qirra_chap"].forEach((f) => d.set_value(f, t));
		});
		d.fields_dict.kozgu_x_btn.$input.on("click", () => me.kozgu("x"));
		d.fields_dict.kozgu_y_btn.$input.on("click", () => me.kozgu("y"));
		d.fields_dict.tort_burchak_btn.$input.on("click", () => me.tort_burchak());
		// kesim turi → default o'lcham
		d.$wrapper.on("change", '[data-fieldname="kesimlar"] select', function () {
			const tur = $(this).val();
			const def = (akfa_diller.oz._kesim_turlari || akfa_diller.oz.KESIM_DEFAULT)[tur];
			const $row = $(this).closest(".grid-row");
			if (def) {
				const $e = $row.find('[data-fieldname="eni_mm"] input'), $b = $row.find('[data-fieldname="boyi_mm"] input');
				if ($e.length && !$e.val()) $e.val(def[0]).trigger("change");
				if ($b.length && !$b.val()) $b.val(def[1]).trigger("change");
			}
		});
		// har qanday o'zgarish → preview
		d.$wrapper.on("change input", "input, select, textarea", () => me.schedule_preview());
		d.$wrapper.on("click", ".grid-remove-rows, .grid-add-row, .btn-open-row, .grid-delete-row", () => me.schedule_preview(600));
		d.$wrapper.on("keydown", (e) => {
			if (e.ctrlKey && e.key === "Enter") { e.preventDefault(); me.save(); }
		});
		// Frappe control-darajasidagi o'zgarish (set_value, Link tanlash, Select) → preview
		Object.values(d.fields_dict).forEach((f) => {
			if (["Table", "Button", "HTML", "Section Break", "Column Break"].includes(f.df.fieldtype)) return;
			const prev = f.df.onchange;
			f.df.onchange = () => { if (prev) prev(); me._dirty = true; me.schedule_preview(); };
		});
		const prev_oyna = d.fields_dict.oyna_item.df.onchange;
		d.fields_dict.oyna_item.df.onchange = () => { me.oyna_info(); if (prev_oyna) prev_oyna(); };
		d.show();
		akfa_diller.oz.load_kesim_turlari();
		this.oyna_info();
		this.schedule_preview(50);
	}

	close() {
		if (this._dirty) {
			frappe.confirm(__("O'zgarishlar saqlanmaydi. Yopilsinmi?"), () => this.dialog.hide());
		} else {
			this.dialog.hide();
		}
	}

	oyna_info() {
		const item = this.dialog.get_value("oyna_item");
		const $w = this.dialog.fields_dict.oyna_info.$wrapper;
		if (!item) return $w.html("");
		akfa_diller.oz.load_variants().then((vs) => {
			const v = vs.find((x) => x.name === item);
			$w.html(v ? `<div class="oz-hint">${__("Qalinlik")}: <b>${v.qalinlik || "?"} mm</b> · ${__("Rang")}: <b>${v.rang || "-"}</b>${v.turi && v.turi !== "Oddiy" ? ` · ${__("Turi")}: <b>${v.turi}</b>` : ""}</div>`
				: `<div class="oz-hint text-danger">${__("Bu item uchun qalinlik kiritilmagan (Item → Oyna qalinligi)")}</div>`);
		});
	}

	table_data(fn) {
		const g = this.dialog.fields_dict[fn];
		if (!g || !g.grid) return [];
		return (g.grid.get_data() || []).map((r) => Object.assign({}, r)).filter((r) => Object.keys(r).length);
	}

	collect() {
		const vals = this.dialog.get_values(true) || {};
		const p = { nr: this.nr };
		// bo'shatilgan maydon ham yuborilsin (get_values bo'sh qiymatni tashlab yuboradi → eski qiymat qolib ketardi)
		const SON = ["Int", "Float", "Currency", "Percent", "Check"];
		akfa_diller.oz.POZ_FIELDS.forEach((f) => {
			const c = this.dialog.fields_dict[f];
			if (!c) return;
			let v = vals[f];
			if (v === undefined || v === null) v = SON.includes(c.df.fieldtype) ? 0 : "";
			p[f] = v;
		});
		const teshiklar = this.table_data("teshiklar").filter((t) => cint(t.diametr_mm));
		const kesimlar = this.table_data("kesimlar").filter((k) => cint(k.eni_mm) && cint(k.boyi_mm));
		return { p, teshiklar, kesimlar };
	}

	kozgu(axis) {
		const { p, teshiklar } = this.collect();
		const W = cint(p.shakl === "Doira" ? p.diametr_mm : p.eni_mm), H = cint(p.shakl === "Doira" ? p.diametr_mm : p.boyi_mm);
		if (!W || !H) return frappe.show_alert({ message: __("Avval o'lcham kiriting"), indicator: "orange" });
		const g = this.dialog.fields_dict.teshiklar.grid;
		const bor = (x, y) => teshiklar.some((t) => cint(t.x_mm) === x && cint(t.y_mm) === y);
		const koordli = teshiklar.filter((t) => cint(t.x_mm) && cint(t.y_mm));
		const yangi = koordli.map((t) => (axis === "x"
			? { diametr_mm: t.diametr_mm, x_mm: W - cint(t.x_mm), y_mm: cint(t.y_mm), soni: t.soni || 1, izoh: t.izoh }
			: { diametr_mm: t.diametr_mm, x_mm: cint(t.x_mm), y_mm: H - cint(t.y_mm), soni: t.soni || 1, izoh: t.izoh }))
			.filter((n) => !bor(n.x_mm, n.y_mm));  // ikkinchi bosishda dublikat chiqmaydi
		if (!yangi.length) return frappe.show_alert({ message: __("Ko'zgu-nusxa uchun yangi teshik yo'q"), indicator: "orange" });
		g.df.data = teshiklar.concat(yangi);
		g.refresh();
		this.schedule_preview(50);
	}

	tort_burchak() {
		const me = this;
		const { p, teshiklar } = this.collect();
		const W = cint(p.eni_mm), H = cint(p.boyi_mm);
		if (!W || !H) return frappe.show_alert({ message: __("Avval o'lcham kiriting"), indicator: "orange" });
		frappe.prompt([
			{ fieldname: "d", fieldtype: "Int", label: __("Ø (mm)"), default: 12, reqd: 1 },
			{ fieldname: "off", fieldtype: "Int", label: __("Qirradan masofa (markazgacha, mm)"), default: 60, reqd: 1 },
		], (v) => {
			const g = me.dialog.fields_dict.teshiklar.grid;
			g.df.data = teshiklar.concat([[v.off, v.off], [W - v.off, v.off], [v.off, H - v.off], [W - v.off, H - v.off]].map(([x, y]) => ({ diametr_mm: v.d, x_mm: x, y_mm: y, soni: 1 })));
			g.refresh();
			me.schedule_preview(50);
		}, __("4 burchakka teshik"));
	}

	schedule_preview(ms) {
		this._dirty = true;
		clearTimeout(this._timer);
		this._timer = setTimeout(() => this.preview(), ms || 350);
	}

	preview() {
		const me = this;
		const seq = ++this._seq;
		const { p, teshiklar, kesimlar } = this.collect();
		this.$preview.addClass("oz-preview--busy");
		frappe.call({
			method: "akfa_diller.akfa_diller.api.oyna_zakaz.preview_pozitsiya",
			args: { pozitsiya: p, teshiklar, kesimlar, opts: { box_w: 400, box_h: 310, prefix: "dlg", standart_rejimi: me.frm.doc.standart_rejimi } },
			callback: (r) => {
				if (seq !== me._seq) return;
				me.$preview.removeClass("oz-preview--busy");
				if (!r.message) return;
				me.last = r.message;
				me.render_preview(r.message);
			},
			error: () => me.$preview.removeClass("oz-preview--busy"),
		});
	}

	render_preview(m) {
		const $p = this.$preview;
		$p.find(".oz-preview__svg").html(m.svg);
		const h = m.hisob || {};
		$p.find(".oz-preview__hisob").html(`
			<span class="oz-chip">${h.olcham_matn || ""}</span>
			<span class="oz-chip">${flt(h.hisob_maydon_m2).toFixed(4)} m² <small>(${__("hisob")})</small></span>
			<span class="oz-chip">${flt(h.jami_maydon_m2).toFixed(3)} m² <small>×${cint(this.dialog.get_value("dona")) || 1}</small></span>
			<span class="oz-chip">${flt(h.ogirlik_kg).toFixed(1)} kg</span>
			${h.xulosa ? `<span class="oz-chip oz-chip--muted">${h.xulosa}</span>` : ""}`);
		const xato = (m.xatolar || []).filter((x) => x.daraja === "xato");
		const std = (m.xatolar || []).filter((x) => x.daraja !== "xato");
		$p.find(".oz-preview__xato").html(
			xato.map((x) => `<div class="oz-alert oz-alert--xato">${x.matn}</div>`).join("") +
			std.map((x) => `<div class="oz-alert oz-alert--ogoh">${x.matn}</div>`).join(""));
		const q = (m.narx && m.narx.qatorlar) || [];
		let rows = q.map((r) => `<tr><td>${r[1]}</td><td class="text-muted">${r[2]}</td><td class="r">${akfa_diller.oz.fmt(r[3])}</td><td class="r">${akfa_diller.oz.fmt(r[4])}</td></tr>`).join("");
		const qolda = cint(this.dialog.get_value("narx_qolda"));
		$p.find(".oz-preview__narx").html(`<table class="oz-narx-tbl">${rows}
			<tr class="t"><td colspan="3">${__("Pozitsiya summasi")}${qolda ? " <small>(" + __("qo'lda") + ")</small>" : ""}${h.taxminiy ? ' <span class="oz-tax">TAXMINIY tarif</span>' : ""}</td><td class="r">${akfa_diller.oz.fmt(m.narx ? m.narx.summa : 0)}</td></tr></table>`);
		const $nh = this.dialog.fields_dict.narx_html.$wrapper;
		$nh.html(`<table class="oz-narx-tbl">${rows}<tr class="t"><td colspan="3">${__("Jami")}</td><td class="r">${akfa_diller.oz.fmt(m.narx ? m.narx.summa : 0)}</td></tr></table>`);
		if (!qolda) {
			["oyna", "reska", "qirra", "burchak", "teshik", "kesim", "matofka", "boyash", "zakalka", "markirovka", "paket"].forEach((c) => {
				const f = this.dialog.fields_dict[c + "_summa"];
				if (f && f.value !== h[c + "_summa"]) f.set_value(h[c + "_summa"] || 0).then(() => {});
			});
		}
		this.dialog.get_primary_btn().prop("disabled", xato.length > 0);
	}

	save() {
		const me = this;
		const { p, teshiklar, kesimlar } = this.collect();
		if (!p.oyna_item) return frappe.msgprint(__("Oyna tanlanmagan"));
		if (cint(p.narx_qolda) && !(p.narx_izoh || "").trim()) return frappe.msgprint(__("Narx qo'lda — sabab kiriting"));
		// kesim uchun X/Y majburiy: saqlangach bo'sh qiymat 0 bo'lib qoladi (pastki-chap burchak)
		const koordsiz = kesimlar.filter((k) => k.x_mm === undefined || k.x_mm === null || k.x_mm === "" || k.y_mm === undefined || k.y_mm === null || k.y_mm === "");
		if (koordsiz.length) return frappe.msgprint(__("Kesim uchun X va Y kiritilishi shart (chap-pastki burchak, mm)"));
		if (this.last && this.last.xatolar.some((x) => x.daraja === "xato")) return frappe.msgprint(__("Xatolar bor — avval tuzating"));
		const frm = this.frm;
		let row = this.row;
		if (!row) {
			row = frm.add_child("pozitsiyalar");
			row.nr = this.nr;
		}
		akfa_diller.oz.POZ_FIELDS.forEach((f) => { if (p[f] !== undefined) row[f] = p[f]; });
		if (this.last && this.last.hisob) {
			akfa_diller.oz.HISOB_FIELDS.forEach((f) => { if (this.last.hisob[f] !== undefined && this.last.hisob[f] !== null) row[f] = this.last.hisob[f]; });
		}
		// teshik/kesim qatorlarini qayta yozish
		(frm.doc.teshiklar || []).filter((r) => cint(r.pozitsiya_nr) === cint(me.nr)).forEach((r) => frappe.model.clear_doc(r.doctype, r.name));
		(frm.doc.kesimlar || []).filter((r) => cint(r.pozitsiya_nr) === cint(me.nr)).forEach((r) => frappe.model.clear_doc(r.doctype, r.name));
		const tn = (this.last && this.last.teshik_narx) || [], kn = (this.last && this.last.kesim_narx) || [], ir = (this.last && this.last.ichki_radius) || [];
		teshiklar.forEach((t, i) => {
			const r = frm.add_child("teshiklar");
			Object.assign(r, { pozitsiya_nr: me.nr, diametr_mm: cint(t.diametr_mm), x_mm: t.x_mm === "" ? null : t.x_mm, y_mm: t.y_mm === "" ? null : t.y_mm, soni: cint(t.soni) || 1, izoh: t.izoh, narx: tn[i] ? tn[i][0] : 0, summa: tn[i] ? tn[i][1] : 0 });
		});
		kesimlar.forEach((k, i) => {
			const r = frm.add_child("kesimlar");
			Object.assign(r, { pozitsiya_nr: me.nr, tur: k.tur || "Fortochka", eni_mm: cint(k.eni_mm), boyi_mm: cint(k.boyi_mm), x_mm: k.x_mm === "" ? null : k.x_mm, y_mm: k.y_mm === "" ? null : k.y_mm, ichki_radius_mm: cint(k.ichki_radius_mm) || ir[i] || 0, soni: cint(k.soni) || 1, izoh: k.izoh, narx: kn[i] ? kn[i][0] : 0, summa: kn[i] ? kn[i][1] : 0 });
		});
		frm.refresh_field("pozitsiyalar");
		frm.refresh_field("teshiklar");
		frm.refresh_field("kesimlar");
		frm.dirty();
		this._dirty = false;
		this.dialog.hide();
		frappe.show_alert({ message: __("Pozitsiya №{0} saqlandi — hujjatni saqlang", [this.nr]), indicator: "green" });
		if (this.on_save) this.on_save(row);
	}
};

akfa_diller.oz.next_nr = function (frm) {
	return (frm.doc.pozitsiyalar || []).reduce((m, r) => Math.max(m, cint(r.nr)), 0) + 1;
};
akfa_diller.oz.last_position = function (frm) {
	const rows = frm.doc.pozitsiyalar || [];
	return rows.length ? rows[rows.length - 1] : null;
};
akfa_diller.oz.load_kesim_turlari = function () {
	if (akfa_diller.oz._kesim_turlari) return Promise.resolve(akfa_diller.oz._kesim_turlari);
	return frappe.xcall("akfa_diller.akfa_diller.api.oyna_konfigurator.kesim_turlari").then((v) => {
		akfa_diller.oz._kesim_turlari = v && Object.keys(v).length ? v : akfa_diller.oz.KESIM_DEFAULT;
		return akfa_diller.oz._kesim_turlari;
	});
};

akfa_diller.oz.load_variants = function () {
	if (akfa_diller.oz._variants) return Promise.resolve(akfa_diller.oz._variants);
	return frappe.xcall("akfa_diller.akfa_diller.api.oyna_zakaz.oyna_variantlar").then((v) => {
		akfa_diller.oz._variants = v || [];
		return akfa_diller.oz._variants;
	});
};
