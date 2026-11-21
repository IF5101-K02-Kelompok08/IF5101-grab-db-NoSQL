// validate_doc_update for grab_fleet: one document per driver partner, with the vehicles
// the driver registered embedded in it (the "registers" aggregation of Part 2).
function (newDoc, oldDoc, userCtx, secObj) {
  if (newDoc._id.indexOf('_design/') === 0 || newDoc._deleted) {
    return;
  }

  function fail(message) {
    throw ({forbidden: message});
  }
  function checkText(obj, field, label, maxLength, required) {
    var value = obj[field];
    if (value === undefined || value === null || value === '') {
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

  if (newDoc.type !== 'driver_partner') {
    fail('type must be driver_partner');
  }
  if (!/^DRV[0-9]{7}$/.test(newDoc._id) || newDoc.driver_partner_id !== newDoc._id) {
    fail('driver_partner_id must match DRV0000000 and equal _id');
  }
  checkText(newDoc, 'full_name', 'driver', 100, true);
  if (typeof newDoc.phone_number !== 'string' || !/^\+?[0-9]{7,15}$/.test(newDoc.phone_number)) {
    fail('driver.phone_number must be a valid phone number');
  }
  if (typeof newDoc.email !== 'string' || newDoc.email.length > 100
      || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(newDoc.email)) {
    fail('driver.email must be a valid email address');
  }
  checkEnum(newDoc, 'driver_status', ['ACTIVE', 'INACTIVE', 'SUSPENDED'], 'driver');

  if (Object.prototype.toString.call(newDoc.vehicles) !== '[object Array]') {
    fail('vehicles must be an array');
  }
  var seen = {};
  for (var i = 0; i < newDoc.vehicles.length; i++) {
    var v = newDoc.vehicles[i];
    if (typeof v.vehicle_id !== 'string' || !/^VEH[0-9]{7}$/.test(v.vehicle_id)) {
      fail('vehicle.vehicle_id must match VEH0000000');
    }
    if (seen[v.vehicle_id]) {
      fail('Duplicate vehicle_id ' + v.vehicle_id);
    }
    seen[v.vehicle_id] = true;
    checkText(v, 'plate_number', 'vehicle', 15, true);
    checkText(v, 'vehicle_type', 'vehicle', 30, true);
    checkEnum(v, 'vehicle_status', ['ACTIVE', 'INACTIVE', 'UNDER_MAINTENANCE'], 'vehicle');
  }
  // Vehicle IDs are immutable: a vehicle stays with the driver who registered it.
  if (oldDoc && !oldDoc._deleted) {
    for (var j = 0; j < oldDoc.vehicles.length; j++) {
      if (!seen[oldDoc.vehicles[j].vehicle_id]) {
        fail('vehicle ' + oldDoc.vehicles[j].vehicle_id + ' cannot be removed or renamed');
      }
    }
  }
}
