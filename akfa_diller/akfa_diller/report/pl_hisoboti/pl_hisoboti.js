// PL Hisoboti — Akfa diller (dvigatel jazira_app'dan, dinamik xarajat-guruhlar).

// PDF tugmasi — idempotent qo'yiladi (Frappe inner toolbar'ni qayta chizishi
// mumkin, shuning uchun onload'da ham, jadval chizilganda ham tekshiriladi).
function akfa_pl_pdf_button(report) {
	if (!report || !report.page || !report.page.inner_toolbar) return;
	report.page.inner_toolbar.removeClass("hidden-xs hidden-md");
	if (report.page.inner_toolbar.find(".btn-akfa-pl-pdf").length) return;

	const $btn = report.page.add_inner_button(__("PDF"), function () {
		const filters = frappe.query_report.get_filter_values();
		if (!filters.company) {
			frappe.show_alert({ message: __("Avval kompaniyani tanlang"), indicator: "orange" });
			return;
		}
		if (!filters.from_date || !filters.to_date) {
			frappe.show_alert({ message: __("Avval sana oralig'ini tanlang"), indicator: "orange" });
			return;
		}
		window.open(
			"/api/method/akfa_diller.akfa_diller.report.pl_hisoboti.pl_hisoboti_pdf.generate_pl_pdf" +
				"?filters=" + encodeURIComponent(JSON.stringify(filters))
		);
	});
	if ($btn && $btn.addClass) $btn.addClass("btn-akfa-pl-pdf").addClass("btn-primary");
}

frappe.query_reports["PL Hisoboti"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Kompaniya"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("Dan"),
			fieldtype: "Date",
			default: frappe.datetime.year_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("Gacha"),
			fieldtype: "Date",
			default: frappe.datetime.year_end(),
			reqd: 1,
		},
		{
			fieldname: "periodicity",
			label: __("Davr"),
			fieldtype: "Select",
			options: "Monthly\nQuarterly\nHalf-Yearly\nYearly",
			default: "Monthly",
			reqd: 1,
		},
	],

	tree: true,
	name_field: "label",
	initial_depth: 0,

	onload: function (report) {
		akfa_pl_pdf_button(report);
	},
	after_datatable_render: function () {
		akfa_pl_pdf_button(frappe.query_report);
	},

	formatter: function (value, row, column, data, default_formatter) {
		const rt = data ? data.row_type : null;

		if (column.fieldname === "label") {
			if (!data || rt === "divider") return "";
			const level = data.indent_level || 0;
			const pad = 6 + level * 22;
			let s = `padding-left:${pad}px;white-space:nowrap;color:var(--text-color);`;
			if (rt === "root") {
				s += "font-weight:700;text-transform:uppercase;letter-spacing:.3px;";
			} else if (rt === "result") {
				s += "font-weight:800;";
			} else if (rt === "sub") {
				s += "font-weight:600;";
			} else if (rt === "detail") {
				s += "font-size:12.5px;";
			} else if (rt === "percent" || rt === "ratio") {
				s += "font-style:italic;font-size:11.5px;";
			}
			const label = frappe.utils.escape_html(data.label || "");
			return `<div style="${s}">${label}</div>`;
		}

		if (!data || rt === "divider") return "";
		if (value === null || value === undefined || value === "") return "";
		const num = flt(value);

		if (rt === "percent") {
			const v = Math.round(num);
			return `<span style="font-style:italic;font-weight:600;color:var(--text-muted);">${v}%</span>`;
		}
		if (rt === "ratio") {
			const v = num.toLocaleString("ru-RU", {
				minimumFractionDigits: 2,
				maximumFractionDigits: 2,
			});
			return `<span style="font-style:italic;color:var(--text-muted);">${v}</span>`;
		}

		const rounded = Math.round(num);
		const w = rt === "root" || rt === "result" ? 800 : rt === "sub" ? 700 : 400;
		if (rounded < 0) {
			const absTxt = Math.abs(rounded).toLocaleString("ru-RU");
			return `<span style="font-weight:${w};color:var(--alert-text-danger);">(${absTxt})</span>`;
		}
		return `<span style="font-weight:${w};color:var(--text-color);">${rounded.toLocaleString("ru-RU")}</span>`;
	},
};
