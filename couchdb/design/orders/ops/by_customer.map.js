// PF-01: a customer's order history, newest first when queried with descending=true.
function (doc) {
  if (doc.type !== 'service_order') {
    return;
  }
  emit([doc.customer_id, doc.created_at], {
    order_status: doc.order_status,
    service_type: doc.service_type,
    total_amount: doc.total_amount
  });
}
