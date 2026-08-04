# Copyright (c) 2026, akfa_diller and contributors
# For license information, please see license.txt

"""
Set up the Oyna sex Sales Order flow:
  - Custom fields on Sales Order (consumed-materials table + material issue ref).
  - A Frappe Workflow: Draft -> Zakaz olindi -> Tayyor -> Topshirildi (submit).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

WORKFLOW_NAME = "Oyna Sex Zakaz"

# state -> (doc_status, style)
STATES = {
	"Draft": ("0", ""),
	"Zakaz olindi": ("0", "Warning"),
	"Tayyor": ("0", "Primary"),
	"Topshirildi": ("1", "Success"),
}

# (from_state, action, next_state, allowed_role)
TRANSITIONS = [
	("Draft", "Zakaz olish", "Zakaz olindi", "Sales User"),
	("Zakaz olindi", "Zakaz tayyor bo'ldi", "Tayyor", "Sales User"),
	("Tayyor", "Mijozga topshirildi", "Topshirildi", "Sales Manager"),
]


def execute():
	_create_custom_fields()
	_create_workflow()


def _create_custom_fields():
	create_custom_fields(
		{
			"Sales Order": [
				{
					"fieldname": "custom_tovar_rasxod_section",
					"label": "Tovar rasxod (sarflangan materiallar)",
					"fieldtype": "Section Break",
					"insert_after": "items",
					"collapsible": 1,
					"depends_on": "eval:doc.company=='Oyna sex'",
				},
				{
					"fieldname": "custom_sarflangan_tovarlar",
					"label": "Sarflangan tovarlar",
					"fieldtype": "Table",
					"options": "Oyna Sarflangan Tovar",
					"insert_after": "custom_tovar_rasxod_section",
				},
				{
					"fieldname": "custom_material_issue",
					"label": "Material Issue (tan narx)",
					"fieldtype": "Link",
					"options": "Stock Entry",
					"insert_after": "custom_sarflangan_tovarlar",
					"read_only": 1,
					"no_copy": 1,
				},
			]
		},
		ignore_validate=True,
	)


def _ensure_workflow_state(state, style):
	if not frappe.db.exists("Workflow State", state):
		frappe.get_doc(
			{"doctype": "Workflow State", "workflow_state_name": state, "style": style}
		).insert(ignore_permissions=True)


def _ensure_workflow_action(action):
	if not frappe.db.exists("Workflow Action Master", action):
		frappe.get_doc(
			{"doctype": "Workflow Action Master", "workflow_action_name": action}
		).insert(ignore_permissions=True)


def _create_workflow():
	# Ensure the master records referenced by the workflow exist.
	for state, (_doc_status, style) in STATES.items():
		_ensure_workflow_state(state, style)
	for _from, action, _next, _role in TRANSITIONS:
		_ensure_workflow_action(action)

	if frappe.db.exists("Workflow", WORKFLOW_NAME):
		return

	wf = frappe.new_doc("Workflow")
	wf.workflow_name = WORKFLOW_NAME
	wf.document_type = "Sales Order"
	wf.is_active = 1
	wf.workflow_state_field = "workflow_state"
	wf.send_email_alert = 0

	for state, (doc_status, _style) in STATES.items():
		allow_edit = "Sales Manager" if state == "Topshirildi" else "Sales User"
		wf.append("states", {
			"state": state,
			"doc_status": doc_status,
			"allow_edit": allow_edit,
		})

	for from_state, action, next_state, role in TRANSITIONS:
		wf.append("transitions", {
			"state": from_state,
			"action": action,
			"next_state": next_state,
			"allowed": role,
			"allow_self_approval": 1,
		})

	wf.insert(ignore_permissions=True)
