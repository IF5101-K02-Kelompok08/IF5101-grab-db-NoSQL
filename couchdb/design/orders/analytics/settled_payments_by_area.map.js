// R6 (PF-10): settled (PAID or REFUNDED) payments per service area of the order's first trip.
// Value: [settled payments, PAID amount in cents, REFUNDED amount in cents, refunded payments].
// Amounts are summed in integer cents so the totals are exact.
function (doc) {
  if (doc.type !== 'service_order' || !doc.payment || doc.trips.length === 0) {
    return;
  }
  var p = doc.payment;
  if (p.payment_status !== 'PAID' && p.payment_status !== 'REFUNDED') {
    return;
  }
  var first = doc.trips[0];
  for (var i = 1; i < doc.trips.length; i++) {
    if (doc.trips[i].trip_id < first.trip_id) {
      first = doc.trips[i];
    }
  }
  var cents = Math.round(p.amount * 100);
  if (p.payment_status === 'PAID') {
    emit(first.start_service_area_id, [1, cents, 0, 0]);
  } else {
    emit(first.start_service_area_id, [1, 0, cents, 1]);
  }
}
