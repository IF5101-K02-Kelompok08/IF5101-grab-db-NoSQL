// R5 (PF-09): trips per service area of their pickup location. The area is copied into the
// trip when the document is written, so no join with locations is needed.
function (doc) {
  if (doc.type !== 'service_order') {
    return;
  }
  for (var i = 0; i < doc.trips.length; i++) {
    emit(doc.trips[i].start_service_area_id, null);
  }
}
