"""Stream the canonical CSV files into CouchDB documents.

The Part 2 generator writes every order's rows in order: service_orders, its subtype row,
its payment and its trip are all sorted by order_id, and driver_assignments by trip_id.
The order documents are therefore built with a streaming merge join over the seven files,
so memory stays small even for 2,000,000 orders. The merge checks the sort order and fails
loudly on rows that do not belong to any order instead of dropping them.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from couchdb import model


def read_rows(data_dir: Path, table: str) -> Iterator[dict[str, str]]:
    with (data_dir / f'{table}.csv').open(newline='', encoding='utf-8') as file:
        yield from csv.DictReader(file)


class Cursor:
    """A sorted row stream from which the rows of one key can be taken at a time."""

    def __init__(self, rows: Iterator[dict], key: str, table: str) -> None:
        self.rows, self.key, self.table = rows, key, table
        self.head = next(self.rows, None)

    def take(self, value: str) -> list[dict]:
        if self.head is not None and self.head[self.key] < value:
            raise ValueError(f'{self.table}: {self.key}={self.head[self.key]} has no parent '
                             f'row or the file is not sorted by {self.key}')
        taken = []
        while self.head is not None and self.head[self.key] == value:
            taken.append(self.head)
            self.head = next(self.rows, None)
            if self.head is not None and self.head[self.key] < value:
                raise ValueError(f'{self.table} is not sorted by {self.key}')
        return taken

    def check_exhausted(self) -> None:
        if self.head is not None:
            raise ValueError(f'{self.table}: {self.key}={self.head[self.key]} has no parent row')


def load_lookups(data_dir: Path) -> model.Lookups:
    location_area = {row['location_id']: row['service_area_id']
                     for row in read_rows(data_dir, 'locations')}
    outlet_area = {row['merchant_outlet_id']: location_area[row['location_id']]
                   for row in read_rows(data_dir, 'merchant_outlets')}
    vehicles = {row['vehicle_id']: (row['driver_partner_id'], row['vehicle_type'])
                for row in read_rows(data_dir, 'vehicles')}
    return model.Lookups(location_area, outlet_area, vehicles)


def geo_docs(data_dir: Path) -> Iterator[dict]:
    yield from (model.service_area_doc(row) for row in read_rows(data_dir, 'service_areas'))
    locations = {}
    for row in read_rows(data_dir, 'locations'):
        doc = model.location_doc(row)
        locations[doc['_id']] = {k: doc[k] for k in ('location_id', 'address', 'latitude',
                                                     'longitude', 'service_area_id')}
        yield doc
    for row in read_rows(data_dir, 'merchant_outlets'):
        yield model.outlet_doc(row, locations[row['location_id']])


def customer_docs(data_dir: Path) -> Iterator[dict]:
    return (model.customer_doc(row) for row in read_rows(data_dir, 'customers'))


def fleet_docs(data_dir: Path) -> Iterator[dict]:
    vehicles = Cursor(read_rows(data_dir, 'vehicles'), 'driver_partner_id', 'vehicles')
    for row in read_rows(data_dir, 'driver_partners'):
        yield model.driver_doc(row, vehicles.take(row['driver_partner_id']))
    vehicles.check_exhausted()


def order_docs(data_dir: Path, lookups: model.Lookups) -> Iterator[dict]:
    subtypes = {kind: Cursor(read_rows(data_dir, table), 'order_id', table)
                for kind, table in model.SUBTYPE_TABLE.items()}
    payments = Cursor(read_rows(data_dir, 'payments'), 'order_id', 'payments')
    trips = Cursor(read_rows(data_dir, 'trips'), 'order_id', 'trips')
    assignments = Cursor(read_rows(data_dir, 'driver_assignments'), 'trip_id',
                         'driver_assignments')
    for order in read_rows(data_dir, 'service_orders'):
        order_id = order['order_id']
        found = [(kind, rows) for kind, cursor in subtypes.items()
                 if (rows := cursor.take(order_id))]
        if len(found) != 1 or len(found[0][1]) != 1:
            raise ValueError(f'{order_id} must have exactly one subtype row, found {len(found)}')
        payment = payments.take(order_id)
        if len(payment) > 1:
            raise ValueError(f'{order_id} has {len(payment)} payments; at most one is allowed')
        order_trips = [(trip, assignments.take(trip['trip_id'])) for trip in trips.take(order_id)]
        kind, rows = found[0]
        yield model.order_doc(order, kind, rows[0], payment[0] if payment else None,
                              order_trips, lookups)
    for cursor in (*subtypes.values(), payments, trips, assignments):
        cursor.check_exhausted()


def database_docs(data_dir: Path, db: str, lookups: model.Lookups | None = None) -> Iterator[dict]:
    """All documents of one logical database ('geo', 'customers', 'fleet' or 'orders')."""
    if db == 'geo':
        return geo_docs(data_dir)
    if db == 'customers':
        return customer_docs(data_dir)
    if db == 'fleet':
        return fleet_docs(data_dir)
    if db == 'orders':
        return order_docs(data_dir, lookups or load_lookups(data_dir))
    raise KeyError(db)
