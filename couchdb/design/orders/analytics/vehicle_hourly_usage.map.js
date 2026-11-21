// R3 (PF-08): trips per vehicle and start hour (UTC). The value is an array whose element
// at index `hour` is 1; _sum pads shorter arrays with zeros, so the reduced value of one
// vehicle is its trip count for each hour. From those rows the client gets both the trip
// count and the number of distinct vehicles per (hour, vehicle type), without having to
// enumerate one row per (hour, vehicle) pair.
function (doc) {
  if (doc.type !== 'service_order') {
    return;
  }
  for (var i = 0; i < doc.trips.length; i++) {
    var trip = doc.trips[i];
    if ((trip.trip_status !== 'COMPLETED' && trip.trip_status !== 'IN_PROGRESS') || !trip.started_at) {
      continue;
    }
    var hour = parseInt(trip.started_at.substr(11, 2), 10);
    var perHour = [];
    for (var h = 0; h < hour; h++) {
      perHour.push(0);
    }
    perHour.push(1);
    for (var j = 0; j < trip.assignments.length; j++) {
      var a = trip.assignments[j];
      if (a.assignment_status === 'ACCEPTED') {
        emit([a.vehicle_type, a.vehicle_id], perHour);
      }
    }
  }
}
