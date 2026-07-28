frappe.query_reports["Kontragent Otchet"] = {
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
            "fieldname": "party_type",
            "label": __("Контрагент тури"),
            "fieldtype": "Select",
            "options": "\nCustomer\nSupplier\nEmployee\nOther",
            "default": ""
        },
        {
            "fieldname": "party",
            "label": __("Контрагент"),
            "fieldtype": "Dynamic Link",
            "get_options": function() {
                var party_type = frappe.query_report.get_filter_value('party_type');
                if(!party_type) {
                    return null;
                }
                return party_type;
            }
        },
        {
            "fieldname": "currency",
            "label": __("Валюта"),
            "fieldtype": "Select",
            "options": "\nUZS\nUSD",
            "default": ""
        }
    ],

    "formatter": function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        // Akt Sverka link yaratish
        if (column.fieldname === "akt_sverka_link" && value && data.party && !data.is_total_row) {
            var from_date = frappe.query_report.get_filter_value('from_date');
            var to_date = frappe.query_report.get_filter_value('to_date');
            var url = `/app/query-report/Akt Sverka?party_type=${encodeURIComponent(data.party_type)}&party=${encodeURIComponent(data.party)}&from_date=${from_date}&to_date=${to_date}`;
            value = `<a href="${url}" style="color: #2490EF; text-decoration: none;">📊 ${value}</a>`;
        }

        // Currency fieldlarida $ ni olib tashlash
        if (column.fieldtype == "Currency" && value) {
            value = value.replace(/\$/g, '');
        }

        // Total qatorini highlight qilish
        if (data && data.is_total_row) {
            value = `<span style="font-weight: bold; background-color: #e3f2fd;">${value}</span>`;
        }

        return value;
    }
}
