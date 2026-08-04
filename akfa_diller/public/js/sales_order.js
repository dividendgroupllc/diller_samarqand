// Sales Order — "Oyna sex" kompaniyasi uchun maxsus oqim.
//
// 1) Customer/Item ro'yxatlarini cheklash (company === "Oyna sex"):
//    - Customer  -> "Oyna sex" parent Customer Group ostidagi mijozlar
//    - Items.item_code -> maintain stock = 0 (xizmat) + "Oyna sex" Item Group
// 2) "Tovar rasxod" tugmasi -> dialog: "Oyna sex" Item Group ostidagi
//    maintain stock = 1 (materiallar) tanlanadi -> custom_sarflangan_tovarlar.
//
// MUHIM (query override): ERPNext SellingController (cscript/old_style) o'z
// item_code/customer query'sini frappe.ui.form.on (new_style) handlerlaridan
// KEYIN o'rnatadi. Shuning uchun set_query'ni setTimeout bilan kechiktiramiz.

frappe.provide("akfa_diller");

akfa_diller.OYNA_SEX = "Oyna sex";
akfa_diller.OYNA_WAREHOUSE = "Oyna sex - Os";

frappe.ui.form.on("Sales Order", {
	onload(frm) {
		akfa_diller.apply_oyna_sex_queries(frm);
	},
	refresh(frm) {
		akfa_diller.apply_oyna_sex_queries(frm);
		akfa_diller.add_tovar_rasxod_button(frm);
	},
	company(frm) {
		akfa_diller.apply_oyna_sex_queries(frm);
		akfa_diller.add_tovar_rasxod_button(frm);
	},
	customer(frm) {
		akfa_diller.apply_oyna_sex_queries(frm);
	},
});

akfa_diller.apply_oyna_sex_queries = function (frm) {
	setTimeout(function () {
		frm.set_query("customer", function () {
			if (frm.doc.company === akfa_diller.OYNA_SEX) {
				return {
					query: "akfa_diller.akfa_diller.api.sales_order_filters.oyna_sex_customers",
				};
			}
			return {};
		});

		frm.set_query("item_code", "items", function () {
			if (frm.doc.company === akfa_diller.OYNA_SEX) {
				return {
					query: "akfa_diller.akfa_diller.api.sales_order_filters.oyna_sex_items",
				};
			}
			return {
				query: "erpnext.controllers.queries.item_query",
				filters: { is_sales_item: 1 },
			};
		});
	}, 200);
};

akfa_diller.add_tovar_rasxod_button = function (frm) {
	if (frm.doc.company !== akfa_diller.OYNA_SEX || frm.doc.docstatus !== 0) {
		return;
	}
	if (frm.custom_buttons[__("Tovar rasxod")]) {
		return;
	}
	frm.add_custom_button(__("Tovar rasxod"), function () {
		akfa_diller.open_tovar_rasxod_dialog(frm);
	}).addClass("btn-primary");
};

akfa_diller.open_tovar_rasxod_dialog = function (frm) {
	const existing = (frm.doc.custom_sarflangan_tovarlar || []).map(function (r) {
		return { item_code: r.item_code, qty: r.qty, warehouse: r.warehouse };
	});

	const d = new frappe.ui.Dialog({
		title: __("Tovar rasxod — sarflangan materiallar"),
		size: "extra-large",
		fields: [
			{
				fieldname: "materiallar",
				fieldtype: "Table",
				label: __("Sarflangan tovarlar"),
				cannot_add_rows: false,
				in_place_edit: false,
				data: existing,
				fields: [
					{
						fieldname: "item_code",
						fieldtype: "Link",
						options: "Item",
						label: __("Tovar"),
						in_list_view: 1,
						columns: 5,
						reqd: 1,
						get_query: function () {
							return {
								query: "akfa_diller.akfa_diller.api.sales_order_filters.oyna_sex_stock_items",
							};
						},
					},
					{
						fieldname: "qty",
						fieldtype: "Float",
						label: __("Miqdor"),
						in_list_view: 1,
						columns: 2,
						default: 1,
						reqd: 1,
					},
					{
						fieldname: "warehouse",
						fieldtype: "Link",
						options: "Warehouse",
						label: __("Ombor"),
						in_list_view: 1,
						columns: 3,
						default: akfa_diller.OYNA_WAREHOUSE,
						get_query: function () {
							return { filters: { company: akfa_diller.OYNA_SEX, is_group: 0 } };
						},
					},
				],
			},
		],
		primary_action_label: __("Saqlash"),
		primary_action(values) {
			frm.clear_table("custom_sarflangan_tovarlar");
			(values.materiallar || []).forEach(function (r) {
				if (!r.item_code) return;
				const row = frm.add_child("custom_sarflangan_tovarlar");
				row.item_code = r.item_code;
				row.qty = r.qty || 1;
				row.warehouse = r.warehouse || akfa_diller.OYNA_WAREHOUSE;
			});
			frm.refresh_field("custom_sarflangan_tovarlar");
			d.hide();
			frappe.show_alert({ message: __("Sarflangan tovarlar saqlandi"), indicator: "green" });
		},
	});

	d.show();
};
