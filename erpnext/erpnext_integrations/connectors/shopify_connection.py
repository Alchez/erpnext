import json

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
from erpnext.erpnext_integrations.doctype.shopify_log.shopify_log import dump_request_data, make_shopify_log
from erpnext.erpnext_integrations.doctype.shopify_settings.sync_customer import create_customer
from erpnext.erpnext_integrations.doctype.shopify_settings.sync_product import make_item
from erpnext.erpnext_integrations.utils import validate_webhooks_request
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note, make_sales_invoice
from frappe import _
from frappe.utils import cint, cstr, flt, getdate, nowdate


@frappe.whitelist(allow_guest=True)
@validate_webhooks_request("Shopify Settings", 'X-Shopify-Hmac-Sha256', secret_key='shared_secret')
def store_request_data(order=None, event=None):
	if frappe.request:
		order = json.loads(frappe.request.data)
		event = frappe.request.headers.get('X-Shopify-Topic')

	dump_request_data(order, event)


def sync_sales_order(order, request_id=None):
	"""
	Create the following from a Shopify order:

		- Sales Order
		- Sales Invoice and Payment Entry (if paid)
		- Delivery Note (if fulfilled)

	Args:

		order (dict): The Shopify order data
		request_id (str, optional): The ID of the existing Shopify Log document
			for this request. Defaults to None.
	"""

	frappe.set_user('Administrator')
	frappe.flags.request_id = request_id
	so = create_shopify_order(order, request_id)
	if so:
		create_shopify_invoice(order, so, request_id)
		create_shopify_delivery(order, so, request_id)


def create_shopify_order(order, request_id=None):
	shopify_settings = frappe.get_single("Shopify Settings")
	frappe.flags.request_id = request_id

	existing_so = frappe.db.get_value("Sales Order",
		filters={
			"docstatus": ["<", 2],
			"shopify_order_id": cstr(order['id'])
		})

	if existing_so:
		return frappe.get_doc("Sales Order", existing_so)

	try:
		validate_customer(order, shopify_settings)
		validate_item(order, shopify_settings)
		so = create_sales_order(order, shopify_settings)
	except Exception as e:
		make_shopify_log(status="Error", response_data=order, exception=e)
	else:
		make_shopify_log(status="Success", response_data=order)
		return so


def create_shopify_invoice(order, so, request_id=None):
	shopify_settings = frappe.get_single("Shopify Settings")
	frappe.flags.request_id = request_id

	if not order.get("financial_status") in ["paid", "refunded"]:
		return

	try:
		si = create_sales_invoice(order, shopify_settings, so)
		if si and order.get("financial_status") == "refunded":
			# TODO: use correct posting date for returns
			return_invoice = make_sales_return(si)
			return_invoice.save()
			return_invoice.submit()
	except Exception as e:
		make_shopify_log(status="Error", response_data=order, exception=e)
	else:
		return si


def create_shopify_delivery(order, so, request_id=None):
	shopify_settings = frappe.get_single("Shopify Settings")
	frappe.flags.request_id = request_id

	if not order.get("fulfillments"):
		return

	try:
		delivery_notes = create_delivery_note(order, shopify_settings, so)
	except Exception as e:
		make_shopify_log(status="Error", response_data=order, exception=e)
		return
	else:
		return delivery_notes


def prepare_sales_invoice(order, request_id=None):
	frappe.set_user('Administrator')
	shopify_settings = frappe.get_doc("Shopify Settings")
	frappe.flags.request_id = request_id

	try:
		sales_order = get_sales_order(cstr(order['id']))
		if sales_order:
			create_sales_invoice(order, shopify_settings, sales_order)
			make_shopify_log(status="Success", response_data=order)
	except Exception as e:
		make_shopify_log(status="Error", response_data=order, exception=e, rollback=True)


def prepare_delivery_note(order, request_id=None):
	frappe.set_user('Administrator')
	shopify_settings = frappe.get_doc("Shopify Settings")
	frappe.flags.request_id = request_id

	try:
		sales_order = get_sales_order(cstr(order['id']))
		if sales_order:
			create_delivery_note(order, shopify_settings, sales_order)
		make_shopify_log(status="Success", response_data=order)
	except Exception as e:
		make_shopify_log(status="Error", response_data=order, exception=e, rollback=True)


def cancel_shopify_order(order, request_id=None):
	frappe.set_user('Administrator')
	frappe.flags.request_id = request_id

	doctypes = ["Delivery Note", "Sales Invoice", "Sales Order"]
	for doctype in doctypes:
		name = frappe.db.get_value(doctype, {"docstatus": 1, "shopify_order_id": cstr(order['id'])})
		if name:
			try:
				frappe.get_doc(doctype, name).cancel()
			except Exception as e:
				make_shopify_log(status="Error", response_data=order,
					exception=e, rollback=True)


def get_sales_order(shopify_order_id):
	sales_order = frappe.db.get_value("Sales Order", filters={"shopify_order_id": shopify_order_id})
	if sales_order:
		so = frappe.get_doc("Sales Order", sales_order)
		return so


def validate_customer(order, shopify_settings):
	customer_id = order.get("customer", {}).get("id")
	if customer_id:
		if not frappe.db.get_value("Customer", {"shopify_customer_id": customer_id}, "name"):
			create_customer(order.get("customer"), shopify_settings)


def validate_item(order, shopify_settings):
	for item in order.get("line_items"):
		product_id = item.get("product_id") or item.get("id")
		if product_id and not frappe.db.exists("Item", {"shopify_product_id": product_id}):
			make_item(shopify_settings.warehouse, item)


def create_sales_order(shopify_order, shopify_settings, company=None):
	customer = frappe.db.get_value("Customer", {"shopify_customer_id": shopify_order.get("customer", {}).get("id")}, "name")
	so = frappe.db.get_value("Sales Order", {"docstatus": ["<", 2], "shopify_order_id": shopify_order.get("id")}, "name")

	if not so:
		items = get_order_items(shopify_order.get("line_items"), shopify_settings)

		so = frappe.get_doc({
			"doctype": "Sales Order",
			"naming_series": shopify_settings.sales_order_series or "SO-Shopify-",
			"shopify_order_id": shopify_order.get("id"),
			"customer": customer or shopify_settings.default_customer,
			"delivery_date": nowdate(),
			"company": shopify_settings.company,
			"selling_price_list": shopify_settings.price_list,
			"ignore_pricing_rule": 1,
			"items": items,
			"taxes": get_order_taxes(shopify_order, shopify_settings),
			"apply_discount_on": "Grand Total",
			"discount_amount": get_discounted_amount(shopify_order),
		})

		if company:
			so.update({
				"company": company,
				"status": "Draft"
			})
		so.flags.ignore_mandatory = True
		so.save(ignore_permissions=True)
		so.submit()

	else:
		so = frappe.get_doc("Sales Order", so)

	frappe.db.commit()
	return so


def create_sales_invoice(shopify_order, shopify_settings, so):
	if not frappe.db.get_value("Sales Invoice", {"shopify_order_id": shopify_order.get("id")}, "name")\
		and so.docstatus == 1 and not so.per_billed and cint(shopify_settings.sync_sales_invoice):

		si = make_sales_invoice(so.name, ignore_permissions=True)
		si.shopify_order_id = shopify_order.get("id")
		si.shopify_order_number = shopify_order.get("name")
		si.set_posting_time = 1
		si.posting_date = getdate(shopify_order.get('created_at'))
		si.naming_series = shopify_settings.sales_invoice_series or "SI-Shopify-"
		si.flags.ignore_mandatory = True
		set_cost_center(si.items, shopify_settings.cost_center)
		si.insert(ignore_mandatory=True)
		si.submit()
		make_payment_entry_against_sales_invoice(si, shopify_settings)
		frappe.db.commit()
		return si.name


def set_cost_center(items, cost_center):
	for item in items:
		item.cost_center = cost_center


def make_payment_entry_against_sales_invoice(doc, shopify_settings):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
	payment_entry = get_payment_entry(doc.doctype, doc.name, bank_account=shopify_settings.cash_bank_account)
	payment_entry.flags.ignore_mandatory = True
	payment_entry.reference_no = doc.name
	payment_entry.reference_date = nowdate()
	payment_entry.insert(ignore_permissions=True)
	payment_entry.submit()


def create_delivery_note(shopify_order, shopify_settings, so):
	if not cint(shopify_settings.sync_delivery_note):
		return

	delivery_notes = []
	for fulfillment in shopify_order.get("fulfillments"):
		if not frappe.db.get_value("Delivery Note", {"shopify_fulfillment_id": fulfillment.get("id")}, "name")\
			and so.docstatus == 1:

			dn = make_delivery_note(so.name)
			dn.shopify_order_id = fulfillment.get("order_id")
			dn.shopify_order_number = shopify_order.get("name")
			dn.set_posting_time = 1
			dn.posting_date = getdate(fulfillment.get("created_at"))
			dn.shopify_fulfillment_id = fulfillment.get("id")
			dn.naming_series = shopify_settings.delivery_note_series or "DN-Shopify-"
			dn.items = get_fulfillment_items(dn.items, fulfillment.get("line_items"))
			dn.flags.ignore_mandatory = True
			dn.save()
			dn.submit()
			frappe.db.commit()
			delivery_notes.append(dn.name)

	return delivery_notes


def get_fulfillment_items(dn_items, fulfillment_items):
	# TODO: figure out a better way to add items without
	# setting valuation rate to zero
	return [dn_item.update({"qty": item.get("quantity"), "allow_zero_valuation_rate": 1})
		for item in fulfillment_items for dn_item in dn_items
		if get_item_code(item) == dn_item.item_code]


def get_discounted_amount(order):
	return sum(flt(discount.get("amount")) for discount in order.get("discount_codes"))


def get_order_items(order_items, shopify_settings):
	items = []
	for shopify_item in order_items:
		item_code = get_item_code(shopify_item)
		items.append({
			"item_code": item_code,
			"item_name": shopify_item.get("name"),
			"rate": shopify_item.get("price"),
			"delivery_date": nowdate(),
			"qty": shopify_item.get("quantity"),
			"stock_uom": shopify_item.get("uom") or _("Nos"),
			"warehouse": shopify_settings.warehouse
		})
	return items


def get_item_code(shopify_item):
	item_code = frappe.db.get_value("Item", {"shopify_variant_id": shopify_item.get("variant_id")}, "item_code")
	if not item_code:
		item_code = frappe.db.get_value("Item",
			{"shopify_product_id": shopify_item.get("product_id") or shopify_item.get("id")}, "item_code")
	if not item_code:
		item_code = frappe.db.get_value("Item", {"item_name": shopify_item.get("title")}, "item_code")

	return item_code


def get_order_taxes(shopify_order, shopify_settings):
	taxes = []
	for tax in shopify_order.get("tax_lines"):
		taxes.append({
			"charge_type": _("On Net Total"),
			"account_head": get_tax_account_head(tax),
			"description": "{0} - {1}%".format(tax.get("title"), tax.get("rate") * 100.0),
			"rate": tax.get("rate") * 100.00,
			"included_in_print_rate": 1 if shopify_order.get("taxes_included") else 0,
			"cost_center": shopify_settings.cost_center
		})

	taxes = update_taxes_with_shipping_lines(taxes, shopify_order.get("shipping_lines"), shopify_settings)

	return taxes


def update_taxes_with_shipping_lines(taxes, shipping_lines, shopify_settings):
	"""Shipping lines represents the shipping details,
		each such shipping detail consists of a list of tax_lines"""
	for shipping_charge in shipping_lines:
		shipping_tax_lines = [shipping_charge] or shipping_charge.get('tax_lines')
		for tax in shipping_tax_lines:
			taxes.append({
				"charge_type": _("Actual"),
				"account_head": get_tax_account_head(tax),
				"description": tax["title"],
				"tax_amount": tax["price"],
				"cost_center": shopify_settings.cost_center
			})

	return taxes


def get_tax_account_head(tax):
	tax_title = tax.get("title").encode("utf-8")

	tax_account = frappe.db.get_value("Shopify Tax Account",
		{"parent": "Shopify Settings", "shopify_tax": tax_title}, "tax_account")

	if not tax_account:
		frappe.throw(_("Tax Account not specified for Shopify Tax {0}".format(tax.get("title"))))

	return tax_account
