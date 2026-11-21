// validate_doc_update for grab_customers: one document per customer.
function (newDoc, oldDoc, userCtx, secObj) {
  if (newDoc._id.indexOf('_design/') === 0 || newDoc._deleted) {
    return;
  }

  function fail(message) {
    throw ({forbidden: message});
  }

  if (newDoc.type !== 'customer') {
    fail('type must be customer');
  }
  if (!/^CUS[0-9]{7}$/.test(newDoc._id) || newDoc.customer_id !== newDoc._id) {
    fail('customer_id must match CUS0000000 and equal _id');
  }
  if (typeof newDoc.full_name !== 'string' || newDoc.full_name === ''
      || newDoc.full_name.length > 100) {
    fail('customer.full_name is required (at most 100 characters)');
  }
  var phone = newDoc.phone_number;
  if (phone !== null && phone !== undefined
      && (typeof phone !== 'string' || !/^\+?[0-9]{7,15}$/.test(phone))) {
    fail('customer.phone_number must be empty or a valid phone number');
  }
  var email = newDoc.email;
  if (email !== null && email !== undefined
      && (typeof email !== 'string' || email.length > 100 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))) {
    fail('customer.email must be empty or a valid email address');
  }
}
