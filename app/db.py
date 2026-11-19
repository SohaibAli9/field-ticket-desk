"""Postgres schema and the few queries the app needs. Ticket fields live in JSONB so a new ticket layout
needs a template change, not a migration."""
from __future__ import annotations

import json
import os

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DSN = os.environ.get("TICKETS_DSN", "postgresql://postgres:demo@127.0.0.1:55432/tickets")

SCHEMA = """
create table if not exists tickets (
  id serial primary key,
  image text unique not null,
  source text not null,
  ticket_no text,
  customer text,
  load_id text,
  values jsonb not null,          -- current value per field
  field_status jsonb not null,    -- per field: state auto|review|person, how, the two readings
  issues jsonb not null default '[]',
  duplicate_of text,
  state text not null,            -- review | held | ready | invoiced | duplicate
  read_cost numeric(8,4),
  created_at timestamptz default now()
);
create table if not exists review_log (
  id serial primary key,
  ticket_id int references tickets(id),
  field text, chosen text, reading_a text, reading_b text,
  reviewer text, at timestamptz default now()
);
create table if not exists lost_loads (
  load_id text primary key, date date, customer text, lease_well text, truck text, driver text,
  swd_ticket text, bbls numeric, value_at_risk numeric
);
create table if not exists invoices (
  doc_number text primary key, customer text, total numeric, qbo_payload jsonb, state text, ticket_ids int[]
);
"""


def connect():
    return psycopg.connect(DSN, row_factory=dict_row, autocommit=True, options="-c timezone=America/Chicago")


def init(reset: bool = False) -> None:
    with connect() as c:
        if reset:
            c.execute("drop table if exists review_log, invoices, lost_loads, tickets cascade")
        c.execute(SCHEMA)


def j(v):
    return Jsonb(json.loads(json.dumps(v, default=str)))
