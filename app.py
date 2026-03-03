from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import requests
from nicegui import app, run, ui

TEMPLATES: dict[str, dict[str, Any]] = {
    "sir": {
        "name": "Collective SIR Table",
        "sections": [
            {"id": "site", "title": "Site Information", "rows": [("owner_name", "Owner / Parcel Name"), ("parcel_address", "Parcel Address"), ("county", "County"), ("site_acreage", "Site Acreage"), ("existing_condition", "Existing Condition")]},
            {"id": "zoning", "title": "Zoning", "rows": [("authority", "Authority"), ("existing_zoning_plan", "Existing Zoning/Comp Plan"), ("proposed_zoning", "Proposed Zoning")]},
            {"id": "env", "title": "Environmental", "rows": [("wetlands_flood", "Wetlands/Flood Plains"), ("storm_treatment", "Storm Water Requirements")]},
            {"id": "perm", "title": "Permitting", "rows": [("local_permits", "Local"), ("wmd", "WMD"), ("fdep", "FDEP"), ("fdot", "FDOT")]},
        ],
    },
    "typical": {
        "name": "Typical Due Diligence",
        "sections": [
            {"id": "gpi", "title": "General Project Information", "rows": [("owner_name", "Owner / Parcel Name"), ("project_location", "Project Location"), ("current_use", "Current Use"), ("proposed_use", "Proposed Use"), ("project_area", "Project Area (acres)")]},
            {"id": "existing", "title": "Existing Conditions", "rows": [("floodplain", "Floodplain"), ("wetlands", "Wetlands"), ("access", "Access")]},
            {"id": "zlu", "title": "Zoning and Land Use", "rows": [("flu", "Future Land Use"), ("zoning", "Current Zoning"), ("building_requirements", "Building Requirements")]},
            {"id": "perm", "title": "Permitting", "rows": [("local", "Local"), ("swfwmd", "SWFWMD"), ("fdep", "FDEP")]},
        ],
    },
}

SIR_PREFILL: dict[str, dict[str, str]] = {
    "site": {
        "owner_name": "24-31-16-53478-000-0210, 24-31-16-53478-000-0300, 24-31-16-53478-000-0211, and a portion of 24-31-16-53478-000-0010",
        "parcel_address": "1st Ave South and 11th St North, St. Petersburg, FL",
        "county": "Pinellas",
        "site_acreage": "2.14 acres",
        "existing_condition": "Existing vacant restaurants and associated parking",
    },
    "zoning": {
        "authority": "City of St. Petersburg",
        "existing_zoning_plan": "Zoning: DC-1, Intown Activity Center",
        "proposed_zoning": "CBD",
    },
    "env": {
        "wetlands_flood": "No wetlands, Flood Zone X",
        "storm_treatment": "3.2-inch rainfall in 10 yr/1 hr per COSP master stormwater plan; post-development runoff rate = pre-development runoff rate",
    },
    "perm": {
        "local_permits": "Pre-Application Meeting; Plat Application; Site Plan Review Application with Variance; Neighborhood Noticing Deadline; Tentative DRC Meeting; DRC Approval; Construction Drawings and Building Permit",
        "wmd": "N/A",
        "fdep": "N/A",
        "fdot": "N/A",
    },
}

ui.add_css(
    ".muted{color:#5a647f;font-size:12px}.section-title{font-weight:700}.dd-textarea .q-field__control{min-height:100px!important}"
    ".sir-table{width:100%;border-collapse:collapse;table-layout:fixed}"
    ".sir-table th,.sir-table td{border:1px solid #c9c9cf;padding:8px;vertical-align:top;font-size:13px;line-height:1.35;white-space:pre-wrap}"
    ".sir-table th{background:#f1f2f5;text-align:left;font-weight:700}"
    ".sir-section{background:#e4e7ec;font-weight:700}",
    shared=True,
)
_NON_DIGIT = re.compile(r"[^0-9]")
_S = requests.Session()


def digits_only(v: str) -> str: return _NON_DIGIT.sub("", str(v or ""))
def first_value(v: str) -> str: return [p.strip() for p in str(v or "").split(",") if p.strip()][0] if str(v or "").strip() else ""
def sql_quote(v: str) -> str: return "'" + str(v or "").replace("'", "''") + "'"
def fmt_acres(v: Any) -> str:
    try: return f"{float(v):.2f}"
    except Exception: return "" if v is None else str(v).strip()


def _q(url: str, where: str, out_fields: list[str]) -> dict[str, Any]:
    r = _S.get(url, params={"where": where, "outFields": ",".join(out_fields), "returnGeometry": "false", "f": "json"}, timeout=20)
    r.raise_for_status()
    d = r.json()
    if isinstance(d, dict) and d.get("error"): raise RuntimeError(d["error"].get("message", "ArcGIS error"))
    return d


def lookup_property(county: str, parcel_raw: str) -> dict[str, Any]:
    parcel = first_value(parcel_raw)
    if not parcel: raise ValueError("Parcel/Folio is required")
    digits = digits_only(parcel)
    county = (county or "Pinellas").strip()
    if county == "Pinellas":
        url = "https://egis.pinellas.gov/gis/rest/services/PublicWebGIS/Parcels/MapServer/1/query"
        where = f"PARCELID_DSP1={sql_quote(parcel)} OR PARCELID_DSP2={sql_quote(parcel)}" + (f" OR STRAP={sql_quote(digits)} OR PARCELID={sql_quote(digits)}" if digits else "")
        d = _q(url, where, ["SITE_NUM", "SITE_ADDRESS", "SITE_CITY", "SITE_STATE", "SITE_ZIP", "OWNER1", "OWNER2", "USE_CODE", "LAND_USE_CODE", "Acres"])
        f = (d.get("features") or [])
        if not f: return {}
        a = f[0].get("attributes") or {}
        street = " ".join([str(a.get("SITE_NUM") or "").strip(), str(a.get("SITE_ADDRESS") or "").strip()]).strip()
        city = str(a.get("SITE_CITY") or "").strip()
        st = str(a.get("SITE_STATE") or "FL").strip() or "FL"
        z = str(a.get("SITE_ZIP") or "").strip()
        return {"owner": " ".join([str(a.get("OWNER1") or "").strip(), str(a.get("OWNER2") or "").strip()]).strip(), "city": city, "address": ", ".join([p for p in [street, city, f"{st} {z}".strip()] if p]), "acreage": fmt_acres(a.get("Acres")), "existing_condition": str(a.get("USE_CODE") or a.get("LAND_USE_CODE") or "").strip(), "source_url": "https://egis.pinellas.gov/gis/rest/services/PublicWebGIS/Parcels/MapServer/1"}
    if county == "Hillsborough":
        url = "https://arcgis.tampagov.net/arcgis/rest/services/Parcels/TaxParcel/MapServer/0/query"
        where = f"FOLIO={sql_quote(parcel)} OR PIN={sql_quote(parcel)}" + (f" OR STRAP={sql_quote(digits)}" if digits else "")
        d = _q(url, where, ["OWNER", "SITE_ADDR", "SITE_CITY", "SITE_ZIP", "ACREAGE", "TYPE", "DOR_C"])
        f = (d.get("features") or [])
        if not f: return {}
        a = f[0].get("attributes") or {}
        city = str(a.get("SITE_CITY") or "").strip(); z = str(a.get("SITE_ZIP") or "").strip()
        return {"owner": str(a.get("OWNER") or "").strip(), "city": city, "address": ", ".join([p for p in [str(a.get("SITE_ADDR") or "").strip(), city, f"FL {z}".strip()] if p]), "acreage": fmt_acres(a.get("ACREAGE")), "existing_condition": str(a.get("TYPE") or a.get("DOR_C") or "").strip(), "source_url": "https://arcgis.tampagov.net/arcgis/rest/services/Parcels/TaxParcel/MapServer/0"}
    if county == "Pasco":
        url = "https://maps.pascopa.com/arcgis/rest/services/Parcels/MapServer/3/query"
        d = _q(url, f"ParcelID={sql_quote(parcel)}", ["NAD_NAME_1", "NAD_NAME_2", "PHYS_STREET", "PHYS_CITY", "PHYS_STATE", "PHYS_ZIP", "TR_AC", "VAL_ACRES", "SALE_VAC_IMP", "DIR_CLASS"])
        f = (d.get("features") or [])
        if not f: return {}
        a = f[0].get("attributes") or {}
        city = str(a.get("PHYS_CITY") or "").strip(); st = str(a.get("PHYS_STATE") or "FL").strip() or "FL"; z = str(a.get("PHYS_ZIP") or "").strip()
        existing = " | ".join([x for x in [str(a.get("SALE_VAC_IMP") or "").strip(), str(a.get("DIR_CLASS") or "").strip()] if x])
        acres = fmt_acres(a.get("TR_AC") if a.get("TR_AC") is not None else a.get("VAL_ACRES"))
        return {"owner": " ".join([str(a.get("NAD_NAME_1") or "").strip(), str(a.get("NAD_NAME_2") or "").strip()]).strip(), "city": city, "address": ", ".join([p for p in [str(a.get("PHYS_STREET") or "").strip(), city, f"{st} {z}".strip()] if p]), "acreage": acres, "existing_condition": existing, "source_url": "https://maps.pascopa.com/arcgis/rest/services/Parcels/MapServer/3"}
    raise ValueError(f"Unsupported county: {county}")


def default_state() -> dict[str, Any]:
    return {"parcel_id": "", "county": "Pinellas", "owner": "", "city": "", "address": "", "acreage": "", "existing_condition": "", "template": "sir", "dd": {}}


def ensure_dd(state: dict[str, Any]) -> None:
    dd = state.setdefault("dd", {})
    for tk, tpl in TEMPLATES.items():
        t = dd.setdefault(tk, {})
        for sec in tpl["sections"]:
            s = t.setdefault(sec["id"], {})
            for k, label in sec["rows"]:
                s.setdefault(k, {"label": label, "value": "", "auto": False, "source_url": "", "accessed": "", "notes": ""})


def autofill(state: dict[str, Any], source_url: str = "") -> None:
    ensure_dd(state)
    src = {"owner_name": state.get("owner", ""), "parcel_address": state.get("address", ""), "county": state.get("county", ""), "site_acreage": state.get("acreage", ""), "existing_condition": state.get("existing_condition", "")}
    for key, val in src.items():
        cell = state["dd"]["sir"]["site"].get(key)
        if cell and (not str(cell.get("value") or "").strip() or cell.get("auto")):
            cell["value"] = str(val or ""); cell["auto"] = True; cell["source_url"] = source_url or cell.get("source_url", ""); cell["accessed"] = dt.datetime.now().strftime("%b %d, %Y")


def widget(label: str, value: str, textarea: bool = False) -> Any:
    return (ui.textarea if textarea else ui.input)(label=label, value=value).props("outlined dense stack-label autogrow" if textarea else "outlined dense stack-label").classes("w-full dd-textarea" if textarea else "w-full")


def sir_prefill_value(section_id: str, field_key: str, fallback_label: str) -> str:
    return SIR_PREFILL.get(section_id, {}).get(field_key, fallback_label)


@ui.page("/")
def main() -> None:
    state = app.storage.user.get("site_dd")
    if not isinstance(state, dict): state = default_state()
    state["template"] = "sir"
    ensure_dd(state); autofill(state); app.storage.user["site_dd"] = state

    def persist() -> None: app.storage.user["site_dd"] = state

    @ui.refreshable
    def dd_form() -> None:
        tk = state.get("template", "sir"); tpl = TEMPLATES.get(tk, TEMPLATES["sir"])
        for sec in tpl["sections"]:
            with ui.card().classes("w-full q-mt-sm"):
                ui.label(sec["title"]).classes("section-title")
                for fk, label in sec["rows"]:
                    c = state["dd"][tk][sec["id"]][fk]
                    inp = widget(label, c.get("value", ""), textarea=(sec["id"] in {"env", "perm", "zoning", "existing", "zlu"} or fk in {"existing_condition", "building_requirements"}))
                    inp.on("change", lambda e, t=tk, s=sec["id"], k=fk: (state["dd"][t][s][k].__setitem__("value", e.value), state["dd"][t][s][k].__setitem__("auto", False), persist()))

    @ui.refreshable
    def sir_lookup_table() -> None:
        sir = TEMPLATES["sir"]
        with ui.element("table").classes("sir-table q-mt-sm"):
            with ui.element("thead"):
                with ui.element("tr"):
                    ui.element("th").text("SIR Section / Field")
                    ui.element("th").text("SIR Table Value (Prefilled)")
                    ui.element("th").text("Lookup Result")
            with ui.element("tbody"):
                for sec in sir["sections"]:
                    with ui.element("tr"):
                        ui.element("td").classes("sir-section").props("colspan=3").text(sec["title"])
                    for fk, label in sec["rows"]:
                        left = sir_prefill_value(sec["id"], fk, label)
                        right = str(state["dd"]["sir"][sec["id"]][fk].get("value", "") or "")
                        with ui.element("tr"):
                            ui.element("td").text(label)
                            ui.element("td").text(left)
                            ui.element("td").text(right if right.strip() else "-")

    ui.label("Site Data Due Diligence").classes("text-h4")
    with ui.tabs() as tabs:
        tab1 = ui.tab("Lookup"); tab2 = ui.tab("Due Diligence"); tab3 = ui.tab("Export")
    with ui.tab_panels(tabs, value=tab1).classes("w-full"):
        with ui.tab_panel(tab1):
            with ui.row().classes("w-full"):
                with ui.column().classes("col-4"):
                    parcel = ui.input("Parcel # / Folio", value=state.get("parcel_id", "")).props("outlined dense").classes("w-full")
                    county = ui.select(["Pinellas", "Hillsborough", "Pasco"], label="County", value=state.get("county", "Pinellas")).props("outlined dense").classes("w-full")
                    status = ui.label("").classes("muted")

                    async def do_lookup() -> None:
                        state["parcel_id"] = str(parcel.value or "").strip(); state["county"] = str(county.value or "Pinellas")
                        persist()
                        if not state["parcel_id"]: ui.notify("Enter Parcel/Folio", type="warning"); return
                        status.text = f"Looking up {state['county']} - {state['parcel_id']}"; status.update()
                        try: r = await run.io_bound(lookup_property, state["county"], state["parcel_id"])
                        except Exception as ex: status.text = f"Lookup failed: {ex}"; status.update(); ui.notify("Lookup failed", type="negative"); return
                        if not r: status.text = "No match found"; status.update(); ui.notify("No match found", type="warning"); return
                        for key in ["owner", "city", "address", "acreage", "existing_condition"]: state[key] = r.get(key, "")
                        autofill(state, r.get("source_url", ""))
                        persist(); dd_form.refresh(); sir_lookup_table.refresh(); status.text = "Lookup OK"; status.update(); ui.notify("Property data retrieved", type="positive")

                    ui.button("LOOKUP PROPERTY DATA", on_click=do_lookup, color="primary").classes("w-full")
                    ui.label("Left column is prefilled from the SIR table; right column updates from lookup output.").classes("muted")
                with ui.column().classes("col-8"):
                    sir_lookup_table()
        with ui.tab_panel(tab2):
            dd_form()
        with ui.tab_panel(tab3):
            def download_json() -> None:
                ensure_dd(state); p = Path(tempfile.gettempdir()) / "site-data-export.json"; p.write_text(json.dumps(state, indent=2), encoding="utf-8"); ui.download(p)
            def download_csv() -> None:
                ensure_dd(state); tk = state.get("template", "sir"); tpl = TEMPLATES.get(tk, TEMPLATES["sir"]); b = io.StringIO(); w = csv.writer(b, lineterminator="\n")
                w.writerow(["template", "section", "field", "value", "source_url", "accessed", "notes"])
                for sec in tpl["sections"]:
                    for fk, label in sec["rows"]:
                        c = state["dd"][tk][sec["id"]][fk]; w.writerow([tk, sec["title"], label, c.get("value", ""), c.get("source_url", ""), c.get("accessed", ""), c.get("notes", "")])
                p = Path(tempfile.gettempdir()) / "site-data-export.csv"; p.write_text(b.getvalue(), encoding="utf-8"); ui.download(p)
            def reset_user() -> None: app.storage.user.pop("site_dd", None); ui.run_javascript("location.reload()")
            with ui.row().classes("gap-2"):
                ui.button("Download JSON", on_click=download_json, color="primary"); ui.button("Download CSV", on_click=download_csv); ui.button("Reset This User", on_click=reset_user)


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), reload=not os.environ.get("ENV", "").lower().startswith("prod"), show=not os.environ.get("ENV", "").lower().startswith("prod"), storage_secret=os.environ.get("NICEGUI_SECRET_KEY", "dev-secret-change-me"), title="Site Data Due Diligence")
