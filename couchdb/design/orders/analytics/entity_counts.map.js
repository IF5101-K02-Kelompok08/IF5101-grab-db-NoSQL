// Section 3.1 validation: how many rows of each Part 2 table the order documents hold.
function (doc) {
  if (doc.type !== 'service_order') {
    return;
  }
  emit('service_orders', 1);
  emit(doc.service_type.toLowerCase() + '_orders', 1);
  if (doc.payment) {
    emit('payments', 1);
  }
  var assignments = 0;
  for (var i = 0; i < doc.trips.length; i++) {
    assignments += doc.trips[i].assignments.length;
  }
  emit('trips', doc.trips.length);
  emit('driver_assignments', assignments);
}
