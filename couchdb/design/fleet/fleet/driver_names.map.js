// R1 / R2 (PF-08): driver names, joined with the per-driver results on the client.
function (doc) {
  if (doc.type === 'driver_partner') {
    emit(doc._id, doc.full_name);
  }
}
