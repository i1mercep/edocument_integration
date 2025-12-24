# Copyright (c) 2025, Prilk Consulting BV and contributors
# For license information, please see license.txt

# API endpoints for PEPPOL e-document functionality

from typing import Any

import frappe
from frappe import _


@frappe.whitelist()
def get_edocument_integration_settings(profile, company=None):
	# Get EDocument Integration Settings for the given profile
	filters = {"edocument_profile": profile}
	if company:
		filters["company"] = company

	docs = frappe.get_list("EDocument Integration Settings", filters=filters, limit_page_length=1)

	if docs:
		# Get full document to decrypt password field
		settings_doc = frappe.get_doc("EDocument Integration Settings", docs[0].name)
		return {
			"api_key": settings_doc.api_key,
			"api_secret": settings_doc.get_password("api_secret"),  # Decrypt password field
			"base_url": settings_doc.base_url,
			"edocument_integrator": settings_doc.edocument_integrator,
			"company": settings_doc.company,
			"account_id": settings_doc.account_id,
			"company_id": settings_doc.company_id,
		}

	return None


@frappe.whitelist()
def transmit_edocument(edocument_name):
	# Transmit E-document using the configured integrator
	try:
		edocument_doc = frappe.get_doc("EDocument", edocument_name)
		if not edocument_doc.edocument_profile:
			frappe.throw(_("No e-document profile configured for this document."))

		# Check if XML is generated and validated
		if edocument_doc.status != "Validation Successful":
			frappe.throw(_("Document is not validated. Please validate XML first."))

		# Get XML content from attached file using EDocument's method
		xml_bytes = edocument_doc._get_xml_from_attached_files()
		# Ensure XML content is a string (transmit functions expect string)
		xml_content = xml_bytes.decode("utf-8") if isinstance(xml_bytes, bytes) else xml_bytes

		integration_settings = get_edocument_integration_settings(
			edocument_doc.edocument_profile, edocument_doc.company
		)
		if not integration_settings:
			frappe.throw(
				_("No integration settings found for profile: {0}").format(edocument_doc.edocument_profile)
			)

		integrator = integration_settings.get("edocument_integrator")
		if integrator == "B2B Router":
			from .b2brouter_api import B2BRouterAPIClient

			api_key = integration_settings.get("api_key")
			if not api_key:
				frappe.throw(_("API key not configured in EDocument Integration Settings"))
			base_url = integration_settings.get("base_url", "https://api.b2brouter.net/v1/")
			client = B2BRouterAPIClient(api_key, base_url)
			transmission_result = client.transmit_invoice(
				xml_content, invoice_doc=edocument_doc, integration_settings=integration_settings
			)
		elif integrator == "Recommand":
			from .recommand_api import transmit_invoice

			transmission_result = transmit_invoice(
				xml_content, invoice_doc=edocument_doc, integration_settings=integration_settings
			)
		else:
			frappe.throw(_("Unsupported E-document integrator: {0}").format(integrator))

		transmission_id = transmission_result.get("id") or transmission_result.get("document_id")
		tracking_id = transmission_result.get("tracking_id", transmission_result.get("id"))
		status = transmission_result.get("status", "transmitted")
		parts = [
			"E-document Transmission Successful:",
			f"• Transmission ID: {transmission_id}",
			f"• Tracking ID: {tracking_id}",
			f"• Status: {status}",
		]
		if transmission_result.get("estimated_delivery"):
			parts.append(f"• Estimated Delivery: {transmission_result.get('estimated_delivery')}")
		if transmission_result.get("recipient"):
			parts.append(f"• Recipient: {transmission_result.get('recipient')}")
		edocument_doc.add_comment(comment_type="Info", text="\n".join(parts))

		# Set status to Transmission Successful and store reference ID
		frappe.db.set_value(
			"EDocument",
			edocument_name,
			{"status": "Transmission Successful", "error": None, "reference": transmission_id},
			update_modified=False,
		)
		frappe.db.commit()

		return transmission_result
	except Exception as e:
		# Set status to Transmission Failed
		frappe.db.set_value(
			"EDocument",
			edocument_name,
			{"status": "Transmission Failed", "error": str(e)},
			update_modified=False,
		)
		frappe.db.commit()

		frappe.log_error(
			f"E-document transmission failed for document {edocument_name}: {e!s}",
			"E-document Transmission Error",
		)
		frappe.throw(_("Transmission failed: {0}").format(str(e)))


@frappe.whitelist(allow_guest=True)
def webhook(**kwargs):
	# Webhook endpoint to receive incoming PEPPOL documents from providers
	request_log = None
	try:
		r = frappe.request
		if not r:
			frappe.log_error("No request data received", "E-Document Webhook Error")
			return {"status": "error", "message": "No request data received"}, 400

		xml_bytes = r.get_data()
		if not xml_bytes:
			frappe.log_error("No XML content found in request", "E-Document Webhook Error")
			return {"status": "error", "message": "No XML content found in request"}, 400
		if isinstance(xml_bytes, str):
			xml_bytes = xml_bytes.encode("utf-8")

		from frappe.integrations.utils import create_request_log

		request_log = create_request_log(
			kwargs,
			request_description="Incoming Document",
			service_name="EDocument Integration",
			request_headers=r.headers,
		)

		# Create EDocument record and attach XML file (no validation triggered)
		edocument = frappe.get_doc(
			{
				"doctype": "EDocument",
			}
		)
		edocument.insert(ignore_permissions=True)
		# Manual commit required: Webhook must persist EDocument before returning response to external service
		frappe.db.commit()  # nosemgrep

		# Attach XML file
		filename = f"document_{edocument.name}.xml"
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": filename,
				"attached_to_doctype": "EDocument",
				"attached_to_name": edocument.name,
				"content": xml_bytes,
				"is_private": 1,
			}
		)
		file_doc.save(ignore_permissions=True)
		# Manual commit required: Webhook must persist File attachment before returning response to external service
		frappe.db.commit()  # nosemgrep

		result = {
			"edocument": edocument.name,
		}

		request_log.status = "Completed"
		request_log.response = frappe.as_json(result)
		return {"status": "success", "result": result}, 200
	except Exception as e:
		if request_log:
			request_log.status = "Failed"
			request_log.error = frappe.get_traceback()
		frappe.db.rollback()
		frappe.log_error(
			f"E-Document webhook processing failed: {e!s}\n{frappe.get_traceback()}",
			"E-Document Webhook Error",
		)
		# Manual commit required: Webhook must commit error state before returning error response to external service
		frappe.db.commit()  # nosemgrep
		return {"status": "error", "message": "Internal server error"}, 500
	finally:
		if request_log:
			request_log.save(ignore_permissions=True)


@frappe.whitelist()
def poll_incoming_invoices(profile=None, company=None):
	"""
	Poll Recommand inbox for incoming invoices and create EDocument records.

	Args:
		profile: EDocument Profile name (optional, will use integration settings)
		company: Company name (optional, will use integration settings)

	Returns:
		Dictionary with status and list of created EDocument records
	"""
	try:
		# Get integration settings
		if not profile:
			frappe.throw(_("EDocument Profile is required to poll incoming invoices."))

		integration_settings = get_edocument_integration_settings(profile, company)
		if not integration_settings:
			frappe.throw(_("No integration settings found for profile: {0}").format(profile))

		integrator = integration_settings.get("edocument_integrator")
		if integrator != "Recommand":
			frappe.throw(_("Polling incoming invoices is only supported for Recommand integrator."))

		# Import poll_inbox function
		from .recommand_api import poll_inbox

		# Poll inbox for incoming invoices
		poll_result = poll_inbox(integration_settings=integration_settings, company_id=company)

		if not poll_result.get("invoices"):
			return {
				"status": "success",
				"message": poll_result.get("message", "No new invoices found"),
				"edocuments": [],
			}

		# Import profile detection function
		from edocument.edocument.doctype.edocument.edocument import _detect_profile_from_xml

		# Create EDocument records for each invoice
		created_edocuments = []
		for invoice_data in poll_result.get("invoices", []):
			try:
				xml_bytes = invoice_data.get("xml_bytes")
				if not xml_bytes:
					frappe.log_error(
						f"No XML content in invoice data: {invoice_data}", "Poll Incoming Invoices Error"
					)
					continue

				# Ensure xml_bytes is bytes
				if isinstance(xml_bytes, str):
					xml_bytes = xml_bytes.encode("utf-8")

				# Detect profile from XML if not provided
				detected_profile = _detect_profile_from_xml(xml_bytes)
				edocument_profile = detected_profile or profile

				# Create EDocument record first (to get the name)
				edocument = frappe.get_doc(
					{
						"doctype": "EDocument",
						"edocument_profile": edocument_profile,
						"company": company or integration_settings.get("company"),
					}
				)
				edocument.insert(ignore_permissions=True)
				frappe.db.commit()

				# Attach XML file using save_file utility
				metadata = invoice_data.get("metadata", {})
				document_id = invoice_data.get("document_id", metadata.get("id", "unknown"))
				filename = f"incoming_{document_id}_{edocument.name}.xml"

				from frappe.utils.file_manager import save_file

				file_doc = save_file(
					fname=filename,
					content=xml_bytes,
					dt="EDocument",
					dn=edocument.name,
					df="xml_file",  # Field name to attach to
					is_private=1,
				)

				# Set xml_file field on EDocument to the file URL
				edocument.db_set("xml_file", file_doc.file_url, update_modified=False)
				frappe.db.commit()

				# Add comment with metadata
				comment_parts = ["Incoming invoice received from Recommand"]
				if document_id:
					comment_parts.append(f"• Document ID: {document_id}")
				if metadata.get("sender"):
					comment_parts.append(f"• Sender: {metadata.get('sender')}")
				if metadata.get("received_at"):
					comment_parts.append(f"• Received: {metadata.get('received_at')}")

				edocument.add_comment(comment_type="Info", text="\n".join(comment_parts))

				created_edocuments.append(
					{
						"edocument": edocument.name,
						"document_id": document_id,
						"profile": edocument_profile,
					}
				)

			except Exception as e:
				frappe.log_error(
					f"Failed to create EDocument for incoming invoice: {e!s}\nTraceback: {frappe.get_traceback()}",
					"Poll Incoming Invoices Error",
				)
				continue

		return {
			"status": "success",
			"message": f"Processed {len(created_edocuments)} invoice(s)",
			"edocuments": created_edocuments,
		}

	except Exception as e:
		frappe.log_error(
			f"Poll incoming invoices failed: {e!s}\nTraceback: {frappe.get_traceback()}",
			"Poll Incoming Invoices Error",
		)
		frappe.throw(_("Failed to poll incoming invoices: {0}").format(str(e)))
