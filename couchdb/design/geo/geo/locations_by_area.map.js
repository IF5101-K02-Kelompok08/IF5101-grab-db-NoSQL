// PF-06: locations per service area. An area must keep at least one location (total
// participation in Part 2); the application checks this count before removing a location.
function (doc) {
  if (doc.type === 'location') {
    emit(doc.service_area_id, null);
  }
}
