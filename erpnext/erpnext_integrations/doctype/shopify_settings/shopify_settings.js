// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.provide("erpnext_integrations.shopify_settings");
frappe.ui.form.on("Shopify Settings", {
	onload: function(frm) {
		frappe.call({
			method: "erpnext.erpnext_integrations.doctype.shopify_settings.shopify_settings.get_series",
			callback: function (r) {
				$.each(r.message, function (key, value) {
					set_field_options(key, value);
				});
			}
		});
		erpnext_integrations.shopify_settings.setup_queries(frm);
	},

	refresh: function(frm) {
		if (!frm.is_new() && frm.doc.enable_shopify === 1) {
			frm.toggle_reqd("price_list", true);
			frm.toggle_reqd("warehouse", true);
			frm.toggle_reqd("taxes", true);
			frm.toggle_reqd("company", true);
			frm.toggle_reqd("cost_center", true);
			frm.toggle_reqd("cash_bank_account", true);
			frm.toggle_reqd("sales_order_series", true);
			frm.toggle_reqd("customer_group", true);
			frm.toggle_reqd("shared_secret", true);

			frm.toggle_reqd("sales_invoice_series", frm.doc.sync_sales_invoice);
			frm.toggle_reqd("delivery_note_series", frm.doc.sync_delivery_note);
		}

		frm.add_custom_button(__("Products"), function () {
			frappe.call({
				method: "erpnext.erpnext_integrations.doctype.shopify_settings.sync_product.sync_items_from_shopify",
				freeze: true,
				callback: function (r) {
					if (r.message) {
						frappe.msgprint(__("Product sync has been queued. This may take a few minutes."));
					}
				}
			})
		}, __("Sync"))

		frm.add_custom_button(__("Orders"), function () {
			frappe.call({
				method: "erpnext.erpnext_integrations.doctype.shopify_settings.sync_order.sync_orders_from_shopify",
				freeze: true,
				callback: function (r) {
					if (r.message) {
						frappe.msgprint(__("Order sync has been queued. This may take a few minutes."));
					}
				}
			})
		}, __("Sync"))

		frm.add_custom_button(__("Payouts"), function() {
			frappe.call({
				method: "erpnext.erpnext_integrations.doctype.shopify_settings.sync_payout.sync_payouts_from_shopify",
				freeze: true,
				callback: function(r) {
					if (r.message) {
						frappe.msgprint(__("Payout sync has been queued. This may take a few minutes."));
					}
				}
			})
		}, __("Sync"))
	},

	app_type: function(frm) {
		frm.toggle_reqd("api_key", (frm.doc.app_type == "Private"));
		frm.toggle_reqd("password", (frm.doc.app_type == "Private"));
	}
})

$.extend(erpnext_integrations.shopify_settings, {
	setup_queries: function(frm) {
		frm.fields_dict["warehouse"].get_query = function(doc) {
			return {
				filters:{
					"company": doc.company,
					"is_group": "No"
				}
			}
		}

		frm.fields_dict["taxes"].grid.get_field("tax_account").get_query = function(doc){
			return {
				"query": "erpnext.controllers.queries.tax_account_query",
				"filters": {
					"account_type": ["Tax", "Chargeable", "Expense Account"],
					"company": doc.company
				}
			}
		}

		frm.fields_dict["cash_bank_account"].get_query = function(doc) {
			return {
				filters: [
					["Account", "account_type", "in", ["Cash", "Bank"]],
					["Account", "root_type", "=", "Asset"],
					["Account", "is_group", "=",0],
					["Account", "company", "=", doc.company]
				]
			}
		}

		frm.fields_dict["cost_center"].get_query = function(doc) {
			return {
				filters:{
					"company": doc.company,
					"is_group": "No"
				}
			}
		}

		frm.fields_dict["price_list"].get_query = function() {
			return {
				filters:{
					"selling": 1
				}
			}
		}
	}
})
