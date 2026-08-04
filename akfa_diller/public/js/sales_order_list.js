// Sales Order list view — Oyna sex workflow status ranglari + filtr tozalash.
//
// ERPNext'ning standart sales_order_list.js AVVAL yuklanadi; bu fayl (doctype_list_js
// hook) KEYIN yuklanadi, shuning uchun mavjud listview_settings'ni ALMASHTIRMAY,
// kengaytiramiz (get_indicator va onload'ni o'rab olamiz).

frappe.listview_settings["Sales Order"] = frappe.listview_settings["Sales Order"] || {};

(function () {
	const settings = frappe.listview_settings["Sales Order"];

	// get_indicator uchun workflow_state ustunini albatta olib kelamiz.
	settings.add_fields = (settings.add_fields || []).concat(["workflow_state"]);

	// workflow_state -> rang
	const STATE_COLORS = {
		"Draft": "gray",
		"Zakaz olindi": "orange",
		"Tayyor": "blue",
		"Topshirildi": "green",
	};

	// Statusni workflow_state bo'yicha rangli ko'rsatish (bosilsa shu status bo'yicha filtr).
	const prev_indicator = settings.get_indicator;
	settings.get_indicator = function (doc) {
		const state = doc.workflow_state;
		if (state && STATE_COLORS[state]) {
			return [__(state), STATE_COLORS[state], "workflow_state,=," + state];
		}
		if (prev_indicator) {
			return prev_indicator(doc);
		}
	};

	// "Customer Name" (title field) standart filtrini yashirish.
	const prev_onload = settings.onload;
	settings.onload = function (listview) {
		if (prev_onload) {
			prev_onload(listview);
		}
		setTimeout(function () {
			const page_form = listview.page && listview.page.page_form;
			if (page_form) {
				page_form
					.find('[data-fieldname="customer_name"]')
					.closest(".frappe-control")
					.hide();
			}
		}, 300);
	};
})();
