// validate_doc_update for grab_geo: service areas, locations and merchant outlets.
// The outlet embeds a copy of its location (address, coordinates, service area), because
// an outlet does not move and every outlet search needs those fields.
function (newDoc, oldDoc, userCtx, secObj) {
  if (newDoc._id.indexOf('_design/') === 0 || newDoc._deleted) {
    return;
  }

  function fail(message) {
    throw ({forbidden: message});
  }
  function checkText(obj, field, label, maxLength) {
    var value = obj[field];
    if (typeof value !== 'string' || value === '' || value.length > maxLength) {
      fail(label + '.' + field + ' is required (at most ' + maxLength + ' characters)');
    }
  }
  function checkId(obj, field, pattern, label) {
    if (typeof obj[field] !== 'string' || !pattern.test(obj[field])) {
      fail(label + '.' + field + ' must match ' + pattern.source);
    }
  }
  function checkCoordinates(obj, label) {
    if (typeof obj.latitude !== 'number' || obj.latitude < -90 || obj.latitude > 90) {
      fail(label + '.latitude must be between -90 and 90');
    }
    if (typeof obj.longitude !== 'number' || obj.longitude < -180 || obj.longitude > 180) {
      fail(label + '.longitude must be between -180 and 180');
    }
  }
  var AREA = /^SAR[0-9]{7}$/;
  var LOCATION = /^LOC[0-9]{7}$/;

  if (newDoc.type === 'service_area') {
    checkId(newDoc, 'service_area_id', AREA, 'service_area');
    if (newDoc.service_area_id !== newDoc._id) {
      fail('service_area_id must equal _id');
    }
    checkText(newDoc, 'area_name', 'service_area', 100);
    checkText(newDoc, 'city', 'service_area', 100);
    if (['ACTIVE', 'INACTIVE'].indexOf(newDoc.area_status) === -1) {
      fail('service_area.area_status must be ACTIVE or INACTIVE');
    }
  } else if (newDoc.type === 'location') {
    checkId(newDoc, 'location_id', LOCATION, 'location');
    if (newDoc.location_id !== newDoc._id) {
      fail('location_id must equal _id');
    }
    checkCoordinates(newDoc, 'location');
    checkText(newDoc, 'address', 'location', 255);
    checkId(newDoc, 'service_area_id', AREA, 'location');
  } else if (newDoc.type === 'merchant_outlet') {
    checkId(newDoc, 'merchant_outlet_id', /^OUT[0-9]{7}$/, 'merchant_outlet');
    if (newDoc.merchant_outlet_id !== newDoc._id) {
      fail('merchant_outlet_id must equal _id');
    }
    checkText(newDoc, 'outlet_name', 'merchant_outlet', 100);
    if (['OPEN', 'CLOSED', 'INACTIVE'].indexOf(newDoc.outlet_status) === -1) {
      fail('merchant_outlet.outlet_status must be OPEN, CLOSED or INACTIVE');
    }
    var location = newDoc.location;
    if (location === null || typeof location !== 'object') {
      fail('merchant_outlet.location is required');
    }
    checkId(location, 'location_id', LOCATION, 'merchant_outlet.location');
    checkId(location, 'service_area_id', AREA, 'merchant_outlet.location');
    checkCoordinates(location, 'merchant_outlet.location');
    checkText(location, 'address', 'merchant_outlet.location', 255);
  } else {
    fail('type must be service_area, location or merchant_outlet');
  }
}
