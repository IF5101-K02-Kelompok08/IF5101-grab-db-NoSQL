// validate_doc_update for grab_orders: one document per service order (order aggregate).
// Ports the Part 2 CHECK constraints and triggers that concern an order, its subtype,
// payment, trips and driver assignments. CouchDB runs this on every write, including
// _bulk_docs during loading, and rejects the write by throwing {forbidden: ...}.
// Rules that need other documents (foreign keys, global uniqueness) cannot be checked
// here and are enforced by the application layer (see docs/couchdb_design.md).
function (newDoc, oldDoc, userCtx, secObj) {
  if (newDoc._id.indexOf('_design/') === 0) {
    return;
  }

  function fail(message) {
    throw ({forbidden: message});
  }

  var roles = (userCtx && userCtx.roles) || [];
  if (newDoc._deleted) {
    // PF-07: orders carry payment history, so the app may not delete them.
    if (roles.indexOf('_admin') === -1) {
      fail('Service orders are retained and cannot be deleted');
    }
    return;
  }

  var ID = {
    order: /^ORD[0-9]{7}$/, customer: /^CUS[0-9]{7}$/, payment: /^PAY[0-9]{7}$/,
    trip: /^TRP[0-9]{7}$/, assignment: /^ASN[0-9]{7}$/, vehicle: /^VEH[0-9]{7}$/,
    driver: /^DRV[0-9]{7}$/, location: /^LOC[0-9]{7}$/, outlet: /^OUT[0-9]{7}$/,
    area: /^SAR[0-9]{7}$/
  };
  var TIMESTAMP = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/;
  var PHONE = /^\+?[0-9]{7,15}$/;
  var ORDER_STATUS = ['PENDING', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED', 'FAILED'];
  var PREPARATION_STATUS = ['PENDING', 'PREPARING', 'READY'];
  var RIDE_TYPE = ['GrabCar', 'GrabCarPlus', 'GrabBike', 'GrabElectric', 'GrabTaxi'];
  var PAYMENT_METHOD = ['CASH', 'E_WALLET', 'CARD', 'PAYLATER'];
  var PAYMENT_STATUS = ['PENDING', 'PAID', 'FAILED', 'REFUNDED'];
  var TRIP_STATUS = ['SEARCHING', 'ALLOCATED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'];
  var ASSIGNMENT_STATUS = ['PENDING', 'ACCEPTED', 'REJECTED', 'EXPIRED', 'CANCELLED'];

  // Valid transitions, as in the Part 2 status triggers. Unchanged values are allowed.
  var ORDER_FLOW = {
    PENDING: ['CONFIRMED', 'FAILED'], CONFIRMED: ['IN_PROGRESS', 'CANCELLED'],
    IN_PROGRESS: ['COMPLETED', 'CANCELLED'], CANCELLED: ['FAILED']
  };
  var PREPARATION_FLOW = {PENDING: ['PREPARING'], PREPARING: ['READY']};
  var PAYMENT_FLOW = {PENDING: ['PAID', 'FAILED'], FAILED: ['PENDING'], PAID: ['REFUNDED']};
  var TRIP_FLOW = {
    SEARCHING: ['ALLOCATED', 'CANCELLED'], ALLOCATED: ['IN_PROGRESS', 'CANCELLED'],
    IN_PROGRESS: ['COMPLETED', 'CANCELLED']
  };

  function isArray(value) {
    return Object.prototype.toString.call(value) === '[object Array]';
  }
  function isObject(value) {
    return value !== null && typeof value === 'object' && !isArray(value);
  }
  function isNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }
  function missing(value) {
    return value === undefined || value === null;
  }
  function checkId(obj, field, kind, label) {
    var value = obj[field];
    if (typeof value !== 'string' || !ID[kind].test(value)) {
      fail(label + '.' + field + ' must match ' + ID[kind].source);
    }
  }
  function checkTimestamp(obj, field, label, required) {
    var value = obj[field];
    if (missing(value)) {
      if (required) {
        fail(label + '.' + field + ' is required');
      }
      return;
    }
    if (typeof value !== 'string' || !TIMESTAMP.test(value)) {
      fail(label + '.' + field + ' must be a UTC timestamp like 2026-05-01T00:00:00Z');
    }
  }
  function checkText(obj, field, label, maxLength, required) {
    var value = obj[field];
    if (missing(value) || value === '') {
      if (required) {
        fail(label + '.' + field + ' is required');
      }
      return;
    }
    if (typeof value !== 'string' || value.length > maxLength) {
      fail(label + '.' + field + ' must be text of at most ' + maxLength + ' characters');
    }
  }
  function checkEnum(obj, field, values, label) {
    if (values.indexOf(obj[field]) === -1) {
      fail(label + '.' + field + ' must be one of ' + values.join(', '));
    }
  }
  function checkTransition(label, field, from, to, flow) {
    if (from === to) {
      return;
    }
    var allowed = flow[from];
    if (!allowed || allowed.indexOf(to) === -1) {
      fail('Invalid ' + label + ' ' + field + ' transition: ' + from + ' -> ' + to);
    }
  }
  function indexBy(items, field) {
    var index = {};
    for (var i = 0; i < items.length; i++) {
      index[items[i][field]] = items[i];
    }
    return index;
  }

  // service_orders
  if (newDoc.type !== 'service_order') {
    fail('type must be service_order');
  }
  checkId(newDoc, 'order_id', 'order', 'order');
  if (newDoc.order_id !== newDoc._id) {
    fail('order.order_id must equal _id');
  }
  checkId(newDoc, 'customer_id', 'customer', 'order');
  checkTimestamp(newDoc, 'created_at', 'order', true);
  checkEnum(newDoc, 'order_status', ORDER_STATUS, 'order');
  if (!isNumber(newDoc.total_amount) || newDoc.total_amount < 0) {
    fail('order.total_amount must be a number >= 0');
  }

  // Total and disjoint specialization: exactly one subtype object, matching service_type.
  var SUBTYPES = {FOOD: 'food', MOBILITY: 'mobility', DELIVERY: 'delivery'};
  checkEnum(newDoc, 'service_type', ['FOOD', 'MOBILITY', 'DELIVERY'], 'order');
  var subtypeCount = 0;
  for (var kind in SUBTYPES) {
    if (!missing(newDoc[SUBTYPES[kind]])) {
      subtypeCount++;
    }
  }
  var subtype = newDoc[SUBTYPES[newDoc.service_type]];
  if (subtypeCount !== 1 || !isObject(subtype)) {
    fail('Order ' + newDoc._id + ' must have exactly one subtype matching service_type');
  }
  if (newDoc.service_type === 'FOOD') {
    checkEnum(subtype, 'preparation_status', PREPARATION_STATUS, 'food');
    checkText(subtype, 'delivery_notes', 'food', 100, false);
    checkId(subtype, 'merchant_outlet_id', 'outlet', 'food');
    checkId(subtype, 'merchant_service_area_id', 'area', 'food');
  } else if (newDoc.service_type === 'MOBILITY') {
    checkEnum(subtype, 'ride_type', RIDE_TYPE, 'mobility');
    if (!isNumber(subtype.passenger_count) || subtype.passenger_count < 1
        || subtype.passenger_count % 1 !== 0) {
      fail('mobility.passenger_count must be an integer >= 1');
    }
    checkText(subtype, 'pickup_notes', 'mobility', 100, false);
  } else {
    checkText(subtype, 'package_description', 'delivery', 100, true);
    if (!isNumber(subtype.package_weight) || Math.abs(subtype.package_weight) >= 1000) {
      fail('delivery.package_weight must be a number below 1000 (NUMERIC(5,2))');
    }
    checkText(subtype, 'recipient_name', 'delivery', 100, true);
    if (typeof subtype.recipient_contact !== 'string' || !PHONE.test(subtype.recipient_contact)) {
      fail('delivery.recipient_contact must be a valid phone number');
    }
  }

  // payments (one-to-one, embedded)
  var payment = newDoc.payment;
  if (!missing(payment)) {
    if (!isObject(payment)) {
      fail('payment must be an object');
    }
    checkId(payment, 'payment_id', 'payment', 'payment');
    checkEnum(payment, 'payment_method', PAYMENT_METHOD, 'payment');
    checkEnum(payment, 'payment_status', PAYMENT_STATUS, 'payment');
    if (!isNumber(payment.amount) || payment.amount <= 0) {
      fail('payment.amount must be a number > 0');
    }
    checkTimestamp(payment, 'paid_at', 'payment', false);
    var settled = payment.payment_status === 'PAID' || payment.payment_status === 'REFUNDED';
    if (settled === missing(payment.paid_at)) {
      fail('payment.paid_at must be set when PAID or REFUNDED and empty when PENDING or FAILED');
    }
  }

  // trips with their driver assignments
  var trips = newDoc.trips;
  if (!isArray(trips)) {
    fail('trips must be an array');
  }
  var tripIds = {};
  var assignmentIds = {};
  for (var t = 0; t < trips.length; t++) {
    var trip = trips[t];
    if (!isObject(trip)) {
      fail('Each trip must be an object');
    }
    checkId(trip, 'trip_id', 'trip', 'trip');
    if (tripIds[trip.trip_id]) {
      fail('Duplicate trip_id ' + trip.trip_id);
    }
    tripIds[trip.trip_id] = true;
    checkEnum(trip, 'trip_status', TRIP_STATUS, 'trip');
    checkTimestamp(trip, 'started_at', 'trip', false);
    checkTimestamp(trip, 'completed_at', 'trip', trip.trip_status === 'COMPLETED');
    if (!missing(trip.started_at) && !missing(trip.completed_at)
        && trip.completed_at < trip.started_at) {
      fail('trip.completed_at must not be earlier than started_at');
    }
    checkId(trip, 'start_location_id', 'location', 'trip');
    checkId(trip, 'end_location_id', 'location', 'trip');
    checkId(trip, 'start_service_area_id', 'area', 'trip');
    if (!isArray(trip.assignments)) {
      fail('trip.assignments must be an array');
    }
    for (var a = 0; a < trip.assignments.length; a++) {
      var assignment = trip.assignments[a];
      if (!isObject(assignment)) {
        fail('Each assignment must be an object');
      }
      checkId(assignment, 'assignment_id', 'assignment', 'assignment');
      if (assignmentIds[assignment.assignment_id]) {
        fail('Duplicate assignment_id ' + assignment.assignment_id);
      }
      assignmentIds[assignment.assignment_id] = true;
      checkEnum(assignment, 'assignment_status', ASSIGNMENT_STATUS, 'assignment');
      checkTimestamp(assignment, 'assigned_at', 'assignment', true);
      checkTimestamp(assignment, 'responded_at', 'assignment', false);
      checkTimestamp(assignment, 'ended_at', 'assignment', false);
      checkText(assignment, 'end_reason', 'assignment', 100, false);
      checkId(assignment, 'vehicle_id', 'vehicle', 'assignment');
      checkId(assignment, 'driver_partner_id', 'driver', 'assignment');
      checkText(assignment, 'vehicle_type', 'assignment', 30, true);
    }
  }

  // Rules that compare the new version with the stored one.
  if (oldDoc && !oldDoc._deleted) {
    if (oldDoc.service_type !== newDoc.service_type) {
      fail('service_type cannot be changed');
    }
    checkTransition('order', 'order_status', oldDoc.order_status, newDoc.order_status, ORDER_FLOW);
    if (newDoc.service_type === 'FOOD') {
      checkTransition('food', 'preparation_status', oldDoc.food.preparation_status,
                      subtype.preparation_status, PREPARATION_FLOW);
    }
    var oldPayment = oldDoc.payment;
    if (!missing(oldPayment)) {
      if (missing(payment)) {
        fail('payment cannot be removed');
      }
      if (payment.payment_id !== oldPayment.payment_id) {
        fail('payment_id cannot be changed');
      }
      checkTransition('payment', 'payment_status', oldPayment.payment_status,
                      payment.payment_status, PAYMENT_FLOW);
      if ((oldPayment.payment_status === 'PAID' || oldPayment.payment_status === 'REFUNDED')
          && payment.payment_status === 'REFUNDED' && payment.paid_at !== oldPayment.paid_at) {
        fail('A refund must retain the original paid_at');
      }
    }
    var newTrips = indexBy(trips, 'trip_id');
    for (var o = 0; o < oldDoc.trips.length; o++) {
      var oldTrip = oldDoc.trips[o];
      var newTrip = newTrips[oldTrip.trip_id];
      if (!newTrip) {
        fail('trip ' + oldTrip.trip_id + ' cannot be removed or renamed');
      }
      checkTransition('trip', 'trip_status', oldTrip.trip_status, newTrip.trip_status, TRIP_FLOW);
      var newAssignments = indexBy(newTrip.assignments, 'assignment_id');
      for (var p = 0; p < oldTrip.assignments.length; p++) {
        if (!newAssignments[oldTrip.assignments[p].assignment_id]) {
          fail('assignment ' + oldTrip.assignments[p].assignment_id + ' cannot be removed or renamed');
        }
      }
    }
  }
}
