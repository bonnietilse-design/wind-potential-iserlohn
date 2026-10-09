import arcpy
import csv
import io
import os
from datetime import datetime

# =============================================================================
# SCRIPT 4 - HARD AND SOFT CRITERIA, STORM DAMAGE AREAS ALLOWED
# =============================================================================
# Wind park potential areas in Iserlohn from the criteria CSV.
# Forest damaged by storm Kyrill (ERASE_FROM_CRITERIA) is cut out of the
# hard forest criterion, so it can become potential area.
#
# PART A - HARD CRITERIA (Negativflaeche(hart)) - exclusion
#   per criterion: SQL query -> select by location -> buffer -> clip ->
#   marker field Hxx = 1; then union -> repair geometry -> count markers ->
#   ANZ_HART = 0 -> dissolve -> multipart to singlepart -> area
#
# PART B - SOFT CRITERIA (Negativflaeche(weich)) - scoring
#   per criterion: the same preparation, marker field Wxx = 1, score from CSV;
#   union -> repair geometry -> ANZ_WEICH, KRITERIEN_WEICH, SCORE_WEICH
#   (sum of the scores of all soft criteria that apply)
#
# PART C - SCORED POTENTIAL AREAS
#   potential areas (A) intersected with the soft union (B) -> every part of a
#   potential area gets its soft score; per potential area: worst score,
#   area-weighted mean score and share without any soft criterion
#
# Results in OUTPUT_GDB:
#   H01_... / W01_...                one layer per criterion (buffered, in AOI)
#   Union_Harte_Kriterien            union of AOI + hard criteria
#   Ausschluss_hart                  areas excluded by a hard criterion
#   Potentialflaechen_hart           potential areas with FLAECHE_HA
#   Union_Weiche_Kriterien           union of AOI + soft criteria, SCORE_WEICH
#                                    for the whole of Iserlohn
#   Potentialflaechen_Teilflaechen   potential areas split by soft criteria,
#                                    each part with its SCORE_WEICH
#   Potentialflaechen_bewertet       potential areas with SCORE_MIN,
#                                    SCORE_MITTEL, ANTEIL_OHNE_WEICH and
#                                    DIST_UW_M, SCORE_NETZ (D),
#                                    WIND_MITTEL/MIN/MAX (E, no score),
#                                    DIST_WEG_M (F, no score),
#                                    SCORE_GESAMT
#
# PART D - GRID CONNECTION
#   straight-line distance from every potential area to the nearest
#   substation (Umspannwerk) -> bonus score by distance class
#
# PART E - WIND SPEED
#   mean / min / max wind speed (Windgeschwindigeit_200m_Hoehe) per
#   potential area - information only, not scored
#
# PART F - ACCESS
#   straight-line distance from every potential area to the nearest path
#   (ax_wegpfadsteig) - information only, not scored
#
# SCORE_GESAMT = SCORE_MITTEL (soft) + SCORE_NETZ

# =============================================================================
# SETTINGS
# =============================================================================

# Output of script 3b (Iserlohn + buffer zone)
INPUT_GDB = r"C:\Bonnie_Winter\Wind_Project\Import_Iserlohn.gdb"

# ---- analysis area ----------------------------------------------------------
AOI_SOURCE = r"C:\Bonnie_Winter\Wind_Project\Projekt_Wind_Teilung.gdb\Nord"
AOI_FIELD = "gemarkungsnummer_klartext"
AOI_NAME = "Iserlohn"
# Optional: your own SQL query instead (overrides AOI_FIELD / AOI_NAME)
AOI_WHERE = ""

# Area used by scripts 2 and 3 - only to check the analysis area lies inside it
IMPORT_AOI = r"C:\Bonnie_Winter\Wind_Project\Projekt_Wind_Teilung.gdb\Nord"

BUFFER_CONFIG = r"C:\Bonnie_Winter\Wind_Project\Projektdaten Wind\SRV_Wind_DE.csv"

# Results (created if it doesn't exist; existing results are overwritten)
OUTPUT_GDB = r"C:\Bonnie_Winter\Wind_Project\Analyse_Wind_Iserlohn_weich_Windwurf.gdb"
REPORT = r"C:\Users\GIS308\Desktop\output\union_report_Iserlohn_weich_Windwurf.csv"
# Table of all potential areas with their values (area, wind, distances, scores)
AREA_TABLE = r"C:\Users\GIS308\Desktop\output\Potentialflaechen_Iserlohn_weich_Windwurf.csv"

COL_PATH = "FC-Name(Pfad)"
COL_USE = "Verwendung"
COL_BUFFER = "Bufferdistanz(Zahloderleer)"
COL_SQL = "SQL-Definitionsabfrage(Text)"
COL_SCORE = "Score"

# Rows whose "Verwendung" contains this text are hard / soft criteria
HARD_KEYWORD = "hart"
SOFT_KEYWORD = "weich"

# Score for soft criteria without a value in the CSV (e.g. Naturparke)
DEFAULT_SCORE = 0

# False = only the hard criteria are run (as the old script 4)
RUN_SOFT = True

# Areas cut OUT of a hard criterion before the union, so they are not
# excluded by it (other criteria and their buffers still apply there).
# Key = layer name without _p/_l/_pt. Value = list of polygon layers.
# Here: forest damaged by storm Kyrill may be used for wind turbines.
ERASE_FROM_CRITERIA = {
    "ax_wald": [
        r"C:\Bonnie_Winter\Wind_Project\Projektdaten Wind\windwurfschadflaechen.gdb\windwurfschadflaechen_kyrill",
    ],
}

# ---- grid connection: distance to the nearest substation ------------------
# Potential areas close to a substation get a bonus score.
USE_SUBSTATIONS = True

# Take the substations from Import_All.gdb, NOT from Import_Iserlohn.gdb:
# the nearest substation may lie outside Iserlohn, and script 3b only
# exported substations inside Iserlohn (buffer 0 in the CSV).
SUBSTATION_FC = r"C:\Bonnie_Winter\Wind_Project\Import_All.gdb\Umspannwerke_merged"

# Bonus by straight-line distance (edge of the area -> substation):
# (up to metres, bonus). Farther than the last class -> 0.
# EXAMPLE VALUES - set and justify your own.
SUBSTATION_SCORES = [(2000, 3), (5000, 2), (10000, 1)]

# ---- access: distance to the nearest path (ax_wegpfadsteig) ---------------
# Information only, not scored.
USE_PATHS = True

# From Import_All.gdb: script 3b only exported paths within 3 m of Iserlohn,
# but the nearest path of an area near the border may lie outside it.
PATH_FC = r"C:\Bonnie_Winter\Wind_Project\Import_All.gdb\ax_wegpfadsteig"

# Paths within this distance of Iserlohn are read. Areas with no path
# within this distance get no value (DIST_WEG_M stays empty).
PATH_SEARCH_M = 2000

# ---- wind speed from Windgeschwindigeit_200m_Hoehe -------------------------
USE_WIND = True

# From Import_All.gdb: script 3b only exported the wind values INSIDE
# Iserlohn (buffer 0), but small areas near the border may need the
# nearest value from outside.
WIND_FC = r"C:\Bonnie_Winter\Wind_Project\Import_All.gdb\Windgeschwindigeit_200m_Hoehe"

# Field with the wind speed. Empty = detect automatically (works if the
# layer has exactly one numeric attribute field; otherwise the script lists
# the numeric fields and you set the right one here).
WIND_FIELD = "energie"     # gridcode = the same value x 10000

# Wind values within this distance of Iserlohn are read (for areas near the
# border that contain no wind value themselves)
WIND_SEARCH_M = 1000

# Wind speed is added as information only - it is NOT scored and not part
# of SCORE_GESAMT. (To score it later: list of (at least value, bonus).)
WIND_SCORES = []

# Potential areas smaller than this are removed (0 = keep all)
MIN_FLAECHE_HA = 0

TARGET_WKID = 25832
GEOMETRY_SUFFIXES = ("_pt", "_p", "_l")
PRO_SUFFIX = "_new_pro"


# =============================================================================
# HELPERS
# =============================================================================

def base_name(name):
    """Same normalisation as scripts 2, 3 and 3b:
    ax_wald_p, AX_Wald, ax_wald_new_pro all become 'ax_wald'."""
    n = os.path.splitext(name.strip())[0].lower()
    if n.endswith(PRO_SUFFIX):
        n = n[:-len(PRO_SUFFIX)]
    for suffix in GEOMETRY_SUFFIXES:
        if n.endswith(suffix):
            return n[:-len(suffix)]
    return n


def has_geometry_suffix(name):
    return name.lower().endswith(GEOMETRY_SUFFIXES)


def read_criteria(path):
    """Return all CSV rows as dicts (same encoding/delimiter detection
    as the other scripts)."""
    text = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            with open(path, encoding=enc, newline="") as f:
                text = f.read()
            print(f"CSV encoding: {enc}")
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise RuntimeError(f"Could not decode {path}")

    first_line = text.splitlines()[0]
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)

    for col in (COL_PATH, COL_USE, COL_BUFFER, COL_SQL):
        if col not in (reader.fieldnames or []):
            raise RuntimeError(
                f"Column '{col}' not found. Fields: {reader.fieldnames}")
    has_score = COL_SCORE in (reader.fieldnames or [])
    if not has_score:
        print(f"WARNING: column '{COL_SCORE}' not found - "
              f"all soft scores = {DEFAULT_SCORE}")

    rows = []
    for line_no, row in enumerate(reader, start=2):
        name = os.path.basename(
            (row.get(COL_PATH) or "").replace("\\", "/").strip())
        if not name:
            continue

        raw = (row.get(COL_BUFFER) or "").strip().replace(",", ".")
        try:
            distance = float(raw) if raw else 0.0
        except ValueError:
            print(f"CSV ERROR line {line_no}: buffer '{raw}' - using 0")
            distance = 0.0

        raw_score = (row.get(COL_SCORE) or "").strip().replace(",", ".") \
            if has_score else ""
        try:
            score = float(raw_score) if raw_score else None
        except ValueError:
            print(f"CSV ERROR line {line_no}: score '{raw_score}' - ignored")
            score = None

        rows.append({
            "line": line_no,
            "name": name,
            "use": (row.get(COL_USE) or "").strip(),
            "buffer": distance,
            "sql": (row.get(COL_SQL) or "").strip(),
            "score": score,
        })
    return rows


def find_layer(csv_name, gdb_layers):
    """Find the input layer for a CSV name.
    1. exact name (ax_dammwalldeich_l -> ax_dammwalldeich_l)
    2. same base name without geometry suffix, e.g. ALKIS data
       (ax_wald_p -> ax_wald)"""
    by_lower = {n.lower(): n for n in gdb_layers}
    if csv_name.lower() in by_lower:
        return by_lower[csv_name.lower()]

    key = base_name(csv_name)
    candidates = [n for n in gdb_layers
                  if base_name(n) == key and not has_geometry_suffix(n)]
    return candidates[0] if len(candidates) == 1 else None


def delete_if_exists(*paths):
    for p in paths:
        if p and arcpy.Exists(p):
            arcpy.management.Delete(p)


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")     # opens correctly in German Excel
        writer.writerow(header)
        writer.writerows(rows)


def erase_areas(target_fc, erase_src, aoi_lyr, code):
    """Remove the polygons of erase_src from target_fc (in place).
    Returns the removed area in ha. Stops the script if erase_src is
    missing or not a polygon layer, because the areas would otherwise
    silently stay excluded."""
    if not arcpy.Exists(erase_src):
        raise SystemExit(f"STOP: {erase_src} not found. "
                         "Fix ERASE_FROM_CRITERIA before running again.")
    if arcpy.Describe(erase_src).shapeType != "Polygon":
        raise SystemExit(f"STOP: {erase_src} is not a polygon layer.")

    gdb = os.path.dirname(target_fc)
    lyr = f"erase_lyr_{code}"
    sel = os.path.join(gdb, f"tmp_erase_sel_{code}")
    diss = os.path.join(gdb, f"tmp_erase_diss_{code}")
    out = os.path.join(gdb, f"tmp_erased_{code}")
    delete_if_exists(lyr, sel, diss, out)

    # only the erase polygons in the AOI, projected (env), repaired, dissolved
    arcpy.management.MakeFeatureLayer(erase_src, lyr)
    arcpy.management.SelectLayerByLocation(lyr, "INTERSECT", aoi_lyr,
                                           None, "NEW_SELECTION")
    if int(arcpy.management.GetCount(lyr)[0]) == 0:
        arcpy.management.Delete(lyr)
        return 0.0
    arcpy.conversion.ExportFeatures(lyr, sel)
    arcpy.management.Delete(lyr)
    arcpy.management.RepairGeometry(sel, "DELETE_NULL")
    arcpy.analysis.PairwiseDissolve(sel, diss)

    before = area_ha(target_fc)
    arcpy.analysis.PairwiseErase(target_fc, diss, out)
    arcpy.management.Delete(target_fc)
    arcpy.management.Rename(out, target_fc)
    delete_if_exists(sel, diss)
    return before - area_ha(target_fc)


def geometry_union(fc):
    """Return all geometries of fc as one geometry (in TARGET_WKID)."""
    sr = arcpy.SpatialReference(TARGET_WKID)
    geom = None
    with arcpy.da.SearchCursor(fc, ["SHAPE@"], spatial_reference=sr) as cur:
        for (g,) in cur:
            if g is None:
                continue
            geom = g if geom is None else geom.union(g)
    return geom


def area_ha(fc):
    total = 0.0
    with arcpy.da.SearchCursor(fc, ["SHAPE@AREA"]) as cur:
        for (a,) in cur:
            total += a or 0.0
    return total / 10000.0


def substation_bonus(dist_m):
    for max_dist, bonus in SUBSTATION_SCORES:
        if dist_m <= max_dist:
            return bonus
    return 0


def add_substation_distance(target_fc):
    """Per potential area: distance to the nearest substation (m) and
    bonus score. Returns number of substations used."""
    if not arcpy.Exists(SUBSTATION_FC):
        print(f"WARNING: {SUBSTATION_FC} not found - no grid connection score")
        return 0

    sr = arcpy.SpatialReference(TARGET_WKID)
    substations = []
    with arcpy.da.SearchCursor(SUBSTATION_FC, ["OID@", "SHAPE@"],
                               spatial_reference=sr) as cur:
        for oid, geom in cur:
            if geom is not None:
                substations.append((oid, geom))
    if not substations:
        print("WARNING: no substations found - no grid connection score")
        return 0

    for name, ftype, alias in (
            ("DIST_UW_M", "DOUBLE", "Entfernung naechstes Umspannwerk [m]"),
            ("UW_OID", "LONG", "OBJECTID naechstes Umspannwerk"),
            ("SCORE_NETZ", "DOUBLE", "Bonus Netzanschluss")):
        arcpy.management.AddField(target_fc, name, ftype, field_alias=alias)

    with arcpy.da.UpdateCursor(target_fc,
                               ["SHAPE@", "DIST_UW_M", "UW_OID", "SCORE_NETZ"],
                               spatial_reference=sr) as cur:
        for rec in cur:
            geom = rec[0]
            dist, oid = min(((geom.distanceTo(g), o) for o, g in substations),
                            key=lambda t: t[0])
            rec[1] = round(dist, 1)
            rec[2] = oid
            rec[3] = substation_bonus(dist)
            cur.updateRow(rec)

    return len(substations)


def add_nearest_distance(target_fc, source_fc, aoi_lyr, search_m,
                         dist_field, id_field, dist_alias, id_alias):
    """Per feature of target_fc: straight-line distance (m) to the nearest
    feature of source_fc (0 if they touch or overlap).
    Only source features within search_m of the AOI are used.
    Returns the number of source features used."""
    if not arcpy.Exists(source_fc):
        print(f"WARNING: {source_fc} not found - {dist_field} not calculated")
        return 0

    sr = arcpy.SpatialReference(TARGET_WKID)
    lyr = "near_src_lyr"
    delete_if_exists(lyr)
    arcpy.management.MakeFeatureLayer(source_fc, lyr)
    arcpy.management.SelectLayerByLocation(
        lyr, "WITHIN_A_DISTANCE", aoi_lyr, f"{search_m} Meters",
        "NEW_SELECTION")

    src = []        # (oid, geometry, xmin, ymin, xmax, ymax)
    with arcpy.da.SearchCursor(lyr, ["OID@", "SHAPE@"],
                               spatial_reference=sr) as cur:
        for oid, g in cur:
            if g is not None:
                e = g.extent
                src.append((oid, g, e.XMin, e.YMin, e.XMax, e.YMax))
    arcpy.management.Delete(lyr)

    if not src:
        print(f"WARNING: no features of {os.path.basename(source_fc)} "
              f"within {search_m} m - {dist_field} not calculated")
        return 0

    arcpy.management.AddField(target_fc, dist_field, "DOUBLE",
                              field_alias=dist_alias)
    arcpy.management.AddField(target_fc, id_field, "LONG",
                              field_alias=id_alias)

    with arcpy.da.UpdateCursor(target_fc, ["SHAPE@", dist_field, id_field],
                               spatial_reference=sr) as cur:
        for rec in cur:
            geom = rec[0]
            e = geom.extent
            best_d, best_oid = None, None
            radius = 50.0
            # search in growing rings: only features whose extent lies
            # within `radius` of the area are measured
            while radius <= search_m * 2:
                for oid, g, xmin, ymin, xmax, ymax in src:
                    if xmax < e.XMin - radius or xmin > e.XMax + radius or \
                            ymax < e.YMin - radius or ymin > e.YMax + radius:
                        continue
                    d = geom.distanceTo(g)
                    if best_d is None or d < best_d:
                        best_d, best_oid = d, oid
                if best_d is not None and best_d <= radius:
                    break
                radius *= 2
            rec[1] = round(best_d, 1) if best_d is not None else None
            rec[2] = best_oid
            cur.updateRow(rec)

    return len(src)


NUMERIC_TYPES = ("Double", "Single", "Integer", "SmallInteger", "BigInteger")
SYSTEM_FIELDS = {"OBJECTID", "FID", "OID", "SHAPE_LENGTH", "SHAPE_AREA",
                 "SHAPE_LENG", "ORIG_FID", "ID"}


def wind_bonus(v):
    for min_speed, bonus in sorted(WIND_SCORES, reverse=True):
        if v is not None and v >= min_speed:
            return bonus
    return 0


def add_wind_speed(target_fc, aoi_lyr):
    """Per potential area: mean / min / max wind speed of the wind features
    inside it (area-weighted for polygon cells). Areas that contain no wind
    value get the value of the nearest wind feature.
    Returns the wind field used, or None."""
    if not arcpy.Exists(WIND_FC):
        print(f"WARNING: {WIND_FC} not found - no wind speed")
        return None

    # ---- which field holds the wind speed? ----------------------------------
    fields = arcpy.ListFields(WIND_FC)
    numeric = [f.name for f in fields if f.type in NUMERIC_TYPES
               and f.name.upper() not in SYSTEM_FIELDS]
    if WIND_FIELD:
        if WIND_FIELD.lower() not in [f.name.lower() for f in fields]:
            print(f"WARNING: field '{WIND_FIELD}' not in {WIND_FC}. "
                  f"Numeric fields: {numeric} - no wind speed")
            return None
        wfield = WIND_FIELD
    elif len(numeric) == 1:
        wfield = numeric[0]
    else:
        print(f"WARNING: cannot tell which field holds the wind speed. "
              f"Numeric fields: {numeric}\n"
              "         Set WIND_FIELD at the top of the script - "
              "no wind speed this run")
        return None

    shape_type = arcpy.Describe(WIND_FC).shapeType
    print(f"Wind speed field: {wfield} ({shape_type} layer)")

    # ---- read the wind features around Iserlohn -----------------------------
    sr = arcpy.SpatialReference(TARGET_WKID)
    lyr = "wind_lyr"
    delete_if_exists(lyr)
    arcpy.management.MakeFeatureLayer(WIND_FC, lyr)
    arcpy.management.SelectLayerByLocation(
        lyr, "WITHIN_A_DISTANCE", aoi_lyr, f"{WIND_SEARCH_M} Meters",
        "NEW_SELECTION")

    wind = []       # (geometry, value, xmin, ymin, xmax, ymax)
    with arcpy.da.SearchCursor(lyr, ["SHAPE@", wfield],
                               spatial_reference=sr) as cur:
        for g, v in cur:
            if g is None or v is None:
                continue
            e = g.extent
            wind.append((g, float(v), e.XMin, e.YMin, e.XMax, e.YMax))
    arcpy.management.Delete(lyr)
    print(f"{len(wind)} wind values within {WIND_SEARCH_M} m of Iserlohn")
    if not wind:
        print("WARNING: no wind values near Iserlohn - no wind speed")
        return None

    for name, ftype, alias in (
            ("WIND_MITTEL", "DOUBLE", "Wind Mittel (Feld energie)"),
            ("WIND_MIN", "DOUBLE", "Wind Min (Feld energie)"),
            ("WIND_MAX", "DOUBLE", "Wind Max (Feld energie)"),
            ("WIND_ANZ", "LONG", "Anzahl Windwerte in der Flaeche"),
            ("WIND_METHODE", "TEXT", "Herkunft Windwert")):
        if ftype == "TEXT":
            arcpy.management.AddField(target_fc, name, ftype,
                                      field_length=30, field_alias=alias)
        else:
            arcpy.management.AddField(target_fc, name, ftype,
                                      field_alias=alias)

    is_polygon = shape_type == "Polygon"
    score_wind = bool(WIND_SCORES)
    if score_wind:
        arcpy.management.AddField(target_fc, "SCORE_WIND", "DOUBLE",
                                  field_alias="Bonus Windgeschwindigkeit")
    out_fields = ["SHAPE@", "WIND_MITTEL", "WIND_MIN", "WIND_MAX",
                  "WIND_ANZ", "WIND_METHODE"] + (["SCORE_WIND"] if score_wind
                                                 else [])

    with arcpy.da.UpdateCursor(target_fc, out_fields,
                               spatial_reference=sr) as cur:
        for rec in cur:
            geom = rec[0]
            e = geom.extent
            values, weights = [], []

            for g, v, xmin, ymin, xmax, ymax in wind:
                # quick extent check first
                if xmax < e.XMin or xmin > e.XMax or \
                        ymax < e.YMin or ymin > e.YMax:
                    continue
                if geom.disjoint(g):
                    continue
                if is_polygon:
                    a = geom.intersect(g, 4).area
                    if a <= 0:
                        continue
                    weights.append(a)
                else:
                    weights.append(1.0)
                values.append(v)

            if values:
                mean = sum(v * w for v, w in zip(values, weights)) / sum(weights)
                rec[1:6] = [round(mean, 2), min(values), max(values),
                            len(values),
                            "Flaechenanteil" if is_polygon else "Werte in Flaeche"]
            else:
                # area too small to contain a wind value -> nearest value
                d, v = min(((geom.distanceTo(g), v)
                            for g, v, *_ in wind), key=lambda t: t[0])
                rec[1:6] = [round(v, 2), v, v, 0,
                            f"naechster Wert ({d:.0f} m)"]
            if score_wind:
                rec[6] = wind_bonus(rec[1])
            cur.updateRow(rec)

    return wfield


def add_total_score(target_fc):
    """SCORE_GESAMT = soft score (SCORE_MITTEL) + SCORE_NETZ + SCORE_WIND,
    using whichever of these exist."""
    names = [f.name for f in arcpy.ListFields(target_fc)]
    parts = [f for f in ("SCORE_MITTEL", "SCORE_NETZ", "SCORE_WIND")
             if f in names]
    if not parts:
        return []
    if "SCORE_GESAMT" not in names:
        arcpy.management.AddField(target_fc, "SCORE_GESAMT", "DOUBLE",
                                  field_alias="Gesamtscore")
    with arcpy.da.UpdateCursor(target_fc, parts + ["SCORE_GESAMT"]) as cur:
        for rec in cur:
            rec[-1] = round(sum((v or 0.0) for v in rec[:-1]), 2)
            cur.updateRow(rec)
    return parts


def fmt_score(v):
    return "" if v is None else f"{v:g}"


# =============================================================================
# WORKFLOW STEPS
# =============================================================================

def prepare_criteria(criteria, prefix, kind, gdb_layers, aoi, aoi_lyr, report):
    """For every criterion: SQL query -> select by location -> buffer ->
    clip to AOI -> marker field <prefix>xx = 1.
    Returns [(code, output fc, description, score)]."""
    prepared = []

    for i, row in enumerate(criteria, start=1):
        code = f"{prefix}{i:02d}"
        score = row["score"]
        desc = (f"{row['name']} {row['buffer']:g}m"
                + (f" [{row['sql']}]" if row["sql"] else "")
                + (f" Score {fmt_score(score)}" if kind == "weich" else ""))
        print(f"\n{code}: {desc}")

        def add(layer, shape, count, status, note):
            report.append([code, kind, row["line"], row["name"], layer,
                           row["buffer"], row["sql"], fmt_score(score),
                           shape, count, status, note])

        layer = find_layer(row["name"], gdb_layers)
        if not layer:
            print(f"    not in {os.path.basename(INPUT_GDB)} - skipped")
            add("", "", "", "SKIPPED",
                f"layer not in {os.path.basename(INPUT_GDB)}")
            continue

        in_fc = os.path.join(INPUT_GDB, layer)
        shape_type = arcpy.Describe(in_fc).shapeType
        dist = row["buffer"]

        if shape_type != "Polygon" and dist <= 0:
            print(f"    {shape_type} layer without buffer - no area, skipped")
            add(layer, shape_type, "", "SKIPPED",
                "point/line layer with buffer 0")
            continue

        out_name = arcpy.ValidateTableName(f"{code}_{layer}", OUTPUT_GDB)
        out_fc = os.path.join(OUTPUT_GDB, out_name)
        tmp = os.path.join(OUTPUT_GDB, f"tmp_{code}")
        lyr = f"lyr_{code}"

        try:
            delete_if_exists(lyr, tmp, out_fc)

            # definition query
            arcpy.management.MakeFeatureLayer(in_fc, lyr, row["sql"] or None)

            # only features that can reach the AOI
            if dist > 0:
                arcpy.management.SelectLayerByLocation(
                    lyr, "WITHIN_A_DISTANCE", aoi_lyr, f"{dist} Meters",
                    "NEW_SELECTION")
            else:
                arcpy.management.SelectLayerByLocation(
                    lyr, "INTERSECT", aoi_lyr, None, "NEW_SELECTION")

            count = int(arcpy.management.GetCount(lyr)[0])
            if count == 0:
                print("    no features in/near the AOI - skipped")
                add(layer, shape_type, 0, "SKIPPED", "no features in/near AOI")
                continue

            print(f"    {count} features from {layer}")

            # buffer (or dissolve) into one feature, then clip to the AOI
            if dist > 0:
                arcpy.analysis.PairwiseBuffer(
                    lyr, tmp, f"{dist} Meters", dissolve_option="ALL")
            else:
                arcpy.analysis.PairwiseDissolve(lyr, tmp)

            arcpy.analysis.PairwiseClip(tmp, aoi, out_fc)

            # hard criteria only: cut out areas that should not be excluded
            # (e.g. storm damage areas from forest)
            erase_note = ""
            if kind == "hart":
                for erase_src in ERASE_FROM_CRITERIA.get(
                        base_name(row["name"]), []):
                    erased_ha = erase_areas(out_fc, erase_src, aoi_lyr, code)
                    erase_note += (f"; {erased_ha:.1f} ha removed "
                                   f"({os.path.basename(erase_src)})")
                    print(f"    {erased_ha:,.1f} ha of "
                          f"{os.path.basename(erase_src)} removed from {code}")

            if int(arcpy.management.GetCount(out_fc)[0]) == 0:
                print("    buffer does not reach the AOI - skipped")
                add(layer, shape_type, count, "SKIPPED",
                    "buffer does not reach AOI")
                delete_if_exists(out_fc)
                continue

            # marker field: <code> = 1
            arcpy.management.AddField(out_fc, code, "SHORT",
                                      field_alias=desc[:255])
            arcpy.management.CalculateField(out_fc, code, "1", "PYTHON3")

            ha = area_ha(out_fc)
            print(f"    -> {out_name}: {ha:,.1f} ha in AOI")
            prepared.append((code, out_fc, desc, score))
            note = f"{ha:.1f} ha in AOI{erase_note}"
            if kind == "weich" and score is None:
                note += f"; no score in CSV - {DEFAULT_SCORE} used"
            add(layer, shape_type, count, "OK", note)

        except Exception as e:
            print(f"    FAILED: {e}")
            add(layer, shape_type, "", "FAILED", str(e))

        finally:
            delete_if_exists(lyr, tmp)

    return prepared


def union_all(aoi, prepared, out_fc, tmp_prefix):
    """Union of AOI + all prepared criterion layers, then repair geometry."""
    delete_if_exists(out_fc)
    inputs = [aoi] + [p[1] for p in prepared]

    if arcpy.ProductInfo() == "ArcInfo":
        # ArcGIS Pro Advanced: all inputs in one Union
        arcpy.analysis.Union(inputs, out_fc, "NO_FID")
    else:
        # Basic/Standard: Union only takes 2 inputs -> step by step
        print("(Basic/Standard licence - union step by step)")
        current = inputs[0]
        for n, fc in enumerate(inputs[1:], start=1):
            out = out_fc if n == len(inputs) - 1 else \
                os.path.join(OUTPUT_GDB, f"{tmp_prefix}_{n}")
            print(f"    {n}/{len(inputs) - 1}: + {os.path.basename(fc)}")
            arcpy.analysis.Union([current, fc], out, "NO_FID")
            if current != aoi:
                delete_if_exists(current)
            current = out

    # (all criteria were clipped to the AOI, so the union covers exactly the AOI)

    # Union can create slivers and invalid geometries along buffer edges
    print("Repairing geometry of the union result ...")
    arcpy.management.RepairGeometry(out_fc, "DELETE_NULL")
    print(f"    {int(arcpy.management.GetCount(out_fc)[0])} polygons")


def count_markers(union_fc, prepared, count_field, list_field,
                  score_field=None):
    """Per union polygon: number and list of criteria that apply
    (+ sum of their scores)."""
    codes = [p[0] for p in prepared]
    scores = {p[0]: (p[3] if p[3] is not None else DEFAULT_SCORE)
              for p in prepared}

    arcpy.management.AddField(union_fc, count_field, "SHORT")
    arcpy.management.AddField(union_fc, list_field, "TEXT", field_length=500)
    fields = codes + [count_field, list_field]
    if score_field:
        arcpy.management.AddField(union_fc, score_field, "DOUBLE")
        fields.append(score_field)

    n = len(codes)
    with arcpy.da.UpdateCursor(union_fc, fields) as cur:
        for rec in cur:
            hits = [c for c, v in zip(codes, rec[:n]) if v == 1]
            rec[n] = len(hits)
            rec[n + 1] = ";".join(hits)
            if score_field:
                rec[n + 2] = sum(scores[c] for c in hits)
            cur.updateRow(rec)


# =============================================================================
# MAIN
# =============================================================================

def main():
    start = datetime.now()
    arcpy.env.overwriteOutput = True
    arcpy.env.parallelProcessingFactor = "100%"

    # ---- output gdb ---------------------------------------------------------
    if not arcpy.Exists(OUTPUT_GDB):
        arcpy.management.CreateFileGDB(os.path.dirname(OUTPUT_GDB),
                                       os.path.basename(OUTPUT_GDB))
        print(f"Created: {OUTPUT_GDB}")

    # ---- analysis area (one dissolved polygon) -----------------------------
    arcpy.env.outputCoordinateSystem = arcpy.SpatialReference(TARGET_WKID)

    where = AOI_WHERE.strip()
    if not where:
        field_sql = arcpy.AddFieldDelimiters(AOI_SOURCE, AOI_FIELD)
        where = f"{field_sql} = '{AOI_NAME.replace(chr(39), chr(39) * 2)}'"

    src_lyr = "aoi_source_lyr"
    delete_if_exists(src_lyr)
    arcpy.management.MakeFeatureLayer(AOI_SOURCE, src_lyr, where)
    n_src = int(arcpy.management.GetCount(src_lyr)[0])
    if n_src == 0:
        values = set()
        try:
            with arcpy.da.SearchCursor(AOI_SOURCE, [AOI_FIELD]) as cur:
                for (v,) in cur:
                    if v is not None:
                        values.add(str(v))
        except Exception:
            pass
        similar = sorted(v for v in values
                         if AOI_NAME.strip().lower() in v.lower())
        raise RuntimeError(
            f"No features selected with: {where}\n"
            + (f"Values containing '{AOI_NAME}': {similar}" if similar else
               f"Values in {AOI_FIELD}: {sorted(values)[:50]}"))
    print(f"Analysis area query: {where} -> {n_src} feature(s)")

    aoi = os.path.join(OUTPUT_GDB, "AOI_dissolved")
    arcpy.analysis.PairwiseDissolve(src_lyr, aoi)
    arcpy.management.Delete(src_lyr)
    aoi_ha = area_ha(aoi)
    print(f"Analysis area: {aoi_ha:,.1f} ha")

    if IMPORT_AOI and arcpy.Exists(IMPORT_AOI):
        outside_ha = (geometry_union(aoi).difference(
            geometry_union(IMPORT_AOI)).area / 10000.0)
        if outside_ha > 1:
            print(f"WARNING: {outside_ha:,.1f} ha of the analysis area lie "
                  f"outside {os.path.basename(IMPORT_AOI)} - data may be "
                  "missing there.")

    aoi_lyr = "aoi_lyr"
    delete_if_exists(aoi_lyr)
    arcpy.management.MakeFeatureLayer(aoi, aoi_lyr)

    # ---- criteria from the CSV ---------------------------------------------
    rows = read_criteria(BUFFER_CONFIG)
    hard = [r for r in rows if HARD_KEYWORD in r["use"].lower()]
    soft = [r for r in rows if SOFT_KEYWORD in r["use"].lower()]
    print(f"\nCriteria in CSV: {len(hard)} hard, {len(soft)} soft "
          f"(of {len(rows)} rows; other rows are not used)")

    arcpy.env.workspace = INPUT_GDB
    gdb_layers = arcpy.ListFeatureClasses() or []
    report = []

    # =========================================================================
    # PART A - HARD CRITERIA
    # =========================================================================

    print("\n################################################")
    print("PART A - HARD CRITERIA")
    print("################################################")

    hard_prep = prepare_criteria(hard, "H", "hart", gdb_layers,
                                 aoi, aoi_lyr, report)
    if not hard_prep:
        write_csv(REPORT, REPORT_HEADER, report)
        raise RuntimeError("No hard criterion could be prepared - see report")

    print(f"\nUNION of AOI + {len(hard_prep)} hard criteria")
    union_hard = os.path.join(OUTPUT_GDB, "Union_Harte_Kriterien")
    union_all(aoi, hard_prep, union_hard, "tmp_union_h")
    count_markers(union_hard, hard_prep, "ANZ_HART", "KRITERIEN")

    # ---- exclusion areas and potential areas -------------------------------
    excl_fc = os.path.join(OUTPUT_GDB, "Ausschluss_hart")
    pot_fc = os.path.join(OUTPUT_GDB, "Potentialflaechen_hart")
    tmp_diss = os.path.join(OUTPUT_GDB, "tmp_pot_dissolved")
    tmp_pot = os.path.join(OUTPUT_GDB, "tmp_pot")
    delete_if_exists(excl_fc, pot_fc, tmp_diss, tmp_pot)

    arcpy.management.MakeFeatureLayer(union_hard, "excl_lyr", "ANZ_HART > 0")
    arcpy.analysis.PairwiseDissolve("excl_lyr", excl_fc)
    arcpy.management.Delete("excl_lyr")

    # dissolve areas without hard criterion, then split into separate areas
    arcpy.management.MakeFeatureLayer(union_hard, "pot_lyr", "ANZ_HART = 0")
    arcpy.analysis.PairwiseDissolve("pot_lyr", tmp_diss,
                                    multi_part="MULTI_PART")
    arcpy.management.Delete("pot_lyr")
    arcpy.management.MultipartToSinglepart(tmp_diss, tmp_pot)
    delete_if_exists(tmp_diss)

    arcpy.management.AddField(tmp_pot, "FLAECHE_HA", "DOUBLE",
                              field_alias="Flaeche [ha]")
    arcpy.management.CalculateField(tmp_pot, "FLAECHE_HA",
                                    "!shape.area@hectares!", "PYTHON3")

    where_min = f"FLAECHE_HA >= {MIN_FLAECHE_HA}" if MIN_FLAECHE_HA > 0 else None
    arcpy.conversion.ExportFeatures(tmp_pot, pot_fc, where_min)
    delete_if_exists(tmp_pot)

    # ID per potential area (used to link the soft parts back)
    arcpy.management.AddField(pot_fc, "PF_ID", "LONG",
                              field_alias="ID Potentialflaeche")
    oid = arcpy.Describe(pot_fc).OIDFieldName
    arcpy.management.CalculateField(pot_fc, "PF_ID", f"!{oid}!", "PYTHON3")

    excl_ha = area_ha(excl_fc)
    pot_ha = area_ha(pot_fc)
    n_pot = int(arcpy.management.GetCount(pot_fc)[0])

    # =========================================================================
    # PART B - SOFT CRITERIA
    # =========================================================================

    soft_prep = []
    if RUN_SOFT and n_pot > 0:
        print("\n################################################")
        print("PART B - SOFT CRITERIA")
        print("################################################")

        soft_prep = prepare_criteria(soft, "W", "weich", gdb_layers,
                                     aoi, aoi_lyr, report)

    if soft_prep:
        print(f"\nUNION of AOI + {len(soft_prep)} soft criteria")
        union_soft = os.path.join(OUTPUT_GDB, "Union_Weiche_Kriterien")
        union_all(aoi, soft_prep, union_soft, "tmp_union_w")
        count_markers(union_soft, soft_prep, "ANZ_WEICH", "KRITERIEN_WEICH",
                      "SCORE_WEICH")

        # =====================================================================
        # PART C - SCORED POTENTIAL AREAS
        # =====================================================================

        print("\n################################################")
        print("PART C - SCORING THE POTENTIAL AREAS")
        print("################################################")

        parts_fc = os.path.join(OUTPUT_GDB, "Potentialflaechen_Teilflaechen")
        scored_fc = os.path.join(OUTPUT_GDB, "Potentialflaechen_bewertet")
        delete_if_exists(parts_fc, scored_fc)

        # every potential area split by the soft criteria
        arcpy.analysis.Intersect([pot_fc, union_soft], parts_fc, "NO_FID")
        arcpy.management.RepairGeometry(parts_fc, "DELETE_NULL")
        arcpy.management.AddField(parts_fc, "TEIL_HA", "DOUBLE",
                                  field_alias="Teilflaeche [ha]")
        arcpy.management.CalculateField(parts_fc, "TEIL_HA",
                                        "!shape.area@hectares!", "PYTHON3")

        # per potential area: worst score, area-weighted mean, share without
        stats = {}      # PF_ID -> [total area, score*area, min score, area 0]
        with arcpy.da.SearchCursor(
                parts_fc, ["PF_ID", "SCORE_WEICH", "SHAPE@AREA"]) as cur:
            for pf_id, score, area in cur:
                score = score or 0.0
                s = stats.setdefault(pf_id, [0.0, 0.0, None, 0.0])
                s[0] += area
                s[1] += score * area
                s[2] = score if s[2] is None else min(s[2], score)
                if score == 0:
                    s[3] += area

        arcpy.conversion.ExportFeatures(pot_fc, scored_fc)
        arcpy.management.AddField(scored_fc, "SCORE_MIN", "DOUBLE",
                                  field_alias="Schlechtester Score")
        arcpy.management.AddField(scored_fc, "SCORE_MITTEL", "DOUBLE",
                                  field_alias="Mittlerer Score (flaechengewichtet)")
        arcpy.management.AddField(scored_fc, "ANTEIL_OHNE_WEICH", "DOUBLE",
                                  field_alias="Anteil ohne weiche Kriterien [%]")

        with arcpy.da.UpdateCursor(
                scored_fc, ["PF_ID", "SCORE_MIN", "SCORE_MITTEL",
                            "ANTEIL_OHNE_WEICH"]) as cur:
            for rec in cur:
                s = stats.get(rec[0])
                if s and s[0] > 0:
                    rec[1] = s[2]
                    rec[2] = round(s[1] / s[0], 2)
                    rec[3] = round(s[3] / s[0] * 100, 1)
                cur.updateRow(rec)

    # =========================================================================
    # PART D - GRID CONNECTION (distance to the nearest substation)
    # =========================================================================

    result_fc = scored_fc if soft_prep else pot_fc
    n_uw = 0
    if USE_SUBSTATIONS and n_pot > 0:
        print("\n################################################")
        print("PART D - DISTANCE TO THE NEAREST SUBSTATION")
        print("################################################")
        n_uw = add_substation_distance(result_fc)
        if n_uw:
            print(f"Distance to {n_uw} substations calculated for "
                  f"{n_pot} potential areas ({os.path.basename(result_fc)})")

    # =========================================================================
    # PART E - WIND SPEED
    # =========================================================================

    wfield = None
    if USE_WIND and n_pot > 0:
        print("\n################################################")
        print("PART E - WIND SPEED")
        print("################################################")
        wfield = add_wind_speed(result_fc, aoi_lyr)

    # =========================================================================
    # PART F - ACCESS (distance to the nearest path)
    # =========================================================================

    if USE_PATHS and n_pot > 0:
        print("\n################################################")
        print("PART F - DISTANCE TO THE NEAREST PATH (ax_wegpfadsteig)")
        print("################################################")
        n_paths = add_nearest_distance(
            result_fc, PATH_FC, aoi_lyr, PATH_SEARCH_M,
            "DIST_WEG_M", "WEG_OID",
            "Entfernung naechster Weg/Pfad/Steig [m]",
            "OBJECTID naechster Weg/Pfad/Steig")
        if n_paths:
            print(f"Distance to the nearest of {n_paths} paths calculated")

    # ---- total score --------------------------------------------------------
    score_parts = add_total_score(result_fc) if n_pot > 0 else []

    # ---- summary: best potential areas -------------------------------------
    names = [f.name for f in arcpy.ListFields(result_fc)]
    want = ["PF_ID", "FLAECHE_HA", "SCORE_MITTEL", "SCORE_MIN",
            "ANTEIL_OHNE_WEICH", "DIST_UW_M", "DIST_WEG_M", "WIND_MITTEL",
            "SCORE_GESAMT"]
    have = [f for f in want if f in names]
    best = []
    if n_pot > 0:
        with arcpy.da.SearchCursor(result_fc, have) as cur:
            for rec in cur:
                best.append(dict(zip(have, rec)))
    sort_field = ("SCORE_GESAMT" if "SCORE_GESAMT" in have else
                  "SCORE_MITTEL" if "SCORE_MITTEL" in have else "FLAECHE_HA")
    best.sort(key=lambda r: (-(r.get(sort_field) or 0),
                             -(r.get("FLAECHE_HA") or 0)))
    no_soft = sum(1 for r in best if r.get("ANTEIL_OHNE_WEICH") == 100)

    # rank 1 = best area -> sort field / page number for a map series
    if best and "PF_ID" in have:
        rank = {r["PF_ID"]: i for i, r in enumerate(best, start=1)}
        if "RANG" not in names:
            arcpy.management.AddField(result_fc, "RANG", "LONG",
                                      field_alias="Rang (1 = beste Flaeche)")
        with arcpy.da.UpdateCursor(result_fc, ["PF_ID", "RANG"]) as cur:
            for rec in cur:
                rec[1] = rank.get(rec[0])
                cur.updateRow(rec)

    # =========================================================================
    # REPORT
    # =========================================================================

    write_csv(REPORT, REPORT_HEADER, report)

    # ---- table of all potential areas (sorted by rank) ---------------------
    if best:
        cols = [c for c in ("RANG", "PF_ID", "FLAECHE_HA", "WIND_MITTEL",
                            "WIND_MIN", "WIND_MAX", "WIND_METHODE",
                            "DIST_UW_M", "DIST_WEG_M", "SCORE_MITTEL",
                            "SCORE_MIN", "ANTEIL_OHNE_WEICH", "SCORE_NETZ",
                            "SCORE_GESAMT")
                if c in [f.name for f in arcpy.ListFields(result_fc)]]
        rows_out = []
        with arcpy.da.SearchCursor(result_fc, cols) as cur:
            for rec in cur:
                rows_out.append([("" if v is None else
                                  f"{v:.2f}".replace(".", ",")
                                  if isinstance(v, float) else v)
                                 for v in rec])
        if "RANG" in cols:
            i = cols.index("RANG")
            rows_out.sort(key=lambda r: (r[i] == "", r[i] or 0))
        write_csv(AREA_TABLE, cols, rows_out)

    print("\n================================================")
    print("ANALYSIS COMPLETE")
    print("================================================")
    print(f"Hard criteria used: {len(hard_prep)} of {len(hard)}")
    for code, _, desc, _ in hard_prep:
        print(f"  {code}: {desc}")
    if soft_prep:
        print(f"Soft criteria used: {len(soft_prep)} of {len(soft)}")
        for code, _, desc, _ in soft_prep:
            print(f"  {code}: {desc}")

    print(f"\nAnalysis area:       {aoi_ha:12,.1f} ha")
    print(f"Excluded (hard):     {excl_ha:12,.1f} ha "
          f"({excl_ha / aoi_ha * 100:.1f} %)")
    print(f"Potential areas:     {pot_ha:12,.1f} ha "
          f"({pot_ha / aoi_ha * 100:.1f} %), {n_pot} polygons"
          + (f" >= {MIN_FLAECHE_HA} ha" if MIN_FLAECHE_HA > 0 else ""))

    if soft_prep:
        print(f"\nPotential areas without any soft criterion: {no_soft}")
    if best:
        print(f"\nBest potential areas (by {sort_field}, then size):")
        if score_parts:
            print(f"  (SCORE_GESAMT = {' + '.join(score_parts)})")
        print(f"  {'PF_ID':>6} {'ha':>8} {'soft':>7} {'worst':>6} "
              f"{'%nosoft':>7} {'dist UW m':>10} {'dist Weg':>8} "
              f"{'wind':>6} {'total':>7}")

        def f(v, spec):
            return format(v, spec) if v is not None else "-"

        for r in best[:10]:
            print(f"  {f(r.get('PF_ID'), '>6')} "
                  f"{f(r.get('FLAECHE_HA'), '>8.1f')} "
                  f"{f(r.get('SCORE_MITTEL'), '>7.2f')} "
                  f"{f(r.get('SCORE_MIN'), '>6g')} "
                  f"{f(r.get('ANTEIL_OHNE_WEICH'), '>7.1f')} "
                  f"{f(r.get('DIST_UW_M'), '>10,.0f')} "
                  f"{f(r.get('DIST_WEG_M'), '>8,.0f')} "
                  f"{f(r.get('WIND_MITTEL'), '>6.2f')} "
                  f"{f(r.get('SCORE_GESAMT'), '>7.2f')}")

    if best and "WIND_MITTEL" in have:
        winds = [r["WIND_MITTEL"] for r in best
                 if r.get("WIND_MITTEL") is not None]
        if winds:
            print(f"\nMean wind speed of the potential areas: "
                  f"{min(winds):.2f} - {max(winds):.2f} m/s "
                  f"(average over all areas {sum(winds) / len(winds):.2f})")

    print(f"\nResults: {OUTPUT_GDB}")
    print(f"Report:  {REPORT}")
    if best:
        print(f"Table of all potential areas: {AREA_TABLE}")
    print(f"Runtime: {datetime.now() - start}")


REPORT_HEADER = ["Code", "Type", "CSV_Line", "CSV_Layer", "Input_Layer",
                 "Buffer_m", "SQL", "Score", "Geometry", "Features_used",
                 "Status", "Note"]


if __name__ == "__main__":
    main()