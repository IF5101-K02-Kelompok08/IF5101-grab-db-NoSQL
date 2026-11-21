// PF-05 / PF-06: find a service area by city and name (unique together in Part 2).
function (doc) {
  if (doc.type === 'service_area') {
    emit([doc.city, doc.area_name], doc.area_status);
  }
}
