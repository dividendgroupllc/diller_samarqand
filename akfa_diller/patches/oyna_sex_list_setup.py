# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""
Extra Oyna sex Sales Order setup:
  - custom_jami_kvadrat (total m2) field next to "Set Source Warehouse".
  - Sales Order list standard filters: drop Delivery Status + Billing Status,
    add the workflow Status.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
	_add_jami_kvadrat()
	_adjust_list_filters()


def _add_jami_kvadrat():
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
				},
			]
		},
		ignore_validate=True,
	)


def _adjust_list_filters():
	# Remove Delivery Status + Billing Status from the list standard-filter bar.
	for fieldname in ("delivery_status", "billing_status"):
		make_property_setter(
			"Sales Order",
			fieldname,
			"in_standard_filter",
			0,
			"Check",
			validate_fields_for_doctype=False,
		)

	# Add the workflow Status to the standard-filter bar.
	make_property_setter(
		"Sales Order",
		"workflow_state",
		"in_standard_filter",
		1,
		"Check",
		validate_fields_for_doctype=False,
	)
