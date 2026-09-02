// Zakazlar Hisoboti — zakaz + faktura sanasi + tannarx + foyda (Oyna sex).
frappe.query_reports["Zakazlar Hisoboti"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Kompaniya"),
			fieldtype: "Link",
			options: "Company",
			default: "Oyna sex",
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("Boshlanish sanasi"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("Tugash sanasi"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
	],
};
