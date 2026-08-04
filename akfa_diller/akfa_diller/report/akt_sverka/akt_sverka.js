frappe.query_reports["Akt Sverka"] = {
    "tree": true,
    "name_field": "row_id",
    "parent_field": "parent_row_id",
    "initial_depth": 0,

    "after_datatable_render": function(datatable_obj) {
        if (!frappe.query_report || !frappe.query_report.$report) {
            return;
        }

        frappe.query_report.$report.addClass("akt-sverka-report");

        if (!document.getElementById("akt-sverka-report-style")) {
            var style = document.createElement("style");
            style.id = "akt-sverka-report-style";
            style.textContent = `
                .akt-sverka-report .dt-tree-node {
                    padding-left: 0 !important;
                }
                .akt-sverka-report .dt-tree-node__toggle {
                    display: none;
                }
                .akt-sverka-items-toggle {
                    width: 22px;
                    height: 22px;
                    padding: 0;
                    border: 0;
                    background: transparent;
                    flex: 0 0 auto;
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    border-radius: 4px;
                    cursor: pointer;
                    color: var(--text-muted, #6b7280);
                    font-size: 16px;
                    line-height: 1;
                }
                .akt-sverka-items-toggle:hover {
                    background: var(--control-bg, #f3f4f6);
                }
                .akt-sverka-voucher-cell {
                    display: flex;
                    align-items: center;
                    gap: 4px;
                    min-width: 0;
                }
                .akt-sverka-voucher-link {
                    min-width: 0;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    white-space: nowrap;
                }
                .akt-sverka-item-cell {
                    color: #525252;
                }
            `;
            document.head.appendChild(style);
        }
    },

    "toggle_invoice_items": function(row_index, button) {
        var report = frappe.query_report;
        var datatable = report && report.datatable;

        if (!datatable || row_index === undefined || row_index === null) {
            return false;
        }

        row_index = cint(row_index);
        var row = datatable.datamanager.getRow(row_index);

        if (!row || row.meta.isLeaf) {
            return false;
        }

        if (row.meta.isTreeNodeClose) {
            if (button) {
                button.textContent = "▾";
                button.setAttribute("aria-label", __("Ёпиш") + " (" + button.dataset.itemCount + ")");
                button.setAttribute("title", __("Ёпиш") + " (" + button.dataset.itemCount + ")");
            }
            datatable.rowmanager.openSingleNode(row_index);
        } else {
            if (button) {
                button.textContent = "▸";
                button.setAttribute("aria-label", __("Маҳсулотлар") + " (" + button.dataset.itemCount + ")");
                button.setAttribute("title", __("Маҳсулотлар") + " (" + button.dataset.itemCount + ")");
            }
            datatable.rowmanager.closeSingleNode(row_index);
        }

        return false;
    },

    "onload": function(report) {
        var original_show_footer_message = report.show_footer_message;
        report.show_footer_message = function() {
            original_show_footer_message.apply(report, arguments);

            if (report.$tree_footer) {
                report.$tree_footer.hide();
            }
        };

        // Add back button to return to Kontragent Otchet
        report.page.add_inner_button(__("Контрагент Отчётга Қайтиш"), function() {
            var from_date = frappe.query_report.get_filter_value('from_date');
            var to_date = frappe.query_report.get_filter_value('to_date');
            var party_type = frappe.query_report.get_filter_value('party_type');

            // Redirect to Kontragent Otchet with filters
            var url = `/app/query-report/Kontragent Otchet?from_date=${from_date}&to_date=${to_date}`;
            if (party_type) {
                url += `&party_type=${encodeURIComponent(party_type)}`;
            }
            frappe.set_route('query-report', 'Kontragent Otchet', {
                from_date: from_date,
                to_date: to_date,
                party_type: party_type || ''
            });
        });

        report.page.add_inner_button(__("PDF юклаш"), function() {
            var filters = frappe.query_report.get_values();

            if (!filters || !filters.party) {
                frappe.msgprint({
                    title: __("Хато"),
                    message: __("Контрагентни танланг"),
                    indicator: "red"
                });
                return;
            }

            if (!filters.from_date || !filters.to_date) {
                frappe.msgprint({
                    title: __("Хато"),
                    message: __("Санани кўрсатинг"),
                    indicator: "red"
                });
                return;
            }

            frappe.dom.freeze(__("PDF тайёрланмоқда... Илтимос кутинг"));

            frappe.call({
                method: "akfa_diller.akfa_diller.report.akt_sverka.akt_sverka.generate_akt_sverka_pdf",
                args: { filters: filters },
                timeout: 120,
                callback: function(r) {
                    frappe.dom.unfreeze();

                    if (!r.message) {
                        frappe.msgprint({
                            title: __("Хато"),
                            message: __("PDF бўш қайтди. Error Log ни текширинг."),
                            indicator: "red"
                        });
                        return;
                    }

                    try {
                        var byteChars = atob(r.message);
                        var byteNumbers = new Array(byteChars.length);
                        for (var i = 0; i < byteChars.length; i++) {
                            byteNumbers[i] = byteChars.charCodeAt(i);
                        }

                        var byteArray = new Uint8Array(byteNumbers);
                        var blob = new Blob([byteArray], { type: "application/pdf" });
                        var url = URL.createObjectURL(blob);
                        var filename = "Akt_Sverka_"
                            + (filters.party || "")
                            + "_" + (filters.from_date || "")
                            + "_" + (filters.to_date || "")
                            + ".pdf";

                        var a = document.createElement("a");
                        a.href = url;
                        a.download = filename;
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        URL.revokeObjectURL(url);
                    } catch (e) {
                        frappe.msgprint({
                            title: __("Хато"),
                            message: __("PDF юклашда браузер хатоси: ") + e.message,
                            indicator: "red"
                        });
                    }
                },
                error: function() {
                    frappe.dom.unfreeze();
                    frappe.msgprint({
                        title: __("Сервер хатоси"),
                        message: __("PDF генерация қилишда хато. Error Log ни текширинг."),
                        indicator: "red"
                    });
                }
            });
        });
    },
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
            "fieldname": "company",
            "label": __("Компания"),
            "fieldtype": "Link",
            "options": "Company",
            "default": frappe.defaults.get_user_default("Company")
        },
        {
            "fieldname": "party_type",
            "label": __("Контрагент тури"),
            "fieldtype": "Select",
            "options": "\nCustomer\nSupplier\nEmployee\nOther",
            "default": "Customer",
            "reqd": 1
        },
        {
            "fieldname": "party",
            "label": __("Контрагент"),
            "fieldtype": "Dynamic Link",
            "reqd": 1,
            "get_options": function() {
                var party_type = frappe.query_report.get_filter_value('party_type');
                var party = frappe.query_report.get_filter_value('party');
                if(party && !party_type) {
                    frappe.throw(__("Please select Party Type first"));
                }
                return party_type;
            }
        }
    ],
    
    "formatter": function(value, row, column, data, default_formatter) {
        // Dollar belgisini olib tashlash
        value = default_formatter(value, row, column, data);
        
        // Currency fieldlarida $ ni olib tashlash
        if (column.fieldtype == "Currency" && value) {
            value = value.replace(/\$/g, '');
        }
        
        // Total qatorini highlight qilish
        if (data && data.voucher_type === "Total") {
            value = `<span style="font-weight: bold; background-color: #e3f2fd;">${value}</span>`;
        }

        if (data && data.is_item_row) {
            if (["posting_date", "voucher_type", "voucher_no"].includes(column.fieldname)) {
                return "";
            }

            if (column.fieldname === "item_name") {
                return `<span class="akt-sverka-item-cell">${value || ""}</span>`;
            }
        }

        if (
            data
            && data.has_item_details
            && column.fieldname === "voucher_no"
            && row
            && row.meta
        ) {
            var row_index = row.meta.rowIndex;
            var is_closed = row.meta.isTreeNodeClose;
            var label = is_closed ? __("Маҳсулотлар") : __("Ёпиш");
            var count = cint(data.item_count || 0);

            value = `
                <span class="akt-sverka-voucher-cell">
                <button
                    type="button"
                    class="akt-sverka-items-toggle"
                    data-item-count="${count}"
                    aria-label="${label} (${count})"
                    title="${label} (${count})"
                    onclick="event.preventDefault(); event.stopPropagation(); return frappe.query_reports['Akt Sverka'].toggle_invoice_items(${row_index}, this);"
                >${is_closed ? "▸" : "▾"}</button>
                    <span class="akt-sverka-voucher-link">${value || ""}</span>
                </span>
            `;
        }
        
        return value;
    }
}
