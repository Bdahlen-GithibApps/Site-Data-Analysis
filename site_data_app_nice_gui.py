from __future__ import annotations

import datetime as dt
import os
import re
from typing import Any

import requests
from nicegui import app, run, ui

SIR_SECTIONS: list[dict[str, Any]] = [
    {
        "id": "site",
        "title": "Site Information",
        "rows": [
            ("owner_name", "Owner / Parcel Name"),
            ("parcel_address", "Parcel Address"),
            ("county", "County"),
            ("site_acreage", "Site Acreage"),
            ("existing_condition", "Existing Condition"),
        ],
    },
    {
        "id": "zoning",
        "title": "Zoning",
        "rows": [
            ("authority", "Authority"),
            ("existing_zoning_plan", "Existing Zoning/Comp Plan"),
            ("proposed_zoning", "Proposed Zoning"),
        ],
    },
    {
        "id": "env",
        "title": "Environmental",
        "rows": [
            ("wetlands_flood", "Wetlands/Flood Plains"),
            ("storm_treatment", "Storm Water Requirements"),
        ],
    },
    {
        "id": "perm",
        "title": "Permitting",
        "rows": [
            ("local_permits", "Local"),
            ("wmd", "WMD"),
            ("fdep", "FDEP"),
            ("fdot", "FDOT"),
        ],
    },
]

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
    ".muted{color:#5a647f;font-size:12px}"
    ".sir-table{width:100%;border-collapse:collapse;table-layout:fixed}"
    ".sir-table th,.sir-table td{border:1px solid #c9c9cf;padding:8px;vertical-align:top;font-size:13px;line-height:1.35;white-space:pre-wrap}"
    ".sir-table th{background:#f1f2f5;text-align:left;font-weight:700}"
    ".sir-section{background:#e4e7ec;font-weight:700}",
    shared=True,
)

_NON_DIGIT = re.compile(r"[^0-9]")
_S = requests.Session()


def digits_only(v: str) -> str:
    return _NON_DIGIT.sub("", str(v or ""))


def first_value(v: str) -> str:
    parts = [p.strip() for p in str(v or "").split(",") if p.strip()]
    return parts[0] if parts else ""


def sql_quote(v: str) -> str:
    return "'" + str(v or "").replace("'", "''") + "'"


def fmt_acres(v: Any) -> str:
    try:
        return f"{float(v):.2f}"
    except Exception:
        return "" if v is None else str(v).strip()


def _q(url: str, where: str, out_fields: list[str]) -> dict[str, Any]:
    response = _S.get(
        url,
        params={
            "where": where,
            "outFields": ",".join(out_fields),
            "returnGeometry": "false",
            "f": "json",
        },
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data["error"].get("message", "ArcGIS error"))
    return data


def lookup_property(county: str, parcel_raw: str) -> dict[str, Any]:
    parcel = first_value(parcel_raw)
    if not parcel:
        raise ValueError("Parcel/Folio is required")
    digits = digits_only(parcel)
    county = (county or "Pinellas").strip()

    if county == "Pinellas":
        url = "https://egis.pinellas.gov/gis/rest/services/PublicWebGIS/Parcels/MapServer/1/query"
        where = (
            f"PARCELID_DSP1={sql_quote(parcel)} OR PARCELID_DSP2={sql_quote(parcel)}"
            + (f" OR STRAP={sql_quote(digits)} OR PARCELID={sql_quote(digits)}" if digits else "")
        )
        data = _q(
            url,
            where,
            [
                "SITE_NUM",
                "SITE_ADDRESS",
                "SITE_CITY",
                "SITE_STATE",
                "SITE_ZIP",
                "OWNER1",
                "OWNER2",
                "USE_CODE",
                "LAND_USE_CODE",
                "Acres",
            ],
        )
        features = data.get("features") or []
        if not features:
            return {}
        attrs = features[0].get("attributes") or {}
        street = " ".join(
            [str(attrs.get("SITE_NUM") or "").strip(), str(attrs.get("SITE_ADDRESS") or "").strip()]
        ).strip()
        city = str(attrs.get("SITE_CITY") or "").strip()
        state = str(attrs.get("SITE_STATE") or "FL").strip() or "FL"
        zip_code = str(attrs.get("SITE_ZIP") or "").strip()
        return {
            "owner": " ".join([str(attrs.get("OWNER1") or "").strip(), str(attrs.get("OWNER2") or "").strip()]).strip(),
            "city": city,
            "address": ", ".join([p for p in [street, city, f"{state} {zip_code}".strip()] if p]),
            "acreage": fmt_acres(attrs.get("Acres")),
            "existing_condition": str(attrs.get("USE_CODE") or attrs.get("LAND_USE_CODE") or "").strip(),
            "source_url": "https://egis.pinellas.gov/gis/rest/services/PublicWebGIS/Parcels/MapServer/1",
        }

    if county == "Hillsborough":
        url = "https://arcgis.tampagov.net/arcgis/rest/services/Parcels/TaxParcel/MapServer/0/query"
        where = f"FOLIO={sql_quote(parcel)} OR PIN={sql_quote(parcel)}" + (
            f" OR STRAP={sql_quote(digits)}" if digits else ""
        )
        data = _q(url, where, ["OWNER", "SITE_ADDR", "SITE_CITY", "SITE_ZIP", "ACREAGE", "TYPE", "DOR_C"])
        features = data.get("features") or []
        if not features:
            return {}
        attrs = features[0].get("attributes") or {}
        city = str(attrs.get("SITE_CITY") or "").strip()
        zip_code = str(attrs.get("SITE_ZIP") or "").strip()
        return {
            "owner": str(attrs.get("OWNER") or "").strip(),
            "city": city,
            "address": ", ".join(
                [p for p in [str(attrs.get("SITE_ADDR") or "").strip(), city, f"FL {zip_code}".strip()] if p]
            ),
            "acreage": fmt_acres(attrs.get("ACREAGE")),
            "existing_condition": str(attrs.get("TYPE") or attrs.get("DOR_C") or "").strip(),
            "source_url": "https://arcgis.tampagov.net/arcgis/rest/services/Parcels/TaxParcel/MapServer/0",
        }

    if county == "Pasco":
        url = "https://maps.pascopa.com/arcgis/rest/services/Parcels/MapServer/3/query"
        data = _q(
            url,
            f"ParcelID={sql_quote(parcel)}",
            [
                "NAD_NAME_1",
                "NAD_NAME_2",
                "PHYS_STREET",
                "PHYS_CITY",
                "PHYS_STATE",
                "PHYS_ZIP",
                "TR_AC",
                "VAL_ACRES",
                "SALE_VAC_IMP",
                "DIR_CLASS",
            ],
        )
        features = data.get("features") or []
        if not features:
            return {}
        attrs = features[0].get("attributes") or {}
        city = str(attrs.get("PHYS_CITY") or "").strip()
        state = str(attrs.get("PHYS_STATE") or "FL").strip() or "FL"
        zip_code = str(attrs.get("PHYS_ZIP") or "").strip()
        existing = " | ".join(
            [x for x in [str(attrs.get("SALE_VAC_IMP") or "").strip(), str(attrs.get("DIR_CLASS") or "").strip()] if x]
        )
        acres = fmt_acres(attrs.get("TR_AC") if attrs.get("TR_AC") is not None else attrs.get("VAL_ACRES"))
        return {
            "owner": " ".join([str(attrs.get("NAD_NAME_1") or "").strip(), str(attrs.get("NAD_NAME_2") or "").strip()]).strip(),
            "city": city,
            "address": ", ".join([p for p in [str(attrs.get("PHYS_STREET") or "").strip(), city, f"{state} {zip_code}".strip()] if p]),
            "acreage": acres,
            "existing_condition": existing,
            "source_url": "https://maps.pascopa.com/arcgis/rest/services/Parcels/MapServer/3",
        }

    raise ValueError(f"Unsupported county: {county}")


def default_state() -> dict[str, Any]:
    return {
        "parcel_id": "",
        "county": "Pinellas",
        "owner": "",
        "city": "",
        "address": "",
        "acreage": "",
        "existing_condition": "",
        "source_url": "",
        "accessed": "",
    }


def sir_prefill_value(section_id: str, field_key: str, fallback_label: str) -> str:
    return SIR_PREFILL.get(section_id, {}).get(field_key, fallback_label)


def lookup_value(state: dict[str, Any], section_id: str, field_key: str) -> str:
    if section_id != "site":
        return ""
    mapping = {
        "owner_name": "owner",
        "parcel_address": "address",
        "county": "county",
        "site_acreage": "acreage",
        "existing_condition": "existing_condition",
    }
    state_key = mapping.get(field_key)
    return str(state.get(state_key, "") if state_key else "")


@ui.page("/")
def main() -> None:
    state = app.storage.user.get("site_lookup")
    if not isinstance(state, dict):
        state = default_state()
    app.storage.user["site_lookup"] = state

    def persist() -> None:
        app.storage.user["site_lookup"] = state

    @ui.refreshable
    def sir_lookup_table() -> None:
        with ui.element("table").classes("sir-table q-mt-sm"):
            with ui.element("thead"):
                with ui.element("tr"):
                    with ui.element("th"):
                        ui.label("SIR Section / Field")
                    with ui.element("th"):
                        ui.label("SIR Table Value (Prefilled)")
                    with ui.element("th"):
                        ui.label("Lookup Result")
            with ui.element("tbody"):
                for section in SIR_SECTIONS:
                    with ui.element("tr"):
                        with ui.element("td").classes("sir-section").props("colspan=3"):
                            ui.label(section["title"])
                    for field_key, label in section["rows"]:
                        left = sir_prefill_value(section["id"], field_key, label)
                        right = lookup_value(state, section["id"], field_key)
                        with ui.element("tr"):
                            with ui.element("td"):
                                ui.label(label)
                            with ui.element("td"):
                                ui.label(left)
                            with ui.element("td"):
                                ui.label(right if right.strip() else "-")

    ui.label("Site Data Due Diligence").classes("text-h4")
    with ui.row().classes("w-full"):
        with ui.column().classes("col-4"):
            parcel = ui.input("Parcel # / Folio", value=state.get("parcel_id", "")).props("outlined dense").classes("w-full")
            county = ui.select(["Pinellas", "Hillsborough", "Pasco"], label="County", value=state.get("county", "Pinellas")).props("outlined dense").classes("w-full")
            status = ui.label("").classes("muted")

            async def do_lookup() -> None:
                state["parcel_id"] = str(parcel.value or "").strip()
                state["county"] = str(county.value or "Pinellas")
                persist()
                if not state["parcel_id"]:
                    ui.notify("Enter Parcel/Folio", type="warning")
                    return
                status.text = f"Looking up {state['county']} - {state['parcel_id']}"
                status.update()
                try:
                    result = await run.io_bound(lookup_property, state["county"], state["parcel_id"])
                except Exception as ex:
                    status.text = f"Lookup failed: {ex}"
                    status.update()
                    ui.notify("Lookup failed", type="negative")
                    return
                if not result:
                    status.text = "No match found"
                    status.update()
                    ui.notify("No match found", type="warning")
                    return
                for key in ["owner", "city", "address", "acreage", "existing_condition", "source_url"]:
                    state[key] = result.get(key, "")
                state["accessed"] = dt.datetime.now().strftime("%b %d, %Y")
                persist()
                sir_lookup_table.refresh()
                status.text = "Lookup OK"
                status.update()
                ui.notify("Property data retrieved", type="positive")

            ui.button("LOOKUP PROPERTY DATA", on_click=do_lookup, color="primary").classes("w-full")
            ui.label("Single-tab version: only Lookup behavior and lookup results table are included.").classes("muted")
        with ui.column().classes("col-8"):
            sir_lookup_table()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        reload=not os.environ.get("ENV", "").lower().startswith("prod"),
        show=not os.environ.get("ENV", "").lower().startswith("prod"),
        storage_secret=os.environ.get("NICEGUI_SECRET_KEY", "dev-secret-change-me"),
        title="Site Data Due Diligence",
    )
