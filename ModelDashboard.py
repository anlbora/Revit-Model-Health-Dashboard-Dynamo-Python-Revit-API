import sys
import clr
import datetime
from collections import defaultdict
import codecs

clr.AddReference("RevitServices")
from RevitServices.Persistence import DocumentManager

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import *

doc = DocumentManager.Instance.CurrentDBDocument
html_path = IN[0]

# ── Unit conversion helper ─────────────────────────────────────────────────────

def ft2_to_m2(val):
    return round(val * 0.0929, 2)

def ft_to_m(val):
    return round(val * 0.3048, 2)

# ── Collectors ────────────────────────────────────────────────────────────────

rooms = (FilteredElementCollector(doc)
    .OfCategory(BuiltInCategory.OST_Rooms)
    .WhereElementIsNotElementType().ToElements())

views = (FilteredElementCollector(doc)
    .OfClass(View).ToElements())

sheets = (FilteredElementCollector(doc)
    .OfClass(ViewSheet).ToElements())

cad_imports = (FilteredElementCollector(doc)
    .OfClass(ImportInstance).ToElements())

links = (FilteredElementCollector(doc)
    .OfClass(RevitLinkInstance).ToElements())

families = (FilteredElementCollector(doc)
    .OfClass(Family).ToElements())

text_notes = (FilteredElementCollector(doc)
    .OfClass(TextNote).ToElements())

warnings = doc.GetWarnings()

# ── Element schedule collectors ───────────────────────────────────────────────

walls = (FilteredElementCollector(doc)
    .OfCategory(BuiltInCategory.OST_Walls)
    .WhereElementIsNotElementType().ToElements())

floors = (FilteredElementCollector(doc)
    .OfCategory(BuiltInCategory.OST_Floors)
    .WhereElementIsNotElementType().ToElements())

ceilings = (FilteredElementCollector(doc)
    .OfCategory(BuiltInCategory.OST_Ceilings)
    .WhereElementIsNotElementType().ToElements())

materials = (FilteredElementCollector(doc)
    .OfClass(Material).ToElements())

# ── Analysis ──────────────────────────────────────────────────────────────────

SKIP_VIEW_TYPES = [ViewType.Legend, ViewType.Schedule,
                   ViewType.ProjectBrowser, ViewType.SystemBrowser,
                   ViewType.DrawingSheet, ViewType.Undefined]

views_no_template = []
views_on_sheets   = set()
non_template_views = []

for v in views:
    if v.IsTemplate:
        continue
    if v.ViewType in SKIP_VIEW_TYPES:
        continue
    non_template_views.append(v)
    if v.ViewTemplateId.IntegerValue == -1:
        views_no_template.append(v)

for sheet in sheets:
    for vid in sheet.GetAllPlacedViews():
        views_on_sheets.add(vid.IntegerValue)

unplaced_views = [v for v in non_template_views
                  if v.Id.IntegerValue not in views_on_sheets
                  and v.ViewType not in [ViewType.ThreeD]]

unplaced_rooms = [r for r in rooms if r.Location is None]
inplace_families = [f for f in families if f.IsInPlace]
dup_warnings = [w for w in warnings
                if "duplicate" in w.GetDescriptionText().lower()]

# ── Wall schedule analysis ─────────────────────────────────────────────────────

def get_param_double(elem, bip):
    p = elem.get_Parameter(bip)
    return p.AsDouble() if p and p.HasValue else 0.0

def get_param_string(elem, bip):
    p = elem.get_Parameter(bip)
    return p.AsString() or "" if p and p.HasValue else ""

def get_type_name(elem):
    t = doc.GetElement(elem.GetTypeId())

    if not t:
        return "Unknown"

    p = (
        t.LookupParameter("Type Name")
        or t.LookupParameter("Tip Adı")
    )

    if p:
        val = p.AsString()
        if val:
            return val

    return "Unknown"

def get_level_name(elem):
    lvl_id = elem.LevelId if hasattr(elem, "LevelId") else ElementId.InvalidElementId
    if lvl_id != ElementId.InvalidElementId:
        lvl = doc.GetElement(lvl_id)
        return lvl.Name if lvl else "—"
    return "—"

wall_rows = []
wall_type_totals = defaultdict(float)  # type_name -> total area m²

for w in walls:
    type_name = get_type_name(w)
    area_ft2  = get_param_double(w, BuiltInParameter.HOST_AREA_COMPUTED)
    length_ft = get_param_double(w, BuiltInParameter.CURVE_ELEM_LENGTH)
    height_ft = get_param_double(w, BuiltInParameter.WALL_USER_HEIGHT_PARAM)
    area_m2   = ft2_to_m2(area_ft2)
    length_m  = ft_to_m(length_ft)
    height_m  = ft_to_m(height_ft)
    level     = get_level_name(w)
    func_p    = w.get_Parameter(BuiltInParameter.FUNCTION_PARAM)
    func      = func_p.AsValueString() if func_p and func_p.HasValue else "—"
    wall_type_totals[type_name] += area_m2
    wall_rows.append([
        type_name, level, func,
        "{} m".format(length_m),
        "{} m".format(height_m),
        "{} m²".format(area_m2),
        w.Id.IntegerValue,
    ])

# Wall type summary rows (sorted by total area desc)
wall_summary_rows = sorted(
    [[t, "{} m²".format(round(a, 2)), 0] for t, a in wall_type_totals.items()],
    key=lambda x: float(x[1].replace(" m²", "")), reverse=True
)

# Add count
for row in wall_summary_rows:
    row[2] = sum(1 for w in wall_rows if w[0] == row[0])
wall_summary_rows = [[r[0], r[2], r[1]] for r in wall_summary_rows]

# ── Floor schedule analysis ────────────────────────────────────────────────────

floor_rows = []
floor_type_totals = defaultdict(float)

for f in floors:
    type_name = get_type_name(f)
    area_ft2  = get_param_double(f, BuiltInParameter.HOST_AREA_COMPUTED)
    thick_ft  = get_param_double(f, BuiltInParameter.FLOOR_ATTR_THICKNESS_PARAM)
    area_m2   = ft2_to_m2(area_ft2)
    thick_m   = ft_to_m(thick_ft)
    level     = get_level_name(f)
    floor_type_totals[type_name] += area_m2
    floor_rows.append([
        type_name, level,
        "{} mm".format(int(thick_m * 1000)),
        "{} m²".format(area_m2),
        f.Id.IntegerValue,
    ])

floor_summary_rows = sorted(
    [[t, sum(1 for r in floor_rows if r[0] == t), "{} m²".format(round(a, 2))]
     for t, a in floor_type_totals.items()],
    key=lambda x: float(x[2].replace(" m²", "")), reverse=True
)

# ── Ceiling schedule analysis ──────────────────────────────────────────────────

ceiling_rows = []
ceiling_type_totals = defaultdict(float)

for c in ceilings:
    type_name = get_type_name(c)
    area_ft2  = get_param_double(c, BuiltInParameter.HOST_AREA_COMPUTED)
    thick_ft  = get_param_double(c, BuiltInParameter.CEILING_THICKNESS)
    area_m2   = ft2_to_m2(area_ft2)
    thick_mm  = int(ft_to_m(thick_ft) * 1000)
    level     = get_level_name(c)
    ceiling_type_totals[type_name] += area_m2
    ceiling_rows.append([
        type_name, level,
        "{} mm".format(thick_mm) if thick_mm > 0 else "—",
        "{} m²".format(area_m2),
        c.Id.IntegerValue,
    ])

ceiling_summary_rows = sorted(
    [[t, sum(1 for r in ceiling_rows if r[0] == t), "{} m²".format(round(a, 2))]
     for t, a in ceiling_type_totals.items()],
    key=lambda x: float(x[2].replace(" m²", "")), reverse=True
)

# ── Material schedule analysis ─────────────────────────────────────────────────

material_rows = []
for m in materials:

    ap = m.AppearanceAssetId
    ap_name = "—"
    if ap != ElementId.InvalidElementId:
        ap_elem = doc.GetElement(ap)
        ap_name = ap_elem.Name if ap_elem else "—"


    color = m.Color
    if color.IsValid:
        hex_color = "#{:02X}{:02X}{:02X}".format(color.Red, color.Green, color.Blue)
        swatch = ('<span style="display:inline-block;width:12px;height:12px;'
                  'border-radius:3px;background:{clr};border:1px solid #ccc;'
                  'vertical-align:middle;margin-right:5px"></span>{clr}'
                  ).format(clr=hex_color)
    else:
        swatch = "—"


    cut_pat = m.CutForegroundPatternId
    has_cut = "✓" if cut_pat != ElementId.InvalidElementId else "—"


    surf_pat = m.SurfaceForegroundPatternId
    has_surf = "✓" if surf_pat != ElementId.InvalidElementId else "—"

    material_rows.append([
        m.Name,
        m.MaterialCategory or "—",
        m.MaterialClass or "—",
        swatch,
        has_cut,
        has_surf,
        ap_name,
        m.Id.IntegerValue,
    ])

material_rows.sort(key=lambda x: x[0])  

# ── Scoring ───────────────────────────────────────────────────────────────────

published_views_without_templates = []

for v in non_template_views:

    if v.Id.IntegerValue not in views_on_sheets:
        continue

    if v.ViewTemplateId.IntegerValue == -1:
        published_views_without_templates.append(v)


positive_points = {
    "No CAD Imports": (len(cad_imports) == 0, 8),
    "No Unplaced Rooms": (len(unplaced_rooms) == 0, 15),
    "No Duplicate Warnings": (len(dup_warnings) == 0, 10),
    "Sheets Exist": (len(sheets) > 0, 5),
    "Rooms Exist": (len(rooms) > 0, 5),
}

deductions = {
    "Published Views w/o Templates":
        (len(published_views_without_templates), 1.5, "WARNING"),

    "Unplaced Views":
        (len(unplaced_views), 0.3, "INFO"),

    "Model Warnings":
        (len(warnings), 0.03, "INFO"),
}

raw_score = 70.0


for label, (condition, points) in positive_points.items():
    if condition:
        raw_score += points

# Apply deductions
for label, (count, weight, severity) in deductions.items():
    raw_score -= count * weight

score = round(max(0.0, min(100.0, raw_score)), 1)

if score >= 90:
    status = "Excellent";  status_color = "#16a34a"
elif score >= 75:
    status = "Good";       status_color = "#2563eb"
elif score >= 50:
    status = "Fair";       status_color = "#d97706"
else:
    status = "Needs attention"; status_color = "#dc2626"

# ── HTML helpers ──────────────────────────────────────────────────────────────

def badge(text, level):
    colors = {
        "CRITICAL": ("fee2e2", "dc2626"),
        "WARNING":  ("fef3c7", "d97706"),
        "INFO":     ("dbeafe", "2563eb"),
        "OK":       ("dcfce7", "16a34a"),
    }
    bg, fg = colors.get(level, ("f3f4f6", "374151"))
    return ('<span style="background:#{bg};color:#{fg};font-size:11px;font-weight:600;'
            'padding:2px 8px;border-radius:4px;letter-spacing:.03em">{text}</span>'
            ).format(bg=bg, fg=fg, text=text)

def score_ring(s):
    r, cx, cy = 52, 60, 60
    circ = 2 * 3.14159 * r
    dash = circ * s / 100
    clr_map = "#16a34a" if s >= 90 else "#2563eb" if s >= 75 else "#d97706" if s >= 50 else "#dc2626"
    return (
        '<svg width="120" height="120" viewBox="0 0 120 120">'
        '<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#e5e7eb" stroke-width="10"/>'
        '<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{clr}" stroke-width="10"'
        ' stroke-dasharray="{dash} {circ}" stroke-linecap="round" transform="rotate(-90 {cx} {cy})"/>'
        '<text x="{cx}" y="{cy}" text-anchor="middle" dominant-baseline="central"'
        ' font-family="system-ui,sans-serif" font-size="22" font-weight="700" fill="#fff">{score}%</text>'
        '</svg>'
    ).format(cx=cx, cy=cy, r=r, clr=clr_map,
             dash=round(dash, 2), circ=round(circ, 2), score=s)

def section_table(title, icon, headers, rows, empty_msg="No items found.",
                  summary_rows=None, summary_headers=None):
    """Collapsible section with optional summary sub-table above the detail table."""
    cnt = ('<span style="background:#f1f5f9;color:#6b7280;font-size:11px;'
           'padding:1px 8px;border-radius:20px;margin-left:6px">{}</span>').format(len(rows))
    hdr = "".join("<th>{}</th>".format(h) for h in headers)

    if rows:
        body = ""
        for i, row in enumerate(rows):
            bg = ' style="background:#263449"' if i % 2 == 0 else ""
            cells = "".join("<td>{}</td>".format(c) for c in row)
            body += "<tr{}>{}</tr>".format(bg, cells)
    else:
        body = ('<tr><td colspan="{}" style="color:#9ca3af;padding:20px;'
                'text-align:center;font-style:italic">{}</td></tr>'
                ).format(len(headers), empty_msg)

    summary_html = ""
    if summary_rows and summary_headers:
        shdr = "".join("<th>{}</th>".format(h) for h in summary_headers)
        sbody = ""
        for i, row in enumerate(summary_rows):
            bg = ' style="background:#263449"' if i % 2 == 0 else ""
            cells = "".join("<td>{}</td>".format(c) for c in row)
            sbody += "<tr{}>{}</tr>".format(bg, cells)
        summary_html = """
<div style="padding:12px 14px 0">
  <p style="font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;
     letter-spacing:.06em;margin-bottom:8px">By Type — Summary</p>
  <div class="table-wrap" style="margin-bottom:12px">
    <table style="font-size:12px">
      <thead><tr style="background:#f1f5f9">{shdr}</tr></thead>
      <tbody>{sbody}</tbody>
    </table>
  </div>
  <p style="font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;
     letter-spacing:.06em;margin-bottom:8px">All Instances</p>
</div>""".format(shdr=shdr, sbody=sbody)

    return """
<details class="section">
  <summary>
    <span class="section-icon">{icon}</span>
    <span class="section-title">{title}</span>
    {cnt}
    <span class="chevron">▸</span>
  </summary>
  {summary_html}
  <div class="table-wrap" style="padding: 0 0 4px">
    <table>
      <thead><tr>{hdr}</tr></thead>
      <tbody>{body}</tbody>
    </table>
  </div>
</details>""".format(icon=icon, title=title, cnt=cnt,
                     hdr=hdr, body=body, summary_html=summary_html)

# ── Row builders ──────────────────────────────────────────────────────────────

def room_row(room):
    n   = room.LookupParameter("Name")
    num = room.LookupParameter("Number")
    lvl = room.Level
    ap  = room.LookupParameter("Area")
    area = ft2_to_m2(ap.AsDouble()) if ap and ap.HasValue else 0
    placed = "✓" if room.Location else "⚠ Unplaced"
    return [num.AsString() if num else "—",
            n.AsString() if n else "—",
            lvl.Name if lvl else "—",
            "{} m²".format(area) if area else "—",
            placed, room.Id.IntegerValue]

def view_row(view):
    tmpl   = "✓" if view.ViewTemplateId.IntegerValue != -1 else "⚠ None"
    on_sht = "✓" if view.Id.IntegerValue in views_on_sheets else "—"
    return [view.Name, str(view.ViewType), tmpl, on_sht, view.Id.IntegerValue]

def sheet_row(sheet):
    placed = len(list(sheet.GetAllPlacedViews()))
    return [sheet.SheetNumber, sheet.Name, placed, sheet.Id.IntegerValue]

def cad_row(cad):
    ov    = doc.GetElement(cad.OwnerViewId)
    owner = ov.Name if ov else "Model / Linked"
    kind  = "Linked" if cad.IsLinked else "Imported"
    return [cad.Name, owner, kind, cad.Id.IntegerValue]

def warning_row(w):
    ids = ", ".join(str(i.IntegerValue) for i in w.GetFailingElements())
    return [w.GetDescriptionText()[:80], ids]

room_rows    = [room_row(r) for r in rooms]
view_rows    = [view_row(v) for v in non_template_views]
sheet_rows   = [sheet_row(s) for s in sheets]
cad_rows     = [cad_row(c) for c in cad_imports]
warning_rows = [warning_row(w) for w in warnings[:50]]

# ── Issue cards ───────────────────────────────────────────────────────────────

issue_cards_html = ""

for label, (condition, points) in positive_points.items():
    if condition:
        issue_cards_html += """
<div class="issue-card">
  <div class="issue-top"><span class="issue-label">{label}</span>{badge}</div>
  <div class="issue-count" style="color:#16a34a">+{points}</div>
  <div class="issue-impact">Positive contribution</div>
</div>""".format(
            label=label,
            badge=badge("OK", "OK"),
            points=points
        )

for label, (count, weight, severity) in deductions.items():
    sev_badge = badge("OK", "OK") if count == 0 else badge(severity, severity)
    val_clr = "#16a34a" if count == 0 else "#d97706" if severity == "WARNING" else "#2563eb"
    impact = round(count * weight, 1)
    impact_str = "-{} pts".format(impact) if count > 0 else "No deduction"

    issue_cards_html += """
<div class="issue-card">
  <div class="issue-top"><span class="issue-label">{label}</span>{sev_badge}</div>
  <div class="issue-count" style="color:{val_clr}">{count}</div>
  <div class="issue-impact">{impact_str}</div>
</div>""".format(
        label=label,
        sev_badge=sev_badge,
        count=count,
        val_clr=val_clr,
        impact_str=impact_str
    )

# ── Detail sections ───────────────────────────────────────────────────────────

details_html = ""

# Totals banner for element schedules
total_wall_area    = round(sum(wall_type_totals.values()), 1)
total_floor_area   = round(sum(floor_type_totals.values()), 1)
total_ceiling_area = round(sum(ceiling_type_totals.values()), 1)

details_html += """
<div class="schedule-banner">
  <div class="sb-item">
    <span class="sb-icon">🧱</span>
    <div><div class="sb-label">Walls</div>
    <div class="sb-val">{nw} instances &nbsp;·&nbsp; {aw} m² total</div></div>
  </div>
  <div class="sb-item">
    <span class="sb-icon">⬜</span>
    <div><div class="sb-label">Floors</div>
    <div class="sb-val">{nf} instances &nbsp;·&nbsp; {af} m² total</div></div>
  </div>
  <div class="sb-item">
    <span class="sb-icon">🔲</span>
    <div><div class="sb-label">Ceilings</div>
    <div class="sb-val">{nc} instances &nbsp;·&nbsp; {ac} m² total</div></div>
  </div>
  <div class="sb-item">
    <span class="sb-icon">🎨</span>
    <div><div class="sb-label">Materials</div>
    <div class="sb-val">{nm} defined in project</div></div>
  </div>
</div>""".format(
    nw=len(walls), aw=total_wall_area,
    nf=len(floors), af=total_floor_area,
    nc=len(ceilings), ac=total_ceiling_area,
    nm=len(materials))

details_html += section_table(
    "Walls", "🧱",
    ["Type", "Level", "Function", "Length", "Height", "Area", "Element ID"],
    wall_rows, "No walls found.",
    summary_rows=wall_summary_rows,
    summary_headers=["Type Name", "Count", "Total Area"])

details_html += section_table(
    "Floors", "⬜",
    ["Type", "Level", "Thickness", "Area", "Element ID"],
    floor_rows, "No floors found.",
    summary_rows=floor_summary_rows,
    summary_headers=["Type Name", "Count", "Total Area"])

details_html += section_table(
    "Ceilings", "🔲",
    ["Type", "Level", "Thickness", "Area", "Element ID"],
    ceiling_rows, "No ceilings found.",
    summary_rows=ceiling_summary_rows,
    summary_headers=["Type Name", "Count", "Total Area"])

details_html += section_table(
    "Materials", "🎨",
    ["Name", "Category", "Class", "Shading Color",
     "Cut Pattern", "Surface Pattern", "Appearance Asset", "Element ID"],
    material_rows, "No materials found.")

details_html += section_table(
    "Rooms", "🏠",
    ["Number", "Name", "Level", "Area", "Placed", "Element ID"],
    room_rows, "No rooms found.")

details_html += section_table(
    "Sheets", "📋",
    ["Sheet No.", "Sheet Name", "Placed Views", "Element ID"],
    sheet_rows, "No sheets found.")

details_html += section_table(
    "Views", "👁",
    ["View Name", "Type", "Template", "On Sheet", "Element ID"],
    view_rows, "No views found.")

details_html += section_table(
    "CAD Imports", "📐",
    ["Name", "Owner View", "Type", "Element ID"],
    cad_rows, "No CAD imports — great!")

details_html += section_table(
    "Warnings", "⚠️",
    ["Description", "Failing Element IDs"],
    warning_rows, "No warnings — model is clean!")

# ── Timestamp ─────────────────────────────────────────────────────────────────
generated = datetime.datetime.now().strftime("%d %b %Y, %H:%M")

# ── Full HTML ──────────────────────────────────────────────────────────────────

html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Revit Model Health — {doc_title}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#f8fafc;
  color:#111827;font-size:14px;line-height:1.5}}
.page{{max-width:1100px;margin:0 auto;padding:24px 20px}}

.header{{background:#0f172a;color:#fff;border-radius:16px;padding:28px 32px;
  margin-bottom:20px;display:flex;align-items:center;gap:32px;flex-wrap:wrap}}
.header-text h1{{font-size:22px;font-weight:600;margin-bottom:4px;letter-spacing:-.01em}}
.header-text .subtitle{{color:#94a3b8;font-size:13px}}
.header-meta{{margin-left:auto;text-align:right;color:#94a3b8;font-size:12px;line-height:1.8}}
.status-pill{{display:inline-block;padding:4px 14px;border-radius:20px;
  font-size:13px;font-weight:600;margin-top:8px;
  background:{status_color}22;color:{status_color};border:1px solid {status_color}44}}

.issue-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));
  gap:12px;margin-bottom:20px}}
.issue-card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px 16px}}
.issue-top{{display:flex;align-items:flex-start;justify-content:space-between;
  gap:8px;margin-bottom:8px}}
.issue-label{{font-size:12px;color:#6b7280;font-weight:500;line-height:1.3}}
.issue-count{{font-size:30px;font-weight:700;line-height:1}}
.issue-impact{{font-size:11px;color:#9ca3af;margin-top:4px}}

.stats-row{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));
  gap:10px;margin-bottom:20px}}
.stat{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 14px}}
.stat-label{{font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}}
.stat-value{{font-size:24px;font-weight:700;color:#111827}}

/* Schedule banner */
.schedule-banner{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));
  gap:12px;margin-bottom:20px}}
.sb-item{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;
  padding:14px 16px;display:flex;align-items:center;gap:14px}}
.sb-icon{{font-size:26px;line-height:1}}
.sb-label{{font-size:12px;font-weight:600;color:#374151;margin-bottom:2px}}
.sb-val{{font-size:12px;color:#6b7280}}

.section{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;
  margin-bottom:10px;overflow:hidden}}
.section summary{{display:flex;align-items:center;gap:10px;
  padding:14px 16px;cursor:pointer;list-style:none;user-select:none}}
.section summary::-webkit-details-marker{{display:none}}
.section[open] summary{{border-bottom:1px solid #f1f5f9}}
.section-icon{{font-size:16px}}
.section-title{{font-weight:600;font-size:14px;flex:1}}
.chevron{{color:#9ca3af;font-size:11px;transition:transform .15s}}
.section[open] .chevron{{transform:rotate(90deg)}}
.table-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
thead tr{{background:#f8fafc}}
th{{padding:10px 14px;text-align:left;font-weight:600;font-size:12px;
  color:#6b7280;text-transform:uppercase;letter-spacing:.05em;
  border-bottom:1px solid #e5e7eb;white-space:nowrap}}
td{{padding:9px 14px;border-bottom:1px solid #f1f5f9;color:#374151}}
tbody tr:last-child td{{border-bottom:none}}
tbody tr:hover{{background:#f8fafc}}



@media(prefers-color-scheme:dark){{
  body{{background:#0f172a;color:#f1f5f9}}
  .issue-card,.stat,.section,.sb-item{{background:#1e293b;border-color:#334155}}
  .stat-value{{color:#f1f5f9}}
  .sb-label{{color:#e2e8f0}}
  .sb-val{{color:#94a3b8}}
  th{{background:#1e293b;color:#94a3b8;border-color:#334155}}
  td{{color:#cbd5e1;border-color:#1e293b}}
  thead tr{{background:#1e293b}}
  tbody tr:hover{{background:#273549}}
  .section[open] summary{{border-color:#334155}}
}}
</style>
</head>
<body>
<div class="page">

  <div class="header">
    {ring}
    <div class="header-text">
      <h1>Revit Model Health Report</h1>
      <div class="subtitle">{doc_title}</div>
      <div class="status-pill">{status}</div>
    </div>
    <div class="header-meta">
      Generated {generated}<br>
      Rooms: {n_rooms} &nbsp;·&nbsp; Sheets: {n_sheets}<br>
      Views: {n_views} &nbsp;·&nbsp; Links: {n_links}
    </div>
  </div>

  <div class="issue-grid">{issue_cards}</div>

  <div class="stats-row">
    <div class="stat"><div class="stat-label">Total rooms</div>
      <div class="stat-value">{n_rooms}</div></div>
    <div class="stat"><div class="stat-label">Unplaced rooms</div>
      <div class="stat-value" style="color:{ur_clr}">{n_unplaced_rooms}</div></div>
    <div class="stat"><div class="stat-label">Views</div>
      <div class="stat-value">{n_views}</div></div>
    <div class="stat"><div class="stat-label">Unplaced views</div>
      <div class="stat-value" style="color:{uv_clr}">{n_unplaced_views}</div></div>
    <div class="stat"><div class="stat-label">Sheets</div>
      <div class="stat-value">{n_sheets}</div></div>
    <div class="stat"><div class="stat-label">Revit links</div>
      <div class="stat-value">{n_links}</div></div>
    <div class="stat"><div class="stat-label">In-place families</div>
      <div class="stat-value" style="color:{ip_clr}">{n_inplace}</div></div>
    <div class="stat"><div class="stat-label">Text notes</div>
      <div class="stat-value">{n_text}</div></div>
  </div>

  {details}

</div>
</body>
</html>""".format(
    doc_title   = doc.Title or "Untitled Model",
    ring        = score_ring(score),
    status      = status,
    status_color= status_color,
    generated   = generated,
    issue_cards = issue_cards_html,
    details     = details_html,
    n_rooms     = len(rooms),
    n_sheets    = len(sheets),
    n_views     = len(non_template_views),
    n_links     = len(links),
    n_unplaced_rooms = len(unplaced_rooms),
    n_inplace   = len(inplace_families),
    n_text      = len(text_notes),
    n_unplaced_views = len(unplaced_views),
    ur_clr = "#dc2626" if unplaced_rooms  else "#16a34a",
    uv_clr = "#d97706" if unplaced_views  else "#16a34a",
    ip_clr = "#dc2626" if inplace_families else "#16a34a",
)

with codecs.open(html_path, "w", "utf-8") as f:
    f.write(html)

# ── Dynamo outputs ─────────────────────────────────────────────────────────────
OUT = [
    score,
    status,
    len(cad_imports),
    len(inplace_families),
    len(views_no_template),
    len(unplaced_views),
    len(unplaced_rooms),
    len(warnings),
    len(walls),
    len(floors),
    len(ceilings),
    len(materials),
    "Exported: " + html_path,
]
