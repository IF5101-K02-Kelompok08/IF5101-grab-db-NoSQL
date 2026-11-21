// PF-06 / R9 (PF-09): outlets per service area and status. With reduce it counts the outlet
// supply of each area; with reduce=false&include_docs=true it lists the outlets of an area.
function (doc) {
  if (doc.type === 'merchant_outlet') {
    emit([doc.location.service_area_id, doc.outlet_status], null);
  }
}
