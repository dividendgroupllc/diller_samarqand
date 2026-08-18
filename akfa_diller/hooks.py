app_name = "akfa_diller"
app_title = "Akfa diller"
app_publisher = "musulmanabdulloh@gmail.com"
app_description = "diller"
app_email = "abdullohuchkunov@gmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "akfa_diller",
# 		"logo": "/assets/akfa_diller/logo.png",
# 		"title": "Akfa diller",
# 		"route": "/akfa_diller",
# 		"has_permission": "akfa_diller.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/akfa_diller/css/akfa_diller.css"
# app_include_js = "/assets/akfa_diller/js/akfa_diller.js"

# include js, css files in header of web template
# web_include_css = "/assets/akfa_diller/css/akfa_diller.css"
# web_include_js = "/assets/akfa_diller/js/akfa_diller.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "akfa_diller/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"Sales Order": "public/js/sales_order.js",
	"Purchase Invoice": "public/js/purchase_invoice.js",
}
doctype_list_js = {"Sales Order": "public/js/sales_order_list.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "akfa_diller/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "akfa_diller.utils.jinja_methods",
# 	"filters": "akfa_diller.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "akfa_diller.install.before_install"
# after_install = "akfa_diller.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "akfa_diller.uninstall.before_uninstall"
# after_uninstall = "akfa_diller.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "akfa_diller.utils.before_app_install"
# after_app_install = "akfa_diller.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "akfa_diller.utils.before_app_uninstall"
# after_app_uninstall = "akfa_diller.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "akfa_diller.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Sales Order": {
		"on_submit": "akfa_diller.akfa_diller.api.oyna_order.on_sales_order_submit",
	},
	"Report": {
		"validate": "akfa_diller.akfa_diller.api.report_roles.ensure_report_roles",
	},
}

# Guarantee standard roles on all custom reports after every migrate/deploy.
after_migrate = ["akfa_diller.akfa_diller.api.report_roles.ensure_roles_on_all_custom_reports"]

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		"*/5 * * * *": ["akfa_diller.akfa_diller.api.report_service_sync.sync_report_service"],
		# Kunlik orqaga-qarash: manbada TAHRIRLANGAN/O'CHIRILGAN tranzaksiyalarni
		# ERPNext hujjatlariga moslashtirish + 3-kunlik sync oynasidan kech
		# kelganlarni yaratish (oxirgi 7 kun). Kechasi 02:30 da -- ish soatlari
		# tashqarisida, API band bo'lmagan payt.
		"30 2 * * *": ["akfa_diller.akfa_diller.api.report_service_sync.reverify_recent_transactions"],
		# Kassa (payments) sinxroni: soatlik, 0=0 bucket-tamoyili (oxirgi 3 kun).
		"25 * * * *": ["akfa_diller.akfa_diller.api.payments_sync.sync_payments"],
		# Kassa chuqur tekshiruvi: kechasi 03:10, oxirgi 30 kun 0=0.
		"10 3 * * *": ["akfa_diller.akfa_diller.api.payments_sync.deep_check_payments"],
	},
}

# Testing
# -------

# before_tests = "akfa_diller.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "akfa_diller.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "akfa_diller.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["akfa_diller.utils.before_request"]
# after_request = ["akfa_diller.utils.after_request"]

# Job Events
# ----------
# before_job = ["akfa_diller.utils.before_job"]
# after_job = ["akfa_diller.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"akfa_diller.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

