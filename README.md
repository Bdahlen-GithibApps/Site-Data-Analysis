# Site Data — Due Diligence (NiceGUI)

A Python + NiceGUI app for property due diligence in **Pinellas, Hillsborough, and Pasco** counties.

The UI is modeled after the **DS Proposal Generator** “Tab 1” style:
- Two-column intake + lookup summary
- Template-driven DD sections (Collective SIR Table + Typical Due Diligence)
- Export JSON/CSV

## Features
- Parcel/Folio + County lookup (public ArcGIS REST services)
- Auto-fill key fields (Owner, Address, City/Jurisdiction, Site Acreage, Existing Condition proxy)
- Template-driven due diligence entry
- JSON and CSV export
- Per-user saved state via NiceGUI storage

## Local development

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open the URL printed in the terminal.

## Environment variables
- `NICEGUI_SECRET_KEY` (required in production): used to secure NiceGUI storage.
- `ENV=prod` (optional): disables auto-reload.
- `PORT` (set by hosting platforms like Render).

## Deploy to Render (simple)
- Create a new **Python Web Service**
- Build command: `pip install -r requirements.txt`
- Start command: `python app.py`
- Set env vars:
  - `ENV=prod`
  - `NICEGUI_SECRET_KEY=<random>`

> Note: Render provides `PORT` automatically.

## Security notes (high level)
- Lookups call **public county GIS endpoints**.
- Avoid entering confidential client data if you do not intend to persist it.
- By default, NiceGUI saves per-user state server-side in `.nicegui/` (or Redis if you configure it).
