// Copyright (c) 2025, Prilk Consulting BV and contributors
// For license information, please see license.txt

frappe.ui.form.on("EDocument Integration Settings", {
	refresh(frm) {
		// Only show button if document is saved and has required fields
		if (!frm.is_new() && frm.doc.edocument_profile && frm.doc.edocument_integrator) {
			frm.add_custom_button(__('Poll Incoming Documents'), function() {
				frm.call({
					method: 'poll_incoming_documents',
					doc: frm.doc,
					freeze: true,
					freeze_message: __('Polling for incoming documents...'),
					callback: function(r) {
						if (r.message && r.message.status === 'success') {
							const processed = r.message.processed || 0;
							if (processed > 0) {
								frappe.show_alert({
									message: __('Successfully processed {0} document(s)', [processed]),
									indicator: 'green'
								});
							} else {
								frappe.show_alert({
									message: __('No new documents found'),
									indicator: 'blue'
								});
							}
							// Refresh to show new EDocument documents
							frappe.set_route('List', 'EDocument');
						} else {
							frappe.show_alert({
								message: __('Error: {0}', [r.message.message || 'Unknown error']),
								indicator: 'red'
							});
						}
					}
				});
			}, __('Actions'));
		}
	}
});

