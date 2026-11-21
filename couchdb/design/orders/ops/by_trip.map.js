// PF-03: look up a trip (and the order document that holds it) by trip_id.
function (doc) {
  if (doc.type !== 'service_order') {
    return;
  }
  for (var i = 0; i < doc.trips.length; i++) {
    var trip = doc.trips[i];
    emit(trip.trip_id, {
      trip_status: trip.trip_status,
      started_at: trip.started_at,
      completed_at: trip.completed_at,
      order_status: doc.order_status
    });
  }
}
