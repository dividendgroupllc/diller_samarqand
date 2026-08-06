def execute():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    custom_fields = {
        "Customer": [
            {
                "fieldname": "custom_report_service_client_cid",
                "label": "Report Service Client CID",
                "fieldtype": "Data",
                "insert_after": "customer_name",
                "unique": 1,
                "read_only": 1,
            },
        ],
        "Supplier": [
            {
                "fieldname": "custom_report_service_client_cid",
                "label": "Report Service Client CID",
                "fieldtype": "Data",
                "insert_after": "supplier_name",
                "unique": 1,
                "read_only": 1,
            },
        ],
        "Sales Invoice": [
            {
                "fieldname": "custom_report_service_cid",
                "label": "Report Service CID",
                "fieldtype": "Data",
                "insert_after": "customer",
                "unique": 1,
                "read_only": 1,
            },
        ],
        "Purchase Invoice": [
            {
                "fieldname": "custom_report_service_cid",
                "label": "Report Service CID",
                "fieldtype": "Data",
                "insert_after": "supplier",
                "unique": 1,
                "read_only": 1,
            },
        ],
        "Stock Entry": [
            {
                "fieldname": "custom_report_service_cid",
                "label": "Report Service CID",
                "fieldtype": "Data",
                "insert_after": "stock_entry_type",
                "unique": 1,
                "read_only": 1,
            },
        ],
    }

    create_custom_fields(custom_fields, ignore_validate=True, update=True)
