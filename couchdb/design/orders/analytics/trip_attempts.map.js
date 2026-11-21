// R4 (PF-02): trips that were offered to more than one driver and were accepted at least
// once, keyed by [number of attempts, started_at, trip_id]. Querying with descending=true
// and endkey=[3] returns the Part 2 result (more than two attempts), largest first.
function (doc) {
  if (doc.type !== 'service_order') {
    return;
  }
  for (var i = 0; i < doc.trips.length; i++) {
    var trip = doc.trips[i];
    var attempts = trip.assignments.length;
    if (attempts < 2) {
      continue;
    }
    var accepted = false;
    for (var j = 0; j < attempts; j++) {
      if (trip.assignments[j].assignment_status === 'ACCEPTED') {
        accepted = true;
      }
    }
    if (accepted) {
      emit([attempts, trip.started_at, trip.trip_id], {
        trip_status: trip.trip_status,
        completed_at: trip.completed_at
      });
    }
  }
}
