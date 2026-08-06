def execute():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    # Follow-up to add_report_service_custom_fields: that patch already ran (once)
    # without unique=1 on these two fields, so re-running it wouldn't apply the
    # change to already-migrated sites -- a separate patch is needed.
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
    }

    create_custom_fields(custom_fields, ignore_validate=True, update=True)
