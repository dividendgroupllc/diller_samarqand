

def sd_plastik_live_test():
    from akfa_diller.akfa_diller.api import daily_kassa_import as dki
    from akfa_diller.akfa_diller.api.daily_kassa_import import _process_import_sync

    file_url = attach_file(
        "/tmp/claude-1000/-home-sardorbek-armada-bench/2d4307f4-4bbd-48b6-a840-aa07dbeac7d9/scratchpad/sd_plastik_live_test.xlsx",
        "test_sd_plastik_live.xlsx",
    )
    doc_name = make_kassa_import("Samarqand Diller", "Cash", file_url)
    preview = dki.get_preview_data(doc_name)
    result = _process_import_sync(doc_name)

    doc = frappe.get_doc("Kassa Import", doc_name)
    names = [n.strip() for n in (doc.kassa_docs or "").split(",") if n.strip()]
    kassas = frappe.db.get_all(
        "Kassa", filters={"name": ["in", names]},
        fields=["name", "mode_of_payment", "cash_account", "amount", "date", "docstatus"]
    )
    _p({"doc_name": doc_name, "preview": preview, "process_result": result, "kassas": kassas})
