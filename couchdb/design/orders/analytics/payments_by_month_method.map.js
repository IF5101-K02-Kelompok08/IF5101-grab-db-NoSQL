// R7 (PF-10): payments per order month (UTC) and payment method. Value: [payments, PAID,
// REFUNDED, FAILED, PENDING, PAID amount in cents, seconds from order to paid_at,
// payments with paid_at]. The last two give the average settlement time.
function (doc) {
  if (doc.type !== 'service_order' || !doc.payment) {
    return;
  }
  var p = doc.payment;
  var s = p.payment_status;
  var settleSeconds = 0;
  var settled = 0;
  if (p.paid_at) {
    settleSeconds = (Date.parse(p.paid_at) - Date.parse(doc.created_at)) / 1000;
    settled = 1;
  }
  emit([doc.created_at.substr(0, 7), p.payment_method], [
    1,
    s === 'PAID' ? 1 : 0,
    s === 'REFUNDED' ? 1 : 0,
    s === 'FAILED' ? 1 : 0,
    s === 'PENDING' ? 1 : 0,
    s === 'PAID' ? Math.round(p.amount * 100) : 0,
    settleSeconds,
    settled
  ]);
}
