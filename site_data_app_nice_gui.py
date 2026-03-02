"""Site Data — Due Diligence (NiceGUI)

This app is a Python/NiceGUI rewrite of the original single-file HTML prototype.
UI layout is intentionally modeled after the DS Proposal Generator "Tab 1" style
(two-column intake with section cards).

Notes:
- County lookups use public ArcGIS REST services (no API keys).
- Persistence uses NiceGUI storage. By default, `app.storage.user` is stored
  server-side in the `.nicegui` folder.

Run locally:
  pip install -r requirements.txt
  python app.py

Deploy (e.g., Render):
- Set environment variables:
    PORT=<provided by host>
    NICEGUI_SECRET_KEY=<strong random string>
    ENV=prod
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from nicegui import app, run, ui


# -----------------------------------------------------------------------------
# Templates (ported from the HTML prototype)
# -----------------------------------------------------------------------------

TEMPLATES: Dict[str, Dict[str, Any]] = {
    "sir": {
        "name": "Collective SIR Table",
        "sections": [
            {
                "id": "site",
                "title": "Site Information",
                "desc": "Identifiers + existing condition.",
                "rows": [
                    ("owner_name", "Owner / Parcel Name"),
                    ("parcel_address", "Parcel Address"),
                    ("county", "County (jurisdiction)"),
                    ("site_acreage", "Site Acreage (gross)"),
                    ("existing_condition", "Existing Condition of Site"),
                ],
            },
            {
                "id": "zoning",
                "title": "Zoning",
                "desc": "Zoning / comp plan / proposed changes.",
                "rows": [
                    ("authority", "Jurisdictions having Authority"),
                    ("existing_zoning_plan", "Existing Zoning and Comp Plan Designation"),
                    ("proposed_zoning", "Proposed Zoning / Designation"),
                ],
            },
            {
                "id": "env",
                "title": "Environmental",
                "desc": "Flood and wetlands screening.",
                "rows": [
                    ("wetlands_flood", "Wetlands or Flood Plains Present"),
                    ("storm_treatment", "Storm Water Treatment Requirements"),
                ],
            },
            {
                "id": "perm",
                "title": "Permitting",
                "desc": "Local + WMD + FDEP + FDOT (if applicable).",
                "rows": [
                    ("local_permits", "Local permitting path"),
                    ("wmd", "WMD permitting (ERP/exemption/mod)"),
                    ("fdep", "FDEP permits or records"),
                    ("fdot", "FDOT permits (if state road)"),
                ],
            },
        ],
    },
    "typical": {
        "name": "Typical Due Diligence",
        "sections": [
            {
                "id": "gpi",
                "title": "General Project Information",
                "desc": "Location and proposed scope.",
                "rows": [
                    ("owner_name", "Owner / Parcel Name"),
                    ("project_location", "Project Location"),
                    ("current_use", "Current Use"),
                    ("proposed_use", "Proposed Use"),
                    ("project_area", "Proposed Project Area (acres)"),
                ],
            },
            {
                "id": "existing",
                "title": "Existing Conditions",
                "desc": "Floodplain and access snapshot.",
                "rows": [
                    ("floodplain", "Floodplain (FIRM panel and zone)"),
                    ("wetlands", "Wetlands (screening notes)"),
                    ("access", "Access (roads and constraints)"),
                ],
            },
            {
                "id": "zlu",
                "title": "Zoning and Land Use",
                "desc": "FLU and zoning basics.",
                "rows": [
                    ("flu", "Future Land Use (FLU)"),
                    ("zoning", "Current Zoning (base and overlays)"),
                    ("building_requirements", "Key standards (height, setbacks, FAR)"),
                ],
            },
            {
                "id": "perm",
                "title": "Permitting",
                "desc": "Local process + SWFWMD + FDEP.",
                "rows": [
                    ("local", "Local permitting process and timeline"),
                    ("swfwmd", "SWFWMD (exemption or ERP)"),
                    ("fdep", "FDEP (NOI or NPDES)"),
                ],
            },
        ],
    },
}


# -----------------------------------------------------------------------------
# Styling (inspired by DS Proposal Generator tab 1)
# -----------------------------------------------------------------------------

ui.add_css(
    """
:root {
  --bg: #0b1020;
  --panel: #111833;
  --panel2: #0f1530;
  --text: #e7ecff;
  --muted: #a9b2d6;
  --border: rgba(231,236,255,.14);
  --accent: #7aa2ff;
  --radius: 16px;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f6f7fb;
    --panel: #ffffff;
    --panel2: #f2f4ff;
    --text: #0b1020;
    --muted: #4b5475;
    --border: rgba(11,16,32,.12);
  }
}
html, body { background: var(--bg); color: var(--text); }
.q-page { background: var(--bg) !important; }

.tabs-left .q-tabs__content { gap: 8px; }
.tabs-left .q-tab { border: 1px solid var(--border); border-radius: 12px; min-height: 36px; }
.tabs-left .q-tab--active { background: rgba(122,162,255,.16); border-color: rgba(122,162,255,.45); }

.tab-card { background: transparent !important; box-shadow: none !important; border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; }
.section-card { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; box-shadow: none; padding: 14px; }

.section-title { font-weight: 900; font-size: 14px; margin: 0 0 8px 0; }
.muted { color: var(--muted); font-size: 12px; }

.info-box { border: 1px dashed var(--border); border-radius: 14px; padding: 10px 12px; background: rgba(0,0,0,.04); color: var(--muted); }

.lookup-summary-field { width: 100%; }
.dd-textarea .q-field__control { min-height: 110px !important; }

.center-select .q-field__control { justify-content: center; }
.center-select .q-field__native { text-align: center; }

/* Make inputs look consistent in dark mode */
.q-field--outlined .q-field__control:before { border-color: var(--border) !important; }
.q-field--outlined .q-field__control { border-radius: 12px; }

.q-btn { border-radius: 12px; font-weight: 800; }
"""
)


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

_RE_NON_DIGIT = re.compile(r"[^0-9]")


def now_ny_string() -> str:
    """Best-effort Eastern time string without external deps."""
    # NOTE: We avoid pytz/zoneinfo complexity here; date is good enough for auditing.
    return _dt.datetime.now().strftime("%b %d, %Y")


def digits_only(value: str) -> str:
    return _RE_NON_DIGIT.sub("", str(value or ""))


def first_value(value: str) -> str:
    parts = [p.strip() for p in str(value or "").split(",") if p.strip()]
    return parts[0] if parts else ""


def sql_quote(value: str) -> str:
    # ArcGIS uses SQL-like where clauses.
    safe = str(value or "").replace("'", "''")
    return f"'{safe}'"


def fmt_acres(value: Any) -> str:
    if value is None:
        return ""
    try:
        n = float(value)
        if n != n:
            return ""
        return f"{n:.2f}"
    except Exception:
        s = str(value).strip()
        return s


# -----------------------------------------------------------------------------
# Public lookups (ArcGIS REST)
# -----------------------------------------------------------------------------

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": "SiteDataDueDiligence/1.0 (+internal testing)",
        "Accept": "application/json,text/plain,*/*",
    }
)


def _arcgis_query(query_url: str, where: str, out_fields: List[str], timeout_s: int = 20) -> Dict[str, Any]:
    params = {
        "where": where,
        "outFields": ",".join(out_fields) if out_fields else "*",
        "returnGeometry": "false",
        "f": "json",
    }
    resp = _SESSION.get(query_url, params=params, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        msg = data["error"].get("message", "ArcGIS error")
        raise RuntimeError(msg)
    return data


def lookup_property(county: str, parcel_id_raw: str) -> Dict[str, Any]:
    """Lookup owner/address/city/acreage using public county GIS services.

    Returns a dict with keys:
      owner, city, address, acreage, existing_condition, source_url, raw

    Raises on network/parse errors.
    """
    county = (county or "").strip() or "Pinellas"
    parcel_id = first_value(parcel_id_raw)
    if not parcel_id:
        raise ValueError("Parcel/Folio is required")

    digits = digits_only(parcel_id)

    if county == "Pinellas":
        # Pinellas PublicWebGIS/Parcels layer 1
        query_url = "https://egis.pinellas.gov/gis/rest/services/PublicWebGIS/Parcels/MapServer/1/query"
        where = f"PARCELID_DSP1={sql_quote(parcel_id)} OR PARCELID_DSP2={sql_quote(parcel_id)}"
        if digits:
            where += f" OR STRAP={sql_quote(digits)} OR PARCELID={sql_quote(digits)}"
        out = [
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
            "PARCELID_DSP1",
            "PARCELID_DSP2",
            "STRAP",
            "PARCELID",
        ]
        data = _arcgis_query(query_url, where, out)
        feats = (data.get("features") or []) if isinstance(data, dict) else []
        if not feats:
            return {}
        a = feats[0].get("attributes") or {}
        street = " ".join([str(a.get("SITE_NUM") or "").strip(), str(a.get("SITE_ADDRESS") or "").strip()]).strip()
        city = str(a.get("SITE_CITY") or "").strip()
        st = str(a.get("SITE_STATE") or "FL").strip() or "FL"
        zip_code = str(a.get("SITE_ZIP") or "").strip()
        owner = " ".join([str(a.get("OWNER1") or "").strip(), str(a.get("OWNER2") or "").strip()]).strip()
        acres = fmt_acres(a.get("Acres"))
        existing = str(a.get("USE_CODE") or a.get("LAND_USE_CODE") or "").strip()

        full_addr = ", ".join([p for p in [street, city, (f"{st} {zip_code}".strip() if zip_code else st)] if p])
        return {
            "owner": owner,
            "city": city,
            "address": full_addr,
            "acreage": acres,
            "existing_condition": (f"Use code: {existing}" if existing else ""),
            "source_url": "https://egis.pinellas.gov/gis/rest/services/PublicWebGIS/Parcels/MapServer/1",
            "raw": a,
        }

    if county == "Hillsborough":
        # TampaGIS Parcels/TaxParcel layer 0
        query_url = "https://arcgis.tampagov.net/arcgis/rest/services/Parcels/TaxParcel/MapServer/0/query"
        where = f"FOLIO={sql_quote(parcel_id)} OR PIN={sql_quote(parcel_id)}"
        if digits:
            where += f" OR STRAP={sql_quote(digits)}"
        out = [
            "FOLIO",
            "PIN",
            "STRAP",
            "OWNER",
            "SITE_ADDR",
            "SITE_CITY",
            "SITE_ZIP",
            "ACREAGE",
            "TYPE",
            "DOR_C",
            "VI",
        ]
        data = _arcgis_query(query_url, where, out)
        feats = (data.get("features") or []) if isinstance(data, dict) else []
        if not feats:
            return {}
        a = feats[0].get("attributes") or {}
        street = str(a.get("SITE_ADDR") or "").strip()
        city = str(a.get("SITE_CITY") or "").strip()
        zip_code = str(a.get("SITE_ZIP") or "").strip()
        owner = str(a.get("OWNER") or "").strip()
        acres = fmt_acres(a.get("ACREAGE"))
        existing = str(a.get("TYPE") or a.get("DOR_C") or "").strip()
        full_addr = ", ".join([p for p in [street, city, (f"FL {zip_code}".strip() if zip_code else "FL")] if p])
        return {
            "owner": owner,
            "city": city,
            "address": full_addr,
            "acreage": acres,
            "existing_condition": (f"Parcel description: {existing}" if existing else ""),
            "source_url": "https://arcgis.tampagov.net/arcgis/rest/services/Parcels/TaxParcel/MapServer/0",
            "raw": a,
        }

    if county == "Pasco":
        # Pasco Parcels (Clickable Info) layer 3
        query_url = "https://maps.pascopa.com/arcgis/rest/services/Parcels/MapServer/3/query"
        where = f"ParcelID={sql_quote(parcel_id)}"
        out = [
            "ParcelID",
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
        ]
        data = _arcgis_query(query_url, where, out)
        feats = (data.get("features") or []) if isinstance(data, dict) else []
        if not feats:
            return {}
        a = feats[0].get("attributes") or {}
        street = str(a.get("PHYS_STREET") or "").strip()
        city = str(a.get("PHYS_CITY") or "").strip()
        st = str(a.get("PHYS_STATE") or "FL").strip() or "FL"
        zip_code = str(a.get("PHYS_ZIP") or "").strip()
        owner = " ".join([str(a.get("NAD_NAME_1") or "").strip(), str(a.get("NAD_NAME_2") or "").strip()]).strip()
        acres = fmt_acres(a.get("TR_AC") if a.get("TR_AC") is not None else a.get("VAL_ACRES"))

        vac_imp = str(a.get("SALE_VAC_IMP") or "").strip()
        dir_class = str(a.get("DIR_CLASS") or "").strip()
        existing_bits = []
        if vac_imp:
            existing_bits.append(f"Vac/Imp flag: {vac_imp}")
        if dir_class:
            existing_bits.append(f"Class: {dir_class}")
        existing = " • ".join(existing_bits)

        full_addr = ", ".join([p for p in [street, city, (f"{st} {zip_code}".strip() if zip_code else st)] if p])
        return {
            "owner": owner,
            "city": city,
            "address": full_addr,
            "acreage": acres,
            "existing_condition": existing,
            "source_url": "https://maps.pascopa.com/arcgis/rest/services/Parcels/MapServer/3",
            "raw": a,
        }

    raise ValueError(f"Unsupported county: {county}")


# -----------------------------------------------------------------------------
# State + autofill logic
# -----------------------------------------------------------------------------


def _default_state() -> Dict[str, Any]:
    return {
        "parcel_id": "",
        "county": "Pinellas",
        "owner": "",
        "city": "",
        "address": "",
        "acreage": "",
        "existing_condition": "",
        "template": "sir",
        "updated_at": _dt.datetime.utcnow().isoformat(),
        "last_lookup": None,
        "dd": {},
    }


def ensure_dd(state: Dict[str, Any]) -> None:
    dd = state.setdefault("dd", {})
    for t_key, tpl in TEMPLATES.items():
        t_bag = dd.setdefault(t_key, {})
        for sec in tpl["sections"]:
            s_id = sec["id"]
            s_bag = t_bag.setdefault(s_id, {})
            for field_key, label in sec["rows"]:
                if field_key not in s_bag:
                    s_bag[field_key] = {
                        "label": label,
                        "value": "",
                        "auto": False,
                        "source_url": "",
                        "accessed": "",
                        "notes": "",
                    }


def _maybe_autofill(cell: Dict[str, Any], value: str, *, source_url: str = "") -> None:
    value = str(value or "").strip()
    if not value:
        return
    cur = str(cell.get("value") or "").strip()
    if not cur or bool(cell.get("auto")):
        cell["value"] = value
        cell["auto"] = True
        if source_url:
            cell["source_url"] = source_url
        cell["accessed"] = now_ny_string()


def autofill_from_summary(state: Dict[str, Any], *, source_url: str = "") -> None:
    ensure_dd(state)
    owner = state.get("owner", "")
    addr = state.get("address", "")
    county = state.get("county", "")
    acres = state.get("acreage", "")
    existing = state.get("existing_condition", "")

    # Collective SIR - Site Information
    _maybe_autofill(state["dd"]["sir"]["site"]["owner_name"], owner, source_url=source_url)
    _maybe_autofill(state["dd"]["sir"]["site"]["parcel_address"], addr, source_url=source_url)
    _maybe_autofill(state["dd"]["sir"]["site"]["county"], county, source_url=source_url)
    _maybe_autofill(state["dd"]["sir"]["site"]["site_acreage"], acres, source_url=source_url)
    _maybe_autofill(state["dd"]["sir"]["site"]["existing_condition"], existing, source_url=source_url)

    # Typical DD - General Project Information
    _maybe_autofill(state["dd"]["typical"]["gpi"]["owner_name"], owner, source_url=source_url)
    _maybe_autofill(state["dd"]["typical"]["gpi"]["project_location"], addr, source_url=source_url)
    _maybe_autofill(state["dd"]["typical"]["gpi"]["current_use"], existing, source_url=source_url)
    _maybe_autofill(state["dd"]["typical"]["gpi"]["project_area"], acres, source_url=source_url)


def build_sources(county: str, parcel_id: str) -> List[Tuple[str, str]]:
    """(label, url) list."""
    parcel_id = first_value(parcel_id)
    if county == "Pinellas":
        return [
            ("PCPAO Quick Search", "https://www.pcpao.gov/quick-search"),
            ("Pinellas Parcel Viewer", "https://legacy.pcpao.org/PaoTpv/"),
        ]
    if county == "Hillsborough":
        return [
            ("HCPA Home", "https://www.hcpafl.org/"),
            ("TampaGIS TaxParcel", "https://arcgis.tampagov.net/arcgis/rest/services/Parcels/TaxParcel/MapServer"),
        ]
    if county == "Pasco":
        return [
            ("Pasco PA Search", "https://search.pascopa.com/"),
            ("Pasco PA Parcel Card", f"https://search.pascopa.com/parcel.aspx?parcel={parcel_id}"),
            ("Pasco Parcels (ArcGIS)", "https://maps.pascopa.com/arcgis/rest/services/Parcels/MapServer"),
        ]
    return []


# -----------------------------------------------------------------------------
# UI helpers
# -----------------------------------------------------------------------------


def labeled_input(
    label: str,
    *,
    value: str = "",
    placeholder: str = "",
    classes: str = "",
    props: str = "",
) -> ui.input:
    return (
        ui.input(label=label, value=value, placeholder=placeholder)
        .props(f"outlined dense stack-label {props}".strip())
        .classes(classes)
    )


def labeled_select(
    label: str,
    options: Any,
    *,
    value: Any = None,
    classes: str = "",
    props: str = "",
) -> ui.select:
    return (
        ui.select(options=options, label=label, value=value)
        .props(f"outlined dense stack-label {props}".strip())
        .classes(classes)
    )


def labeled_textarea(
    label: str,
    *,
    value: str = "",
    placeholder: str = "",
    classes: str = "",
    props: str = "",
) -> ui.textarea:
    return (
        ui.textarea(label=label, value=value, placeholder=placeholder)
        .props(f"outlined dense stack-label autogrow {props}".strip())
        .classes(classes)
    )


def cell_is_textarea(section_id: str, field_key: str) -> bool:
    # Keep most fields single-line; allow multi-line for narrative fields.
    if section_id in {"env", "perm"}:
        return True
    if section_id == "zoning":
        return True
    if section_id == "existing":
        return True
    if section_id == "zlu":
        return True
    return field_key in {"existing_condition", "building_requirements"}


# -----------------------------------------------------------------------------
# Main page
# -----------------------------------------------------------------------------


@ui.page("/")
def main() -> None:
    store = app.storage.user
    state = store.get("site_dd")
    if not isinstance(state, dict):
        state = _default_state()
    ensure_dd(state)
    autofill_from_summary(state)
    store["site_dd"] = state

    def persist() -> None:
        state["updated_at"] = _dt.datetime.utcnow().isoformat()
        store["site_dd"] = state

    ui_refs: Dict[str, Any] = {}

    @ui.refreshable
    def dd_form() -> None:
        tpl_key = state.get("template", "sir")
        tpl = TEMPLATES.get(tpl_key, TEMPLATES["sir"])

        ui.label(f"{tpl['name']} — Data Entry").classes("text-h5 q-mb-sm")
        ui.label("These fields auto-fill where possible from the lookup summary.").classes("muted q-mb-md")

        for sec in tpl["sections"]:
            sec_id = sec["id"]
            with ui.card().classes("section-card w-full q-mt-md"):
                ui.label(sec["title"]).classes("section-title")
                if sec.get("desc"):
                    ui.label(sec["desc"]).classes("muted q-mb-sm")

                for field_key, label in sec["rows"]:
                    cell = state["dd"][tpl_key][sec_id][field_key]

                    def on_change(e: Any, *, t=tpl_key, s=sec_id, k=field_key) -> None:
                        c = state["dd"][t][s][k]
                        c["value"] = e.value
                        c["auto"] = False
                        persist()

                    if cell_is_textarea(sec_id, field_key):
                        inp = labeled_textarea(label, value=cell.get("value", ""), classes="w-full dd-textarea")
                    else:
                        inp = labeled_input(label, value=cell.get("value", ""), classes="w-full")

                    inp.on("change", on_change)

                    # lightweight source/notes (optional)
                    with ui.expansion("Source / Notes", icon="link").classes("q-mt-xs"):
                        src = labeled_input("Source URL", value=cell.get("source_url", ""), classes="w-full")
                        notes = labeled_textarea("Notes", value=cell.get("notes", ""), classes="w-full dd-textarea")

                        def on_src(e: Any, *, t=tpl_key, s=sec_id, k=field_key) -> None:
                            state["dd"][t][s][k]["source_url"] = e.value
                            persist()

                        def on_notes(e: Any, *, t=tpl_key, s=sec_id, k=field_key) -> None:
                            state["dd"][t][s][k]["notes"] = e.value
                            persist()

                        src.on("change", on_src)
                        notes.on("change", on_notes)

    # Header
    ui.label("Site Data — Due Diligence").classes("text-h4 q-mb-sm")
    ui.label("Parcel/Folio + County → Lookup → Auto-fill + Due Diligence entry").classes("muted q-mb-md")

    # Tabs (DS tab layout style)
    with ui.row().classes("w-full items-end justify-between q-mb-md"):
        with ui.column().classes("col"):
            with ui.tabs().classes("tabs-left") as tabs:
                tab1 = ui.tab("Lookup")
                tab2 = ui.tab("Due Diligence")
                tab3 = ui.tab("Export")

    with ui.tab_panels(tabs, value=tab1).classes("w-full"):
        # ------------------------------------------------------------------
        # Tab 1: Lookup (mimics DS Proposal Generator Tab 1 layout)
        # ------------------------------------------------------------------
        with ui.tab_panel(tab1):
            with ui.card().classes("w-full tab-card"):
                ui.label("Project Info — Intake (Lookup)").classes("text-h5 q-mb-md")
                with ui.row().classes("w-full items-start no-wrap gap-8"):
                    with ui.column().classes("col-6"):
                        with ui.card().classes("section-card w-full"):
                            ui.label("Property Lookup").classes("section-title")

                            with ui.row().classes("w-full items-start no-wrap gap-4"):
                                parcel_input = labeled_input(
                                    "Parcel # / Folio",
                                    value=state.get("parcel_id", ""),
                                    placeholder="e.g. 19-31-17-73166-001-0010",
                                    classes="col-8",
                                )
                                county_input = labeled_select(
                                    "County",
                                    ["Pinellas", "Hillsborough", "Pasco"],
                                    value=state.get("county", "Pinellas"),
                                    classes="col-4 center-select",
                                )

                            ui_refs["parcel_input"] = parcel_input
                            ui_refs["county_input"] = county_input

                            def _sync_parcel(e: Any) -> None:
                                state["parcel_id"] = e.value
                                persist()

                            def _sync_county(e: Any) -> None:
                                state["county"] = e.value
                                persist()

                            parcel_input.on("change", _sync_parcel)
                            county_input.on("change", _sync_county)

                            status = ui.label("").classes("info-box q-mt-md")
                            status.visible = False
                            ui_refs["status"] = status

                            async def do_lookup() -> None:
                                parcel_id = (parcel_input.value or "").strip()
                                county = (county_input.value or "Pinellas").strip()
                                state["parcel_id"] = parcel_id
                                state["county"] = county
                                persist()

                                if not parcel_id:
                                    ui.notify("Please enter a Parcel/Folio.", type="warning")
                                    return

                                status.text = f"Looking up {county} • {parcel_id} ..."
                                status.visible = True
                                status.update()

                                try:
                                    result = await run.io_bound(lookup_property, county, parcel_id)
                                except Exception as ex:
                                    state["last_lookup"] = {"ok": False, "error": str(ex), "at": _dt.datetime.utcnow().isoformat()}
                                    persist()
                                    status.text = f"Lookup failed: {ex}"
                                    status.update()
                                    ui.notify("Lookup failed", type="negative")
                                    return

                                if not result:
                                    state["last_lookup"] = {"ok": False, "error": "not_found", "at": _dt.datetime.utcnow().isoformat()}
                                    persist()
                                    status.text = "No match returned for that Parcel/Folio."
                                    status.update()
                                    ui.notify("No match found", type="warning")
                                    return

                                # Apply results
                                state["owner"] = result.get("owner", "")
                                state["city"] = result.get("city", "")
                                state["address"] = result.get("address", "")
                                state["acreage"] = result.get("acreage", "")
                                state["existing_condition"] = result.get("existing_condition", "")
                                state["last_lookup"] = {"ok": True, "source": result.get("source_url"), "at": _dt.datetime.utcnow().isoformat()}

                                # Autofill DD fields
                                autofill_from_summary(state, source_url=result.get("source_url", ""))
                                persist()

                                # Update UI summary fields
                                for k in ("owner", "city", "address", "acreage", "existing_condition"):
                                    if k in ui_refs:
                                        ui_refs[k].value = state.get(k, "")
                                        ui_refs[k].update()

                                status.text = f"Lookup OK • Source: {result.get('source_url','')}"
                                status.update()
                                ui.notify("Property data retrieved", type="positive")

                                # Keep DD tab in sync
                                dd_form.refresh()

                            ui.button("LOOKUP PROPERTY DATA", on_click=do_lookup, color="primary").classes("q-mt-md w-full")

                            def open_sources() -> None:
                                items = build_sources(state.get("county", ""), state.get("parcel_id", ""))
                                if not items:
                                    ui.notify("No sources configured", type="warning")
                                    return
                                # open the first, show list
                                label, url = items[0]
                                status.text = "Sources: " + " • ".join([f"{l}: {u}" for l, u in items])
                                status.visible = True
                                status.update()
                                ui.navigate.to(url, new_tab=True)

                            ui.button("OPEN SOURCES", on_click=open_sources).classes("q-mt-sm w-full")

                        with ui.card().classes("section-card q-mt-md w-full"):
                            ui.label("Lookup Summary (Auto-fills)").classes("section-title")

                            ui_refs["owner"] = labeled_input("Owner / Parcel Name", value=state.get("owner", ""), classes="lookup-summary-field")
                            ui_refs["city"] = labeled_input("City / Jurisdiction", value=state.get("city", ""), classes="lookup-summary-field")
                            ui_refs["address"] = labeled_input("Site Address", value=state.get("address", ""), classes="lookup-summary-field")
                            ui_refs["acreage"] = labeled_input("Site Acreage (gross)", value=state.get("acreage", ""), classes="lookup-summary-field")
                            ui_refs["existing_condition"] = labeled_input("Existing Condition", value=state.get("existing_condition", ""), classes="lookup-summary-field")

                            def _bind_summary(key: str) -> None:
                                ui_refs[key].on(
                                    "change",
                                    lambda e, k=key: (
                                        state.__setitem__(k, e.value),
                                        autofill_from_summary(state),
                                        persist(),
                                        dd_form.refresh(),
                                    ),
                                )

                            for _k in ("owner", "city", "address", "acreage", "existing_condition"):
                                _bind_summary(_k)

                    # Right column
                    with ui.column().classes("col-6"):
                        with ui.card().classes("section-card w-full"):
                            ui.label("Due Diligence Template").classes("section-title")
                            template_select = labeled_select(
                                "Template",
                                {"sir": "Collective SIR Table", "typical": "Typical Due Diligence"},
                                value=state.get("template", "sir"),
                                classes="w-full",
                            )

                            def on_tpl_change(e: Any) -> None:
                                state["template"] = e.value
                                persist()
                                dd_form.refresh()

                            template_select.on("change", on_tpl_change)
                            ui.label("Switch to the Due Diligence tab to edit all sections.").classes("muted q-mt-sm")

                        with ui.card().classes("section-card q-mt-md w-full"):
                            ui.label("Quick Links").classes("section-title")
                            links = build_sources(state.get("county", ""), state.get("parcel_id", ""))
                            if links:
                                for label, url in links:
                                    ui.link(label, url).props("target=_blank").classes("q-mb-xs")
                            else:
                                ui.label("Enter Parcel/Folio + County to see links.").classes("muted")

        # ------------------------------------------------------------------
        # Tab 2: Due Diligence (full form)
        # ------------------------------------------------------------------
        with ui.tab_panel(tab2):
            with ui.card().classes("w-full tab-card"):
                # Template selector here too (so you can change without going back)
                with ui.row().classes("w-full items-start gap-4"):
                    tpl_sel = labeled_select(
                        "Template",
                        {"sir": "Collective SIR Table", "typical": "Typical Due Diligence"},
                        value=state.get("template", "sir"),
                        classes="w-80",
                    )

                    def on_tpl_change2(e: Any) -> None:
                        state["template"] = e.value
                        persist()
                        dd_form.refresh()

                    tpl_sel.on("change", on_tpl_change2)
                    ui.label("Tip: after a lookup, Site Information fields are auto-filled.").classes("muted q-mt-md")

                dd_form()

        # ------------------------------------------------------------------
        # Tab 3: Export
        # ------------------------------------------------------------------
        with ui.tab_panel(tab3):
            with ui.card().classes("w-full tab-card"):
                ui.label("Export / Reset").classes("text-h5 q-mb-md")

                def download_json() -> None:
                    ensure_dd(state)
                    path = Path(tempfile.gettempdir()) / f"site-data-{state.get('parcel_id','').replace('/', '-')}-{now_ny_string().replace(' ', '-')}.json"
                    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
                    ui.download(path)

                def download_csv() -> None:
                    ensure_dd(state)
                    tpl_key = state.get("template", "sir")
                    tpl = TEMPLATES.get(tpl_key, TEMPLATES["sir"])
                    rows: List[List[str]] = [["template", "section", "field", "value", "source_url", "accessed", "notes"]]
                    for sec in tpl["sections"]:
                        s_id = sec["id"]
                        for field_key, label in sec["rows"]:
                            cell = state["dd"][tpl_key][s_id][field_key]
                            rows.append(
                                [
                                    tpl_key,
                                    sec["title"],
                                    label,
                                    str(cell.get("value", "")),
                                    str(cell.get("source_url", "")),
                                    str(cell.get("accessed", "")),
                                    str(cell.get("notes", "")),
                                ]
                            )

                    def esc(v: str) -> str:
                        v = str(v)
                        if any(ch in v for ch in [",", "
", "", '"']):
                            return '"' + v.replace('"', '""') + '"'
                        return v

                    csv_text = "
".join([",".join([esc(c) for c in r]) for r in rows])
                    path = Path(tempfile.gettempdir()) / f"site-data-{state.get('parcel_id','').replace('/', '-')}-{now_ny_string().replace(' ', '-')}.csv"
                    path.write_text(csv_text, encoding="utf-8")
                    ui.download(path)

                def reset() -> None:
                    store.pop("site_dd", None)
                    ui.run_javascript("location.reload()")

                with ui.row().classes("gap-4"):
                    ui.button("Download JSON", on_click=download_json, color="primary")
                    ui.button("Download CSV", on_click=download_csv)
                    ui.button("Reset This User", on_click=reset)

                ui.separator().classes("q-my-md")
                ui.label("Current storage location").classes("section-title")
                ui.label(
                    "NiceGUI user storage is saved server-side in the `.nicegui` folder (unless you configure Redis)."
                ).classes("muted")


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------


def _is_prod() -> bool:
    return os.environ.get("ENV", "dev").lower().startswith("prod")


if __name__ in {"__main__", "__mp_main__"}:
    port = int(os.environ.get("PORT", "8080"))
    secret = os.environ.get("NICEGUI_SECRET_KEY")
    if not secret:
        # Safe for local dev only; set NICEGUI_SECRET_KEY in production.
        secret = "dev-secret-change-me"

    ui.run(
        title="Site Data — Due Diligence",
        host="0.0.0.0",
        port=port,
        reload=not _is_prod(),
        show=not _is_prod(),
        storage_secret=secret,
    )

