frappe.query_reports["DDS"] = {
    "filters": [
        {
            "fieldname": "from_date",
            "label": __("Сана дан"),
            "fieldtype": "Date",
            "default": frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            "reqd": 1
        },
        {
            "fieldname": "to_date",
            "label": __("Сана гача"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today(),
            "reqd": 1
        },
        {
            "fieldname": "mode_of_payment",
            "label": __("Способ оплаты"),
            "fieldtype": "Link",
            "options": "Mode of Payment"
        },
        {
            "fieldname": "party_type",
            "label": __("Контрагент тури"),
            "fieldtype": "Select",
            "options": "\nCustomer\nSupplier\nEmployee",
            "on_change": function() {
                frappe.query_report.set_filter_value('party', '');
            }
        },
        {
            "fieldname": "party",
            "label": __("Контрагент"),
            "fieldtype": "Dynamic Link",
            "options": "party_type",
            "get_options": function() {
                return frappe.query_report.get_filter_value('party_type');
            }
        },
        {
            "fieldname": "category",
            "label": __("Категория"),
            "fieldtype": "Select",
            "options": "\nПокупатели\nПоставщики\nРасходы\nДивиденд 1\nДивиденд 2\nДивиденд 3\nСотрудники\nПеремещения"
        }
    ],

    "formatter": function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (column.fieldtype == "Currency" && value) {
            value = value.replace(/\$/g, '');
        }

        if (column.fieldname == "kirim" && data) {
            if (!data.kirim) return "";
            value = `<span style="color: #1b5e20; font-weight: 600;">${value}</span>`;
        }

        if (column.fieldname == "chiqim" && data) {
            if (!data.chiqim) return "";
            value = `<span style="color: #b71c1c; font-weight: 600;">${value}</span>`;
        }

        return value;
    }
}
