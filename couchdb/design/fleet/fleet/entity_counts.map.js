// Section 3.1 validation: Part 2 rows held by the fleet documents.
function (doc) {
  if (doc.type === 'driver_partner') {
    emit('driver_partners', 1);
    emit('vehicles', doc.vehicles.length);
  }
}
