// Section 3.1 validation: Part 2 rows held by the geo documents.
function (doc) {
  if (doc.type === 'service_area') {
    emit('service_areas', 1);
  } else if (doc.type === 'location') {
    emit('locations', 1);
  } else if (doc.type === 'merchant_outlet') {
    emit('merchant_outlets', 1);
  }
}
