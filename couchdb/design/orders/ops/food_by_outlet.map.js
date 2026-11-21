// PF-06 / PF-07: food orders per outlet and order status. Used to find outlets that still
// have running orders (W10) and outlets that are referenced by any order (deletion check).
function (doc) {
  if (doc.type === 'service_order' && doc.service_type === 'FOOD') {
    emit([doc.food.merchant_outlet_id, doc.order_status], null);
  }
}
