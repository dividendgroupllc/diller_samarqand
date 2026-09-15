frappe.listview_settings["Oyna Zakaz"] = {
	add_fields: ["holat", "sales_invoice", "jami_summa"],
	get_indicator(doc) {
		const m = { "Qoralama": "gray", "Zakaz olindi": "orange", "Tayyor": "blue", "Topshirildi": "green", "Bekor qilindi": "red" };
		return [__(doc.holat), m[doc.holat] || "gray", "holat,=," + doc.holat];
	},
};
