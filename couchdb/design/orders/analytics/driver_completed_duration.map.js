// R2 (PF-08): duration in seconds of each COMPLETED trip, keyed by the driver whose
// assignment was ACCEPTED. _stats gives the trip count and total duration per driver.
function (doc) {
  if (doc.type !== 'service_order') {
    return;
  }
  for (var i = 0; i < doc.trips.length; i++) {
    var trip = doc.trips[i];
    if (trip.trip_status !== 'COMPLETED' || !trip.started_at || !trip.completed_at) {
      continue;
    }
    var seconds = (Date.parse(trip.completed_at) - Date.parse(trip.started_at)) / 1000;
    for (var j = 0; j < trip.assignments.length; j++) {
      var a = trip.assignments[j];
      if (a.assignment_status === 'ACCEPTED') {
        emit(a.driver_partner_id, seconds);
      }
    }
  }
}
