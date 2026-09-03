// Copyright (c) 2026, akfa_diller and contributors
// For license information, please see license.txt
//
// Biznes paneli — 4 kompaniya uchun yagona boshqaruv-dashboard (frontend).
// Dvigatel oyna_dashboard'dan olingan; farqi: kompaniya-almashtirgich va
// profil (trade/orders/production) bo'yicha bo'lim-to'plamlari.
//
// Tuzilishi:
//   • OynaDashboard      — holat (state), filtrlar, ma'lumot olish (fetch)
//   • render_*()         — faqat DOM chizadi, hisob-kitob qilmaydi
//   • chart_*()          — faqat grafiklar
//   • fmt/util           — formatlash yordamchilari
//
// Barcha hisob-kitoblar serverda (oyna_dashboard.py). Bu yerda biznes-mantiq yo'q.

frappe.pages["biznes-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Biznes paneli"),
		single_column: true,
	});

	wrapper.biznes_dashboard = new BiznesDashboard(page, wrapper);
};

frappe.pages["biznes-dashboard"].on_page_show = function (wrapper) {
	if (wrapper.biznes_dashboard && wrapper.biznes_dashboard.ready) {
		wrapper.biznes_dashboard.refresh();
	}
};

const OD_METHOD = "akfa_diller.akfa_diller.api.biznes_dashboard";

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
//: Panel ko'rinishlari — chap menyu (yangi panel qo'shilsa shu ro'yxatga).
const BD_VIEWS = [
	{ key: "umumiy", label: __("Umumiy panel") },
	{ key: "kunlik", label: __("Kunlik panel") },
	{ key: "tahlil", label: __("Savdo tahlili") },
	{ key: "marja", label: __("Marja va rentabellik") },
	{ key: "tovarlar", label: __("Tovarlar tahlili") },
	{ key: "mijozlar", label: __("Mijozlar tahlili") },
];

//: get_tahlil ma'lumotidan oziqlanadigan panellar (bitta so'rov — Redis kesh
//: tufayli panellar orasida almashish serverga qayta bormaydi)
const BD_TAHLIL_OILA = ["tahlil", "marja", "tovarlar", "mijozlar"];

const BD_OYLAR = [
	__("Yanvar"), __("Fevral"), __("Mart"), __("Aprel"), __("May"), __("Iyun"),
	__("Iyul"), __("Avgust"), __("Sentabr"), __("Oktabr"), __("Noyabr"), __("Dekabr"),
];

//: Hafta kunlari — dushanbadan (backend/kalendar ham dushanba-boshli).
const BD_HAFTA = [__("Du"), __("Se"), __("Ch"), __("Pa"), __("Ju"), __("Sh"), __("Ya")];

const bd_saqlash = {
	get(kalit, standart) {
		try { const v = localStorage.getItem(kalit); return v === null ? standart : v; }
		catch (e) { return standart; }
	},
	set(kalit, qiymat) {
		try { localStorage.setItem(kalit, qiymat); } catch (e) { /* jim */ }
	},
};

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

	/** Pul: 456 475 000 UZS — kasr KO'RSATILMAYDI (foydalanuvchi talabi:
	 *  ",dan keyingi sifralar ko'rinmasin"; butun songa yaxlitlanadi). */
	money(value, currency, decimals) {
		if (decimals === undefined) decimals = 0;
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

class BiznesDashboard {
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
		// Ochilishda "oxirgi 30 kun": oy boshida ham panel bo'sh ko'rinmasin
		// (masalan 1-sentabrda "shu oy" hali bitta kun — hammasi 0 chiqardi).
		this.state = {
			view: (() => {
				const v = bd_saqlash.get("bd:view", "umumiy");
				return BD_VIEWS.some((x) => x.key === v) ? v : "umumiy";
			})(),
			kun_yil: null,
			kun_oy: null,
			kun_mijoz: null,
			kun_tanlangan: null,
			tahlil_yil: null,
			tahlil_oylar: [],
			tahlil_metrika: bd_saqlash.get("bd:tahlil-metrika", "savdo"),
			period: "custom",
			from_date: OD_DATE.str(OD_DATE.add_days(OD_DATE.today(), -29)),
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

		this.state.company = this.meta.default;
		this.apply_saved_filial();
		this.render_filters();
		this.ready = true;
		this.apply_view();
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

	/** Kompaniya uchun saqlangan filial-tanlovni holatga tiklaydi. */
	apply_saved_filial() {
		const branches = this.company_meta().branches || [];
		const saqlangan = bd_saqlash.get("bd:filial:" + this.state.company, "");
		const b = branches.find((x) => x.cost_center === saqlangan);
		this.state.cost_center = b ? b.cost_center : null;
		this.state.warehouse = b ? b.warehouse : null;
	}

	company_meta() {
		return (this.meta.companies || []).find((c) => c.name === this.state.company) || {};
	}

	profile() {
		return this.company_meta().profile || "trade";
	}

	profile_sections() {
		return this.company_meta().sections || ["overview"];
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
		// Sahifani to'liq kenglikka ochamiz (chap menyu ekran chekkasida turadi,
		// kontent qisilmaydi) — faqat shu sahifa uchun.
		$(this.wrapper).addClass("bd-page");
		$(this.wrapper).closest(".content.page-container, .page-container").addClass("bd-page-container");
		this.page.main.html(`
			<div class="oyna-dash biznes-dash">
				<div class="bd-layout">
					<aside class="bd-sidenav"></aside>
					<div class="bd-main">
						<div class="od-toolbar"></div>
						<div class="od-context"></div>
						<div class="od-kpis od-kpis--loading"></div>
						<div class="od-sections"></div>
					</div>
				</div>
			</div>
		`);

		this.$sidenav = this.page.main.find(".bd-sidenav");
		this.$toolbar = this.page.main.find(".od-toolbar");
		this.$context = this.page.main.find(".od-context");
		this.$kpis = this.page.main.find(".od-kpis");
		this.$sections = this.page.main.find(".od-sections");

		this.render_sidenav();
		this.$kpis.html(this.skeleton_cards(11));
	}

	// -- chap menyu (dashboards-ilovasi andozasida, bizning uslubda) --------

	render_sidenav() {
		const ochiq = bd_saqlash.get("bd:sidenav-ochiq", "1") === "1";
		this.$sidenav.html(`
			<div class="bd-sidenav__card">
				<div class="bd-sidenav__title">${__("Panellar")}</div>
				<div class="bd-sidenav__group ${ochiq ? "is-open" : ""}">
					<button type="button" class="bd-sidenav__toggle">
						<span>${__("Panellar ro'yxati")}</span>
						<span class="bd-sidenav__arrow"></span>
					</button>
					<div class="bd-sidenav__items">
						${BD_VIEWS.map(
							(v) => `
							<button type="button" class="bd-sidenav__item ${
								v.key === this.state.view ? "is-active" : ""
							}" data-view="${v.key}">
								<span class="bd-sidenav__dot"></span>
								<span>${od.esc(v.label)}</span>
							</button>`
						).join("")}
					</div>
				</div>
			</div>
		`);

		this.$sidenav.on("click", ".bd-sidenav__toggle", () => {
			const $g = this.$sidenav.find(".bd-sidenav__group");
			$g.toggleClass("is-open");
			bd_saqlash.set("bd:sidenav-ochiq", $g.hasClass("is-open") ? "1" : "0");
		});

		this.$sidenav.on("click", ".bd-sidenav__item", (event) => {
			const view = $(event.currentTarget).data("view");
			if (view === this.state.view) return;
			this.state.view = view;
			bd_saqlash.set("bd:view", view);
			this.$sidenav
				.find(".bd-sidenav__item")
				.removeClass("is-active")
				.filter(`[data-view="${view}"]`)
				.addClass("is-active");
			this.apply_view();
			this.refresh();
		});
	}

	/** Ko'rinishga qarab toolbar/kpi zonalarini moslash. */
	/** Toolbar ichidagi davr-kalendari faqat Umumiy panelda ko'rinadi —
	 *  toolbar qayta chizilganda (filial/kompaniya almashganda) ham. */
	sync_toolbar_view() {
		this.$toolbar.find(".od-cal").toggle(this.state.view === "umumiy");
	}

	apply_view() {
		const maxsus = this.state.view !== "umumiy";
		this.sync_toolbar_view();
		this.$kpis.toggle(!maxsus);
		this.$context.toggle(!maxsus);
		if (!maxsus) {
			this.$kpis.addClass("od-kpis--loading").html(this.skeleton_cards(8));
		}
		this.$sections.html(maxsus ? this.skeleton_block(420) : this.sections_placeholder());
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
		const companies = this.meta.companies || [];
		const chips = companies.length > 1
			? `<div class="od-toolbar__row bd-companies">${companies
					.map(
						(c) => `<button type="button" class="bd-comp${
							c.name === this.state.company ? " is-active" : ""
						}" data-company="${od.esc(c.name)}">${od.esc(c.name)}</button>`
					)
					.join("")}</div>`
			: "";

		// Filial-chiplar (faqat trade-kompaniyalar; Report Service Dealer Branch)
		const branches = this.company_meta().branches || [];
		const filial_chips = branches.length
			? `<div class="od-toolbar__row bd-filiallar">
					<span class="bd-filiallar__label">${__("Filial")}</span>
					<button type="button" class="bd-chip${
						!this.state.cost_center ? " is-active" : ""
					}" data-filial="">${__("Hammasi")}</button>
					${branches
						.map(
							(b) => `<button type="button" class="bd-chip${
								b.cost_center === this.state.cost_center ? " is-active" : ""
							}" data-filial="${od.esc(b.cost_center)}" data-wh="${od.esc(
								b.warehouse || ""
							)}">${od.esc(b.label)}</button>`
						)
						.join("")}
				</div>`
			: "";

		this.$toolbar.html(`
			${chips}
			${filial_chips}
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
			<!-- OFF-BELGI:qoshimcha-filtrlar — qo'shimcha filtrlar (kompaniya / filial / ombor / mijoz).
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
		if (this.ready) this.sync_toolbar_view();

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

		this.$toolbar.on("click", ".bd-filiallar .bd-chip[data-filial]", (event) => {
			const cc = $(event.currentTarget).data("filial") || null;
			if ((cc || null) === (this.state.cost_center || null)) return;
			this.state.cost_center = cc;
			this.state.warehouse = $(event.currentTarget).data("wh") || null;
			bd_saqlash.set("bd:filial:" + this.state.company, cc || "");
			this.render_filters();
			this.$kpis.addClass("od-kpis--loading").html(this.skeleton_cards(8));
			this.refresh();
		});

		this.$toolbar.on("click", ".bd-comp[data-company]", (event) => {
			const company = $(event.currentTarget).data("company");
			if (company === this.state.company) return;
			this.state.company = company;
			this.state.kun_yil = null;
			this.state.kun_oy = null;
			this.state.kun_mijoz = null;
			this.state.kun_tanlangan = null;
			this.state.tahlil_yil = null;
			this.state.tahlil_oylar = [];
			this.apply_saved_filial();
			this.render_filters();
			this.$kpis.addClass("od-kpis--loading").html(this.skeleton_cards(8));
			this.refresh();
		});

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
		if (this.state.view === "kunlik") {
			return this.refresh_daily(options);
		}
		if (BD_TAHLIL_OILA.includes(this.state.view)) {
			return this.refresh_tahlil(options);
		}
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
			const body_sections = this.profile_sections().filter((s) => s !== "overview");
			const body = await this.call("get_dashboard", {
				filters: this.get_filters({ refresh: force ? 1 : 0 }),
				sections: body_sections,
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
		// Ikonkali kartada yuqori-o'ng burchak IKONKAGA qoladi — barcha izohlar
		// pastki qatorga tushadi (aks holda ular ustma-ust tushardi).
		const top_count = card.icon ? 0 : 1;
		const details = all_details.slice(0, top_count).map(detail_html).join("");
		const details_foot = all_details.slice(top_count).map(detail_html).join("");

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
					${
						card.icon
							? `<span class="bd-card__icon bd-card__icon--${od.esc(
									card.tone || "info"
							  )}">${frappe.utils.icon(card.icon, "md")}</span>`
							: ""
					}
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

		// STANDART joylashuv hamma kompaniyada bir xil (foydalanuvchi talabi);
		// kompaniyaga xos blok ENG PASTDA qo'shiladi.
		const profile = this.profile();
		const tail = { trade: "branches", orders: "orders", production: "production" }[profile];
		this.$sections.html(`
			<div class="od-row">
				<div class="od-block od-block--full" data-block="sales"></div>
			</div>
			<div class="od-row">
				<div class="od-block od-block--full" data-block="tops"></div>
			</div>
			<div class="od-row">
				<div class="od-block" data-block="cash"></div>
				<div class="od-block" data-block="expenses"></div>
			</div>
			<div class="od-row">
				<div class="od-block od-block--full" data-block="minus_stock"></div>
			</div>
			<div class="od-row">
				<div class="od-block od-block--full" data-block="${tail}"></div>
			</div>
		`);

		// Har bir blok alohida try/catch ichida: bittasida xato bo'lsa,
		// faqat o'sha blok "xatolik" ko'rsatadi, qolganlari normal chiziladi.
		this.safe_render("sales", () => this.render_sales(data.sales));
		this.safe_render("tops", () => this.render_tops(data.tops));
		this.safe_render("minus_stock", () => this.render_minus_stock(data.minus_stock));
		if (profile === "orders") {
			this.safe_render("orders", () => this.render_orders(data.orders));
		} else if (profile === "production") {
			this.safe_render("production", () => this.render_production(data.production));
		} else {
			this.safe_render("branches", () => this.render_branches(data.branches));
		}
				this.safe_render("cash", () => this.render_cash(data.cash));
		this.safe_render("expenses", () => this.render_expenses(data.expenses));
	}

	// -- Top mijozlar / Top tovarlar (standart) -----------------------------

	render_tops(tops) {
		const $body = this.block_shell(
			"tops",
			__("Top ko'rsatkichlar"),
			__("Davr bo'yicha eng katta mijozlar va eng ko'p sotilgan tovarlar")
		);
		if (!this.guard(tops, $body)) return;

		const cur = tops.currency || "USD";
		const KORINADI = 10;

		const mijoz_rows = (tops.customers || []).map((m, i) => ({
			nr: od.number(i + 1),
			name: od.esc(m.name),
			savdo: od.money(m.savdo, cur),
			tolov: od.money(m.tolov, cur),
			docs: od.number(m.docs),
			ulush: m.ulush === null ? "—" : od.percent(m.ulush),
			_class: i >= KORINADI ? "od-row-extra" : "",
			_click: () => {
				frappe.route_options = {
					company: this.state.company,
					customer: m.customer,
					docstatus: 1,
				};
				frappe.set_route("List", "Sales Invoice");
			},
		}));

		const tovar_rows = (tops.items || []).map((t, i) => ({
			nr: od.number(i + 1),
			item: od.esc(t.item),
			qty: od.number(t.qty),
			summa: od.money(t.summa, cur),
			mijozlar: od.number(t.mijozlar),
			ulush: t.ulush === null ? "—" : od.percent(t.ulush),
			_class: i >= KORINADI ? "od-row-extra" : "",
		}));

		$body.html(`
			<div class="bd-tops-grid">
				<div>
					<h4 class="bd-subtitle">${__("Top mijozlar")}</h4>
					<div class="od-table-wrap" data-table="top-mijozlar">${this.table(
						[
							{ key: "nr", label: "#" },
							{ key: "name", label: __("Mijoz") },
							{ key: "savdo", label: __("Savdo"), align: "right" },
							{ key: "tolov", label: __("To'lov"), align: "right" },
							{ key: "docs", label: __("Hujjat"), align: "right" },
							{ key: "ulush", label: __("Ulush"), align: "right" },
						],
						mijoz_rows
					)}</div>
				</div>
				<div>
					<h4 class="bd-subtitle">${__("Top tovarlar")}</h4>
					<div class="od-table-wrap" data-table="top-tovarlar">${this.table(
						[
							{ key: "nr", label: "#" },
							{ key: "item", label: __("Tovar") },
							{ key: "qty", label: __("Dona"), align: "right" },
							{ key: "summa", label: __("Summa"), align: "right" },
							{ key: "mijozlar", label: __("Mijozlar"), align: "right" },
							{ key: "ulush", label: __("Ulush"), align: "right" },
						],
						tovar_rows
					)}</div>
				</div>
			</div>
		`);

		this.setup_table_toggle(
			$body.find('[data-table="top-mijozlar"]'), mijoz_rows.length, KORINADI);
		this.setup_table_toggle(
			$body.find('[data-table="top-tovarlar"]'), tovar_rows.length, KORINADI);
	}

	// -- KUNLIK PANEL: oy-kalendar (savdo-heatmap) --------------------------

	async refresh_daily(options) {
		const token = (this._request = (this._request || 0) + 1);
		const stale = () => token !== this._request;
		const force = options && options.force;
		this.$toolbar.find(".od-refresh").addClass("is-busy");
		try {
			const data = await this.call("get_daily", {
				filters: {
					company: this.state.company,
					cost_center: this.state.cost_center,
					warehouse: this.state.warehouse,
					yil: this.state.kun_yil,
					oy: this.state.kun_oy,
					mijoz: this.state.kun_mijoz,
					kun: this.state.kun_tanlangan,
					refresh: force ? 1 : 0,
				},
			});
			if (stale()) return;
			this.state.kun_yil = data.yil;
			this.state.kun_oy = data.oy;
			this.render_daily(data);
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

	render_daily(data) {
		const cur = data.currency || "USD";
		const bugun = OD_DATE.today();
		const oy_nomi = BD_OYLAR[data.oy - 1];

		// --- yil/oy chiplari ---
		const yil_chips = (data.yillar || [data.yil])
			.map(
				(y) => `<button type="button" class="bd-chip ${
					y === data.yil ? "is-active" : ""
				}" data-yil="${y}">${y}</button>`
			)
			.join("");
		const oy_chips = BD_OYLAR.map(
			(nom, i) => `<button type="button" class="bd-chip ${
				i + 1 === data.oy ? "is-active" : ""
			}" data-oy="${i + 1}">${od.esc(nom)}</button>`
		).join("");

		// --- mijoz-chiplar (oyning top mijozlari, bosilsa filtr) ---
		const mijoz_chips = (data.mijozlar || [])
			.map((m) => {
				const qisqa = (m.name || "").split("(")[0].trim().slice(0, 26);
				return `<button type="button" class="bd-chip bd-chip--mijoz ${
					m.customer === data.mijoz ? "is-active" : ""
				}" data-mijoz="${od.esc(m.customer)}" title="${od.esc(m.name)} — ${od.money(
					m.savdo, cur
				)}">${od.esc(qisqa)}</button>`;
			})
			.join("");

		// --- faol filtr belgilari ---
		const filtrlar = [];
		if (data.mijoz) {
			const m = (data.mijozlar || []).find((x) => x.customer === data.mijoz);
			filtrlar.push(
				`<span class="bd-filtr">${od.esc((m && m.name) || data.mijoz)}
					<button type="button" data-tozala="mijoz">×</button></span>`
			);
		}
		if (data.kun) {
			filtrlar.push(
				`<span class="bd-filtr">${data.kun}-${od.esc(oy_nomi)}
					<button type="button" data-tozala="kun">×</button></span>`
			);
		}

		// --- KPI qatori (qamrov: oy yoki tanlangan kun) ---
		const k = data.kpi || {};
		const kpi = `
			<div class="bd-kpi-strip">
				<div class="bd-kpi"><span>${__("Savdo")}</span><b>${od.money(k.savdo || 0, cur)}</b></div>
				<div class="bd-kpi"><span>${__("Tannarx")}</span><b>${od.money(k.tannarx || 0, cur)}</b></div>
				<div class="bd-kpi"><span>${__("Marja")}</span><b class="${
					(k.marja || 0) >= 0 ? "od-good" : "od-bad"
				}">${od.money(k.marja || 0, cur)}${
					k.marja_pct === null || k.marja_pct === undefined
						? ""
						: ` <small>(${od.percent(k.marja_pct)})</small>`
				}</b></div>
				<div class="bd-kpi"><span>${__("Hujjatlar")}</span><b>${od.number(k.docs || 0)}</b></div>
				<div class="bd-kpi"><span>${__("Mijozlar")}</span><b>${od.number(k.mijozlar || 0)}</b></div>
				<div class="bd-kpi"><span>${__("O'rtacha chek")}</span><b>${od.money(
					k.ortacha_chek || 0, cur
				)}</b></div>
				<div class="bd-kpi"><span>${__("Eng yaxshi kun")}</span><b>${
					k.eng_kun ? `${k.eng_kun}-${od.esc(oy_nomi)} · ${od.compact(k.eng_summa, cur)}` : "—"
				}</b></div>
			</div>`;

		// --- kalendar ---
		const birinchi = new Date(data.yil, data.oy - 1, 1);
		const offset = (birinchi.getDay() + 6) % 7;
		const kunlar = data.kunlar || {};
		const summalar = Object.values(kunlar).map((x) => x.summa).filter(Boolean);
		const max = Math.max(...summalar, 1);
		const heat = (summa) => {
			const nisbat = summa / max;
			if (nisbat >= 0.85) return 5;
			if (nisbat >= 0.65) return 4;
			if (nisbat >= 0.45) return 3;
			if (nisbat >= 0.2) return 2;
			return 1;
		};
		const cells = [];
		for (let i = 0; i < offset; i += 1) {
			cells.push('<div class="bd-kun bd-kun--bosh"></div>');
		}
		for (let kun = 1; kun <= data.kunlar_soni; kun += 1) {
			const v = kunlar[kun];
			const joriy =
				bugun.getFullYear() === data.yil &&
				bugun.getMonth() + 1 === data.oy &&
				bugun.getDate() === kun;
			cells.push(`
				<div class="bd-kun ${v ? `bd-kun--h${heat(v.summa)} is-bosiladigan` : "bd-kun--nol"}${
					joriy ? " bd-kun--bugun" : ""
				}${data.kun === kun ? " bd-kun--tanlangan" : ""}" data-kun="${v ? kun : ""}"
					${v ? `title="${od.money(v.summa, cur)} · ${v.docs} ${__("hujjat")}"` : ""}>
					<div class="bd-kun__raqam">${kun}</div>
					<div class="bd-kun__summa">${v ? od.compact(v.summa) : ""}</div>
				</div>
			`);
		}

		// --- tovarlar jadvali ---
		const tovar_rows = (data.tovarlar || []).map((t, i) => ({
			nr: od.number(i + 1),
			item: od.esc(t.item),
			qty: od.number(t.qty),
			summa: od.money(t.summa, cur),
			tannarx: od.money(t.tannarx, cur),
			marja: `<span class="${t.marja >= 0 ? "od-good" : "od-bad"}">${od.money(
				t.marja, cur
			)}</span>`,
			pct: t.marja_pct === null ? "—" : od.percent(t.marja_pct),
			_class: i >= 15 ? "od-row-extra" : "",
		}));
		// JAMI — backend'dagi TO'LIQ qamrov yig'indisi (jadval 60 qator bilan
		// kesiladi; ko'rinayotganini yig'ish oy jamlamasini kam ko'rsatardi)
		const tj = data.tovarlar_jami || {};
		const jami_summa = tj.summa || 0;
		const jami_tan = tj.tannarx || 0;
		tovar_rows.push({
			nr: "",
			item: `<b>${__("JAMI")}</b>${
				(tj.soni || 0) > (data.tovarlar || []).length
					? ` <small class="bd-jami-izoh">${__("{0} tovar bo'yicha", [
							od.number(tj.soni),
					  ])}</small>`
					: ""
			}`,
			qty: tj.qty ? `<b>${od.number(tj.qty)}</b>` : "",
			summa: `<b>${od.money(jami_summa, cur)}</b>`,
			tannarx: `<b>${od.money(jami_tan, cur)}</b>`,
			marja: `<b>${od.money(jami_summa - jami_tan, cur)}</b>`,
			pct: jami_summa
				? `<b>${od.percent(((jami_summa - jami_tan) / jami_summa) * 100)}</b>`
				: "—",
			_class: "bd-total-row",
		});

		this.$sections.html(`
			<div class="od-block od-block--full bd-kunlik">
				<div class="od-block__head">
					<div>
						<h3 class="od-block__title">${__("Kunlik savdo")} — ${od.esc(oy_nomi)} ${data.yil}</h3>
						<p class="od-block__subtitle">${__(
							"Kalendar kunini yoki mijozni bosing — jadval va ko'rsatkichlar shunga filtrlanadi"
						)}</p>
					</div>
					<div class="bd-filtrlar">${filtrlar.join("")}</div>
				</div>
				<div class="od-block__body">
					<div class="bd-chips">${yil_chips}<span class="bd-chips__ajratgich"></span>${oy_chips}</div>
					${mijoz_chips ? `<div class="bd-chips bd-chips--mijozlar">${mijoz_chips}</div>` : ""}
					${kpi}
					<div class="bd-kunlik-grid">
						<div class="bd-kunlik-kalendar">
							<div class="bd-hafta">${BD_HAFTA.map((h) => `<div>${od.esc(h)}</div>`).join("")}</div>
							<div class="bd-kalendar">${cells.join("")}</div>
						</div>
						<div class="bd-kunlik-tovarlar">
							<h4 class="bd-subtitle">${__("Tovarlar")}${
								data.kun ? ` — ${data.kun}-${od.esc(oy_nomi)}` : ""
							}</h4>
							<div class="od-table-wrap" data-table="kun-tovarlar">${this.table(
								[
									{ key: "nr", label: "#" },
									{ key: "item", label: __("Tovar") },
									{ key: "qty", label: __("Dona"), align: "right" },
									{ key: "summa", label: __("Savdo"), align: "right" },
									{ key: "tannarx", label: __("Tannarx"), align: "right" },
									{ key: "marja", label: __("Marja"), align: "right" },
									{ key: "pct", label: "%", align: "right" },
								],
								tovar_rows
							)}</div>
						</div>
					</div>
				</div>
			</div>
		`);
		this.setup_table_toggle(
			this.$sections.find('[data-table="kun-tovarlar"]'),
			tovar_rows.length - 1,
			15
		);

		// --- hodisalar ---
		this.$sections.off("click.bdkun");
		this.$sections.on("click.bdkun", ".bd-chip[data-yil]", (e) => {
			this.state.kun_yil = Number($(e.currentTarget).data("yil"));
			this.state.kun_tanlangan = null;
			this.refresh_daily();
		});
		this.$sections.on("click.bdkun", ".bd-chip[data-oy]", (e) => {
			this.state.kun_oy = Number($(e.currentTarget).data("oy"));
			this.state.kun_tanlangan = null;
			this.refresh_daily();
		});
		this.$sections.on("click.bdkun", ".bd-chip[data-mijoz]", (e) => {
			const m = $(e.currentTarget).data("mijoz");
			this.state.kun_mijoz = this.state.kun_mijoz === m ? null : m;
			this.refresh_daily();
		});
		this.$sections.on("click.bdkun", ".bd-kun.is-bosiladigan", (e) => {
			const kun = Number($(e.currentTarget).data("kun"));
			if (!kun) return;
			this.state.kun_tanlangan = this.state.kun_tanlangan === kun ? null : kun;
			this.refresh_daily();
		});
		this.$sections.on("click.bdkun", "[data-tozala]", (e) => {
			const t = $(e.currentTarget).data("tozala");
			if (t === "mijoz") this.state.kun_mijoz = null;
			if (t === "kun") this.state.kun_tanlangan = null;
			this.refresh_daily();
		});
	}

	// -- SAVDO TAHLILI: yil/oy filtri + grafik + donut + marja-jadvallar ----

	async refresh_tahlil(options) {
		const token = (this._request = (this._request || 0) + 1);
		const stale = () => token !== this._request;
		const force = options && options.force;
		this.$toolbar.find(".od-refresh").addClass("is-busy");
		try {
			const data = await this.call("get_tahlil", {
				filters: {
					company: this.state.company,
					cost_center: this.state.cost_center,
					warehouse: this.state.warehouse,
					yil: this.state.tahlil_yil,
					oylar: this.state.tahlil_oylar,
					refresh: force ? 1 : 0,
				},
			});
			if (stale()) return;
			this.state.tahlil_yil = data.yil;
			this._tahlil_data = data;
			this.render_tahlil(data);
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

	render_tahlil(data) {
		const cur = data.currency || "USD";
		const metrika = this.state.tahlil_metrika || "savdo";
		const k = data.kpi || {};
		const oylar = data.oylar || [];
		const davr_nomi = oylar.length
			? oylar.map((o) => BD_OYLAR[o - 1]).join(", ") + " " + data.yil
			: __("Butun {0}-yil", [data.yil]);

		const yil_chips = (data.yillar || [data.yil])
			.map(
				(y) => `<button type="button" class="bd-chip ${
					y === data.yil ? "is-active" : ""
				}" data-t-yil="${y}">${y}</button>`
			)
			.join("");
		const oy_chips = BD_OYLAR.map(
			(nom, i) => `<button type="button" class="bd-chip ${
				oylar.includes(i + 1) ? "is-active" : ""
			}" data-t-oy="${i + 1}">${od.esc(nom)}</button>`
		).join("");

		const kpi = `
			<div class="bd-kpi-strip">
				<div class="bd-kpi"><span>${__("Savdo")}</span><b>${od.money(k.savdo || 0, cur)}</b></div>
				<div class="bd-kpi"><span>${__("Tannarx")}</span><b>${od.money(k.tannarx || 0, cur)}</b></div>
				<div class="bd-kpi"><span>${__("Marja")}</span><b class="${
					(k.marja || 0) >= 0 ? "od-good" : "od-bad"
				}">${od.money(k.marja || 0, cur)}${
					k.marja_pct === null || k.marja_pct === undefined
						? ""
						: ` <small>(${od.percent(k.marja_pct)})</small>`
				}</b></div>
				<div class="bd-kpi"><span>${__("Vozvrat")}</span><b class="${
					k.vozvrat ? "od-bad" : ""
				}">${od.money(k.vozvrat || 0, cur)}</b></div>
				<div class="bd-kpi"><span>${__("Dona")}</span><b>${od.number(k.dona || 0)}</b></div>
				<div class="bd-kpi"><span>${__("Hujjatlar")}</span><b>${od.number(k.docs || 0)}</b></div>
				<div class="bd-kpi"><span>${__("O'rtacha chek")}</span><b>${od.money(
					k.ortacha_chek || 0, cur
				)}</b></div>
			</div>`;

		// --- donut: savdo tarkibi (tannarx + marja) — conic-gradient, kutubxonasiz ---
		const savdo = k.savdo || 0;
		const t_ulush = savdo > 0 ? Math.min((k.tannarx / savdo) * 100, 100) : 0;
		const donut = `
			<div class="bd-donut">
				<div class="bd-donut__ring" style="background: conic-gradient(
					var(--bd-primary) 0 ${t_ulush.toFixed(1)}%,
					var(--bd-success) ${t_ulush.toFixed(1)}% 100%)">
					<div class="bd-donut__center">
						<b class="${(k.marja || 0) >= 0 ? "od-good" : "od-bad"}">${od.compact(k.marja || 0)}</b>
						<span>${__("marja")}</span>
					</div>
				</div>
				<div class="bd-donut__legend">
					<div class="bd-donut__jami"><i class="bd-donut__mini" style="background: conic-gradient(
							var(--bd-primary) 0 ${t_ulush.toFixed(1)}%,
							var(--bd-success) ${t_ulush.toFixed(1)}% 100%)"></i>${__(
						"Savdo — butun doira"
					)}<b>${od.money(k.savdo || 0, cur)}</b></div>
					<div><i style="background:var(--bd-primary)"></i>${__("Tannarx")}
						<small class="bd-donut__ulush">${od.percent(t_ulush)}</small>
						<b>${od.money(k.tannarx || 0, cur)}</b></div>
					<div><i style="background:var(--bd-success)"></i>${__("Yalpi marja")}
						<small class="bd-donut__ulush">${od.percent(100 - t_ulush)}</small>
						<b>${od.money(k.marja || 0, cur)}</b></div>
					<div><i class="bd-donut__hollow"></i>${__("Vozvrat (savdodan ayirilgan)")}
						<b>${od.money(k.vozvrat || 0, cur)}</b></div>
				</div>
			</div>`;

		const tovar_rows = (data.tovarlar || []).map((t, i) => ({
			nr: od.number(i + 1),
			item: od.esc(t.item),
			qty: od.number(t.qty),
			summa: od.money(t.summa, cur),
			tannarx: od.money(t.tannarx, cur),
			marja: `<span class="${t.marja >= 0 ? "od-good" : "od-bad"}">${od.money(t.marja, cur)}</span>`,
			pct: t.marja_pct === null ? "—" : od.percent(t.marja_pct),
			_class: i >= 12 ? "od-row-extra" : "",
		}));
		const mijoz_rows = (data.mijozlar || []).map((m, i) => ({
			nr: od.number(i + 1),
			name: od.esc(m.name),
			summa: od.money(m.summa, cur),
			tannarx: od.money(m.tannarx, cur),
			marja: `<span class="${m.marja >= 0 ? "od-good" : "od-bad"}">${od.money(m.marja, cur)}</span>`,
			pct: m.marja_pct === null ? "—" : od.percent(m.marja_pct),
			docs: od.number(m.docs),
			_class: i >= 12 ? "od-row-extra" : "",
		}));

		// --- blok-bo'laklar: har panel (chap menyu) o'ziga keraklisini yig'adi ---
		const filtrbar = `
			<div class="od-block od-block--full bd-filtrbar">
				<div class="bd-filtrbar__qator">
					<span class="bd-filtrbar__label">${__("Yil")}</span>${yil_chips}
					<span class="bd-chips__ajratgich"></span>
					<span class="bd-filtrbar__label">${__("Oylar")}</span>${oy_chips}
					${
						oylar.length
							? `<button type="button" class="od-link" data-t-tozala="1">${__(
									"Hammasi"
							  )} ×</button>`
							: ""
					}
				</div>
			</div>`;

		const kpi_blok = `
			<div class="od-block od-block--full">
				<div class="od-block__head"><div>
					<h3 class="od-block__title">${__("Ko'rsatkichlar")} — ${od.esc(davr_nomi)}</h3>
				</div></div>
				<div class="od-block__body">${kpi}</div>
			</div>`;

		const grafik_blok = `
			<div class="od-block bd-tahlil-chart" data-block="t-grafik">
				<div class="od-block__head"><div>
					<h3 class="od-block__title">${
						metrika === "dona" ? __("Oylik savdo (dona)") : __("Oylik savdo hajmi")
					}</h3>
					<p class="od-block__subtitle">${data.yil}${__("-yil, oylar kesimida")}</p>
				</div>
				<div class="bd-metrika">
					<button type="button" class="bd-chip ${
						metrika === "savdo" ? "is-active" : ""
					}" data-t-metrika="savdo">${__("Savdo")}</button>
					<button type="button" class="bd-chip ${
						metrika === "dona" ? "is-active" : ""
					}" data-t-metrika="dona">${__("Dona")}</button>
				</div></div>
				<div class="od-block__body"><div class="od-chart" data-chart="tahlil"></div></div>
			</div>`;

		const donut_blok = `
			<div class="od-block bd-tahlil-donut">
				<div class="od-block__head"><div>
					<h3 class="od-block__title">${__("Savdo tarkibi")}</h3>
					<p class="od-block__subtitle">${od.esc(davr_nomi)}</p>
				</div></div>
				<div class="od-block__body">${donut}</div>
			</div>`;

		const balans_blok = `
			<div class="od-block bd-balans">
				<div class="od-block__head"><div>
					<h3 class="od-block__title">${__("Balans detallari")}</h3>
					<p class="od-block__subtitle">${__("Bugungi holat")} · ${od.esc(
						(data.balans || {}).sana || ""
					)}</p>
				</div></div>
				<div class="od-block__body">
					<div class="bd-balans__rows">
						<div><span>${__("Naqd kassa")}</span><b>${this.money((data.balans || {}).naqd)}</b></div>
						<div><span>${__("Plastik / karta")}</span><b>${this.money((data.balans || {}).bank)}</b></div>
						<div><span>${__((data.balans || {}).mijoz_label || "Mijozlar balansi")}</span><b>${this.money(
							(data.balans || {}).mijoz
						)}</b></div>
						<div><span>${__("Ombor qiymati")}</span><b>${this.money((data.balans || {}).ombor)}</b></div>
						<div><span>${__("Kreditorlar (minus)")}</span><b>${this.money(
							(data.balans || {}).kreditor
						)}</b></div>
					</div>
					${
						(data.balans || {}).jami === null ||
						(data.balans || {}).jami === undefined
							? ""
							: `<div class="bd-balans__jami"><span>${__(
									"UMUMIY BALANS"
							  )}</span><b class="${
									data.balans.jami >= 0 ? "od-good" : "od-bad"
							  }">${od.money(data.balans.jami, cur)}</b></div>`
					}
					${this.balans_trend_html((data.balans || {}).trend, cur)}
				</div>
			</div>`;

		const treemap_blok = `
			<div class="od-block bd-xarita od-block--full">
				<div class="od-block__head"><div>
					<h3 class="od-block__title">${__("Mijozlar xaritasi")}</h3>
					<p class="od-block__subtitle">${__("Top-10 mijoz — marja hajmi bo'yicha")} · ${od.esc(
						davr_nomi
					)}</p>
				</div></div>
				<div class="od-block__body">
					<div class="bd-treemap">${this.treemap_html(
						(data.mijozlar || [])
							.filter((m) => m.marja > 0)
							.slice(0, 10)
							.map((m) => ({
								label: (m.name || "").split("(")[0].trim(),
								value: m.marja,
								title: `${m.name} — ${od.money(m.marja, cur)} (${
									m.marja_pct === null ? "—" : od.percent(m.marja_pct)
								})`,
							}))
					)}</div>
				</div>
			</div>`;

		const marja_blok = `
			<div class="od-block bd-marja-blok od-block--full">
				<div class="od-block__head"><div>
					<h3 class="od-block__title">${__("Oylik marja va rentabellik")}</h3>
					<p class="od-block__subtitle">${data.yil}${__("-yil")} · ${__(
						"yil bo'yicha"
					)}: ${k.marja_pct === null || k.marja_pct === undefined ? "—" : od.percent(k.marja_pct)}</p>
				</div></div>
				<div class="od-block__body">${this.marja_grafik_html(
					data.marja_oylik || [], oylar, cur
				)}</div>
			</div>`;

		const v_yil_summa = (data.vozvrat_oylik || []).reduce((a, v) => a + (v.summa || 0), 0);
		const v_yil_dona = (data.vozvrat_oylik || []).reduce((a, v) => a + (v.dona || 0), 0);
		const vozvrat_blok = `
			<div class="od-block">
				<div class="od-block__head"><div>
					<h3 class="od-block__title">${__("Vozvratlar tahlili")}</h3>
					<p class="od-block__subtitle">${data.yil}${__("-yil, oylar kesimida")}</p>
				</div></div>
				<div class="od-block__body">
					<div class="bd-kpi-strip bd-kpi-strip--zich">
						<div class="bd-kpi"><span>${__("Davr vozvrati")}</span><b class="${
							k.vozvrat ? "od-bad" : ""
						}">${od.money(k.vozvrat || 0, cur)}</b></div>
						<div class="bd-kpi"><span>${__("Brutto savdoga nisbati")}</span><b>${
							(k.savdo || 0) + (k.vozvrat || 0) > 0
								? od.percent(
										((k.vozvrat || 0) / ((k.savdo || 0) + (k.vozvrat || 0))) * 100
								  )
								: "—"
						}</b></div>
						<div class="bd-kpi"><span>${__("Yil jami")}</span><b>${od.money(v_yil_summa, cur)} · ${od.number(
							v_yil_dona
						)} ${__("dona")}</b></div>
					</div>
					${this.vozvrat_jadval_html(data.vozvrat_oylik || [], oylar, cur)}
				</div>
			</div>`;

		const tovar_blok = `
			<div class="od-block od-block--full">
				<div class="od-block__head"><div>
					<h3 class="od-block__title">${__("Tovarlar — marja bilan")}</h3>
					<p class="od-block__subtitle">${od.esc(davr_nomi)} · ${__("top-100 savdo bo'yicha")}</p>
				</div></div>
				<div class="od-block__body">
					<div class="od-table-wrap" data-table="t-tovarlar">${this.table(
						[
							{ key: "nr", label: "#" },
							{ key: "item", label: __("Tovar") },
							{ key: "qty", label: __("Dona"), align: "right" },
							{ key: "summa", label: __("Savdo"), align: "right" },
							{ key: "tannarx", label: __("Tannarx"), align: "right" },
							{ key: "marja", label: __("Marja"), align: "right" },
							{ key: "pct", label: "%", align: "right" },
						],
						tovar_rows
					)}</div>
				</div>
			</div>`;

		const mijoz_blok = `
			<div class="od-block od-block--full">
				<div class="od-block__head"><div>
					<h3 class="od-block__title">${__("Mijozlar — marja bilan")}</h3>
					<p class="od-block__subtitle">${od.esc(davr_nomi)} · ${__("top-100 savdo bo'yicha")}</p>
				</div></div>
				<div class="od-block__body">
					<div class="od-table-wrap" data-table="t-mijozlar">${this.table(
						[
							{ key: "nr", label: "#" },
							{ key: "name", label: __("Mijoz") },
							{ key: "summa", label: __("Savdo"), align: "right" },
							{ key: "tannarx", label: __("Tannarx"), align: "right" },
							{ key: "marja", label: __("Marja"), align: "right" },
							{ key: "pct", label: "%", align: "right" },
							{ key: "docs", label: __("Hujjat"), align: "right" },
						],
						mijoz_rows
					)}</div>
				</div>
			</div>`;

		// --- panelga qarab joylashuv ---
		const view = this.state.view;
		let tana;
		if (view === "marja") {
			tana = filtrbar + marja_blok + kpi_blok;
		} else if (view === "tovarlar") {
			tana = filtrbar + tovar_blok;
		} else if (view === "mijozlar") {
			tana = filtrbar + treemap_blok + mijoz_blok;
		} else {
			// Savdo tahlili: balans va vozvratlar ham shu yerda (eski joyida)
			tana =
				filtrbar +
				`<div class="od-row">${grafik_blok}${donut_blok}</div>` +
				kpi_blok +
				`<div class="od-row">${balans_blok}${vozvrat_blok}</div>`;
		}
		this.$sections.html(tana);
		if (view === "tovarlar") {
			this.setup_table_toggle(
				this.$sections.find('[data-table="t-tovarlar"]'), tovar_rows.length, 15
			);
		}
		if (view === "mijozlar") {
			this.setup_table_toggle(
				this.$sections.find('[data-table="t-mijozlar"]'), mijoz_rows.length, 15
			);
		}

		// grafik faqat "Savdo tahlili" panelida chiziladi
		Object.values(this.charts).forEach((c) => c && c.destroy && c.destroy());
		this.charts = {};
		if (view === "tahlil") {
			const yil_qatlamlari =
				(metrika === "dona" ? data.dona_yillar : data.oylik_yillar) || [];
			const rang_palitra = [OD_COLORS.muted, OD_COLORS.violet, OD_COLORS.primary];
			this.charts.tahlil = new frappe.Chart(
				this.$sections.find('[data-chart="tahlil"]')[0],
				{
					type: "bar",
					height: 280,
					animate: false,
					colors: rang_palitra.slice(rang_palitra.length - yil_qatlamlari.length),
					barOptions: { spaceRatio: 0.4 },
					data: {
						labels: OD_MONTHS_SHORT,
						datasets: yil_qatlamlari.map((q) => ({
							name: String(q.yil),
							values: q.values,
						})),
					},
					axisOptions: { xAxisMode: "tick", shortenYAxisNumbers: 1 },
					tooltipOptions: {
						formatTooltipY: (v) =>
							metrika === "dona"
								? od.number(v) + " " + __("dona")
								: od.compact(v, cur),
					},
				}
			);
		}

		// hodisalar
		this.$sections.off("click.bdtahlil");
		this.$sections.on("click.bdtahlil", ".bd-chip[data-t-yil]", (e) => {
			this.state.tahlil_yil = Number($(e.currentTarget).data("t-yil"));
			this.state.tahlil_oylar = [];
			this.refresh_tahlil();
		});
		this.$sections.on("click.bdtahlil", ".bd-chip[data-t-oy]", (e) => {
			const oy = Number($(e.currentTarget).data("t-oy"));
			const idx = this.state.tahlil_oylar.indexOf(oy);
			if (idx >= 0) this.state.tahlil_oylar.splice(idx, 1);
			else this.state.tahlil_oylar.push(oy);
			this.refresh_tahlil();
		});
		this.$sections.on("click.bdtahlil", "[data-t-tozala]", () => {
			this.state.tahlil_oylar = [];
			this.refresh_tahlil();
		});
		this.$sections.on("click.bdtahlil", "[data-t-metrika]", (e) => {
			const m = $(e.currentTarget).data("t-metrika");
			if (m === this.state.tahlil_metrika) return;
			this.state.tahlil_metrika = m;
			bd_saqlash.set("bd:tahlil-metrika", m);
			// serverga bormay qayta chizamiz — ma'lumot allaqachon qo'lda
			if (this._tahlil_data) this.render_tahlil(this._tahlil_data);
		});
	}

	/** Umumiy balans 12-oylik mini-trend (andoza: Главный дашборд).
	 *  Yashil — o'sish, qizil — pasayish; title'da aniq summa va ±%. */
	balans_trend_html(trend, cur) {
		const bor = (trend || []).filter((t) => t.value !== null && t.value !== undefined);
		if (bor.length < 2) return "";
		const max = Math.max(...bor.map((t) => Math.abs(t.value)), 1);
		const bars = (trend || [])
			.map((t) => {
				if (t.value === null || t.value === undefined)
					return `<div class="bd-trend__bar bd-trend__bar--bosh"></div>`;
				const h = Math.max(8, (Math.abs(t.value) / max) * 100);
				const yonalish =
					t.pct === null || t.pct === undefined
						? ""
						: t.pct >= 0
						? " is-up"
						: " is-down";
				const title = `${BD_OYLAR[t.oy - 1]}: ${od.money(t.value, cur)}${
					t.pct === null || t.pct === undefined
						? ""
						: ` (${t.pct >= 0 ? "+" : ""}${od.number(t.pct, 1)}%)`
				}`;
				return `<div class="bd-trend__bar${yonalish}" style="height:${h.toFixed(0)}%"
					title="${od.esc(title)}"></div>`;
			})
			.join("");
		return `<div class="bd-trend">
			<div class="bd-trend__sarlavha">${__("Umumiy balans dinamikasi")}</div>
			<div class="bd-trend__maydon">${bars}</div>
		</div>`;
	}

	/** Oylik marja ustun-grafigi (kutubxonasiz): ustun balandligi — marja
	 *  hajmi, ostida rentabellik %; tanlangan oylar aksentda. */
	marja_grafik_html(marja_oylik, oylar, cur) {
		if (!marja_oylik.length || marja_oylik.every((m) => !m.marja)) {
			return this.empty_state();
		}
		const max = Math.max(...marja_oylik.map((m) => Math.abs(m.marja)), 1);
		const cols = marja_oylik
			.map((m) => {
				const h = Math.max(m.marja ? 6 : 2, (Math.abs(m.marja) / max) * 100);
				const tanlangan = oylar.length && oylar.includes(m.oy) ? " is-tanlangan" : "";
				return `<div class="bd-mgraf__col${tanlangan}" title="${od.esc(
					`${BD_OYLAR[m.oy - 1]}: ${od.money(m.marja, cur)}${
						m.pct === null ? "" : ` (${od.percent(m.pct)})`
					}`
				)}">
					<span class="bd-mgraf__qiymat ${m.marja >= 0 ? "od-good" : "od-bad"}">${
						m.marja ? od.compact(m.marja) : ""
					}</span>
					<div class="bd-mgraf__ustun-joy">
						<div class="bd-mgraf__ustun ${
							m.marja >= 0 ? "is-musbat" : "is-manfiy"
						}" style="height:${h.toFixed(0)}%"></div>
					</div>
					<span class="bd-mgraf__pct">${m.pct === null ? "" : od.percent(m.pct)}</span>
					<span class="bd-mgraf__oy">${OD_MONTHS_SHORT[m.oy - 1]}</span>
				</div>`;
			})
			.join("");
		return `<div class="bd-mgraf">${cols}</div>`;
	}

	/** Vozvratlar 12-oylik jadvali (andoza: АНАЛИЗ ВОЗВРАТОВ). */
	vozvrat_jadval_html(vozvrat_oylik, oylar, cur) {
		if (!vozvrat_oylik.length || vozvrat_oylik.every((v) => !v.summa && !v.dona)) {
			return this.empty_state(__("Bu yilda vozvrat qayd etilmagan."));
		}
		const jami_dona = vozvrat_oylik.reduce((a, v) => a + (v.dona || 0), 0);
		const jami_summa = vozvrat_oylik.reduce((a, v) => a + (v.summa || 0), 0);
		const rows = vozvrat_oylik
			.map(
				(v) => `<tr class="${
					oylar.length && oylar.includes(v.oy) ? "is-tanlangan" : ""
				}${v.summa || v.dona ? "" : " bd-xira"}">
					<td>${od.esc(BD_OYLAR[v.oy - 1])}</td>
					<td class="od-align-right">${v.dona ? od.number(v.dona) : "—"}</td>
					<td class="od-align-right">${v.summa ? od.money(v.summa, cur) : "—"}</td>
				</tr>`
			)
			.join("");
		return `<div class="od-table-wrap"><table class="od-table bd-vozvrat-jadval">
			<thead><tr><th>${__("Oy")}</th><th class="od-align-right">${__("Dona")}</th>
			<th class="od-align-right">${__("Summa")}</th></tr></thead>
			<tbody>${rows}</tbody>
			<tfoot><tr><td><b>${__("JAMI")}</b></td>
			<td class="od-align-right"><b>${od.number(jami_dona)}</b></td>
			<td class="od-align-right"><b>${od.money(jami_summa, cur)}</b></td></tr></tfoot>
		</table></div>`;
	}

	/** Sodda ikki-qatorli proporsional treemap (kutubxonasiz).
	 *  Katta 4 tasi yuqori qatorda, qolganlari pastda; kenglik — qiymatga
	 *  proporsional, rang-intensivlik ham shunga bog'liq. */
	treemap_html(items) {
		if (!items || !items.length) return this.empty_state();
		const jami = items.reduce((a, x) => a + x.value, 0) || 1;
		const band1 = items.slice(0, Math.min(4, items.length));
		const band2 = items.slice(band1.length);
		const max = items[0].value || 1;

		const band = (rows) => {
			const bj = rows.reduce((a, x) => a + x.value, 0) || 1;
			return `<div class="bd-treemap__band">${rows
				.map((x) => {
					// Rang-intensivlik 45%..92% oralig'ida; 60%+ da oq matn,
					// undan pastda odatiy matn-rang (kontrast buzilmasin).
					const foiz = 45 + (x.value / max) * 47;
					const och = foiz < 60;
					return `<div class="bd-treemap__cell${
						och ? " bd-treemap__cell--och" : ""
					}" title="${od.esc(x.title)}"
						style="flex-grow:${(x.value / bj) * 1000};
						       background: color-mix(in srgb, var(--bd-primary) ${foiz.toFixed(
									0
							  )}%, var(--fg-color))">
						<span class="bd-treemap__label">${od.esc(x.label.slice(0, 22))}</span>
						<span class="bd-treemap__value">${od.compact(x.value)}</span>
						<span class="bd-treemap__pct">${od.percent((x.value / jami) * 100)}</span>
					</div>`;
				})
				.join("")}</div>`;
		};
		return band(band1) + (band2.length ? band(band2) : "");
	}

	// -- Filiallar jadvali (trade) ----------------------------------------

	render_branches(branches) {
		const $body = this.block_shell(
			"branches",
			__("Filiallar"),
			__("Davrdagi savdo/tushum va sana holatidagi qoldiqlar — filial kesimida")
		);
		if (!this.guard(branches, $body)) return;

		const cur = branches.currency || "USD";
		const rows = (branches.rows || []).map((r) => ({
			label: `${r.is_main ? "★ " : ""}${od.esc(r.label)}`,
			savdo: od.money(r.savdo, cur),
			tushum: od.money(r.tushum, cur),
			kassa: (r.kassa || []).length
				? r.kassa.map((e) => od.money(e.amount, e.currency)).join("<br>")
				: "—",
			ombor: od.money(r.ombor, cur),
			minus: r.minus
				? `<span class="od-pill od-pill--red">${od.number(r.minus)}</span>`
				: `<span class="od-pill od-pill--green">0</span>`,
		}));

		const t = branches.totals || {};
		rows.push({
			label: `<b>${__("JAMI")}</b>`,
			savdo: `<b>${od.money(t.savdo, cur)}</b>`,
			tushum: `<b>${od.money(t.tushum, cur)}</b>`,
			kassa: (t.kassa || []).length
				? `<b>${t.kassa.map((e) => od.money(e.amount, e.currency)).join("<br>")}</b>`
				: "—",
			ombor: `<b>${od.money(t.ombor, cur)}</b>`,
			minus: `<b>${od.number(t.minus)}</b>`,
			_class: "bd-total-row",
		});

		$body.html(`<div class="od-table-wrap">${this.table(
			[
				{ key: "label", label: __("Filial") },
				{ key: "savdo", label: __("Savdo"), align: "right" },
				{ key: "tushum", label: __("Tushum"), align: "right" },
				{ key: "kassa", label: __("Kassa qoldiq"), align: "right" },
				{ key: "ombor", label: __("Ombor qiymati"), align: "right" },
				{ key: "minus", label: __("Minus poz."), align: "right" },
			],
			rows
		)}</div>`);
	}

	// -- Sinxron-salomatlik (trade) ----------------------------------------

	render_sync_health(sync) {
		const $body = this.block_shell(
			"sync_health",
			__("Sinxron holati"),
			__("Zavod API bilan almashinuv salomatligi")
		);
		if (!this.guard(sync, $body)) return;

		const tone = { ok: "green", warn: "orange", bad: "red" }[sync.holat] || "gray";
		const label = {
			ok: __("Sog'lom"),
			warn: __("E'tibor kerak"),
			bad: __("Muammo bor"),
		}[sync.holat] || sync.holat;

		const tops = (sync.xato_top || [])
			.map(
				(r) => `<tr><td>${od.esc(r.sabab)}</td>
					<td class="od-align-right">${od.number(r.soni)}</td></tr>`
			)
			.join("");

		$body.html(`
			<div class="bd-sync-head">
				<span class="od-pill od-pill--${tone}">● ${od.esc(label)}</span>
				<span>${__("Oxirgi sinxron")}: <b>${od.esc(sync.last_synced_date || "—")}</b>
					(${od.esc(sync.last_sync_status || "—")})</span>
				<span>${__("Muhrlangan davr")}: <b>${od.esc(sync.closed_until || "—")}</b></span>
			</div>
			<div class="od-stats">
				<div class="od-stat"><span>${__("24 soat xatolar")}</span>
					<b class="${sync.xato_24h ? "od-bad" : ""}">${od.number(sync.xato_24h)}</b></div>
				<div class="od-stat"><span>${__("Repost xatolari")}</span>
					<b class="${sync.repost_failed ? "od-bad" : ""}">${od.number(
						sync.repost_failed
					)}</b></div>
				<div class="od-stat"><span>${__("Sinxron")}</span>
					<b>${sync.sync_enabled ? __("Yoniq") : __("O'chiq")}</b></div>
			</div>
			${
				tops
					? `<div class="od-table-wrap"><table class="od-table">
						<thead><tr><th>${__("Xato sababi (24 soat)")}</th>
						<th class="od-align-right">${__("Soni")}</th></tr></thead>
						<tbody>${tops}</tbody></table></div>`
					: `<div class="bd-sync-clean">${__("So'nggi 24 soatda xato yo'q.")}</div>`
			}
		`);
	}

	// -- Minus-ombor (trade) -----------------------------------------------

	render_minus_stock(minus) {
		const $body = this.block_shell(
			"minus_stock",
			__("Minusdagi ombor-pozitsiyalari"),
			__("Qabul yozilmasdan sotilgan tovarlar — eng chuqur 10 tasi")
		);
		if (!this.guard(minus, $body)) return;

		if (!minus.positions) {
			$body.html(this.empty_state(__("Minusdagi pozitsiya yo'q — ombor toza."), "check"));
			return;
		}

		const cur = minus.currency || "USD";
		const rows = (minus.rows || []).map((r) => ({
			item: od.esc(r.item),
			warehouse: od.esc(r.warehouse),
			qty: `<span class="od-bad">${od.number(r.qty)}</span>`,
			value: od.money(r.value, cur),
		}));

		$body.html(`
			<div class="od-stats">
				<div class="od-stat"><span>${__("Jami pozitsiya")}</span>
					<b class="od-bad">${od.number(minus.positions)}</b></div>
				<div class="od-stat"><span>${__("Jami qiymat")}</span>
					<b class="od-bad">${od.money(minus.value, cur)}</b></div>
			</div>
			<div class="od-table-wrap">${this.table(
				[
					{ key: "item", label: __("Tovar") },
					{ key: "warehouse", label: __("Ombor") },
					{ key: "qty", label: __("Dona"), align: "right" },
					{ key: "value", label: __("Qiymat"), align: "right" },
				],
				rows
			)}</div>
		`);
	}

	// -- Ishlab chiqarish (production, Imzo) --------------------------------

	render_production(prod) {
		const $body = this.block_shell(
			"production",
			__("Zakazlar va ombor"),
			__("Klaes-zakazlar hamda material-guruh kesimidagi qoldiqlar")
		);
		if (!this.guard(prod, $body)) return;

		const cur = prod.currency || "USD";
		const zakaz_rows = (prod.zakazlar || []).map((r) => ({
			raqam: od.esc(r.raqam),
			sana: od.esc(r.sana ? frappe.datetime.str_to_user(r.sana) : "—"),
			loyiha: od.esc(r.loyiha || "—"),
			pozitsiyalar: od.number(r.pozitsiyalar),
			netto: od.money(r.netto_uzs, "UZS"),
			status: `<span class="od-pill od-pill--${
				r.status === "Processed" ? "green" : "orange"
			}">${od.esc(r.status)}</span>`,
			_click: () => frappe.set_route("List", "Zakaz Import", { zakaz_raqami: r.raqam }),
		}));

		const guruh_rows = (prod.guruhlar || []).map((r) => ({
			guruh: od.esc(r.guruh),
			items: od.number(r.items),
			qty: od.number(r.qty),
			value: od.money(r.value, cur),
		}));

		$body.html(`
			<div class="bd-prod-grid">
				<div>
					<h4 class="bd-subtitle">${__("So'nggi zakazlar")}</h4>
					<div class="od-table-wrap">${this.table(
						[
							{ key: "raqam", label: __("Zakaz") },
							{ key: "sana", label: __("Sana") },
							{ key: "pozitsiyalar", label: __("Poz."), align: "right" },
							{ key: "netto", label: __("Netto"), align: "right" },
							{ key: "status", label: __("Holat") },
						],
						zakaz_rows
					)}</div>
				</div>
				<div>
					<h4 class="bd-subtitle">${__("Ombor — guruhlar kesimida")}</h4>
					<div class="od-table-wrap">${this.table(
						[
							{ key: "guruh", label: __("Guruh") },
							{ key: "items", label: __("Turlar"), align: "right" },
							{ key: "qty", label: __("Dona"), align: "right" },
							{ key: "value", label: __("Qiymat"), align: "right" },
						],
						guruh_rows
					)}</div>
					<p class="bd-note">${__("Klaes tarjima lug'ati")}: <b>${od.number(
						prod.lugat
					)}</b> ${__("qator")}</p>
				</div>
			</div>
		`);
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
					{ label: __("Zakaz sanasi"), key: "date" },
					{ label: __("Mijoz"), key: "customer" },
					{ label: __("Kvadrat"), key: "sqm", align: "right" },
					{ label: __("Summa"), key: "amount", align: "right" },
					{ label: __("Faktura sanasi"), key: "si_sana" },
					{ label: __("Tannarx"), key: "tannarx", align: "right" },
					{ label: __("Foyda"), key: "foyda", align: "right" },
					{ label: __("Holat"), key: "state", align: "center" },
				],
				rows.map((row) => ({
					_click: () => frappe.set_route("Form", "Sales Order", row.name),
					name: `<b>${od.esc(row.name)}</b>`,
					date: frappe.datetime.str_to_user(row.date),
					customer: od.esc(row.customer_name),
					sqm: row.sqm ? od.qty(row.sqm, "m²") : "—",
					amount: this.money(row.amount),
					si_sana: row.si_sana ? frappe.datetime.str_to_user(row.si_sana) : "—",
					tannarx: (row.tannarx || []).length ? this.money(row.tannarx) : "—",
					foyda: (row.foyda || []).length
						? `<span class="${
								(row.foyda[0] || {}).amount >= 0 ? "od-good" : "od-bad"
						  }">${this.money(row.foyda)}</span>`
						: "—",
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
