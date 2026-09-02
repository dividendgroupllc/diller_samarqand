// PL Hisoboti — Akfa diller (dvigatel jazira_app'dan, dinamik xarajat-guruhlar).
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
