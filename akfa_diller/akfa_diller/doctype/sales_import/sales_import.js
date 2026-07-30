frappe.ui.form.on('Sales Import', {

    refresh(frm) {
        frm.trigger('setup_buttons');
        frm.trigger('set_warehouse_filter');
        frm.trigger('set_cost_center_filter');
        frm.trigger('validate_prerequisites');
        frm.trigger('format_sales_invoice_links');

        // Register realtime listeners ONCE per form instance
        if (!frm._realtime_bound) {
            frm._realtime_bound = true;

            frappe.realtime.on('akfa_daily_sales_log', (data) => {
                if (data.doc_name === frm.doc.name) {
                    if (data.msg.includes('MIJOZ') || data.msg.includes('YAKUNLANDI')) {
                        frappe.show_alert({ message: data.msg, indicator: 'blue' });
                    }
                }
            });

            frappe.realtime.on('akfa_daily_sales_success', (data) => {
                if (data.doc_name === frm.doc.name) {
                    if (frm._import_poll) {
                        clearInterval(frm._import_poll);
                        frm._import_poll = null;
                    }
                    frappe.show_alert({ message: __('✅ Import muvaffaqiyatli yakunlandi!'), indicator: 'green' }, 10);
                    frm.reload_doc();
                }
            });

            frappe.realtime.on('akfa_daily_sales_failed', (data) => {
                if (data.doc_name === frm.doc.name) {
                    if (frm._import_poll) {
                        clearInterval(frm._import_poll);
                        frm._import_poll = null;
                    }
                    frappe.show_alert({ message: __('❌ Import xatolik bilan yakunlandi'), indicator: 'red' }, 10);
                    frm.reload_doc();
                }
            });
        }

        const colors = { Draft: 'blue', Processing: 'orange', Processed: 'green', Failed: 'red' };
        frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status] || 'gray');

        if (frm.doc.status === 'Processing' && !frm._import_poll) {
            frm._import_poll = setInterval(() => {
                frm.reload_doc().then(() => {
                    if (frm.doc.status === 'Processed' || frm.doc.status === 'Failed') {
                        clearInterval(frm._import_poll);
                        frm._import_poll = null;
                    }
                });
            }, 5000);
        }
    },

    company(frm) {
        if (!frm.doc.company) {
            frm.set_value('warehouse', '');
            frm.set_value('cost_center', '');
            return;
        }

        clearTimeout(frm._company_timer);
        frm._company_timer = setTimeout(() => frm.trigger('auto_set_warehouse'), 300);
        frm.trigger('set_cost_center_filter');
    },

    auto_set_warehouse(frm) {
        if (frm._loading) return;
        frm._loading = true;

        frappe.call({
            method: 'akfa_diller.akfa_diller.api.daily_sales_import.get_default_warehouse',
            args: { company: frm.doc.company },
            callback(r) {
                frm._loading = false;

                if (r.message?.warehouse) {
                    const wh = r.message.warehouse;
                    if (frm.doc.warehouse === wh) {
                        frm.trigger('validate_prerequisites');
                        return;
                    }

                    frm._skip_wh_trigger = true;
                    frm.set_value('warehouse', wh).then(() => {
                        frappe.show_alert({ message: __('Standart ombor: {0}', [wh]), indicator: 'green' });
                        frm._skip_wh_trigger = false;
                        frm.trigger('validate_prerequisites');
                    });
                } else {
                    frm.trigger('validate_prerequisites');
                }
            },
            error() { frm._loading = false; }
        });

        frm.trigger('set_warehouse_filter');
    },

    warehouse(frm) {
        if (!frm._skip_wh_trigger) frm.trigger('validate_prerequisites');
    },

    cost_center(frm) {
        frm.trigger('validate_prerequisites');
    },

    posting_date(frm) {
        frm.trigger('validate_prerequisites');
    },

    excel_file(frm) {
        if (frm.doc.excel_file && frm.doc.status === 'Draft') {
            frm.save().then(() => frm.trigger('show_preview'));
        }
    },

    set_warehouse_filter(frm) {
        frm.set_query('warehouse', () => ({
            filters: { company: frm.doc.company || '', is_group: 0 }
        }));
    },

    set_cost_center_filter(frm) {
        frm.set_query('cost_center', () => ({
            filters: { company: frm.doc.company || '', is_group: 0 }
        }));
    },

    validate_prerequisites(frm) {
        const valid = frm.doc.company && frm.doc.warehouse && frm.doc.cost_center
            && frm.doc.posting_date;
        frm.set_df_property('excel_file', 'read_only', valid ? 0 : 1);
        frm.set_df_property('excel_file', 'description', valid
            ? 'Kunlik savdo Excel fayli (.xlsx)'
            : '<span style="color:red;">⚠️ Avval Компания, Ombor, Cost Center va Sanani tanlang!</span>'
        );
    },

    format_sales_invoice_links(frm) {
        if (frm.doc.status !== 'Processed') return;

        if (frm.doc.sales_invoice) {
            const si_links = frm.doc.sales_invoice.split(',')
                .map(s => s.trim()).filter(s => s)
                .map(si => `<a href="/app/sales-invoice/${si}">${si}</a>`)
                .join('<br>');

            setTimeout(() => {
                const el = frm.fields_dict.sales_invoice?.$wrapper?.find('.like-disabled-input, .control-value');
                if (el?.length) el.html(si_links);
            }, 100);
        }
    },

    // =========================================================================
    // BUTTONS
    // =========================================================================

    setup_buttons(frm) {
        frm.clear_custom_buttons();

        if (frm.doc.status === 'Draft' && frm.doc.excel_file) {
            frm.add_custom_button(__('📋 Preview'), () => frm.trigger('show_preview'), __('Actions'));
            frm.add_custom_button(__('✓ Validate'), () => frm.trigger('validate_items'), __('Actions'));
            frm.add_custom_button(__('▶ Process Import'), () => frm.trigger('process_import')).addClass('btn-primary');
        }

        if (frm.doc.status === 'Processing') {
            frm.set_intro(__('⏳ Import fonada ishlayapti... Sahifa avtomatik yangilanadi.'), 'blue');
            frm.disable_save();
            frm.add_custom_button(__('✕ Force Cancel'), () => frm.trigger('cancel_import')).addClass('btn-danger');
        }

        if (frm.doc.status === 'Processed') {
            frm.add_custom_button(__('✕ Cancel Import'), () => frm.trigger('cancel_import')).addClass('btn-danger');

            if (frm.doc.sales_invoice) {
                const names = frm.doc.sales_invoice.split(',').map(s => s.trim()).filter(s => s);
                frm.add_custom_button(__('Sales Invoice ({0})', [names.length]),
                    () => frappe.set_route('List', 'Sales Invoice', { name: ['in', names] }), __('View'));
            }
        }

        if (frm.doc.status === 'Failed') {
            frm.add_custom_button(__('🔄 Retry'), () => {
                frm.set_value('status', 'Draft');
                frm.set_value('error_log', '');
                frm.set_value('import_log', '');
                frm.save().then(() => frm.trigger('process_import'));
            }).addClass('btn-warning');
        }
    },

    // =========================================================================
    // ACTIONS
    // =========================================================================

    show_preview(frm) {
        frappe.call({
            method: 'akfa_diller.akfa_diller.api.daily_sales_import.get_preview_data',
            args: { doc_name: frm.doc.name },
            freeze: true,
            freeze_message: __('Excel o\'qilmoqda...'),
            callback(r) {
                if (r.message?.success) {
                    frm.trigger('render_preview', r.message);
                } else {
                    frappe.msgprint({ title: __('Xato'), indicator: 'red', message: r.message?.message || 'Error' });
                }
            }
        });
    },

    render_preview(frm, data) {
        const receipts = data.receipts || [];
        const s = data.summary || {};

        const rows = receipts.map((receipt) => {
            const customer_badge = receipt.customer_matched
                ? `<span class="badge badge-success">${frappe.utils.escape_html(receipt.customer)}</span>`
                : `<span class="badge badge-danger">${__('topilmadi')}: ${frappe.utils.escape_html(receipt.customer_input || '')}</span>`;

            return `<tr class="${receipt.customer_matched ? '' : 'table-danger'}">
                <td>${receipt.date || ''}</td>
                <td>${customer_badge}</td>
                <td class="text-right">${receipt.items.length}</td>
                <td class="text-right">${format_currency(receipt.total_amount, 'UZS')}</td>
            </tr>`;
        }).join('');

        new frappe.ui.Dialog({
            title: __('Excel Preview'),
            size: 'extra-large',
            fields: [{
                fieldtype: 'HTML',
                options: `
                    <div class="row mb-3">
                        <div class="col"><div class="card bg-primary text-white text-center p-2"><h4 class="mb-0">${s.total_items || 0}</h4><small>Jami tovar</small></div></div>
                        <div class="col"><div class="card bg-success text-white text-center p-2"><h4 class="mb-0">${s.found || 0}</h4><small>Topildi</small></div></div>
                        <div class="col"><div class="card bg-danger text-white text-center p-2"><h4 class="mb-0">${s.not_found || 0}</h4><small>Tovar topilmadi</small></div></div>
                        <div class="col"><div class="card bg-info text-white text-center p-2"><h4 class="mb-0">${s.total_receipts || 0}</h4><small>Sales Invoice (Mijoz+Sana)</small></div></div>
                        <div class="col"><div class="card bg-danger text-white text-center p-2"><h4 class="mb-0">${s.date_errors || 0}</h4><small>Sana xato</small></div></div>
                        <div class="col"><div class="card bg-danger text-white text-center p-2"><h4 class="mb-0">${s.customers_not_found || 0}</h4><small>Mijoz topilmadi</small></div></div>
                    </div>
                    <p><strong>💰 Jami:</strong> ${format_currency(s.total_amount || 0, 'UZS')}</p>
                    <div class="table-responsive" style="max-height:350px;overflow-y:auto;">
                        <table class="table table-sm table-bordered">
                            <thead class="thead-dark"><tr>
                                <th>Sana</th><th>Mijoz</th>
                                <th class="text-right">Tovar soni</th><th class="text-right">Summa</th>
                            </tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>`
            }]
        }).show();
    },

    validate_items(frm) {
        frappe.call({
            method: 'akfa_diller.akfa_diller.api.daily_sales_import.validate_excel_items',
            args: { doc_name: frm.doc.name },
            freeze: true,
            freeze_message: __('Tekshirilmoqda...'),
            callback(r) {
                if (!r.message) return;
                const d = r.message;

                if (d.success) {
                    frappe.msgprint({
                        title: __('✅ Validation OK'),
                        indicator: 'green',
                        message: `<p><strong>${d.items.length}</strong> ta tovar topildi</p>
                                  <p>Jami: <strong>${format_currency(d.total_amount, 'UZS')}</strong></p>`
                    });
                } else {
                    const errors = (d.errors || []).map(e => `<li>Qator ${e.row}: ${e.error}</li>`).join('');
                    frappe.msgprint({ title: __('❌ Xatolar'), indicator: 'red', message: `<ul>${errors}</ul>` });
                }
            }
        });
    },

    process_import(frm) {
        const dlg = new frappe.ui.Dialog({
            title: __('Import tasdiqlash'),
            fields: [{
                fieldtype: 'HTML',
                options: `
                    <p><strong>Компания:</strong> ${frm.doc.company}</p>
                    <p><strong>Ombor:</strong> ${frm.doc.warehouse}</p>
                    <p><strong>Cost Center:</strong> ${frm.doc.cost_center}</p>
                    <div class="alert alert-info mt-3">
                        <strong>Workflow:</strong><br>
                        Har bir (Mijoz, Sana) juftligi uchun ALOHIDA Sales Invoice yaratiladi (Update Stock ON) --<br>
                        bir xil mijoz boshqa kunda xarid qilgan bo'lsa ham alohida Invoys bo'ladi.<br>
                        Boshqa hech qanday hujjat (Stock Entry va h.k.) yaratilmaydi.<br><br>
                        <em>Import fonada ishlaydi — katta fayllar ham timeout bermaydi.</em>
                    </div>`
            }],
            primary_action_label: __('✓ Import qilish'),
            primary_action() {
                dlg.hide();

                frappe.call({
                    method: 'akfa_diller.akfa_diller.api.daily_sales_import.process_import',
                    args: { doc_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __('Import fonga yuklanmoqda...'),
                    callback(r) {
                        if (!r.message) return;
                        const res = r.message;

                        if (res.success) {
                            frappe.msgprint({
                                title: __('✅ Fon rejimi'),
                                indicator: 'blue',
                                message: __('Import fonada boshlandi. Sahifa avtomatik yangilanadi.')
                            });

                            frm._import_poll = setInterval(() => {
                                frm.reload_doc().then(() => {
                                    if (frm.doc.status === 'Processed' || frm.doc.status === 'Failed') {
                                        clearInterval(frm._import_poll);
                                        frm._import_poll = null;

                                        if (frm.doc.status === 'Processed') {
                                            frappe.show_alert({ message: __('✅ Import muvaffaqiyatli yakunlandi!'), indicator: 'green' }, 10);
                                        } else {
                                            frappe.show_alert({ message: __('❌ Import xatolik bilan yakunlandi'), indicator: 'red' }, 10);
                                        }
                                    }
                                });
                            }, 5000);
                        } else {
                            frappe.msgprint({ title: __('❌ Xato'), indicator: 'red', message: res.message });
                        }
                        frm.reload_doc();
                    }
                });
            }
        });
        dlg.show();
    },

    cancel_import(frm) {
        const siCount = frm.doc.sales_invoice ? frm.doc.sales_invoice.split(',').filter(s => s.trim()).length : 0;

        const dlg = new frappe.ui.Dialog({
            title: __('Import bekor qilish'),
            fields: [{
                fieldtype: 'HTML',
                options: `
                    <p><strong>Bekor qilinadigan Sales Invoice'lar (${siCount} ta):</strong></p>
                    <p>${frm.doc.sales_invoice || 'N/A'}</p>
                    <p class="text-danger mt-3"><strong>⚠️ Bu amalni ortga qaytarib bo'lmaydi!</strong></p>`
            }],
            primary_action_label: __('✓ Bekor qilish'),
            primary_action() {
                dlg.hide();

                frappe.call({
                    method: 'akfa_diller.akfa_diller.api.daily_sales_import.cancel_import',
                    args: { doc_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __('Bekor qilinmoqda...'),
                    callback(r) {
                        if (r.message?.success) {
                            frappe.show_alert({ message: __('Import bekor qilindi'), indicator: 'green' });
                        } else {
                            frappe.msgprint({ title: __('Xato'), indicator: 'red', message: r.message?.message });
                        }
                        frm.reload_doc();
                    }
                });
            }
        });
        dlg.show();
    }
});
