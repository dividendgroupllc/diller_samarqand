// Copyright (c) 2026, abdulloh and contributors
// For license information, please see license.txt

frappe.ui.form.on("Zakaz Import", {
    setup: function(frm) {
        // Ombor va Cost Center ro'yxatlari tanlangan KOMPANIYAGA tegishli
        // yozuvlarnigina ko'rsatadi (foydalanuvchi 2026-09-03)
        frm.set_query("warehouse", function() {
            return {filters: {company: frm.doc.company, is_group: 0}};
        });
        frm.set_query("cost_center", function() {
            return {filters: {company: frm.doc.company, is_group: 0}};
        });
    },

    company: function(frm) {
        // kompaniya almashsa, boshqa kompaniyaning tanlovlari tozalanadi
        if (frm.doc.warehouse) {
            frappe.db.get_value("Warehouse", frm.doc.warehouse, "company")
                .then(function(r) {
                    if ((r.message || {}).company !== frm.doc.company)
                        frm.set_value("warehouse", null);
                });
        }
        if (frm.doc.cost_center) {
            frappe.db.get_value("Cost Center", frm.doc.cost_center, "company")
                .then(function(r) {
                    if ((r.message || {}).company !== frm.doc.company)
                        frm.set_value("cost_center", null);
                });
        }
    },

    refresh: function(frm) {
        if (!frm._realtime_bound) {
            frm._realtime_bound = true;
            frappe.realtime.on("akfa_zakaz_import_log", function(d) {
                if (d.doc_name !== frm.doc.name) return;
                frm.set_value("import_log", d.full_log || d.msg);
            });
            frappe.realtime.on("akfa_zakaz_import_success", function(d) {
                if (d.doc_name !== frm.doc.name) return;
                frappe.show_alert({message: __("Import tugadi: {0}", [d.result]), indicator: "green"});
                frm.reload_doc();
            });
            frappe.realtime.on("akfa_zakaz_import_failed", function(d) {
                if (d.doc_name !== frm.doc.name) return;
                frappe.msgprint({title: __("Import xato"), indicator: "red",
                                 message: frappe.utils.escape_html(d.msg || "")});
                frm.reload_doc();
            });
        }

        if (frm.is_new()) return;

        if (frm.doc.xml_file && frm.doc.status !== "Processing") {
            frm.add_custom_button(__("1) Ko'rib chiqish"), function() {
                frappe.call({
                    method: "akfa_diller.akfa_diller.api.zakaz_import.get_preview_data",
                    args: {doc_name: frm.doc.name},
                    freeze: true,
                    freeze_message: __("XML o'qilmoqda..."),
                    callback: function(r) {
                        if (!r.message) return;
                        if (!r.message.success) {
                            frappe.msgprint({title: __("Fayl xatosi"), indicator: "red",
                                             message: frappe.utils.escape_html(r.message.message)});
                            return;
                        }
                        render_preview(frm, r.message);
                        frm.reload_doc();
                    }
                });
            });
        }

        if (frm.doc.xml_file && ["Draft", "Failed"].includes(frm.doc.status)) {
            frm.add_custom_button(__("2) Import qilish"), function() {
                frappe.confirm(
                    __("Zakaz {0} import qilinsinmi? Materiallar ombordan chiqadi, tayyor mahsulot kirim bo'ladi.",
                       [frm.doc.zakaz_raqami || "?"]),
                    function() {
                        frappe.call({
                            method: "akfa_diller.akfa_diller.api.zakaz_import.process_import",
                            args: {doc_name: frm.doc.name},
                            freeze: true,
                            callback: function() { frm.reload_doc(); }
                        });
                    }
                );
            }).addClass("btn-primary");
        }

        if (["Processed", "Failed"].includes(frm.doc.status)) {
            frm.add_custom_button(__("Bekor qilish"), function() {
                frappe.confirm(__("Yaratilgan hujjatlar bekor qilinib o'chiriladi. Davom etilsinmi?"),
                    function() {
                        frappe.call({
                            method: "akfa_diller.akfa_diller.api.zakaz_import.cancel_import",
                            args: {doc_name: frm.doc.name},
                            freeze: true,
                            callback: function() { frm.reload_doc(); }
                        });
                    });
            });
        }

        if (frm.doc.status === "Processing") {
            frm.dashboard.set_headline(__("Import ishlayapti — jurnal pastda yangilanadi"));
        }

        // "Yaratilgan hujjatlar" — o'z joyida, lekin matn o'rniga bosiladigan
        // havolalar ko'rsatiladi
        if (frm.doc.created_docs) {
            const links = frm.doc.created_docs.split(",")
                .map(function(s) { return s.trim(); })
                .filter(Boolean)
                .map(function(n) {
                    return `<a href="/app/stock-entry/${encodeURIComponent(n)}">${frappe.utils.escape_html(n)}</a>`;
                })
                .join(" &nbsp;|&nbsp; ");
            const f = frm.get_field("created_docs");
            if (f && f.$wrapper) {
                const joy = f.$wrapper.find(".control-value, .like-disabled-input").first();
                if (joy.length) joy.html(links);
            }
        }
    }
});

function render_preview(frm, m) {
    const h = m.hujjat;
    const esc = frappe.utils.escape_html;
    let rows = "";
    (m.materiallar || []).slice(0, 200).forEach(function(x) {
        const akfa = x.item_kod && x.item_kod !== x.kod
            ? esc(x.item_kod)
            : `<span style="color:#b45309">${esc(x.item_kod || x.kod)} (tarjimasiz)</span>`;
        rows += `<tr><td>${esc(x.sinf)}</td><td>${esc(x.kod)}</td><td>${akfa}</td>` +
                `<td style="text-align:right">${flt(x.miqdor).toFixed(2)}</td><td>${esc(x.birlik)}</td></tr>`;
    });
    let ogoh = "";
    (m.ogohlantirishlar || []).forEach(function(o) {
        ogoh += `<div style="color:#b45309">&#9888; ${esc(o)}</div>`;
    });
    const html = `
        <div><b>Zakaz:</b> ${esc(h.raqam)} | <b>Sana:</b> ${esc(h.sana)} |
             <b>Loyiha:</b> ${esc(h.loyiha)}</div>
        <div><b>Netto:</b> ${format_number(h.netto_uzs)} UZS |
             <b>Aksessuar:</b> ${format_number(m.aksessuar_summa_uzs)} UZS</div>
        <div><b>Pozitsiyalar:</b> ${m.pozitsiyalar.length} ta |
             <b>Materiallar:</b> ${m.materiallar.length} xil |
             <b>Tarjimasiz:</b> ${(m.tarjimasiz || []).length} ta</div>
        ${ogoh}
        <hr>
        <div style="max-height:340px;overflow:auto">
        <table class="table table-bordered table-sm">
          <thead><tr><th>Sinf</th><th>Klaes kod</th><th>Akfa nomi (item)</th><th>Miqdor</th><th>Birlik</th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>`;
    frappe.msgprint({title: __("Zakaz ko'rib chiqish"), message: html, wide: true});
}
