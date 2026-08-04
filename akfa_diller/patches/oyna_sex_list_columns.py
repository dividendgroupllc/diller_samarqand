# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""
Sales Order list tweaks:
  - Replace the "% Delivered" column with "Jami kvadrat".
  - Make Jami kvadrat editable even after submit (allow_on_submit) and shown
    as a list column.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
	# Update the Jami kvadrat custom field: list column + editable after submit.
	create_custom_fields(
		{
			"Sales Order": [
				{
					"fieldname": "custom_jami_kvadrat",
					"label": "Jami kvadrat (m²)",
					"fieldtype": "Float",
					"precision": "3",
					"insert_after": "set_warehouse",
					"depends_on": "eval:doc.company=='Oyna sex'",
					"in_list_view": 1,
					"allow_on_submit": 1,
				},
			]
		},
		ignore_validate=True,
	)

	# Drop the "% Delivered" column from the Sales Order list.
	make_property_setter(
		"Sales Order",
		"per_delivered",
		"in_list_view",
		0,
		"Check",
		validate_fields_for_doctype=False,
	)
