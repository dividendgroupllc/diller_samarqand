// Copyright (c) 2026, akfa_diller and contributors
// For license information, please see license.txt
//
// Oyna sex — boshqaruv paneli (frontend).
//
// Tuzilishi:
//   • OynaDashboard      — holat (state), filtrlar, ma'lumot olish (fetch)
//   • render_*()         — faqat DOM chizadi, hisob-kitob qilmaydi
//   • chart_*()          — faqat grafiklar
//   • fmt/util           — formatlash yordamchilari
//
// Barcha hisob-kitoblar serverda (oyna_dashboard.py). Bu yerda biznes-mantiq yo'q.

frappe.pages["oyna-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Oyna sex — Boshqaruv paneli"),
		single_column: true,
	});

	wrapper.oyna_dashboard = new OynaDashboard(page, wrapper);
};

frappe.pages["oyna-dashboard"].on_page_show = function (wrapper) {
	if (wrapper.oyna_dashboard && wrapper.oyna_dashboard.ready) {
		wrapper.oyna_dashboard.refresh();
	}
};

const OD_METHOD = "akfa_diller.akfa_diller.api.oyna_dashboard";

const OD_COLORS = {
	primary: "#3b7ddd",
	success: "#0f9d58",
	danger: "#e24c4c",
	warning: "#e8a33d",
	info: "#5b6be0",
	muted: "#8d99a6",
	violet: "#7d5fd4",
};

// Kalendar popoveridagi tez tanlovlar. Kalitlar backend `_period_bounds()` bilan
// bir xil — shuning uchun taqqoslash davri kalendar bo'yicha to'g'ri suriladi
// (masalan "Shu oy" → o'tgan oyning shu kunlari).
const OD_PERIODS = [
	{ key: "today", label: __("Bugun") },
	{ key: "yesterday", label: __("Kecha") },
	{ key: "this_week", label: __("Shu hafta") },
	{ key: "last_week", label: __("O'tgan hafta") },
	{ key: "this_month", label: __("Shu oy") },
	{ key: "last_month", label: __("O'tgan oy") },
	{ key: "this_year", label: __("Shu yil") },
	{ key: "last_year", label: __("O'tgan yil") },
];

//: Hafta boshi — dushanba. Backend Python `weekday()` dan foydalanadi (Du = 0),
//: shuning uchun mijoz tomonda ham shunday bo'lishi shart.
const OD_WEEK_START = 1;

// Sana arifmetikasi — mahalliy vaqt zonasida, UTC siljishisiz.
const OD_DATE = {
	pad: (n) => String(n).padStart(2, "0"),
	str: (d) => `${d.getFullYear()}-${OD_DATE.pad(d.getMonth() + 1)}-${OD_DATE.pad(d.getDate())}`,
	// "2026-08-01", "2026-08-01 10:00:00" va ISO ("2026-08-01T00:00:00+05:00")
	// ko'rinishlarining barchasini qabul qiladi.
	obj: (s) => {
		const [y, m, d] = String(s).split(/[T ]/)[0].split("-").map(Number);
		return new Date(y, m - 1, d);
	},
	today: () => OD_DATE.obj(frappe.datetime.get_today()),
	add_days: (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n),
	month_start: (d) => new Date(d.getFullYear(), d.getMonth(), 1),
	month_end: (d) => new Date(d.getFullYear(), d.getMonth() + 1, 0),
	add_months: (d, n) => new Date(d.getFullYear(), d.getMonth() + n, 1),
	week_start: (d) => OD_DATE.add_days(d, -(((d.getDay() - OD_WEEK_START) % 7) + 7) % 7),
	days_between: (a, b) => Math.round((OD_DATE.obj(b) - OD_DATE.obj(a)) / 86400000) + 1,
};

// Grafik o'qlari uchun oy nomlari. Frappe'ning str_to_user() sana formatiga
// bog'liq raqamli yorliq beradi ("01-2026"), bu grafikda o'qilmaydi.
const OD_MONTHS_SHORT = [
	__("Yan"), __("Fev"), __("Mar"), __("Apr"), __("May"), __("Iyn"),
	__("Iyl"), __("Avg"), __("Sen"), __("Okt"), __("Noy"), __("Dek"),
];

// Vaqtincha o'chirilgan bo'limlar. Blok qaytarilganda shu ro'yxatdan ham olib
// tashlang — shunda KPI kartasi yana bosiladigan bo'ladi (blokka olib boradi).
// Qarang: "OFF:qarzdor-haqdor" belgisi bilan kommentga olingan joylar.
// "Xarajatlar" jadvali yig'ilgan holatda nechta qator ko'rsatsin.
// 4 ta — "Kassa va pul oqimi" jadvalidagi kassa hisoblari soniga teng, shunda
// ikkala blokning bo'yi taxminan bir xil chiqadi.
const OD_EXPENSE_ROWS = 4;

const OD_HIDDEN_SECTIONS = ["receivables", "payables", "inventory"];

// Workflow State.style -> rang. Backend uslubni workflow sozlamasidan oladi,
// shuning uchun yangi holat qo'shilsa bu yerga tegish shart emas.
const OD_STATE_TONES = {
	warning: "orange",
	primary: "blue",
	success: "green",
	danger: "red",
	info: "blue",
	"": "gray",
};

//: KPI panjarasidagi ustunlar soni (CSS bilan bir xil bo'lishi shart).
const OD_KPI_COLUMNS = 4;

const OD_STATUS = {
	healthy: { label: __("Yetarli"), tone: "ok" },
	low: { label: __("Kam qoldi"), tone: "warn" },
	out: { label: __("Tugagan"), tone: "bad" },
};

// ---------------------------------------------------------------------------
// Formatlash yordamchilari
// ---------------------------------------------------------------------------

const od = {
	esc(value) {
		return frappe.utils.escape_html(String(value == null ? "" : value));
	},

	/** Guruhlangan son: 456 475 000 */
	number(value, decimals = 0) {
		const n = flt(value);
		const fixed = Math.abs(n).toFixed(decimals);
		const [int, dec] = fixed.split(".");
		const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
		return (n < 0 ? "−" : "") + grouped + (dec ? "," + dec : "");
	},

	/** Pul: 456 475 000 UZS */
	money(value, currency, decimals) {
		if (decimals === undefined) decimals = currency === "UZS" ? 0 : 2;
		return od.number(value, decimals) + " " + od.esc(currency || "");
	},

	/** Grafik o'qlari uchun qisqa ko'rinish: 456,5 mln */
	compact(value, currency) {
		const n = flt(value);
		const abs = Math.abs(n);
		let out;
		if (abs >= 1e9) out = od.number(n / 1e9, 2) + " " + __("mlrd");
		else if (abs >= 1e6) out = od.number(n / 1e6, 1) + " " + __("mln");
		else if (abs >= 1e3) out = od.number(n / 1e3, 1) + " " + __("ming");
		else out = od.number(n, 0);
		return currency ? out + " " + currency : out;
	},

	/** Oxirgi to'liqmas qatordagi kartalar uchun ustun kengligi (span).
	 *  Masalan 10 ta karta, 4 ustun -> oxirgi 2 tasi 2 ustundan egallaydi. */
	last_row_spans(total, columns) {
		const spans = new Array(total).fill(1);
		const rest = total % columns;
		if (!rest) return spans;
		const base = Math.floor(columns / rest);
		let extra = columns % rest;
		for (let i = total - rest; i < total; i++) {
			spans[i] = base + (extra-- > 0 ? 1 : 0);
		}
		return spans;
	},

	/** Har qanday pul qiymatini [{currency, amount}] ko'rinishiga keltiradi. */
	money_list(value) {
		if (!value) return [];
		if (Array.isArray(value)) return value.filter((e) => e && e.currency);
		if (value.currency) return [value];
		return [];
	},

	qty(value, uom) {
		const text = od.number(value, Math.abs(flt(value)) % 1 ? 2 : 0);
		return uom ? text + " " + od.esc(uom) : text;
	},

	/** Foiz. null/undefined — ma'nosiz marja (server shunday belgilaydi). */
	percent(value) {
		if (value === null || value === undefined) return "—";
		return od.number(value, 1) + "%";
	},

	/** Grafik o'qi yorlig'i: oylik — "Yan", kunlik/haftalik — "1 Avg". */
	date_label(value, interval, multi_year) {
		const date = OD_DATE.obj(value);
		if (isNaN(date.getTime())) return value;
		if (interval === "month") {
			return OD_MONTHS_SHORT[date.getMonth()] + (multi_year ? " " + date.getFullYear() : "");
		}
		return date.getDate() + " " + OD_MONTHS_SHORT[date.getMonth()];
	},
};

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

class OynaDashboard {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.charts = {};
		this.controls = {};
		this.ready = false;
		this._request = 0;
		// Diqqat: frappe.datetime.month_start() ISO+timezone qaytaradi
		// ("2026-08-01T00:00:00+05:00"), shuning uchun o'z yordamchimizdan
		// foydalanamiz — server "YYYY-MM-DD" kutadi.
		this.state = {
			period: "this_month",
			from_date: OD_DATE.str(OD_DATE.month_start(OD_DATE.today())),
			to_date: OD_DATE.str(OD_DATE.today()),
			company: null,
			warehouse: null,
			cost_center: null,
			customer: null,
		};

		this.setup();
	}

	async setup() {
		this.render_shell();
		try {
			this.meta = await this.call("get_meta", {});
		} catch (error) {
			this.fatal(__("Dashboard sozlamalarini yuklab bo'lmadi."));
			return;
		}

		this.state.company = this.meta.company;
		this.render_filters();
		this.ready = true;
		this.refresh();
	}

	// -- infratuzilma ------------------------------------------------------

	call(method, args) {
		return frappe.call({ method: `${OD_METHOD}.${method}`, args }).then((r) => r.message);
	}

	get_filters(extra) {
		return Object.assign(
			{
				company: this.state.company,
				period: this.state.period,
				from_date: this.state.from_date,
				to_date: this.state.to_date,
				warehouse: this.state.warehouse,
				cost_center: this.state.cost_center,
				customer: this.state.customer,
			},
			extra || {}
		);
	}

	/** Pul qiymati — [{currency, amount}] ro'yxati. Konvertatsiya YO'Q:
	 *  bir nechta valyuta bo'lsa har biri alohida qatorda ko'rsatiladi. */
	money(value) {
		const items = od.money_list(value);
		if (!items.length) return "0";
		return items
			.map((e) => `<span class="od-cur">${od.money(e.amount, e.currency)}</span>`)
			.join("");
	}

	/** Ro'yxatdagi eng katta summaning valyutasi (backend shu tartibda beradi). */
	primary_currency(value) {
		const items = od.money_list(value);
		return items.length ? items[0].currency : null;
	}

	/** Grafik uchun bitta valyutadagi son (aralash valyutani chizib bo'lmaydi). */
	chart_value(value, currency) {
		const found = od.money_list(value).find((e) => e.currency === currency);
		return found ? flt(found.amount) : 0;
	}

	/** Qatorlar to'plamidagi eng katta ulushli valyuta (grafik uchun). */
	series_currency(series, key) {
		const totals = {};
		(series || []).forEach((row) => {
			od.money_list(row[key]).forEach((e) => {
				totals[e.currency] = (totals[e.currency] || 0) + Math.abs(flt(e.amount));
			});
		});
		const sorted = Object.keys(totals).sort((a, b) => totals[b] - totals[a]);
		return sorted[0] || null;
	}

	/** Trend qatorlaridan grafik o'qi yorliqlarini yasaydi. */
	chart_labels(series, interval) {
		const years = new Set((series || []).map((r) => OD_DATE.obj(r.label).getFullYear()));
		const multi_year = years.size > 1;
		return (series || []).map((r) => od.date_label(r.label, interval, multi_year));
	}

	// -- karkas ------------------------------------------------------------

	render_shell() {
		this.page.main.html(`
			<div class="oyna-dash">
				<div class="od-toolbar"></div>
				<div class="od-context"></div>
				<div class="od-kpis od-kpis--loading"></div>
				<div class="od-sections"></div>
			</div>
		`);

		this.$toolbar = this.page.main.find(".od-toolbar");
		this.$context = this.page.main.find(".od-context");
		this.$kpis = this.page.main.find(".od-kpis");
		this.$sections = this.page.main.find(".od-sections");

		this.$kpis.html(this.skeleton_cards(11));
	}

	fatal(message) {
		this.page.main.html(`<div class="od-fatal">${od.esc(message)}</div>`);
	}

	skeleton_cards(count) {
		return new Array(count).fill('<div class="od-card od-skeleton"></div>').join("");
	}

	skeleton_block(height) {
		return `<div class="od-skeleton od-skeleton--block" style="height:${height || 220}px"></div>`;
	}

	// -- filtrlar ----------------------------------------------------------

	render_filters() {
		this.$toolbar.html(`
			<div class="od-toolbar__row">
				<div class="od-cal" data-role="daterange">
					<button type="button" class="od-cal__trigger" title="${__(
						"Kalendardan istalgan sana oralig'ini tanlang"
					)}">
						${frappe.utils.icon("calendar", "sm")}
						<span class="od-cal__label">${od.esc(this.range_label())}</span>
						<span class="od-cal__caret">▾</span>
					</button>
				</div>
				<div class="od-spacer"></div>
				<button type="button" class="od-refresh btn btn-default btn-sm">
					${frappe.utils.icon("refresh", "sm")} <span>${__("Yangilash")}</span>
				</button>
			</div>
			<!-- OFF:qoshimcha-filtrlar — qo'shimcha filtrlar (kompaniya / filial / ombor / mijoz).
			     Dashboard faqat sana oralig'i bo'yicha filtrlanadi.
			<div class="od-toolbar__row od-toolbar__row--filters">
				<div class="od-field" data-role="company"></div>
				<div class="od-field" data-role="cost_center"></div>
				<div class="od-field" data-role="warehouse"></div>
				<div class="od-field" data-role="customer"></div>
				<button type="button" class="od-clear btn btn-link btn-sm">${__("Filtrlarni tozalash")}</button>
			</div>
			-->
		`);

		// OFF:qoshimcha-filtrlar — kompaniya qiymati serverdan keladi (`get_meta().company`):
		// cheklangan foydalanuvchida uning kompaniyasi, aks holda "Oyna sex".
		// this.make_link_control("company", __("Kompaniya"), "Company", {
		// 	read_only: this.meta.locked_company ? 1 : 0,
		// });
		// this.make_link_control("cost_center", __("Filial (Cost Center)"), "Cost Center", {
		// 	get_query: () => ({ filters: { company: this.state.company, is_group: 0 } }),
		// });
		// this.make_link_control("warehouse", __("Ombor"), "Warehouse", {
		// 	get_query: () => ({ filters: { company: this.state.company, is_group: 0 } }),
		// });
		// this.make_link_control("customer", __("Mijoz"), "Customer");

		this.bind_filter_events();
	}

	// ===================================================================
	// OFF:qoshimcha-filtrlar — faqat yuqoridagi filtrlar uchun ishlatilgan yordamchi.
	// Qaytarish: toolbar qatorini, chaqiruvlarni va .od-clear ishlovchisini
	// kommentdan chiqarib, shu metodni ham tiklang.
	// ===================================================================
// 	make_link_control(fieldname, label, options, extra) {
// 		const control = frappe.ui.form.make_control({
// 			parent: this.$toolbar.find(`[data-role="${fieldname}"]`),
// 			df: Object.assign(
// 				{
// 					fieldtype: "Link",
// 					fieldname,
// 					label,
// 					options,
// 					placeholder: label,
// 					change: () => {
// 						const value = control.get_value() || null;
// 						if (value === this.state[fieldname]) return;
// 						this.state[fieldname] = value;
// 						if (fieldname === "company") {
// 							this.state.warehouse = null;
// 							this.state.cost_center = null;
// 							["warehouse", "cost_center"].forEach((f) => {
// 								if (this.controls[f]) this.controls[f].set_value("");
// 							});
// 						}
// 						this.refresh();
// 					},
// 				},
// 				extra || {}
// 			),
// 			render_input: true,
// 		});
// 		if (this.state[fieldname]) control.set_value(this.state[fieldname]);
// 		this.controls[fieldname] = control;
// 	}

	bind_filter_events() {
		this.$toolbar.on("click", ".od-cal__trigger", (event) => {
			event.stopPropagation();
			this.toggle_calendar();
		});

		this.$toolbar.on("click", ".od-refresh", () => this.refresh({ force: true }));

		// OFF:qoshimcha-filtrlar
		// this.$toolbar.on("click", ".od-clear", () => {
		// 	["warehouse", "cost_center", "customer"].forEach((f) => {
		// 		this.state[f] = null;
		// 		if (this.controls[f]) this.controls[f].set_value("");
		// 	});
		// 	this.refresh();
		// });
	}

	// -- sana oralig'i: kalendar -------------------------------------------

	/** Tugmadagi matn: "Shu oy · 01-08-2026 — 13-08-2026" */
	range_label() {
		const preset = OD_PERIODS.find((p) => p.key === this.state.period);
		const range = `${frappe.datetime.str_to_user(
			this.state.from_date
		)} — ${frappe.datetime.str_to_user(this.state.to_date)}`;
		return preset ? `${preset.label} · ${range}` : range;
	}

	update_range_label() {
		this.$toolbar.find(".od-cal__label").text(this.range_label());
	}

	/** Tez tanlov kalitidan sana oralig'ini hisoblaydi (backend bilan bir xil). */
	preset_range(key) {
		const today = OD_DATE.today();
		switch (key) {
			case "today":
				return [today, today];
			case "yesterday": {
				const day = OD_DATE.add_days(today, -1);
				return [day, day];
			}
			case "this_week":
				return [OD_DATE.week_start(today), today];
			case "last_week": {
				const start = OD_DATE.add_days(OD_DATE.week_start(today), -7);
				return [start, OD_DATE.add_days(start, 6)];
			}
			case "this_month":
				return [OD_DATE.month_start(today), today];
			case "last_month": {
				const start = OD_DATE.add_months(today, -1);
				return [start, OD_DATE.month_end(start)];
			}
			case "this_year":
				return [new Date(today.getFullYear(), 0, 1), today];
			case "last_year":
				return [
					new Date(today.getFullYear() - 1, 0, 1),
					new Date(today.getFullYear() - 1, 11, 31),
				];
			default:
				return [OD_DATE.obj(this.state.from_date), OD_DATE.obj(this.state.to_date)];
		}
	}

	build_calendar() {
		const presets = OD_PERIODS.map(
			(p) =>
				`<button type="button" class="od-cal__preset" data-preset="${p.key}">${od.esc(
					p.label
				)}</button>`
		).join("");

		this.$pop = $(`
			<div class="od-cal__pop">
				<div class="od-cal__presets">
					<div class="od-cal__presets-title">${__("Tez tanlov")}</div>
					${presets}
				</div>
				<div class="od-cal__main">
					<div class="od-cal__picker"></div>
					<div class="od-cal__foot">
						<span class="od-cal__preview"></span>
						<span class="od-cal__actions">
							<button type="button" class="btn btn-default btn-xs od-cal__cancel">${__(
								"Bekor"
							)}</button>
							<button type="button" class="btn btn-primary btn-xs od-cal__apply">${__(
								"Qo'llash"
							)}</button>
						</span>
					</div>
					<div class="od-cal__tip">${__(
						"Boshlanish sanasini, so'ng tugash sanasini bosing."
					)}</div>
				</div>
			</div>
		`).appendTo(this.$toolbar.find('[data-role="daterange"]'));

		// Tilni air-datepicker qo'llab-quvvatlamasa — inglizchaga qaytamiz
		// (Frappe'ning o'z Date kontroli ham shunday qiladi).
		const languages = ($.fn.datepicker && $.fn.datepicker.language) || {};
		const user_language = (frappe.boot.user || {}).language;
		const language = languages[user_language] ? user_language : "en";

		this.$pop.find(".od-cal__picker").datepicker({
			language,
			inline: true,
			range: true,
			toggleSelected: false,
			autoClose: false,
			keyboardNav: false,
			todayButton: false,
			clearButton: false,
			firstDay: OD_WEEK_START,
			dateFormat: "yyyy-mm-dd",
			onSelect: (formatted, dates) => this.on_calendar_select(dates),
		});
		this.picker = this.$pop.find(".od-cal__picker").data("datepicker");

		this.$pop.on("click", "[data-preset]", (event) => {
			event.stopPropagation();
			this.apply_preset($(event.currentTarget).data("preset"));
		});
		this.$pop.on("click", ".od-cal__apply", (event) => {
			event.stopPropagation();
			this.apply_calendar();
		});
		this.$pop.on("click", ".od-cal__cancel", (event) => {
			event.stopPropagation();
			this.close_calendar();
		});
	}

	toggle_calendar() {
		if (this.$pop && this.$pop.hasClass("is-open")) {
			this.close_calendar();
		} else {
			this.open_calendar();
		}
	}

	open_calendar() {
		if (!this.$pop) this.build_calendar();
		this.pending = [this.state.from_date, this.state.to_date];
		this.sync_calendar();
		this.$pop.addClass("is-open");

		// Tashqariga bosilganda yopish (popover ichidagi bosishlar hisobga olinmaydi).
		setTimeout(() => {
			$(document).on("mousedown.odcal", (event) => {
				if (!$(event.target).closest(".od-cal").length) this.close_calendar();
			});
			$(document).on("keydown.odcal", (event) => {
				if (event.key === "Escape") this.close_calendar();
			});
		}, 0);
	}

	close_calendar() {
		clearTimeout(this._apply_timer);
		if (this.$pop) this.$pop.removeClass("is-open");
		$(document).off("mousedown.odcal keydown.odcal");
	}

	/** Kalendar tanlovini joriy holatga moslash (qo'llashni ishga tushirmasdan). */
	sync_calendar() {
		if (!this.picker) return;
		this._syncing = true;
		this.picker.clear();
		this.picker.selectDate([
			OD_DATE.obj(this.state.from_date),
			OD_DATE.obj(this.state.to_date),
		]);
		this._syncing = false;

		this.$pop
			.find("[data-preset]")
			.removeClass("is-active")
			.filter(`[data-preset="${this.state.period}"]`)
			.addClass("is-active");
		this.update_preview();
	}

	on_calendar_select(dates) {
		if (this._syncing) return;

		const selected = (Array.isArray(dates) ? dates : [dates])
			.filter(Boolean)
			.sort((a, b) => a - b);
		if (!selected.length) return;

		this.pending = [
			OD_DATE.str(selected[0]),
			OD_DATE.str(selected[selected.length - 1]),
		];
		this.$pop.find("[data-preset]").removeClass("is-active");
		this.update_preview();

		// Ikkinchi sana bosilishi bilan oraliq to'liq — darhol qo'llaymiz.
		if (selected.length > 1) {
			clearTimeout(this._apply_timer);
			this._apply_timer = setTimeout(() => this.apply_calendar(), 180);
		}
	}

	update_preview() {
		if (!this.$pop || !this.pending) return;
		const [from, to] = this.pending;
		const days = OD_DATE.days_between(from, to);
		this.$pop.find(".od-cal__preview").html(`
			<b>${frappe.datetime.str_to_user(from)}</b> — <b>${frappe.datetime.str_to_user(to)}</b>
			<span>${od.number(days)} ${__("kun")}</span>
		`);
	}

	apply_preset(key) {
		const [from, to] = this.preset_range(key);
		this.state.period = key;
		this.state.from_date = OD_DATE.str(from);
		this.state.to_date = OD_DATE.str(to);
		this.close_calendar();
		this.update_range_label();
		this.refresh();
	}

	apply_calendar() {
		if (!this.pending) return this.close_calendar();

		const [from, to] = this.pending;
		const changed =
			from !== this.state.from_date ||
			to !== this.state.to_date ||
			this.state.period !== "custom";

		this.state.period = "custom";
		this.state.from_date = from;
		this.state.to_date = to;
		this.close_calendar();
		this.update_range_label();
		if (changed) this.refresh();
	}

	// -- yuklash -----------------------------------------------------------

	async refresh(options) {
		// Har bir so'rovga token beriladi. Foydalanuvchi davrni tez-tez almashtirsa,
		// eski so'rovning javobi TASHLAB YUBORILADI (aks holda kech kelgan javob
		// yangi davr ustiga chizilib qolardi). Muhimi: yangi so'rov hech qachon
		// "oldingisi tugamadi" deb bekor qilinmaydi — aks holda filtr o'zgargani
		// bilan kartalar eski ma'lumotda qolib ketardi.
		const token = (this._request = (this._request || 0) + 1);
		const stale = () => token !== this._request;
		const force = options && options.force;
		this.$toolbar.find(".od-refresh").addClass("is-busy");

		try {
			// 1-bosqich: KPI kartalari (tez chiziladi)
			const head = await this.call("get_dashboard", {
				filters: this.get_filters({ refresh: force ? 1 : 0 }),
				sections: ["overview"],
			});
			if (stale()) return;

			// Server davrni yakuniy aniqlaydi (preset → aniq sanalar), shuning uchun
			// holat va tugma yorlig'i shu javobdan yangilanadi.
			this.context = head.context;
			this.state.from_date = head.context.from_date;
			this.state.to_date = head.context.to_date;
			this.update_range_label();
			this.render_context();
			this.overview = head.overview;
			this.render_kpis(head.overview);

			// 2-bosqich: qolgan bo'limlar
			this.$sections.html(this.sections_placeholder());
			const body = await this.call("get_dashboard", {
				filters: this.get_filters({ refresh: force ? 1 : 0 }),
				sections: [
					"sales",
					"orders",
					// OFF:qarzdor-haqdor "receivables",
					// OFF:qarzdor-haqdor "payables",
					"cash",
					// OFF:ombor-portfel "inventory",
					"expenses",
				],
			});
			if (stale()) return;
			this.render_sections(body);
		} catch (error) {
			if (stale()) return;
			console.error(error); // eslint-disable-line no-console
			this.$sections.html(
				`<div class="od-fatal">${__("Ma'lumotlarni yuklashda xatolik yuz berdi.")}</div>`
			);
		} finally {
			if (!stale()) this.$toolbar.find(".od-refresh").removeClass("is-busy");
		}
	}

	sections_placeholder() {
		return `
			<div class="od-row">${this.skeleton_block(300)}${this.skeleton_block(300)}</div>
			<div class="od-row">${this.skeleton_block(320)}${this.skeleton_block(320)}</div>
		`;
	}

	// -- kontekst satri ----------------------------------------------------

	render_context() {
		const ctx = this.context || {};
		const period = `${frappe.datetime.str_to_user(ctx.from_date)} — ${frappe.datetime.str_to_user(
			ctx.to_date
		)}`;
		const prev = `${frappe.datetime.str_to_user(ctx.prev_from)} — ${frappe.datetime.str_to_user(
			ctx.prev_to
		)}`;

		const scope = [];
		if (this.state.cost_center) scope.push(this.state.cost_center);
		if (this.state.warehouse) scope.push(this.state.warehouse);
		if (this.state.customer) scope.push(this.state.customer);

		this.$context.html(`
			<span class="od-context__item od-context__item--strong">
				${frappe.utils.icon("organization", "xs")} ${od.esc(ctx.company)}
			</span>
			<span class="od-context__item">${frappe.utils.icon("calendar", "xs")} ${period}</span>
			<span class="od-context__item od-context__item--muted">
				${__("Taqqoslash")}: ${prev}
			</span>
			${
				scope.length
					? `<span class="od-context__item">${frappe.utils.icon(
							"filter",
							"xs"
					  )} ${od.esc(scope.join(" · "))}</span>`
					: ""
			}
			`);
	}

	// -- KPI kartalari -----------------------------------------------------

	render_kpis(overview) {
		this.$kpis.removeClass("od-kpis--loading");

		if (!overview || overview.permitted === false) {
			this.$kpis.html(this.empty_state(overview && overview.message));
			return;
		}
		if (!overview.cards || !overview.cards.length) {
			this.$kpis.html(this.empty_state(__("Ko'rsatkichlarni hisoblash uchun ma'lumot yo'q.")));
			return;
		}

		// Oxirgi qator to'liq bo'lmasa — qolgan kartalar bo'sh joyni bo'lib oladi
		// (masalan 10 ta karta: 4 + 4 + 2, oxirgi ikkitasi ikki barobar keng).
		const spans = od.last_row_spans(overview.cards.length, OD_KPI_COLUMNS);
		this.$kpis.html(
			overview.cards.map((card, index) => this.kpi_html(card, spans[index])).join("")
		);

		this.$kpis.off("click").on("click", ".od-card[data-section]", (event) => {
			const section = $(event.currentTarget).data("section");
			const target = this.$sections.find(`[data-block="${section}"]`);
			if (target.length) {
				target[0].scrollIntoView({ behavior: "smooth", block: "start" });
				target.addClass("od-block--flash");
				setTimeout(() => target.removeClass("od-block--flash"), 1200);
			}
		});

		this.$kpis.on("click", ".od-card[data-route]", (event) => {
			event.stopPropagation();
			const card = overview.cards[$(event.currentTarget).index()];
			if (card && card.route) {
				frappe.route_options = card.route_options || {};
				frappe.set_route(card.route);
			}
		});
	}

	kpi_html(card, span) {
		// Qoldiq kartalarida qaysi sana holatiga ekani ko'rinib tursin — shunda
		// davr boshini o'zgartirganda ular nega o'zgarmagani tushunarli bo'ladi.
		const to_date = (this.context || {}).to_date;
		const as_of_label = to_date
			? `${__("qoldiq")} · ${frappe.datetime.str_to_user(to_date).slice(0, 5)}`
			: __("qoldiq");

		const value =
			card.format === "currency"
				? this.money(card.value)
				: od.number(card.value, card.format === "percent" ? 1 : 0);
		// Taqqoslash foizi asosiy valyuta bo'yicha ko'rsatiladi.
		const primary = card.format === "currency" ? this.primary_currency(card.value) : null;

		const detail_html = (detail) => {
			let text;
			if (detail.format === "currency") text = this.money(detail.value);
			else if (detail.format === "percent") text = od.percent(detail.value);
			else if (detail.format === "sqm") text = od.qty(detail.value, "m²");
			else if (detail.format === "qty") text = od.qty(detail.value);
			else text = od.number(detail.value, 0);
			return `<span class="od-card__detail"><b>${text}</b> ${od.esc(detail.label)}</span>`;
		};

		// Yuqori-o'ng burchakka FAQAT BITTA sanoq chiqadi — ko'p bo'lsa ular
		// pastga cho'zilib, summaning ustiga tushib qolardi. Qolganlari
		// kartaning pastki-o'ng qismiga joylashtiriladi.
		const all_details = card.details || [];
		const details = all_details.slice(0, 1).map(detail_html).join("");
		const details_foot = all_details.slice(1).map(detail_html).join("");

		// Diqqat: KPI kartalarida hover-tooltip ATAYLAB yo'q — kerakli izohlar
		// bo'limlarning o'zida matn sifatida turadi.
		return `
			<div class="od-card od-card--${od.esc(card.tone || "info")}${
				span > 1 ? ` od-card--span${span}` : ""
			}${details ? " od-card--has-details" : ""}"
				${
					card.section && !OD_HIDDEN_SECTIONS.includes(card.section)
						? `data-section="${od.esc(card.section)}"`
						: ""
				}
				${card.route ? 'data-route="1"' : ""}>
				<div class="od-card__head">
					<span class="od-card__label">${od.esc(card.label)}</span>
					<span class="od-card__details">${details}</span>
				</div>
				<div class="od-card__value">${value}</div>
				<div class="od-card__foot">
					${this.delta_html(card.delta, card.invert_delta, primary)}
					${
						details_foot
							? `<span class="od-card__details od-card__details--foot">${details_foot}</span>`
							: ""
					}
					${card.as_of ? `<span class="od-card__badge">${as_of_label}</span>` : ""}
				</div>
			</div>
		`;
	}

	delta_html(delta, invert, currency) {
		// delta — {valyuta: {...}}: konvertatsiya yo'q, har bir valyuta alohida.
		if (delta && !delta.comparable && !delta.direction) {
			delta = currency ? delta[currency] : Object.values(delta)[0];
		}
		if (!delta) return "";
		if (!delta.comparable) {
			return `<span class="od-delta od-delta--flat">${__("taqqoslashsiz")}</span>`;
		}
		if (delta.direction === "flat") {
			return `<span class="od-delta od-delta--flat">0%</span>`;
		}

		const good = invert ? delta.direction === "down" : delta.direction === "up";
		const arrow = delta.direction === "up" ? "↑" : "↓";
		return `<span class="od-delta od-delta--${good ? "good" : "bad"}">
			${arrow} ${od.percent(Math.abs(delta.pct))}
		</span>`;
	}

	empty_state(message, icon) {
		return `<div class="od-empty">
			${frappe.utils.icon(icon || "list", "lg")}
			<span>${od.esc(message || __("Tanlangan davr uchun ma'lumot topilmadi."))}</span>
		</div>`;
	}

	// -- bo'limlar ---------------------------------------------------------

	render_sections(data) {
		Object.values(this.charts).forEach((chart) => chart && chart.destroy && chart.destroy());
		this.charts = {};

		this.$sections.html(`
			<div class="od-row">
				<div class="od-block od-block--full" data-block="sales"></div>
			</div>
			<div class="od-row">
				<div class="od-block od-block--full" data-block="orders"></div>
			</div>
			<!-- OFF:qarzdor-haqdor
			<div class="od-row">
				<div class="od-block" data-block="receivables"></div>
				<div class="od-block" data-block="payables"></div>
			</div>
			-->
			<div class="od-row">
				<div class="od-block" data-block="cash"></div>
				<div class="od-block" data-block="expenses"></div>
			</div>
			<!-- OFF:ombor-portfel  (asl joylashuv: cash|order-book va inventory|expenses)
			<div class="od-row">
				<div class="od-block" data-block="inventory"></div>
				<div class="od-block" data-block="order-book"></div>
			</div>
			-->
		`);

		// Har bir blok alohida try/catch ichida: bittasida xato bo'lsa,
		// faqat o'sha blok "xatolik" ko'rsatadi, qolganlari normal chiziladi.
		this.safe_render("sales", () => this.render_sales(data.sales));
		this.safe_render("orders", () => this.render_orders(data.orders));
		// OFF:qarzdor-haqdor
		// this.safe_render("receivables", () =>
		// 	this.render_party_ledger("receivables", data.receivables)
		// );
		// this.safe_render("payables", () => this.render_party_ledger("payables", data.payables));
		// OFF:ombor-portfel
		// this.safe_render("order-book", () => this.render_order_book(data.sales));
		this.safe_render("cash", () => this.render_cash(data.cash));
		// OFF:ombor-portfel
		// this.safe_render("inventory", () => this.render_inventory(data.inventory));
		this.safe_render("expenses", () => this.render_expenses(data.expenses));
	}

	block(name) {
		return this.$sections.find(`[data-block="${name}"]`);
	}

	/** Bitta blokni xavfsiz chizadi — xato butun dashboardni yiqitmaydi. */
	safe_render(name, fn) {
		try {
			fn();
		} catch (error) {
			console.error(`[oyna-dashboard] ${name}`, error); // eslint-disable-line no-console
			const $block = this.block(name);
			if ($block.length) {
				$block.html(
					`<div class="od-block__head"><h3 class="od-block__title">${od.esc(
						name
					)}</h3></div>` +
						`<div class="od-block__body">${this.empty_state(
							__("Ushbu blokni chizishda xatolik yuz berdi."),
							"solid-warning"
						)}</div>`
				);
			}
		}
	}

	block_shell(name, title, subtitle, action) {
		const $block = this.block(name);
		$block.html(`
			<div class="od-block__head">
				<div>
					<h3 class="od-block__title">${od.esc(title)}</h3>
					${subtitle ? `<p class="od-block__subtitle">${subtitle}</p>` : ""}
				</div>
				${action || ""}
			</div>
			<div class="od-block__body"></div>
		`);
		return $block.find(".od-block__body");
	}

	guard(section, $body) {
		if (!section) {
			$body.html(this.empty_state());
			return false;
		}
		if (section.permitted === false) {
			$body.html(this.empty_state(section.message, "lock"));
			return false;
		}
		if (section.error) {
			$body.html(this.empty_state(section.error, "solid-warning"));
			return false;
		}
		if (section.empty_reason) {
			$body.html(this.empty_state(section.empty_reason, "solid-info"));
			return false;
		}
		return true;
	}

	link_button(label, handler) {
		const id = "od-" + frappe.utils.get_random(8);
		setTimeout(() => this.$sections.find(`#${id}`).on("click", handler), 0);
		return `<button type="button" id="${id}" class="od-link">${od.esc(label)} →</button>`;
	}

	// -- 1. Sotuv dinamikasi ----------------------------------------------

	render_sales(sales) {
		const action = this.link_button(__("P&L hisoboti"), () =>
			this.open_report("Profit and Loss Statement", {
				filter_based_on: "Date Range",
				period_start_date: this.context.from_date,
				period_end_date: this.context.to_date,
				periodicity: "Monthly",
			})
		);

		const $body = this.block_shell(
			"sales",
			__("Sotuv dinamikasi"),
			__("{0}-yil, oylar bo'yicha sotuv", [
				sales.trend_year || OD_DATE.today().getFullYear(),
			]),
			action
		);
		if (!this.guard(sales, $body)) return;

		const s = sales.summary || {};
		// Vozvrat bo'lmasa — u bilan bog'liq ko'rsatkichlar umuman ko'rsatilmaydi.
		const has_returns = (s.returns || []).length > 0;
		$body.html(`
			<div class="od-stats">
				<div class="od-stat"><span>${__("Sotuv")}</span><b>${this.money(s.gross)}</b></div>
				${
					has_returns
						? `<div class="od-stat"><span>${__("Vozvrat")}</span><b class="od-bad">${this.money(
								s.returns
						  )}</b></div>
						   <div class="od-stat"><span>${__("Sof sotuv")}</span><b>${this.money(
								s.amount
						  )}</b></div>`
						: ""
				}
				<div class="od-stat"><span>${__("Hisob-fakturalar")}</span><b>${od.number(
			s.orders
		)}${
			has_returns ? ` <small class="od-bad">+${od.number(s.return_count)}</small>` : ""
		}</b></div>
				<div class="od-stat"><span>${__("Mijozlar")}</span><b>${od.number(
			s.customers
		)}</b></div>
				<div class="od-stat"><span>${__("O'rtacha faktura")}</span><b>${this.money(
			s.avg_order
		)}</b></div>
			</div>
			<div class="od-chart" data-chart="sales"></div>
		`);

		if (!sales.trend || !sales.trend.length) {
			$body.find('[data-chart="sales"]').html(this.empty_state());
			return;
		}

		// Aralash valyutani bitta grafikda chizib bo'lmaydi — eng katta ulushli
		// valyuta olinadi, u sarlavhada ko'rsatiladi.
		const chart_currency = this.series_currency(sales.trend, "amount");
		// Vozvrat bo'lsa — ikkinchi (qizil) ustun qatori qo'shiladi.
		const trend_returns = sales.trend.some((r) => (r.returns || []).length);
		const datasets = [
			{
				name: __("Sotuv"),
				values: sales.trend.map((r) => this.chart_value(r.amount, chart_currency)),
			},
		];
		if (trend_returns) {
			datasets.push({
				name: __("Vozvrat"),
				values: sales.trend.map((r) => this.chart_value(r.returns, chart_currency)),
			});
		}

		this.charts.sales = new frappe.Chart($body.find('[data-chart="sales"]')[0], {
			type: "bar",
			height: 260,
			animate: false,
			colors: trend_returns
				? [OD_COLORS.primary, OD_COLORS.danger]
				: [OD_COLORS.primary],
			barOptions: { spaceRatio: 0.35 },
			data: {
				labels: this.chart_labels(sales.trend, sales.interval),
				datasets,
			},
			axisOptions: { xAxisMode: "tick", shortenYAxisNumbers: 1 },
			tooltipOptions: {
				formatTooltipY: (value) => od.compact(value, chart_currency),
			},
		});
	}

	// -- 2a. Zakazlar ro'yxati (holati bilan) -------------------------------

	render_orders(orders) {
		const action = this.link_button(__("Barcha zakazlar"), () => {
			frappe.route_options = {
				company: this.state.company,
				transaction_date: ["between", [this.context.from_date, this.context.to_date]],
			};
			frappe.set_route("List", "Sales Order");
		});

		const $body = this.block_shell(
			"orders",
			__("Zakazlar"),
			__("Tanlangan davrdagi barcha zakazlar va ularning holati"),
			action
		);
		if (!this.guard(orders, $body)) return;

		const rows = orders.orders || [];
		if (!rows.length) {
			$body.html(this.empty_state(__("Tanlangan davrda zakaz yo'q.")));
			return;
		}

		const tone = (style) => OD_STATE_TONES[style || ""] || "gray";
		const pill = (row) =>
			`<span class="od-pill od-pill--${tone(row.style)}">${od.esc(row.state)}</span>`;

		// Holatlar bo'yicha qisqacha jamlanma
		const chips = (orders.states || [])
			.map(
				(st) => `
			<div class="od-statechip od-statechip--${tone(st.style)}">
				<span class="od-statechip__name">${od.esc(st.state)}</span>
				<b>${od.number(st.orders)} ${__("ta")}</b>
				<span class="od-statechip__sum">${this.money(st.amount)}</span>
			</div>`
			)
			.join("");

		$body.html(`
			<div class="od-statechips">${chips}</div>
			<div class="od-table-wrap od-table-wrap--scroll" data-table="orders"></div>
		`);

		$body.find('[data-table="orders"]').html(
			this.table(
				[
					{ label: __("Zakaz"), key: "name" },
					{ label: __("Sana"), key: "date" },
					{ label: __("Mijoz"), key: "customer" },
					{ label: __("Kvadrat"), key: "sqm", align: "right" },
					{ label: __("Summa"), key: "amount", align: "right" },
					{ label: __("Holat"), key: "state", align: "center" },
				],
				rows.map((row) => ({
					_click: () => frappe.set_route("Form", "Sales Order", row.name),
					name: `<b>${od.esc(row.name)}</b>`,
					date: frappe.datetime.str_to_user(row.date),
					customer: od.esc(row.customer_name),
					sqm: row.sqm ? od.qty(row.sqm, "m²") : "—",
					amount: this.money(row.amount),
					state: pill(row),
				}))
			)
		);

		if (orders.truncated) {
			$body.append(
				`<p class="od-hint">${__("Faqat oxirgi 500 ta zakaz ko'rsatilmoqda.")}</p>`
			);
		}
	}

	// -- 2b. Zakazlar portfeli (rasmiylashtirilmagan) -----------------------
//
// 	render_order_book(sales) {
// 		const action = this.link_button(__("Zakazlar"), () => {
// 			frappe.route_options = {
// 				company: this.state.company,
// 				docstatus: 0,
// 				transaction_date: ["between", [this.context.from_date, this.context.to_date]],
// 			};
// 			frappe.set_route("List", "Sales Order");
// 		});
//
// 		const $body = this.block_shell(
// 			"order-book",
// 			__("Zakazlar portfeli"),
// 			__("Hali TASDIQLANMAGAN zakazlar — sotuvga kirmaydi"),
// 			action
// 		);
// 		if (!this.guard(sales, $body)) return;
//
// 		const book = sales.order_book;
// 		if (!book || !book.states || !book.states.length) {
// 			$body.html(this.empty_state(__("Rasmiylashtirilmagan zakaz yo'q.")));
// 			return;
// 		}
//
// 		const total = flt(book.total) || 1;
// 		$body.html(`
// 			<div class="od-stats">
// 				<div class="od-stat"><span>${__("Portfel summasi")}</span><b>${this.money(
// 			book.total
// 		)}</b></div>
// 				<div class="od-stat"><span>${__("Zakazlar")}</span><b>${od.number(
// 			book.orders
// 		)}</b></div>
// 				<div class="od-stat"><span>${__("Kvadrat")}</span><b>${od.qty(book.sqm, "m²")}</b></div>
// 			</div>
// 			<div class="od-funnel">
// 				${book.states
// 					.map(
// 						(row) => `
// 					<div class="od-funnel__row">
// 						<div class="od-funnel__head">
// 							<span>${od.esc(row.state || __("Noma'lum"))}</span>
// 							<b>${this.money(row.amount)}</b>
// 						</div>
// 						<div class="od-funnel__bar">
// 							<span style="width:${(flt(row.amount) / total) * 100}%"></span>
// 						</div>
// 						<div class="od-funnel__meta">${od.number(row.orders)} ${__("ta zakaz")} · ${od.qty(
// 							row.sqm,
// 							"m²"
// 						)}</div>
// 					</div>`
// 					)
// 					.join("")}
// 			</div>
// 			<p class="od-hint">${__(
// 				"Bu raqamlar buxgalteriyaga hali tushmagan: hisob-faktura ham, mijoz qarzi ham yaratilmagan. Zakaz «Topshirildi» holatiga o'tkazilgandan keyingina sotuvda va P&L da paydo bo'ladi."
// 			)}</p>
// 		`);
// 	}

	// ===================================================================
	// OFF:qarzdor-haqdor — VAQTINCHA O'CHIRILGAN BLOK (o'chirilmagan, kommentda)
	// Qaytarish: (1) shu metodni kommentdan chiqaring, (2) render_sections()
	// dagi grid qatorini va safe_render chaqiruvlarini, (3) refresh() dagi
	// sections ro'yxatini, (4) OD_HIDDEN_SECTIONS ro'yxatini tozalang.
	// ===================================================================
// 	// -- 4. Qarzdorlar / haqdorlar -----------------------------------------
//
// 	/** Bitta renderer ikkala kontragent turi uchun (Customer / Supplier). */
// 	render_party_ledger(block, receivables) {
// 		const is_supplier = block === "payables";
// 		const party_type = is_supplier ? "Supplier" : "Customer";
// 		const text = is_supplier
// 			? {
// 					title: __("Yetkazib beruvchilarga qarz"),
// 					subtitle: __("Biz qarzdor bo'lgan kontragentlar (GL)"),
// 					total: __("Jami qarzimiz"),
// 					count: __("Haqdorlar"),
// 					average: __("O'rtacha qarz"),
// 					party: __("Yetkazib beruvchi"),
// 					advance: __("Bergan avansimiz"),
// 					empty: __("Qarzdor bo'lgan yetkazib beruvchi yo'q."),
// 					charges: __("Hisoblangan"),
// 					payments: __("To'laganimiz"),
// 			  }
// 			: {
// 					title: __("Mijozlar qarzi"),
// 					subtitle: __("Yosh bo'yicha taqsimot va yirik qarzdorlar"),
// 					total: __("Jami qarz"),
// 					count: __("Qarzdorlar"),
// 					average: __("O'rtacha qarz"),
// 					party: __("Mijoz"),
// 					advance: __("Mijoz avanslari"),
// 					empty: __("Qarzdor mijozlar yo'q."),
// 					charges: __("Hisoblangan"),
// 					payments: __("To'langan"),
// 			  };
//
// 		const action = this.link_button(__("Batafsil"), () =>
// 			this.open_report("Kontragent Otchet", { party_type })
// 		);
// 		const $body = this.block_shell(block, text.title, text.subtitle, action);
// 		if (!this.guard(receivables, $body)) return;
//
// 		const s = receivables.summary || {};
// 		if (!s.debtors) {
// 			$body.html(this.empty_state(text.empty));
// 			return;
// 		}
//
// 		// Tartib General Ledger mantiqi bo'yicha: avval SOF qoldiq (GL bilan mos),
// 		// keyin uning tarkibi — qarzdorlik va avans.
// 		$body.html(`
// 			<div class="od-stats">
// 				<div class="od-stat"><span>${__("Sof qoldiq (GL)")}</span><b>${this.money(
// 			s.net
// 		)}</b></div>
// 				<div class="od-stat"><span>${text.total}</span><b>${this.money(s.balance)}</b></div>
// 				<div class="od-stat"><span>${text.advance}</span><b>${this.money(
// 			s.advance
// 		)}</b></div>
// 				<div class="od-stat"><span>${text.count}</span><b>${od.number(s.debtors)}</b></div>
// 			</div>
// 			<div class="od-note">
// 				${__("Sof qoldiq")} = ${text.total.toLowerCase()} − ${text.advance.toLowerCase()}:
// 				<b>${this.money(s.net)}</b> = <b>${this.money(s.balance)}</b> − <b>${this.money(
// 			s.advance
// 		)}</b>.
// 				${__("General Ledger'dagi qoldiq aynan shu — sof qiymat.")}
// 			</div>
// 			<div class="od-stats">
// 				<div class="od-stat"><span>${__("Muddati o'tgan")} (${od.number(
// 			s.overdue_after_days
// 		)}+ ${__("kun")})</span><b class="${flt(s.overdue) ? "od-bad" : ""}">${this.money(
// 			s.overdue
// 		)}</b></div>
// 				<div class="od-stat"><span>${text.average}</span><b>${this.money(
// 			s.average
// 		)}</b></div>
// 				<div class="od-stat"><span>${__("Eng katta")}</span><b>${this.money(s.max)}</b></div>
// 			</div>
// 			<div class="od-chart od-chart--sm" data-chart="aging"></div>
// 			<div class="od-table-wrap" data-table="debtors"></div>
// 		`);
//
// 		const aging = (receivables.aging || []).filter((row) => flt(row.amount) > 0);
// 		if (aging.length) {
// 			this.charts.aging = new frappe.Chart($body.find('[data-chart="aging"]')[0], {
// 				type: "bar",
// 				height: 180,
// 				animate: false,
// 				colors: [OD_COLORS.warning],
// 				data: {
// 					labels: aging.map((row) => row.label),
// 					datasets: [{ name: __("Qarz"), values: aging.map((row) => flt(row.amount)) }],
// 				},
// 				axisOptions: { shortenYAxisNumbers: 1 },
// 				tooltipOptions: { formatTooltipY: (value) => od.compact(value, chart_currency) },
// 			});
// 		}
//
// 		$body.find('[data-table="debtors"]').html(
// 			this.table(
// 				[
// 					{ label: text.party, key: "name" },
// 					{ label: text.charges, key: "charges", align: "right" },
// 					{ label: text.payments, key: "payments", align: "right" },
// 					{ label: __("Qoldiq"), key: "balance", align: "right" },
// 				],
// 				(receivables.customers || []).slice(0, 10).map((row) => ({
// 					_click: () =>
// 						this.open_report("Akt Sverka", {
// 							party_type: party_type,
// 							party: row.customer,
// 						}),
// 					name: `<b>${od.esc(row.customer_name)}</b><small>${__("eng eski")}: ${od.number(
// 						row.oldest_days
// 					)} ${__("kun")}</small>`,
// 					charges: this.money(row.charges),
// 					payments: this.money(row.payments),
// 					balance: `<b>${this.money(row.balance)}</b>`,
// 				}))
// 			)
// 		);
// 	}

	// -- 5. Kassa ----------------------------------------------------------

	render_cash(cash) {
		const action = this.link_button(__("DDS hisoboti"), () => this.open_report("DDS"));
		const $body = this.block_shell(
			"cash",
			__("Kassa va pul oqimi"),
			__("Har bir kassa bo'yicha kirim / chiqim"),
			action
		);
		if (!this.guard(cash, $body)) return;

		const s = cash.summary || {};
		$body.html(`
			<div class="od-stats">
				<div class="od-stat"><span>${__("Boshlang'ich")}</span><b>${this.money(
			s.opening
		)}</b></div>
				<div class="od-stat"><span>${__("Kirim")}</span><b class="od-good">${this.money(
			s.inflow
		)}</b></div>
				<div class="od-stat"><span>${__("Chiqim")}</span><b class="od-bad">${this.money(
			s.outflow
		)}</b></div>
				<div class="od-stat"><span>${__("Yakuniy qoldiq")}</span><b>${this.money(
			s.closing
		)}</b></div>
			</div>
			<div class="od-table-wrap" data-table="registers"></div>
			<div class="od-chart od-chart--sm" data-chart="cashflow"></div>
		`);

		$body.find('[data-table="registers"]').html(
			this.table(
				[
					{ label: __("Kassa"), key: "name" },
					{ label: __("Boshlang'ich"), key: "opening", align: "right" },
					{ label: __("Kirim"), key: "inflow", align: "right" },
					{ label: __("Chiqim"), key: "outflow", align: "right" },
					{ label: __("Qoldiq"), key: "closing", align: "right" },
				],
				(cash.accounts || []).map((row) => ({
					_click: () =>
						this.open_report("DDS", row.mode_of_payment ? { mode_of_payment: row.mode_of_payment } : {}),
					name: `<b>${od.esc(row.label)}</b><small>${od.esc(
						row.kind === "bank" ? __("bank / plastik") : __("naqd")
					)} · ${od.esc(row.account_currency)}</small>`,
					// Valyuta sarlavhada bir marta ko'rsatilgan — ustunlarda takrorlanmaydi.
					// Istisno: hisobning O'Z valyutasidagi qoldiq (pastdagi kichik qator),
					// u boshqa valyutada bo'lishi mumkin, shuning uchun belgisi bilan.
					// Jadvaldagi har bir kassa bitta valyutada — valyuta nomi
					// birinchi ustunda ko'rsatilgani uchun raqamlar toza chiqadi.
					opening: od.number(row.opening, row.account_currency === "UZS" ? 0 : 2),
					inflow: flt(row.inflow)
						? `<span class="od-good">${od.number(row.inflow, row.account_currency === "UZS" ? 0 : 2)}</span>`
						: "—",
					outflow: flt(row.outflow)
						? `<span class="od-bad">${od.number(row.outflow, row.account_currency === "UZS" ? 0 : 2)}</span>`
						: "—",
					closing: `<b>${od.number(row.closing, row.account_currency === "UZS" ? 0 : 2)}</b>`,
				}))
			)
		);

		const trend = cash.trend || [];
		const chart_currency = this.series_currency(trend, "inflow");
		if (trend.length) {
			this.charts.cashflow = new frappe.Chart($body.find('[data-chart="cashflow"]')[0], {
				type: "bar",
				height: 200,
				animate: false,
				barOptions: { stacked: false, spaceRatio: 0.35 },
				colors: [OD_COLORS.success, OD_COLORS.danger],
				data: {
					labels: this.chart_labels(trend, cash.interval),
					datasets: [
						{
							name: __("Kirim"),
							values: trend.map((r) => this.chart_value(r.inflow, chart_currency)),
						},
						{
							name: __("Chiqim"),
							values: trend.map((r) => this.chart_value(r.outflow, chart_currency)),
						},
					],
				},
				axisOptions: { shortenYAxisNumbers: 1 },
				tooltipOptions: { formatTooltipY: (value) => od.compact(value, chart_currency) },
			});
		}
	}

	// ===================================================================
	// OFF:ombor-portfel — VAQTINCHA O'CHIRILGAN BLOK: Ombor zaxirasi (kodi saqlangan)
	// Qaytarish: shu metodni + render_sections() dagi grid qatorini va
	// safe_render chaqiruvini + refresh() sections ro'yxatini kommentdan
	// chiqaring, so'ng OD_HIDDEN_SECTIONS ro'yxatini yangilang.
	// ===================================================================
// 	// -- 6. Ombor ----------------------------------------------------------
//
// 	render_inventory(inventory) {
// 		const action = this.link_button(__("Stock Balance"), () => {
// 			frappe.route_options = {
// 				company: this.state.company,
// 				from_date: this.context.from_date,
// 				to_date: this.context.to_date,
// 			};
// 			frappe.set_route("query-report", "Stock Balance");
// 		});
//
// 		const $body = this.block_shell(
// 			"inventory",
// 			__("Ombor zaxirasi"),
// 			__("Sana holatiga qoldiq va davrdagi sarf"),
// 			action
// 		);
// 		if (!this.guard(inventory, $body)) return;
//
// 		const s = inventory.summary || {};
// 		if (!s.items) {
// 			$body.html(this.empty_state(__("Omborda qoldiq topilmadi.")));
// 			return;
// 		}
//
// 		$body.html(`
// 			<div class="od-stats">
// 				<div class="od-stat"><span>${__("Zaxira qiymati")}</span><b>${this.money(
// 			s.value
// 		)}</b></div>
// 				<div class="od-stat"><span>${__("Tovar Turi")}</span><b>${od.number(
// 			s.items
// 		)}</b></div>
// 				<div class="od-stat"><span>${__("Kam qolgan")}</span><b class="${
// 			s.low_stock ? "od-warn" : ""
// 		}">${od.number(s.low_stock)}</b></div>
// 				<div class="od-stat"><span>${__("Tugagan")}</span><b class="${
// 			s.out_of_stock ? "od-bad" : ""
// 		}">${od.number(s.out_of_stock)}</b></div>
// 			</div>
// 			<div class="od-table-wrap" data-table="stock"></div>
// 		`);
//
// 		$body.find('[data-table="stock"]').html(
// 			this.table(
// 				[
// 					{ label: __("Material"), key: "name" },
// 					{ label: __("Qoldiq"), key: "qty", align: "right" },
// 					{ label: __("Sarflandi"), key: "consumed", align: "right" },
// 					{ label: __("Qiymati"), key: "value", align: "right" },
// 					{ label: __("Holat"), key: "status", align: "center" },
// 				],
// 				(inventory.items || []).slice(0, 12).map((row) => {
// 					const status = OD_STATUS[row.status] || OD_STATUS.healthy;
// 					return {
// 						_click: () => frappe.set_route("Form", "Item", row.item_code),
// 						name: `<b>${od.esc(row.item_name)}</b><small>${od.esc(
// 							row.item_group || ""
// 						)}</small>`,
// 						qty: od.qty(row.qty, row.uom),
// 						consumed: flt(row.consumed)
// 							? od.qty(row.consumed) +
// 							  (row.days_cover != null
// 									? `<small>${od.number(row.days_cover, 0)} ${__("kunga")}</small>`
// 									: "")
// 							: "—",
// 						value: this.money(row.value),
// 						status: `<span class="od-pill od-pill--${status.tone}">${status.label}</span>`,
// 					};
// 				})
// 			)
// 		);
// 	}

	// -- 7. Xarajatlar -----------------------------------------------------

	render_expenses(expenses) {
		const action = this.link_button(__("DDS: xarajatlar"), () =>
			this.open_report("DDS", { category: "Расходы" })
		);
		const $body = this.block_shell(
			"expenses",
			__("Xarajatlar"),
			__("Modda bo'yicha taqsimot"),
			action
		);
		if (!this.guard(expenses, $body)) return;

		const s = expenses.summary || {};
		if (!expenses.categories || !expenses.categories.length) {
			$body.html(this.empty_state(__("Tanlangan davrda xarajat qayd etilmagan.")));
			return;
		}

		$body.html(`
			<div class="od-stats od-stats--single">
				<div class="od-stat"><span>${__("Jami xarajat")}</span><b class="od-bad">${this.money(
			s.total
		)}</b></div>
			</div>
			<div class="od-chart od-chart--sm od-chart--nolegend" data-chart="expenses"></div>
			<div class="od-table-wrap" data-table="expenses"></div>
		`);

		// Donut legendasi o'chirilgan (matnni kesib tashlagani uchun) — uning
		// o'rniga quyidagi jadval ishlaydi, shuning uchun ranglar bir xil bo'lishi shart.
		const SLICE_COLORS = [
			OD_COLORS.danger,
			OD_COLORS.warning,
			OD_COLORS.info,
			OD_COLORS.primary,
			OD_COLORS.violet,
			OD_COLORS.success,
			OD_COLORS.muted,
		];
		const chart_currency = this.series_currency(expenses.categories, "amount");
		const top = expenses.categories.slice(0, SLICE_COLORS.length);
		const color_of = (account) => {
			const index = top.findIndex((r) => r.account === account);
			return index === -1 ? null : SLICE_COLORS[index];
		};

		this.charts.expenses = new frappe.Chart($body.find('[data-chart="expenses"]')[0], {
			type: "donut",
			height: 200,
			animate: false,
			maxSlices: SLICE_COLORS.length,
			colors: SLICE_COLORS,
			data: {
				labels: top.map((r) => r.label),
				datasets: [
					{ values: top.map((r) => Math.max(this.chart_value(r.amount, chart_currency), 0)) },
				],
			},
			tooltipOptions: { formatTooltipY: (value) => od.compact(value, chart_currency) },
		});

		const rows = expenses.categories.slice(0, 10);
		$body.find('[data-table="expenses"]').html(
			this.table(
				[
					{ label: __("Modda"), key: "name" },
					{ label: __("Summa"), key: "amount", align: "right" },
					{ label: __("Ulush"), key: "share", align: "right" },
				],
				rows.map((row, index) => ({
					// OD_EXPENSE_ROWS dan keyingi qatorlar yig'ilgan holatda yashiriladi
					_class: index >= OD_EXPENSE_ROWS ? "od-row-extra" : "",
					_click: () => this.open_general_ledger(row.account),
					name:
						`<span class="od-swatch" style="background:${
							color_of(row.account) || "transparent"
						}"></span>` +
						`<b>${od.esc(row.label)}</b><small>${od.number(row.entries)} ${__(
							"yozuv"
						)}</small>`,
					amount: this.money(row.amount),
					share: `<span class="od-share"><i style="width:${Math.max(
						row.share,
						0
					)}%"></i>${od.percent(row.share)}</span>`,
				}))
			)
		);

		this.setup_table_toggle($body.find('[data-table="expenses"]'), rows.length, OD_EXPENSE_ROWS);
	}

	// -- umumiy jadval -----------------------------------------------------

	table(columns, rows) {
		if (!rows || !rows.length) return this.empty_state();

		const head = columns
			.map((col) => `<th class="od-align-${col.align || "left"}">${od.esc(col.label)}</th>`)
			.join("");

		const body = rows
			.map((row, index) => {
				const cells = columns
					.map(
						(col) =>
							`<td class="od-align-${col.align || "left"}">${row[col.key] || "—"}</td>`
					)
					.join("");
				const cls = [row._click ? "is-clickable" : "", row._class || ""]
					.filter(Boolean)
					.join(" ");
				return `<tr data-row="${index}" class="${cls}">${cells}</tr>`;
			})
			.join("");

		const id = "od-" + frappe.utils.get_random(8);
		setTimeout(() => {
			this.$sections.find(`#${id}`).on("click", "tr[data-row]", (event) => {
				const row = rows[$(event.currentTarget).data("row")];
				if (row && row._click) row._click();
			});
		}, 0);

		return `<table id="${id}" class="od-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
	}

	/** Jadvalni yig'ish / yoyish tugmasi (uzun ro'yxatlar uchun). */
	setup_table_toggle($wrap, total, visible) {
		if (total <= visible) return;

		const $button = $(
			`<button type="button" class="od-more"></button>`
		).insertAfter($wrap);

		const paint = () => {
			const expanded = $wrap.hasClass("is-expanded");
			$button.text(
				expanded ? __("Yig'ish") : __("Yana {0} ta ko'rsatish", [total - visible])
			);
		};

		$button.on("click", () => {
			$wrap.toggleClass("is-expanded");
			paint();
		});
		paint();
	}

	// -- drill-down --------------------------------------------------------

	open_report(name, extra) {
		frappe.route_options = Object.assign(
			{
				company: this.state.company,
				from_date: this.context.from_date,
				to_date: this.context.to_date,
			},
			extra || {}
		);
		frappe.set_route("query-report", name);
	}

	open_general_ledger(account) {
		frappe.route_options = {
			company: this.state.company,
			from_date: this.context.from_date,
			to_date: this.context.to_date,
			account: account,
		};
		frappe.set_route("query-report", "General Ledger");
	}
}
