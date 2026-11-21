// Section 3.1 validation: Part 2 rows held by the customer documents.
function (doc) {
  if (doc.type === 'customer') {
    emit('customers', 1);
  }
}
