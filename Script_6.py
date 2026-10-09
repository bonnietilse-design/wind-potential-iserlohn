import arcpy
import csv
import io
import os
from datetime import datetime

# =============================================================================
# SCRIPT 4 - UNION OF THE HARD CRITERIA (Negativflaeche(hart))
# =============================================================================
# Wind park potential areas: every row of the criteria CSV marked
# "Negativflaeche(hart)" is
#   1. taken from Import_<AREA_NAME>.gdb (output of script 3b),
#   2. filtered with its SQL definition query,
#   3. buffered by its buffer distance and clipped to the AOI,
# then all criteria are combined with UNION together with the AOI.
# The AOI (analysis area) is AREA_NAME, selected from AOI_SOURCE.
#
# Results in OUTPUT_GDB:
#   H01_..., H02_...           one layer per hard criterion (buffered, in AOI)
#   Union_Harte_Kriterien      union of AOI + all criteria (geometry repaired);
#                              field Hxx = 1 where
#                              criterion xx applies, ANZ_HART = number of
#                              criteria, KRITERIEN = list of codes
#   Ausschluss_hart            all areas excluded by at least one hard criterion
#   Potentialflaechen_hart     remaining areas (no hard criterion), with area in ha
#
# Soft criteria (Negativflaeche(weich), scores) are NOT part of this script.
#
# Additional hard exclusions can be added in EXTRA_HARD (currently none).

# =============================================================================
# SETTINGS
# =============================================================================

# ---- analysis area: change only this name (same as in script 3b) ---------
# Value of gemarkungsnummer_klartext in the Nord layer. Input and output
# names below are built from it.
AREA_NAME = "Iserlohn"

# True  = use ALL features of AOI_SOURCE (the whole Nord region);
#         AREA_NAME is then only used for the output names
# False = only the features where gemarkungsnummer_klartext = AREA_NAME
WHOLE_AREA = False

# Output of script 3b (area + buffer zone). Import_All.gdb also works,
# but is much slower.
INPUT_GDB = rf"C:\Bonnie_Winter\Wind_Project\Import_{AREA_NAME}.gdb"
# ---- analysis area ------------------------------------------------------
# Layer that contains the area (the Nord layer, which holds several areas)
AOI_SOURCE = r"C:\Bonnie_Winter\Wind_Project\Projekt_Wind_Teilung.gdb\Nord"

# Which feature(s) of AOI_SOURCE form the analysis area:
# all features where AOI_FIELD = AOI_NAME
AOI_FIELD = "gemarkungsnummer_klartext"
AOI_NAME = AREA_NAME

# Optional: your own SQL query instead (overrides AOI_FIELD / AOI_NAME),
# e.g. "gemarkungsnummer_klartext IN ('Balve', 'Garbeck')"
AOI_WHERE = ""

# Area used by scripts 2 and 3. Only used to check that the analysis area
# lies inside it - otherwise Import_All.gdb may be missing data there.
IMPORT_AOI = r"C:\Bonnie_Winter\Wind_Project\Projekt_Wind_Teilung.gdb\Nord"
BUFFER_CONFIG = r"C:\Bonnie_Winter\Wind_Project\Projektdaten Wind\SRV_Wind_DE.csv"

# Results (created if it doesn't exist; existing results are overwritten)
# Added to the output names, so runs with different settings don't
# overwrite each other (e.g. Analyse_Wind_Balve_Nadelholz.gdb). "" = none.
RUN_SUFFIX = "_Nadelholz"

OUTPUT_GDB = rf"C:\Bonnie_Winter\Wind_Project\Analyse_Wind_{AREA_NAME}{RUN_SUFFIX}.gdb"
REPORT = rf"C:\Users\GIS308\Desktop\output\union_report_{AREA_NAME}{RUN_SUFFIX}.csv"

COL_PATH = "FC-Name(Pfad)"
COL_USE = "Verwendung"
COL_BUFFER = "Bufferdistanz(Zahloderleer)"
COL_SQL = "SQL-Definitionsabfrage(Text)"

# Rows whose "Verwendung" contains this text are hard criteria
HARD_KEYWORD = "hart"

# Potential areas smaller than this are removed (0 = keep all)
MIN_FLAECHE_HA = 0

# Additional hard exclusions that are not hard criteria in the CSV.
# "path" = full path to the layer (read directly, not from INPUT_GDB),
# "buffer" in m, "sql" = optional definition query.
# Currently empty - only the hard criteria from the CSV are used.
# Example entry:
#   {"name": "ax_wegpfadsteig",
#    "path": r"C:\Bonnie_Winter\Wind_Project\Import_All.gdb\ax_wegpfadsteig",
#    "buffer": 3,
#    "sql": ""},
EXTRA_HARD = []

# Exceptions within a hard criterion: features matching "allow" are NOT
# excluded (they stay possible turbine sites). Key = layer name without
# _p/_l/_pt (so ALKIS ax_wald matches the CSV row ax_wald_p).
# Features with an empty field are still excluded.
ALLOW_EXCEPTIONS = {
    "ax_wald": {"field": "vegetationsmerkmal_klartext",
                "allow": ["Nadelholz"]},
}

# Areas cut OUT of a hard criterion before the union, so they are not
# excluded by it (other criteria and their buffers still apply there).
# Key = layer name without _p/_l/_pt. Value = list of polygon layers.
# Currently OFF (empty). Example - storm damage areas (Kyrill) in forest:
#   "ax_wald": [
#       r"C:\Bonnie_Winter\Wind_Project\Projektdaten Wind\windwurfschadflaechen.gdb\windwurfschadflaechen_kyrill",
#   ],
ERASE_FROM_CRITERIA = {}

TARGET_WKID = 25832
GEOMETRY_SUFFIXES = ("_pt", "_p", "_l")
PRO_SUFFIX = "_new_pro"


# =============================================================================
# HELPERS
# =============================================================================

def base_name(name):
    """Same normalisation as scripts 2 and 3:
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
    as scripts 2 and 3)."""
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
        rows.append({
            "line": line_no,
            "name": name,
            "use": (row.get(COL_USE) or "").strip(),
            "buffer": distance,
            "sql": (row.get(COL_SQL) or "").strip(),
        })
    return rows


def find_layer(csv_name, gdb_layers):
    """Find the layer in Import_All.gdb for a CSV name.
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
    if WHOLE_AREA:
        where = None                      # all features of AOI_SOURCE
    elif not where:
        field_sql = arcpy.AddFieldDelimiters(AOI_SOURCE, AOI_FIELD)
        where = f"{field_sql} = '{AOI_NAME.replace(chr(39), chr(39) * 2)}'"

    src_lyr = "aoi_source_lyr"
    delete_if_exists(src_lyr)
    arcpy.management.MakeFeatureLayer(AOI_SOURCE, src_lyr, where)
    n_src = int(arcpy.management.GetCount(src_lyr)[0])
    if n_src == 0:
        # show the values that exist, to find the right spelling
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
    print(f"Analysis area query: {where or '(whole layer)'} "
          f"-> {n_src} feature(s)")

    aoi = os.path.join(OUTPUT_GDB, "AOI_dissolved")
    arcpy.analysis.PairwiseDissolve(src_lyr, aoi)
    arcpy.management.Delete(src_lyr)
    aoi_ha = area_ha(aoi)
    print(f"Analysis area: {aoi_ha:,.1f} ha")

    # Is the analysis area inside the area scripts 2 and 3 were run for?
    if IMPORT_AOI and arcpy.Exists(IMPORT_AOI):
        g_aoi = geometry_union(aoi)
        g_import = geometry_union(IMPORT_AOI)
        outside_ha = g_aoi.difference(g_import).area / 10000.0
        if outside_ha > 1:
            print(f"WARNING: {outside_ha:,.1f} ha of the analysis area lie "
                  f"outside {os.path.basename(IMPORT_AOI)}. Import_All.gdb "
                  "may be missing data there - run scripts 2 and 3 with "
                  "this area as AOI.")
        else:
            print(f"Analysis area lies inside {os.path.basename(IMPORT_AOI)} "
                  "- Import_All.gdb covers it.")

    aoi_lyr = "aoi_lyr"
    delete_if_exists(aoi_lyr)
    arcpy.management.MakeFeatureLayer(aoi, aoi_lyr)

    # ---- hard criteria from the CSV ----------------------------------------
    rows = read_criteria(BUFFER_CONFIG)
    hard = [r for r in rows if HARD_KEYWORD in r["use"].lower()]
    print(f"\nHard criteria in CSV: {len(hard)} of {len(rows)} rows")

    # ---- exceptions: e.g. Nadelholz in ax_wald is not excluded -------------
    for row in hard:
        exc = ALLOW_EXCEPTIONS.get(base_name(row["name"]))
        if not exc:
            continue
        values = ", ".join("'" + v.replace("'", "''") + "'"
                           for v in exc["allow"])
        rule = (f"({exc['field']} NOT IN ({values}) "
                f"OR {exc['field']} IS NULL)")
        row["sql"] = f"({row['sql']}) AND {rule}" if row["sql"] else rule
        row["check_field"] = exc["field"]
        print(f"Exception for {row['name']}: {', '.join(exc['allow'])} in "
              f"{exc['field']} is NOT excluded")

    for extra in EXTRA_HARD:
        hard.append({"line": "extra", "name": extra["name"],
                     "use": "extra", "buffer": float(extra["buffer"]),
                     "sql": extra.get("sql", ""), "path": extra["path"]})
    if EXTRA_HARD:
        print(f"Additional hard exclusions: "
              f"{', '.join(e['name'] for e in EXTRA_HARD)}")

    arcpy.env.workspace = INPUT_GDB
    gdb_layers = arcpy.ListFeatureClasses() or []

    # =========================================================================
    # STEP 1 - PREPARE EACH HARD CRITERION
    # =========================================================================

    report = []
    prepared = []        # (code, output fc, description)

    for i, row in enumerate(hard, start=1):
        code = f"H{i:02d}"
        desc = (f"{row['name']} {row['buffer']:g}m"
                + (f" [{row['sql']}]" if row["sql"] else ""))
        print(f"\n{code}: {desc}")

        if row.get("path"):
            # additional exclusion with its own path
            in_fc = row["path"]
            layer = os.path.basename(in_fc)
            if not arcpy.Exists(in_fc):
                print(f"    {in_fc} not found - skipped")
                report.append([code, row["line"], row["name"], "",
                               row["buffer"], row["sql"], "", "", "SKIPPED",
                               f"{in_fc} not found"])
                continue
        else:
            layer = find_layer(row["name"], gdb_layers)
            if not layer:
                print(f"    not in {os.path.basename(INPUT_GDB)} - skipped")
                report.append([code, row["line"], row["name"], "",
                               row["buffer"], row["sql"], "", "", "SKIPPED",
                               f"layer not in {os.path.basename(INPUT_GDB)}"])
                continue
            in_fc = os.path.join(INPUT_GDB, layer)
        shape_type = arcpy.Describe(in_fc).shapeType
        dist = row["buffer"]

        if shape_type != "Polygon" and dist <= 0:
            print(f"    {shape_type} layer without buffer - no area, skipped")
            report.append([code, row["line"], row["name"], layer, dist,
                           row["sql"], shape_type, "", "SKIPPED",
                           "point/line layer with buffer 0"])
            continue

        out_name = arcpy.ValidateTableName(f"{code}_{layer}", OUTPUT_GDB)
        out_fc = os.path.join(OUTPUT_GDB, out_name)
        tmp = os.path.join(OUTPUT_GDB, f"tmp_{code}")
        lyr = f"lyr_{code}"

        try:
            delete_if_exists(lyr, tmp, out_fc)

            # an exception field that doesn't exist would make the criterion
            # fail and the whole layer (e.g. all forest) would NOT be
            # excluded - stop instead
            if row.get("check_field"):
                names = [f.name.lower() for f in arcpy.ListFields(in_fc)]
                if row["check_field"].lower() not in names:
                    raise SystemExit(
                        f"STOP: field '{row['check_field']}' not found in "
                        f"{in_fc}. Fix ALLOW_EXCEPTIONS before running again.")

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
                report.append([code, row["line"], row["name"], layer, dist,
                               row["sql"], shape_type, 0, "SKIPPED",
                               "no features in/near AOI"])
                continue

            print(f"    {count} features from {layer}")

            # buffer (or dissolve) into one feature, then clip to the AOI
            if dist > 0:
                arcpy.analysis.PairwiseBuffer(
                    lyr, tmp, f"{dist} Meters", dissolve_option="ALL")
            else:
                arcpy.analysis.PairwiseDissolve(lyr, tmp)

            arcpy.analysis.PairwiseClip(tmp, aoi, out_fc)

            # cut out areas that should not be excluded by this criterion
            # (e.g. storm damage areas from forest)
            erase_note = ""
            for erase_src in ERASE_FROM_CRITERIA.get(base_name(row["name"]), []):
                erased_ha = erase_areas(out_fc, erase_src, aoi_lyr, code)
                erase_note += (f"; {erased_ha:.1f} ha removed "
                               f"({os.path.basename(erase_src)})")
                print(f"    {erased_ha:,.1f} ha of "
                      f"{os.path.basename(erase_src)} removed from {code}")

            # one flag field per criterion: Hxx = 1
            arcpy.management.AddField(out_fc, code, "SHORT",
                                      field_alias=desc[:255])
            arcpy.management.CalculateField(out_fc, code, "1", "PYTHON3")

            ha = area_ha(out_fc)
            print(f"    -> {out_name}: {ha:,.1f} ha in AOI")
            prepared.append((code, out_fc, desc))
            report.append([code, row["line"], row["name"], layer, dist,
                           row["sql"], shape_type, count, "OK",
                           f"{ha:.1f} ha in AOI{erase_note}"])

        except Exception as e:
            print(f"    FAILED: {e}")
            report.append([code, row["line"], row["name"], layer, dist,
                           row["sql"], shape_type, "", "FAILED", str(e)])

        finally:
            delete_if_exists(lyr, tmp)

    if not prepared:
        raise RuntimeError("No hard criterion could be prepared - see report")

    # =========================================================================
    # STEP 2 - UNION (AOI + all hard criteria)
    # =========================================================================

    print("\n================================================")
    print(f"UNION of AOI + {len(prepared)} hard criteria")
    print("================================================")

    union_fc = os.path.join(OUTPUT_GDB, "Union_Harte_Kriterien")
    delete_if_exists(union_fc)
    inputs = [aoi] + [fc for _, fc, _ in prepared]

    if arcpy.ProductInfo() == "ArcInfo":
        # ArcGIS Pro Advanced: all inputs in one Union
        arcpy.analysis.Union(inputs, union_fc, "NO_FID")
    else:
        # Basic/Standard: Union only takes 2 inputs -> step by step
        print("(Basic/Standard licence - union step by step)")
        current = inputs[0]
        for n, fc in enumerate(inputs[1:], start=1):
            out = union_fc if n == len(inputs) - 1 else \
                os.path.join(OUTPUT_GDB, f"tmp_union_{n}")
            print(f"    {n}/{len(inputs) - 1}: + {os.path.basename(fc)}")
            arcpy.analysis.Union([current, fc], out, "NO_FID")
            if current != aoi:
                delete_if_exists(current)
            current = out

    # (all criteria were clipped to the AOI, so the union covers exactly the AOI)

    # ---- repair geometry of the union result -------------------------------
    # Union can create slivers and invalid geometries along buffer edges.
    print("\nRepairing geometry of the union result ...")
    arcpy.management.RepairGeometry(union_fc, "DELETE_NULL")
    print(f"    {int(arcpy.management.GetCount(union_fc)[0])} polygons after repair")

    # ---- count criteria per polygon ----------------------------------------
    codes = [c for c, _, _ in prepared]
    arcpy.management.AddField(union_fc, "ANZ_HART", "SHORT",
                              field_alias="Anzahl harte Kriterien")
    arcpy.management.AddField(union_fc, "KRITERIEN", "TEXT",
                              field_length=500,
                              field_alias="Zutreffende harte Kriterien")

    with arcpy.da.UpdateCursor(union_fc,
                               codes + ["ANZ_HART", "KRITERIEN"]) as cur:
        for rec in cur:
            hits = [c for c, v in zip(codes, rec[:len(codes)]) if v == 1]
            rec[-2] = len(hits)
            rec[-1] = ";".join(hits)
            cur.updateRow(rec)

    # =========================================================================
    # STEP 3 - EXCLUSION AREAS AND POTENTIAL AREAS
    # =========================================================================

    excl_fc = os.path.join(OUTPUT_GDB, "Ausschluss_hart")
    pot_fc = os.path.join(OUTPUT_GDB, "Potentialflaechen_hart")
    tmp_pot = os.path.join(OUTPUT_GDB, "tmp_pot")
    delete_if_exists(excl_fc, pot_fc, tmp_pot)

    arcpy.management.MakeFeatureLayer(union_fc, "excl_lyr", "ANZ_HART > 0")
    arcpy.analysis.PairwiseDissolve("excl_lyr", excl_fc)
    arcpy.management.Delete("excl_lyr")

    # Dissolve all union polygons without a hard criterion into one feature,
    # then split it into separate areas with Multipart To Singlepart
    tmp_diss = os.path.join(OUTPUT_GDB, "tmp_pot_dissolved")
    delete_if_exists(tmp_diss)

    arcpy.management.MakeFeatureLayer(union_fc, "pot_lyr", "ANZ_HART = 0")
    arcpy.analysis.PairwiseDissolve("pot_lyr", tmp_diss,
                                    multi_part="MULTI_PART")
    arcpy.management.Delete("pot_lyr")

    arcpy.management.MultipartToSinglepart(tmp_diss, tmp_pot)
    delete_if_exists(tmp_diss)

    arcpy.management.AddField(tmp_pot, "FLAECHE_HA", "DOUBLE",
                              field_alias="Flaeche [ha]")
    arcpy.management.CalculateField(tmp_pot, "FLAECHE_HA",
                                    "!shape.area@hectares!", "PYTHON3")

    where = f"FLAECHE_HA >= {MIN_FLAECHE_HA}" if MIN_FLAECHE_HA > 0 else None
    arcpy.conversion.ExportFeatures(tmp_pot, pot_fc, where)
    delete_if_exists(tmp_pot)

    excl_ha = area_ha(excl_fc)
    pot_ha = area_ha(pot_fc)
    n_pot = int(arcpy.management.GetCount(pot_fc)[0])

    # =========================================================================
    # REPORT
    # =========================================================================

    write_csv(REPORT,
              ["Code", "CSV_Line", "CSV_Layer", "Input_Layer", "Buffer_m",
               "SQL", "Geometry", "Features_used", "Status", "Note"],
              report)

    print("\n================================================")
    print("UNION COMPLETE")
    print("================================================")
    print(f"Hard criteria used: {len(prepared)} of {len(hard)}")
    for code, fc, desc in prepared:
        print(f"  {code}: {desc}")
    print(f"\nAOI:                 {aoi_ha:12,.1f} ha")
    print(f"Excluded (hard):     {excl_ha:12,.1f} ha "
          f"({excl_ha / aoi_ha * 100:.1f} %)")
    print(f"Potential areas:     {pot_ha:12,.1f} ha "
          f"({pot_ha / aoi_ha * 100:.1f} %), {n_pot} polygons"
          + (f" >= {MIN_FLAECHE_HA} ha" if MIN_FLAECHE_HA > 0 else ""))
    print(f"\nResults: {OUTPUT_GDB}")
    print(f"Report:  {REPORT}")
    print(f"Runtime: {datetime.now() - start}")


if __name__ == "__main__":
    main()