// Copyright (c) 2025, abdulloh and contributors
// For license information, please see license.txt

// Konvertatsiya/peremeshenie uchun mode of payment turi NOMI bilan emas,
// ulangan cash account valyutasi bilan aniqlanadi (server query orqali).
const DIVIDEND_PARTY_TYPES = ["Дивиденд", "Дивиденд 1", "Дивиденд 2", "Дивиденд 3"];

frappe.ui.form.on("Kassa", {
    onload: function(frm) {
        frm.trigger("clear_copied_linked_document");
    },

    refresh: function(frm) {
        frm._cash_account_to_currency = frm._cash_account_to_currency || "";
        frm.trigger("clear_copied_linked_document");

        // Set expense account query
        frm.set_query("expense_account", function() {
            return {
                query: "akfa_diller.akfa_diller.doctype.kassa.kassa.get_expense_accounts",
                filters: {
                    company: frm.doc.company
                }
            };
        });

        // Set cost center query — faqat joriy компания bo'yicha, guruh bo'lmagan (leaf) markazlar
        frm.set_query("cost_center", function() {
            return {
                filters: {
                    company: frm.doc.company,
                    is_group: 0
                }
            };
        });

        // Set mode_of_payment query
        frm.trigger("set_mode_of_payment_query");

        // Update balance on refresh
        if (frm.doc.mode_of_payment && frm.doc.company) {
            frm.trigger("update_balance");
        }

        // Update balance_to on refresh for transfer/conversion
        if (frm.doc.mode_of_payment_to && frm.doc.company) {
            frm.trigger("update_balance_to");
        }

        // Set mode_of_payment_to query
        frm.trigger("set_mode_of_payment_to_query");

        if (frm.doc.transaction_type === "Конвертация" && !frm.doc.exchange_rate) {
            frm.trigger("fetch_exchange_rate");
        }

        // Update balance label based on transaction type
        frm.trigger("update_balance_label");
        frm.trigger("sync_currency_fields");
        frm.trigger("update_exchange_fields");
        frm.trigger("render_currency_info");

    },

    before_save: function(frm) {
        frm.trigger("clear_copied_linked_document");
    },

    clear_copied_linked_document: function(frm) {
        if (!frm.is_new()) {
            return;
        }

        if (frm.doc.linked_doctype || frm.doc.linked_entry) {
            frm.doc.linked_doctype = "";
            frm.doc.linked_entry = "";
            frm.refresh_field("linked_doctype");
            frm.refresh_field("linked_entry");
        }
    },

    company: function(frm) {
        frm._cash_account_to_currency = "";
        frm.set_value("mode_of_payment", "");
        frm.set_value("cash_account", "");
        frm.set_value("balance", 0);
        frm.set_value("cash_account_to_currency", "");
        frm.set_value("target_amount_currency", "");
        frm.set_value("party", "");
        frm.set_value("expense_account", "");
        frm.set_value("cost_center", "");
        frm.trigger("sync_currency_fields");
        frm.trigger("update_exchange_fields");
        frm.trigger("render_currency_info");
    },

    mode_of_payment: function(frm) {
        if (frm.doc.mode_of_payment && frm.doc.company) {
            frappe.call({
                method: "akfa_diller.akfa_diller.doctype.kassa.kassa.get_cash_account_with_currency",
                args: {
                    mode_of_payment: frm.doc.mode_of_payment,
                    company: frm.doc.company
                },
                callback: function(r) {
                    if (r.message && r.message.account) {
                        frm.set_value("cash_account", r.message.account);
                        frm.set_value("cash_account_currency", r.message.currency);

                        // Konvertatsiya faqat UZS/USD account valyutasida ishlaydi.
                        if (frm.doc.transaction_type === "Конвертация"
                            && r.message.currency
                            && !["UZS", "USD"].includes(r.message.currency)) {
                            frappe.msgprint(__("Для конвертации выберите способ оплаты в UZS или USD."));
                        }

                        frm.trigger("update_balance");
                        frm.trigger("sync_currency_fields");
                        frm.trigger("update_exchange_fields");
                        frm.trigger("render_currency_info");
                    } else {
                        frappe.msgprint(__("Для данного способа оплаты не настроен счет кассы для компании {0}", [frm.doc.company]));
                        frm.set_value("cash_account", "");
                        frm.set_value("cash_account_currency", "");
                        frm.set_value("balance", 0);
                        frm.trigger("sync_currency_fields");
                        frm.trigger("update_exchange_fields");
                        frm.trigger("render_currency_info");
                    }
                }
            });
        } else {
            frm.set_value("cash_account", "");
            frm.set_value("cash_account_currency", "");
            frm.set_value("balance", 0);
            frm.trigger("sync_currency_fields");
            frm.trigger("update_exchange_fields");
            frm.trigger("render_currency_info");
        }

        // Clear mode_of_payment_to when mode_of_payment changes (for transfer/conversion)
        // Manba o'zgarsa, "куда" tomonni tozalab, query'ni yangi valyutaga qarab qayta quramiz.
        if (in_list(["Перемещения", "Конвертация"], frm.doc.transaction_type)) {
            frm.set_value("mode_of_payment_to", "");
            frm.set_value("cash_account_to", "");
            frm.set_value("balance_to", 0);
            frm.trigger("set_mode_of_payment_to_query");
        }
    },

    update_balance: function(frm) {
        if (frm.doc.cash_account && frm.doc.company) {
            frappe.call({
                method: "akfa_diller.akfa_diller.doctype.kassa.kassa.get_account_balance",
                args: {
                    account: frm.doc.cash_account,
                    company: frm.doc.company
                },
                callback: function(r) {
                    set_derived_value(frm, "balance", r.message || 0);
                }
            });
        }
    },

    transaction_type: function(frm) {
        // Clear party fields
        frm.set_value("party_type", "");
        frm.set_value("party", "");
        frm.set_value("expense_account", "");
        frm.set_value("party_name", "");
        frm.set_value("expense_account_name", "");

        // Clear payment and transfer/conversion fields
        frm._cash_account_to_currency = "";
        frm.set_value("mode_of_payment", "");
        frm.set_value("cash_account", "");
        frm.set_value("balance", 0);
        frm.set_value("mode_of_payment_to", "");
        frm.set_value("cash_account_to", "");
        frm.set_value("cash_account_to_currency", "");
        frm.set_value("target_amount_currency", "");
        frm.set_value("balance_to", 0);
        frm.set_value("exchange_rate", 0);
        frm.set_value("debit_amount", 0);
        frm.set_value("credit_amount", 0);

        // Set queries
        frm.trigger("set_mode_of_payment_query");
        frm.trigger("set_mode_of_payment_to_query");

        // For Перемещения, set default company if not set
        if (frm.doc.transaction_type === "Перемещения" && !frm.doc.company) {
            frappe.call({
                method: "frappe.client.get_value",
                args: {
                    doctype: "Global Defaults",
                    fieldname: "default_company"
                },
                callback: function(r) {
                    if (r.message && r.message.default_company) {
                        frm.set_value("company", r.message.default_company);
                    }
                }
            });
        }

        if (frm.doc.transaction_type === "Конвертация") {
            frm.trigger("fetch_exchange_rate");
        }

        // Update balance label
        frm.trigger("update_balance_label");
        frm.trigger("sync_currency_fields");
        frm.trigger("update_exchange_fields");
        frm.trigger("render_currency_info");
    },

    mode_of_payment_to: function(frm) {
        if (frm.doc.mode_of_payment_to && frm.doc.company) {
            frappe.call({
                method: "akfa_diller.akfa_diller.doctype.kassa.kassa.get_cash_account_with_currency",
                args: {
                    mode_of_payment: frm.doc.mode_of_payment_to,
                    company: frm.doc.company
                },
                callback: function(r) {
                    if (r.message && r.message.account) {
                        frm.set_value("cash_account_to", r.message.account);
                        frm._cash_account_to_currency = r.message.currency || "";
                        frm.set_value("cash_account_to_currency", r.message.currency || "");
                        frm.trigger("update_balance_to");
                        frm.trigger("validate_transfer_pair");
                        if (frm.doc.transaction_type === "Конвертация") {
                            frm.trigger("fetch_exchange_rate");
                            frm.trigger("calculate_exchange_amounts");
                        }
                        frm.trigger("sync_currency_fields");
                        frm.trigger("update_exchange_fields");
                        frm.trigger("render_currency_info");
                    } else {
                        frappe.msgprint(__("Для данного способа оплаты не настроен счет кассы для компании {0}", [frm.doc.company]));
                        frm._cash_account_to_currency = "";
                        frm.set_value("cash_account_to", "");
                        frm.set_value("cash_account_to_currency", "");
                        frm.set_value("balance_to", 0);
                        frm.trigger("sync_currency_fields");
                        frm.trigger("update_exchange_fields");
                        frm.trigger("render_currency_info");
                    }
                }
            });
        } else {
            frm._cash_account_to_currency = "";
            frm.set_value("cash_account_to", "");
            frm.set_value("cash_account_to_currency", "");
            frm.set_value("balance_to", 0);
            frm.trigger("sync_currency_fields");
            frm.trigger("update_exchange_fields");
            frm.trigger("render_currency_info");
        }
    },

    update_balance_to: function(frm) {
        if (frm.doc.cash_account_to && frm.doc.company) {
            frappe.call({
                method: "akfa_diller.akfa_diller.doctype.kassa.kassa.get_account_balance",
                args: {
                    account: frm.doc.cash_account_to,
                    company: frm.doc.company
                },
                callback: function(r) {
                    set_derived_value(frm, "balance_to", r.message || 0);
                }
            });
        }
    },

    set_mode_of_payment_query: function(frm) {
        frm.set_query("mode_of_payment", function() {
            // Konvertatsiya manbasi (откуда) — UZS/USD account valyutasidagi
            // barcha usullar, haqiqiy account_currency bo'yicha (server query).
            if (frm.doc.transaction_type === "Конвертация") {
                return {
                    query: "akfa_diller.akfa_diller.doctype.kassa.kassa.conversion_mode_of_payment_query",
                    filters: {
                        company: frm.doc.company
                    }
                };
            }

            return {
                filters: {
                    enabled: 1
                }
            };
        });
    },

    set_mode_of_payment_to_query: function(frm) {
        frm.set_query("mode_of_payment_to", function() {
            // Konvertatsiyada "куда" tomon manba account valyutasiga qarab,
            // server tomonda haqiqiy account_currency bo'yicha filterlanadi.
            if (frm.doc.transaction_type === "Конвертация") {
                return {
                    query: "akfa_diller.akfa_diller.doctype.kassa.kassa.conversion_mode_of_payment_query",
                    filters: {
                        company: frm.doc.company,
                        source_mode_of_payment: frm.doc.mode_of_payment
                    }
                };
            }

            // Peremeshenie ham "куда" tomonni manba account valyutasiga qarab
            // (bir xil valyuta) server tomonda filterlaydi.
            if (frm.doc.transaction_type === "Перемещения") {
                return {
                    query: "akfa_diller.akfa_diller.doctype.kassa.kassa.transfer_mode_of_payment_query",
                    filters: {
                        company: frm.doc.company,
                        source_mode_of_payment: frm.doc.mode_of_payment
                    }
                };
            }

            let filters = {
                enabled: 1
            };

            if (frm.doc.mode_of_payment) {
                filters.name = ["!=", frm.doc.mode_of_payment];
            }

            return { filters: filters };
        });
    },

    validate_transfer_pair: function(frm) {
        if (!in_list(["Перемещения", "Конвертация"], frm.doc.transaction_type)) return;
        if (!frm.doc.cash_account || !frm.doc.cash_account_to) return;

        frappe.db.get_value("Account", frm.doc.cash_account, "account_currency", function(from_r) {
            frappe.db.get_value("Account", frm.doc.cash_account_to, "account_currency", function(to_r) {
                const from_currency = from_r && from_r.account_currency;
                const to_currency = to_r && to_r.account_currency;

                if (!from_currency || !to_currency) return;

                if (frm.doc.transaction_type === "Перемещения" && from_currency !== to_currency) {
                    frappe.msgprint({
                        title: __("Ошибка валюты"),
                        indicator: "red",
                        message: __("Для перемещения способы оплаты должны иметь одинаковую валюту.")
                    });
                }

                if (frm.doc.transaction_type === "Конвертация" && from_currency === to_currency) {
                    frappe.msgprint({
                        title: __("Ошибка валюты"),
                        indicator: "red",
                        message: __("Для конвертации способы оплаты должны иметь разные валюты.")
                    });
                }
            });
        });
    },

    fetch_exchange_rate: function(frm) {
        const pair = getExchangePair(frm);
        if (!pair.from_currency || !pair.to_currency) {
            return;
        }

        frappe.call({
            method: "akfa_diller.akfa_diller.doctype.kassa.kassa.get_exchange_rate",
            args: {
                from_currency: pair.from_currency,
                to_currency: pair.to_currency,
                date: frm.doc.date || frappe.datetime.get_today()
            },
            callback: function(r) {
                if (r.message) {
                    frm.set_value("exchange_rate", r.message);
                }
            }
        });
    },

    debit_amount: function(frm) {
        if (frm.doc.transaction_type === "Конвертация") {
            set_derived_value(frm, "manual_credit_amount", 0);
        }
        frm.trigger("calculate_exchange_amounts");
        frm.trigger("render_currency_info");
    },

    exchange_rate: function(frm) {
        set_derived_value(frm, "manual_credit_amount", 0);
        frm.trigger("calculate_exchange_amounts");
        frm.trigger("render_currency_info");
    },

    credit_amount: function(frm) {
        if (frm.doc.transaction_type === "Конвертация") {
            if (frm._setting_credit_amount_from_script) {
                return;
            }

            set_derived_value(frm, "manual_credit_amount", flt(frm.doc.credit_amount) > 0 ? 1 : 0);
            frm.trigger("render_currency_info");
            return;
        }

        if (!isPartyMulticurrencyPayment(frm)) {
            return;
        }

        if (frm._setting_credit_amount_from_script) {
            return;
        }

        set_derived_value(frm, "manual_credit_amount", flt(frm.doc.credit_amount) > 0 ? 1 : 0);
        frm.trigger("render_currency_info");
    },

    amount: function(frm) {
        if (isPartyMulticurrencyPayment(frm)) {
            set_derived_value(frm, "manual_credit_amount", 0);
        }
        frm.trigger("calculate_exchange_amounts");
        frm.trigger("render_currency_info");
    },

    sync_currency_fields: function(frm) {
        let targetCurrency = "";

        if (frm.doc.transaction_type === "Конвертация") {
            targetCurrency = frm.doc.cash_account_to_currency || getTargetCurrency(frm) || "";
        } else if (isPartyMulticurrencyPayment(frm)) {
            targetCurrency = frm.doc.party_currency || "";
        }

        set_derived_value(frm, "target_amount_currency", targetCurrency);
        frm.refresh_fields([
            "balance",
            "amount",
            "balance_to",
            "debit_amount",
            "credit_amount",
        ]);
    },

    update_exchange_fields: function(frm) {
        const showPartyExchange = isPartyMulticurrencyPayment(frm);
        const showConversion = frm.doc.transaction_type === "Конвертация";
        const showBlock = showPartyExchange || showConversion;

        frm.set_df_property("section_break_conversion", "hidden", showBlock ? 0 : 1);
        frm.set_df_property("column_break_conversion", "hidden", showBlock ? 0 : 1);
        frm.set_df_property("exchange_rate", "hidden", showBlock ? 0 : 1);
        frm.set_df_property("debit_amount", "hidden", showConversion ? 0 : 1);
        frm.set_df_property("credit_amount", "hidden", showBlock ? 0 : 1);
        frm.set_df_property("debit_amount", "read_only", showPartyExchange ? 1 : 0);
        frm.set_df_property("credit_amount", "read_only", 0);

        if (showPartyExchange) {
            frm.set_df_property("section_break_conversion", "label", "Мультивалютный платеж");
            frm.set_df_property("exchange_rate", "label", `Курс ${frm.doc.cash_account_currency || ""} к ${frm.doc.party_currency || ""}`.trim());
            frm.set_df_property("credit_amount", "label", `Сумма в валюте контрагента${frm.doc.party_currency ? ` (${frm.doc.party_currency})` : ""}`);
        } else {
            frm.set_df_property("section_break_conversion", "label", "Конвертация");
            frm.set_df_property("exchange_rate", "label", "Курс");
            frm.set_df_property("credit_amount", "label", "Сумма прихода");
        }

        if (showPartyExchange) {
            if (frm.doc.mode_of_payment && frm.doc.party && (!frm.doc.exchange_rate || flt(frm.doc.exchange_rate) <= 0 || flt(frm.doc.exchange_rate) === 1)) {
                frm.trigger("fetch_exchange_rate");
            }
            frm.trigger("calculate_exchange_amounts");
        } else if (!showConversion) {
            frm.set_value("exchange_rate", 0);
            frm.set_value("debit_amount", 0);
            frm.set_value("credit_amount", 0);
        }

        frm.trigger("sync_currency_fields");
        frm.refresh_fields(["section_break_conversion", "exchange_rate", "debit_amount", "credit_amount"]);
    },

    calculate_exchange_amounts: function(frm) {
        if (frm.doc.transaction_type === "Конвертация") {
            if (!frm.doc.debit_amount || !frm.doc.exchange_rate) return;
            if (cint(frm.doc.manual_credit_amount)) return;

            const targetCurrency = getTargetCurrency(frm);
            const precision = targetCurrency === "UZS" ? 0 : 2;
            let credit = flt(frm.doc.debit_amount) * flt(frm.doc.exchange_rate);
            frm._setting_credit_amount_from_script = true;
            set_derived_value(frm, "credit_amount", flt(credit, precision));
            frm._setting_credit_amount_from_script = false;
            frm.trigger("render_currency_info");
            return;
        }

        if (!isPartyMulticurrencyPayment(frm)) return;
        if (!frm.doc.amount || !frm.doc.exchange_rate) return;

        set_derived_value(frm, "debit_amount", flt(frm.doc.amount));

        if (!cint(frm.doc.manual_credit_amount)) {
            frm._setting_credit_amount_from_script = true;
            set_derived_value(frm, "credit_amount", flt(flt(frm.doc.amount) * flt(frm.doc.exchange_rate), 2));
            frm._setting_credit_amount_from_script = false;
        }
        frm.trigger("render_currency_info");
    },

    update_balance_label: function(frm) {
        if (in_list(["Перемещения", "Конвертация"], frm.doc.transaction_type)) {
            frm.set_df_property("balance", "label", "Остаток (откуда)");
        } else {
            frm.set_df_property("balance", "label", "Остаток");
        }
        frm.refresh_field("balance");
    },

    party_type: function(frm) {
        frm.set_value("party", "");
        frm.set_value("expense_account", "");
        frm.set_value("party_name", "");
        frm.set_value("expense_account_name", "");
        frm.set_value("cost_center", "");

        if (frm.doc.party_type === "Расходы") {
            frm.set_df_property("expense_account", "reqd", 1);
            frm.set_df_property("party", "reqd", 0);
        } else if (isDividendPartyType(frm.doc.party_type)) {
            frm.set_df_property("expense_account", "reqd", 0);
            frm.set_df_property("party", "reqd", 0);
        } else if (frm.doc.party_type) {
            frm.set_df_property("expense_account", "reqd", 0);
            frm.set_df_property("party", "reqd", 1);
        } else {
            frm.set_df_property("expense_account", "reqd", 0);
            frm.set_df_property("party", "reqd", 0);
        }

        frm.refresh_fields();
    },

    party: function(frm) {
        if (frm.doc.party && frm.doc.party_type) {
            let name_field = get_party_name_field(frm.doc.party_type);
            if (name_field) {
                frappe.db.get_value(frm.doc.party_type, frm.doc.party, name_field, function(r) {
                    if (r && r[name_field]) {
                        frm.set_value("party_name", r[name_field]);
                    }
                });
            }

            if (in_list(["Customer", "Supplier"], frm.doc.party_type)) {
                frappe.call({
                    method: "akfa_diller.akfa_diller.doctype.kassa.kassa.get_party_currency",
                    args: {
                        party_type: frm.doc.party_type,
                        party: frm.doc.party,
                        company: frm.doc.company
                    },
                    callback: function(r) {
                        if (r.message) {
                            frm.set_value("party_currency", r.message);
                            frm.trigger("sync_currency_fields");
                            frm.trigger("update_exchange_fields");
                            frm.trigger("render_currency_info");
                        }
                    }
                });
            }
        } else {
            frm.set_value("party_name", "");
            frm.set_value("party_currency", "");
            frm.trigger("sync_currency_fields");
            frm.trigger("update_exchange_fields");
            frm.trigger("render_currency_info");
        }
    },

    render_currency_info: function(frm) {
        const wrapper = frm.fields_dict.currency_info_html && frm.fields_dict.currency_info_html.$wrapper;
        if (!wrapper) return;

        const rows = [];
        const txType = frm.doc.transaction_type;
        const sourceCurrency = frm.doc.cash_account_currency;
        const targetCurrency = getTargetCurrency(frm);
        const sourceMode = frm.doc.mode_of_payment;
        const targetMode = frm.doc.mode_of_payment_to;

        if (txType) {
            rows.push(`<div><strong>Операция:</strong> ${frappe.utils.escape_html(txType)}</div>`);
        }

        if (sourceMode || sourceCurrency) {
            rows.push(
                `<div><strong>Источник:</strong> ${frappe.utils.escape_html(sourceMode || "-")} ${sourceCurrency ? `(${frappe.utils.escape_html(sourceCurrency)})` : ""}</div>`
            );
        }

        if (targetMode || targetCurrency) {
            rows.push(
                `<div><strong>Назначение:</strong> ${frappe.utils.escape_html(targetMode || "-")} ${targetCurrency ? `(${frappe.utils.escape_html(targetCurrency)})` : ""}</div>`
            );
        }

        if (frm.doc.party && frm.doc.party_currency) {
            rows.push(
                `<div><strong>Контрагент:</strong> ${frappe.utils.escape_html(frm.doc.party)} (${frappe.utils.escape_html(frm.doc.party_currency)})</div>`
            );
        }

        if (frm.doc.exchange_rate && (txType === "Конвертация" || isPartyMulticurrencyPayment(frm))) {
            rows.push(
                `<div><strong>Курс:</strong> 1 ${frappe.utils.escape_html(sourceCurrency || "-")} = ${frappe.format(frm.doc.exchange_rate, {fieldtype: "Float", precision: 6})} ${frappe.utils.escape_html((txType === "Конвертация" ? targetCurrency : frm.doc.party_currency) || "-")}</div>`
            );
        }

        if (isPartyMulticurrencyPayment(frm) && frm.doc.credit_amount) {
            rows.push(
                `<div><strong>Сумма в валюте контрагента:</strong> ${frappe.format(frm.doc.credit_amount, {fieldtype: "Currency"})} ${frappe.utils.escape_html(frm.doc.party_currency || "")}</div>`
            );
        }

        const note = getCurrencyInfoNote(frm, sourceCurrency, targetCurrency);
        if (note) {
            rows.push(`<div style="margin-top: 8px;"><strong>Подсказка:</strong> ${frappe.utils.escape_html(note)}</div>`);
        }

        if (!rows.length) {
            wrapper.empty();
            return;
        }

        wrapper.html(`
            <div style="padding: 12px; border: 1px solid #e5e7eb; border-radius: 10px; background: #fafafa; line-height: 1.6;">
                ${rows.join("")}
            </div>
        `);
    },

	    expense_account: function(frm) {
	        if (frm.doc.expense_account) {
	            frappe.db.get_value("Account", frm.doc.expense_account, "account_name", function(r) {
	                if (r && r.account_name) {
	                    frm.set_value("expense_account_name", r.account_name);
	                }
	            });

	            // Agar cost_center hali tanlanmagan bo'lsa — Expense Cost Center
	            // mappingidan (sozlangan bo'lsa) taklif sifatida to'ldiramiz.
	            // Foydalanuvchi istalgan vaqt buni o'zgartirishi mumkin.
	            if (!frm.doc.cost_center) {
	                var requested_expense_account = frm.doc.expense_account;
	                frappe.db.get_value(
	                    "Expense Cost Center",
	                    {"expense_account": requested_expense_account},
	                    "cost_center",
	                    function(r) {
	                        // Javob kelguncha foydalanuvchi expense_account'ni
	                        // o'zgartirgan yoki cost_center'ni qo'lda tanlagan
	                        // bo'lishi mumkin — shu holatda ustidan yozmaymiz.
	                        if (
	                            r && r.cost_center
	                            && frm.doc.expense_account === requested_expense_account
	                            && !frm.doc.cost_center
	                        ) {
	                            frm.set_value("cost_center", r.cost_center);
	                        }
	                    }
	                );
	            }
	        } else {
	            frm.set_value("expense_account_name", "");
	        }
	    },

    validate_currency: function(frm) {
        return;
    }
});

function has_field(frm, fieldname) {
    return Boolean(frm && frm.fields_dict && frm.fields_dict[fieldname]);
}

function set_derived_value(frm, fieldname, value) {
    const normalizedCurrent = frm.doc[fieldname] == null ? "" : frm.doc[fieldname];
    const normalizedNext = value == null ? "" : value;

    if (normalizedCurrent === normalizedNext) {
        return;
    }

    frm.doc[fieldname] = value;
    frm.refresh_field(fieldname);
}

function getTargetCurrency(frm) {
    return frm._cash_account_to_currency || getDefaultConversionTargetCurrency(frm) || "";
}

function getExchangePair(frm) {
    if (frm.doc.transaction_type === "Конвертация") {
        const inferredTargetCurrency = getTargetCurrency(frm) || getDefaultConversionTargetCurrency(frm);
        return {
            from_currency: frm.doc.cash_account_currency,
            to_currency: inferredTargetCurrency
        };
    }

    if (isPartyMulticurrencyPayment(frm)) {
        return {
            from_currency: frm.doc.cash_account_currency,
            to_currency: frm.doc.party_currency
        };
    }

    return {
        from_currency: "",
        to_currency: ""
    };
}

function getDefaultConversionTargetCurrency(frm) {
    const sourceCurrency = frm.doc.cash_account_currency;
    if (!sourceCurrency) return "";

    if (sourceCurrency === "USD") {
        return "UZS";
    }

    if (sourceCurrency === "UZS") {
        return "USD";
    }

    return "";
}

function isPartyMulticurrencyPayment(frm) {
    return Boolean(
        in_list(["Приход", "Расход"], frm.doc.transaction_type) &&
        in_list(["Customer", "Supplier", "Employee"], frm.doc.party_type) &&
        frm.doc.mode_of_payment &&
        frm.doc.party &&
        frm.doc.cash_account_currency &&
        frm.doc.party_currency &&
        frm.doc.cash_account_currency !== frm.doc.party_currency
    );
}

function isDividendPartyType(partyType) {
    return DIVIDEND_PARTY_TYPES.includes(partyType);
}

function getCurrencyInfoNote(frm, sourceCurrency, targetCurrency) {
    if (frm.doc.transaction_type === "Перемещения") {
        return "Перемещение должно быть между счетами одной валюты.";
    }

    if (frm.doc.transaction_type === "Конвертация") {
        if (sourceCurrency && targetCurrency) {
            return sourceCurrency === targetCurrency
                ? "Для конвертации нужно выбрать счета с разной валютой."
                : "Для конвертации выбраны счета с разной валютой. Проверьте курс и суммы.";
        }
        return "Для конвертации выберите источник, назначение и курс.";
    }

    if (isPartyMulticurrencyPayment(frm)) {
        return "Будет создан мультивалютный Payment Entry: сумма кассы и сумма контрагента будут рассчитаны по курсу.";
    }

    if (frm.doc.transaction_type && frm.doc.party_type && frm.doc.party_currency) {
        return "Для платежа будет использован реальный ledger-счет контрагента из ERPNext.";
    }

    return "";
}

function get_party_name_field(party_type) {
    const name_fields = {
        "Customer": "customer_name",
        "Supplier": "supplier_name",
        "Shareholder": "title",
        "Employee": "employee_name"
    };
    return name_fields[party_type] || null;
}
