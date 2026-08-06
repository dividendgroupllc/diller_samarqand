frappe.query_reports["Report Service Reconciliation"] = {
    "filters": [
        {
            "fieldname": "from_date",
            "label": __("Sana dan"),
            "fieldtype": "Date",
            "default": frappe.datetime.add_days(frappe.datetime.get_today(), -7),
            "reqd": 1
        },
        {
            "fieldname": "to_date",
            "label": __("Sana gacha"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today(),
            "reqd": 1
        }
    ]
};
