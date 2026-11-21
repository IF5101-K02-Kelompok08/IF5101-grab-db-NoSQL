// PF-02 (W2): vehicles that can receive a trip offer, i.e. ACTIVE vehicles of ACTIVE drivers,
// keyed by vehicle type so dispatch can ask for a matching vehicle.
function (doc) {
  if (doc.type !== 'driver_partner' || doc.driver_status !== 'ACTIVE') {
    return;
  }
  for (var i = 0; i < doc.vehicles.length; i++) {
    var v = doc.vehicles[i];
    if (v.vehicle_status === 'ACTIVE') {
      emit([v.vehicle_type, v.vehicle_id], doc._id);
    }
  }
}
