// PF-02: find the driver and type of a vehicle by vehicle_id.
function (doc) {
  if (doc.type !== 'driver_partner') {
    return;
  }
  for (var i = 0; i < doc.vehicles.length; i++) {
    var v = doc.vehicles[i];
    emit(v.vehicle_id, {
      driver_partner_id: doc._id,
      vehicle_type: v.vehicle_type,
      vehicle_status: v.vehicle_status,
      driver_status: doc.driver_status
    });
  }
}
