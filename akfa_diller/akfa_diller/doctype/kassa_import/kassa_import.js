frappe.ui.form.on('Kassa Import', {

    refresh(frm) {
        frm.trigger('setup_buttons');
        frm.trigger('validate_prerequisites');
        frm.trigger('format_kassa_links');

        if (!frm._realtime_bound) {
            frm._realtime_bound = true;

            frappe.realtime.on('akfa_kassa_import_log', (data) => {
                if (data.doc_name === frm.doc.name) {
                    if (data.msg.includes('Qator') || data.msg.includes('YAKUNLANDI')) {
                        frappe.show_alert({ message: data.msg, indicator: 'blue' });
                    }
                }
            });

            frappe.realtime.on('akfa_kassa_import_success', (data) => {
                if (data.doc_name === frm.doc.name) {
                    if (frm._import_poll) {
                        clearInterval(frm._import_poll);
                        frm._import_poll = null;
                    }
                    frappe.show_alert({ message: __('✅ Import muvaffaqiyatli yakunlandi!'), indicator: 'green' }, 10);
                    frm.reload_doc();
                }
            });

            frappe.realtime.on('akfa_kassa_import_failed', (data) => {
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
        frm.trigger('validate_prerequisites');
    },

    mode_of_payment(frm) {
        frm.trigger('validate_prerequisites');
    },

    validate_prerequisites(frm) {
        const valid = frm.doc.company && frm.doc.mode_of_payment;
        frm.set_df_property('excel_file', 'read_only', valid ? 0 : 1);
        frm.set_df_property('excel_file', 'description', valid
            ? "Kassa (to'lovlar) Excel fayli (.xlsx)"
            : '<span style="color:red;">⚠️ Avval Компания va To\'lov usulini tanlang!</span>'
        );
    },

    format_kassa_links(frm) {
        if (frm.doc.status !== 'Processed') return;

        if (frm.doc.kassa_docs) {
            const links = frm.doc.kassa_docs.split(',')
                .map(s => s.trim()).filter(s => s)
                .map(k => `<a href="/app/kassa/${encodeURIComponent(k)}">${k}</a>`)
                .join('<br>');

            setTimeout(() => {
                const el = frm.fields_dict.kassa_docs?.$wrapper?.find('.like-disabled-input, .control-value');
                if (el?.length) el.html(links);
            }, 100);
        }
    },

    // =========================================================================
    // BUTTONS
    // =========================================================================

    setup_buttons(frm) {
        frm.clear_custom_buttons();

        if (frm.doc.status === 'Draft' && frm.doc.excel_file) {
            frm.add_custom_button(__('📋 Load Preview'), () => frm.trigger('load_preview'), __('Actions'));

            const unclassified = (frm.doc.review_rows || []).filter(r => !r.classification).length;
            const process_btn = frm.add_custom_button(__('▶ Process Import'), () => frm.trigger('process_import'));
            process_btn.addClass('btn-primary');
            if (unclassified > 0) {
                process_btn.prop('disabled', true);
                process_btn.attr('title', __('{0} ta qator hali tekshirilmagan (Turi tanlanmagan)', [unclassified]));
            }
        }

        if (frm.doc.status === 'Processing') {
            frm.set_intro(__('⏳ Import fonada ishlayapti... Sahifa avtomatik yangilanadi.'), 'blue');
            frm.disable_save();
            frm.add_custom_button(__('✕ Force Cancel'), () => frm.trigger('cancel_import')).addClass('btn-danger');
        }

        if (frm.doc.status === 'Processed') {
            frm.add_custom_button(__('✕ Cancel Import'), () => frm.trigger('cancel_import')).addClass('btn-danger');

            if (frm.doc.kassa_docs) {
                const names = frm.doc.kassa_docs.split(',').map(s => s.trim()).filter(s => s);
                frm.add_custom_button(__('Kassa ({0})', [names.length]),
                    () => frappe.set_route('List', 'Kassa', { name: ['in', names] }), __('View'));
            }
        }

        if (frm.doc.status === 'Failed') {
            frm.add_custom_button(__('🔄 Retry (davom ettirish)'), () => {
                frm.set_value('status', 'Draft');
                frm.set_value('error_log', '');
                frm.save().then(() => frm.trigger('process_import'));
            }).addClass('btn-warning');
        }
    },

    // =========================================================================
    // ACTIONS
    // =========================================================================

    load_preview(frm) {
        frappe.call({
            method: 'akfa_diller.akfa_diller.api.daily_kassa_import.get_preview_data',
            args: { doc_name: frm.doc.name },
            freeze: true,
            freeze_message: __('Excel o\'qilmoqda...'),
            callback(r) {
                if (!r.message) return;
                const d = r.message;

                if (d.success) {
                    frappe.msgprint({
                        title: __('✅ Preview tayyor'),
                        indicator: 'green',
                        message: `
                            <p>${frappe.utils.escape_html(d.message || '')}</p>
                            <div class="row mt-2">
                                <div class="col"><div class="card bg-primary text-white text-center p-2"><h4 class="mb-0">${d.total_rows || 0}</h4><small>Jami qator</small></div></div>
                                <div class="col"><div class="card bg-success text-white text-center p-2"><h4 class="mb-0">${d.positive_count || 0}</h4><small>Oddiy tushum</small></div></div>
                                <div class="col"><div class="card bg-warning text-white text-center p-2"><h4 class="mb-0">${d.negative_count || 0}</h4><small>Tekshirish kerak</small></div></div>
                                <div class="col"><div class="card bg-danger text-white text-center p-2"><h4 class="mb-0">${d.unmatched_count || 0}</h4><small>Mijoz topilmadi (avtomatik yaratiladi)</small></div></div>
                                <div class="col"><div class="card bg-secondary text-white text-center p-2"><h4 class="mb-0">${d.zero_count || 0}</h4><small>Summasiz (o'tkaziladi)</small></div></div>
                            </div>
                            ${d.negative_count > 0 ? `<p class="mt-3"><strong>⚠️ Pastdagi "Tekshirish kerak bo'lgan qatorlar" jadvalida har bir qatorga "Turi"ni tanlang, keyin Process Import bosing.</strong></p>` : ''}`
                    });
                } else {
                    frappe.msgprint({ title: __('Xato'), indicator: 'red', message: d.message || 'Error' });
                }
                frm.reload_doc();
            }
        });
    },

    process_import(frm) {
        const unclassified = (frm.doc.review_rows || []).filter(r => !r.classification);
        if (unclassified.length) {
            frappe.msgprint({
                title: __('Tekshirilmagan qatorlar bor'),
                indicator: 'red',
                message: __('Quyidagi Excel qatorlari hali tekshirilmagan: {0}. Avval "Turi"ni tanlang.',
                    [unclassified.map(r => r.row_num).join(', ')])
            });
            return;
        }

        const dlg = new frappe.ui.Dialog({
            title: __('Import tasdiqlash'),
            fields: [{
                fieldtype: 'HTML',
                options: `
                    <p><strong>Компания:</strong> ${frm.doc.company}</p>
                    <p><strong>To'lov usuli:</strong> ${frm.doc.mode_of_payment}</p>
                    <div class="alert alert-info mt-3">
                        <strong>Workflow:</strong><br>
                        Har bir Excel qatori uchun ALOHIDA Kassa hujjati yaratiladi va submit qilinadi --<br>
                        Приход/Расход turiga qarab avtomatik Payment Entry yoki Journal Entry ham yaratiladi.<br><br>
                        <em>Import fonada ishlaydi — katta fayllar ham timeout bermaydi.</em>
                    </div>`
            }],
            primary_action_label: __('✓ Import qilish'),
            primary_action() {
                dlg.hide();

                frappe.call({
                    method: 'akfa_diller.akfa_diller.api.daily_kassa_import.process_import',
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
        const count = frm.doc.kassa_docs ? frm.doc.kassa_docs.split(',').filter(s => s.trim()).length : 0;

        const dlg = new frappe.ui.Dialog({
            title: __('Import bekor qilish'),
            fields: [{
                fieldtype: 'HTML',
                options: `
                    <p><strong>Bekor qilinadigan Kassa hujjatlari (${count} ta):</strong></p>
                    <p class="text-danger mt-3"><strong>⚠️ Bu amalni ortga qaytarib bo'lmaydi!</strong></p>`
            }],
            primary_action_label: __('✓ Bekor qilish'),
            primary_action() {
                dlg.hide();

                frappe.call({
                    method: 'akfa_diller.akfa_diller.api.daily_kassa_import.cancel_import',
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
