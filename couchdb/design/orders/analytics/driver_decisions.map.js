// R1 (PF-08): per driver, the number of ACCEPTED, REJECTED and EXPIRED assignments.
// _sum adds the arrays element by element, so one view row per driver holds all three counts.
function (doc) {
  if (doc.type !== 'service_order') {
    return;
  }
  for (var i = 0; i < doc.trips.length; i++) {
    var assignments = doc.trips[i].assignments;
    for (var j = 0; j < assignments.length; j++) {
      var a = assignments[j];
      if (a.assignment_status === 'ACCEPTED') {
        emit(a.driver_partner_id, [1, 0, 0]);
      } else if (a.assignment_status === 'REJECTED') {
        emit(a.driver_partner_id, [0, 1, 0]);
      } else if (a.assignment_status === 'EXPIRED') {
        emit(a.driver_partner_id, [0, 0, 1]);
      }
    }
  }
}
