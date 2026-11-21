// R9 (PF-09): food orders per creation day (UTC) and the service area of the outlet.
// Querying a key range of days with group_level=2 counts the demand for any period.
function (doc) {
  if (doc.type === 'service_order' && doc.service_type === 'FOOD') {
    emit([doc.created_at.substr(0, 10), doc.food.merchant_service_area_id], null);
  }
}
