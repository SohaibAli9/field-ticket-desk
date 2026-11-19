"""One test per check, on hand-made tickets. No model calls, no network; the well registry is a stub."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import checks  # noqa: E402
from wells import Well  # noqa: E402

REG = {
    "30-015-05911": Well("30-015-05911", "BURCH KEELY UNIT #061", "Oil", "Active", "EQV Operating NM LLC", "Eddy", "", 32.8, -104.0),
    "30-015-05411": Well("30-015-05411", "LEA D #001", "Injection", "Plugged (site released)", "SANDRIDGE", "Eddy", "", 32.8, -103.8),
    "30-015-36486": Well("30-015-36486", "KOOL AID STATE #009", "Oil", "Active", "Spur Energy Partners LLC", "Eddy", "", 32.9, -104.1),
    "30-015-22222": Well("30-015-22222", "DORAMI 33 FEDERAL #001", "Oil", "Active", "COG OPERATING LLC", "Eddy", "", 32.4, -103.9),
    "30-015-11111": Well("30-015-11111", "OLD HAT #001", "Oil", "Plugged (site released)", "EQV Operating NM LLC", "Eddy", "", 32.9, -104.1),
}
BOOKS = checks.Books(
    dispatch=[{"load_id": "D-1", "date": "2026-11-02", "customer": "EQV Operating", "lease_well": "Burch Keely Unit #61",
               "api": "30-015-05911", "truck": "T-12", "trailer": "TR-112", "driver": "Ray Baca", "swd": "Romo SWD #1"},
              {"load_id": "D-2", "date": "2026-11-02", "customer": "Spur Energy", "lease_well": "Kool Aid State #9",
               "api": "30-015-36486", "truck": "T-14", "trailer": "TR-114", "driver": "Jesse Torres", "swd": "Romo SWD #1"}],
    receipts={"20417": {"swd": "Romo SWD #1", "swd_ticket": "20417", "date": "2026-11-02", "truck": "T-12", "bbls_received": "125"}},
    rates={"EQV Operating": {"basis": "per_bbl", "rate": 1.15},
           "Riley Permian": {"basis": "hourly", "rate": 98.0, "min_hours": 2.0}},
    operator_of_record={"EQV Operating": "EQV Operating NM LLC", "Spur Energy": "Spur Energy Partners LLC"},
    trailers={"TR-112": 130, "VAC-07": 80}, trucks={"T-12": "TR-112", "T-14": "TR-114", "T-07": "VAC-07"},
    drivers=["Ray Baca", "Jesse Torres"], swds=["Romo SWD #1"], registry=REG,
    operator_aliases={"ConocoPhillips": ["COG OPERATING LLC", "MARATHON OIL PERMIAN LLC"]})
BOOKS.operator_of_record["ConocoPhillips"] = "CONOCOPHILLIPS COMPANY"

GOOD = {"ticket_no": "10412", "date": "11/02/26", "customer": "EQV Operating", "lease_well": "Burch Keely Unit #61",
        "api": "30-015-05911", "county": "Eddy", "truck": "T-12", "trailer": "TR-112", "driver": "Ray Baca",
        "load_type": "Produced Water", "time_in": "09:00", "time_out": "10:30", "hours": "", "swd": "Romo SWD #1",
        "swd_ticket": "20417", "bbls": "125", "rate": "1.15", "total": "143.75", "driver_signed": True,
        "customer_signed": True}


def t(**kw):
    return dict(GOOD, **kw)


def fields(issues):
    return {(i.check, i.field) for i in issues}


def test_clean_ticket_passes_every_check():
    assert checks.run_all(GOOD, BOOKS) == []


def test_required_flags_blank_field_and_missing_signature():
    assert fields(checks.required(t(swd_ticket="", customer_signed=False), BOOKS)) == {
        ("required", "swd_ticket"), ("required", "customer_signed")}


def test_api_form_and_county_prefix():
    assert fields(checks.api_number(t(api="30-15-5911"), BOOKS)) == {("api", "api")}
    assert fields(checks.api_number(t(api="30-025-05911"), BOOKS)) == {("api", "api")}
    assert checks.api_number(GOOD, BOOKS) == []


def test_registry_real_well_but_not_the_named_one():
    # One digit off is still a real well, so only the name check can catch it, and it names the right number.
    iss = checks.registry(t(api="30-015-05411"), BOOKS)
    assert fields(iss) == {("registry", "api")}
    assert "LEA D #001" in iss[0].message and "30-015-05911" in iss[0].suggestion


def test_registry_unknown_api():
    assert fields(checks.registry(t(api="30-015-99999"), BOOKS)) == {("registry", "api")}


def test_registry_bill_to_differs_from_operator_of_record():
    iss = checks.registry(t(customer="Spur Energy"), BOOKS)
    assert fields(iss) == {("registry", "customer")}
    assert "EQV Operating" in iss[0].suggestion


def test_registry_plugged_well():
    iss = checks.registry(t(api="30-015-11111", lease_well="Old Hat #1"), BOOKS)
    assert fields(iss) == {("registry", "api")} and "Plugged" in iss[0].message


def test_dispatch_truck_mismatch_and_no_match():
    assert fields(checks.dispatch(t(truck="T-41"), BOOKS)) == {("dispatch", "truck")}
    assert fields(checks.dispatch(t(date="11/09/26"), BOOKS)) == {("dispatch", "date")}


def test_disposal_volume_outside_tolerance_only():
    assert checks.disposal(t(bbls="126"), BOOKS) == []
    iss = checks.disposal(t(bbls="130", total="149.50"), BOOKS)
    assert fields(iss) == {("disposal", "bbls")} and "125" in iss[0].message


def test_disposal_unknown_receipt():
    assert fields(checks.disposal(t(swd_ticket="99999"), BOOKS)) == {("disposal", "swd_ticket")}


def test_capacity():
    assert fields(checks.capacity(t(bbls="145"), BOOKS)) == {("capacity", "bbls")}
    assert checks.capacity(t(bbls="130"), BOOKS) == []


def test_billing_rate_and_math():
    assert fields(checks.billing(t(rate="1.30", total="162.50"), BOOKS)) == {("billing", "rate")}
    assert fields(checks.billing(t(total="153.75"), BOOKS)) == {("billing", "total")}


def test_billing_hourly_minimum():
    riley = t(customer="Riley Permian", rate="98.00", hours="1.5", time_in="07:15", time_out="08:45")
    assert checks.billing(dict(riley, total="196.00"), BOOKS) == []      # 2 hour minimum
    assert fields(checks.billing(dict(riley, total="147.00"), BOOKS)) == {("billing", "total")}


def test_times_order_and_hours():
    assert fields(checks.times(t(time_in="10:30", time_out="09:00"), BOOKS)) == {("times", "time_out")}
    assert fields(checks.times(t(hours="3"), BOOKS)) == {("times", "hours")}


def test_snap_picks_one_entry_or_none():
    assert checks.snap("JR-114", ["TR-114", "TR-112", "VAC-07"], "trailer")[0] == "TR-114"
    assert checks.snap("TR-14", ["TR-114", "TR-141"], "trailer")[0] is None        # two entries equally close
    assert checks.snap("F07", ["T-07", "T-12"], "truck")[0] is None                # too far to trust


def test_duplicates_and_lost_tickets():
    dup = checks.find_duplicates([GOOD, t(), t(ticket_no="10413")])
    assert dup == {1: 0}
    lost = checks.find_lost([GOOD], BOOKS)
    assert [d["load_id"] for d in lost] == ["D-2"]


def test_registry_accepts_merged_operator_names():
    # ConocoPhillips bought Concho; the state registry still files many of its wells under COG OPERATING LLC.
    cop = t(customer="ConocoPhillips", api="30-015-22222", lease_well="Dorami 33 Federal #1")
    assert checks.registry(cop, BOOKS) == []
    iss = checks.registry(t(customer="EQV Operating", api="30-015-22222", lease_well="Dorami 33 Federal #1"), BOOKS)
    assert fields(iss) == {("registry", "customer")} and iss[0].suggestion == "bill ConocoPhillips"
