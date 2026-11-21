// R8 (PF-10): COMPLETED food orders per outlet. Value: [orders, PAID amount in cents,
// amount in cents of every payment, refunded payments].
function (doc) {
  if (doc.type !== 'service_order' || doc.service_type !== 'FOOD'
      || doc.order_status !== 'COMPLETED' || !doc.payment) {
    return;
  }
  var p = doc.payment;
  var cents = Math.round(p.amount * 100);
  emit(doc.food.merchant_outlet_id, [
    1,
    p.payment_status === 'PAID' ? cents : 0,
    cents,
    p.payment_status === 'REFUNDED' ? 1 : 0
  ]);
}
