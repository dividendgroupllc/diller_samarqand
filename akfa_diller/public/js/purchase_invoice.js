// Purchase Invoice — kompaniya bo'yicha supplier/item/warehouse filtri + update_stock=1.
//
// Kompaniya tanlanganda (Company Group Setup mapping bo'yicha):
//   - Supplier   -> o'sha kompaniya Supplier Group(lar)i ostidagilar
//   - Items.item -> o'sha kompaniya Item Group(lar)i ostidagi purchase itemlar
//   - Warehouse  -> o'sha kompaniya omborlari (Warehouse.company)
//   - update_stock = 1
// Mapping bo'lmagan kompaniyada filter qo'llanmaydi (hammasi chiqadi).
//
// MUHIM: ERPNext buying controller (cscript/old_style) o'z item/supplier query'sini
// new_style handlerlardan KEYIN o'rnatadi -> set_query'ni setTimeout bilan kechiktiramiz.

frappe.provide("akfa_diller");

akfa_diller.pi_apply_queries = function (frm) {
	setTimeout(function () {
		const company_filter = function () {
			return {
				filters: { company: frm.doc.company, is_group: 0 },
			};
		};

		frm.set_query("supplier", function () {
			return {
				query: "akfa_diller.akfa_diller.api.company_groups.company_suppliers",
				filters: { company: frm.doc.company },
			};
		});

		frm.set_query("item_code", "items", function () {
			return {
				query: "akfa_diller.akfa_diller.api.company_groups.company_items",
				filters: { company: frm.doc.company, is_purchase_item: 1 },
			};
		});

		frm.set_query("set_warehouse", company_filter);
		frm.set_query("warehouse", "items", company_filter);
		frm.set_query("rejected_warehouse", "items", company_filter);
	}, 200);
};

akfa_diller.pi_set_defaults = function (frm) {
	if (frm.doc.docstatus !== 0) {
		return;
	}
	// update_stock = 1
	if (!frm.doc.update_stock) {
		frm.set_value("update_stock", 1);
	}
	// Agar kompaniyada bitta ombor bo'lsa - avtomatik qo'yamiz
	if (frm.doc.company && !frm.doc.set_warehouse) {
		frappe.db
			.get_list("Warehouse", {
				filters: { company: frm.doc.company, is_group: 0 },
				fields: ["name"],
				limit: 2,
			})
			.then(function (whs) {
				if (whs && whs.length === 1) {
					frm.set_value("set_warehouse", whs[0].name);
				}
			});
	}
};

frappe.ui.form.on("Purchase Invoice", {
	onload(frm) {
		akfa_diller.pi_apply_queries(frm);
		akfa_diller.pi_set_defaults(frm);
	},
	refresh(frm) {
		akfa_diller.pi_apply_queries(frm);
	},
	company(frm) {
		akfa_diller.pi_apply_queries(frm);
		akfa_diller.pi_set_defaults(frm);
	},
});
