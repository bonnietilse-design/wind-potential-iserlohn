import arcpy
import csv
import io
import os
from datetime import datetime

# =============================================================================
# SETTINGS
# =============================================================================

ROOT_FOLDER = r"C:\Bonnie_Winter\Wind_Project\Projektdaten Wind"
AOI = r"C:\Bonnie_Winter\Wind_Project\Projekt_Wind_Teilung.gdb\Nord"
OUTPUT_FOLDER = r"C:\Users\GIS308\Desktop\output"
BUFFER_CONFIG = r"C:\Bonnie_Winter\Wind_Project\Projektdaten Wind\SRV_Wind_DE.csv"

PREFIX = "NOT_REQUIRED_"

COL_PATH = "FC-Name(Pfad)"
COL_BUFFER = "Bufferdistanz(Zahloderleer)"

# Layers to ignore completely, using the normalised name
# (lower case, without _p/_l/_pt and without _new_pro), e.g. {"ax_strassenachse"}
SKIP_FCS = set()

# True  = a gdb only counts as required if a layer FROM THE CSV hits the AOI
#         (script 3 imports only those layers anyway)
# False = any layer hitting the AOI makes the gdb required (old behaviour)
DECIDE_ON_RULE_LAYERS_ONLY = True

# True  = gdbs with result "NO" are renamed to NOT_REQUIRED_<name> at the end,
#         so script 3 skips them without a manual step.
#         Close ArcGIS Pro (or remove those gdbs from the project) first.
# False = only the report is written; rename by hand (old behaviour)
RENAME_NOT_REQUIRED = False

GEOMETRY_SUFFIXES = ("_pt", "_p", "_l")
PRO_SUFFIX = "_new_pro"


# =============================================================================
# SHARED HELPERS (identical in scripts 2 and 3 - change both together)
# =============================================================================

def base_name(name):
    """ax_wohnbauflaeche_p, AX_Wohnbauflaeche, ax_wohnbauflaeche_new_pro,
    ax_wohnbauflaeche.shp all become 'ax_wohnbauflaeche'."""
    n = os.path.splitext(name.strip())[0].lower()
    if n.endswith(PRO_SUFFIX):
        n = n[:-len(PRO_SUFFIX)]
    for suffix in GEOMETRY_SUFFIXES:
        if n.endswith(suffix):
            return n[:-len(suffix)]
    return n


def read_buffer_rules(path):
    """Returns (rules, sources).
    rules:   {base_name: max buffer in m over all CSV rows of that layer}
    sources: {base_name: set of original CSV layer names}"""

    text = None
    for enc in ("utf-8-sig", "cp1252"):      # German Excel exports are often cp1252
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
    print(f"CSV delimiter: {delimiter!r}")

    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)

    for col in (COL_PATH, COL_BUFFER):
        if col not in (reader.fieldnames or []):
            raise RuntimeError(
                f"Column '{col}' not found. Fields: {reader.fieldnames}"
            )

    rules, sources = {}, {}

    for line_no, row in enumerate(reader, start=2):
        try:
            path_value = (row.get(COL_PATH) or "").replace("\\", "/").strip()
            name = os.path.basename(path_value)
            if not name:
                continue
            raw = (row.get(COL_BUFFER) or "").strip().replace(",", ".")
            distance = float(raw) if raw else 0.0
        except ValueError as e:
            print(f"CSV ERROR line {line_no}: {e}")
            continue

        key = base_name(name)
        rules[key] = max(rules.get(key, 0.0), distance)
        sources.setdefault(key, set()).add(name.lower())

    return rules, sources


def containing_gdb(path):
    """Return the .gdb folder a path lies in, or None (e.g. shapefile folders)."""
    p = path
    while p and os.path.dirname(p) != p:
        if p.lower().endswith(".gdb"):
            return p
        p = os.path.dirname(p)
    return None


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")     # opens correctly in German Excel
        writer.writerow(header)
        writer.writerows(rows)
    print(path)


# =============================================================================
# HELPERS
# =============================================================================

def extent_overlaps(fc_extent, aoi_extent, dist):
    return not (
        fc_extent.XMax < aoi_extent.XMin - dist
        or fc_extent.XMin > aoi_extent.XMax + dist
        or fc_extent.YMax < aoi_extent.YMin - dist
        or fc_extent.YMin > aoi_extent.YMax + dist
    )


# =============================================================================
# MAIN
# =============================================================================

def main():
    arcpy.env.overwriteOutput = True

    # ---- buffer rules -------------------------------------------------------
    rules, sources = read_buffer_rules(BUFFER_CONFIG)

    print(f"\nLoaded {len(rules)} buffer rules (one per layer, max distance):")
    for key in sorted(rules):
        print(f"  {key}: {rules[key]:g} m   <- {sorted(sources[key])}")

    # ---- geodatabases (do not descend into the .gdb folders) ---------------
    gdbs = []
    for root, dirs, files in os.walk(ROOT_FOLDER):
        for d in list(dirs):
            if d.lower().endswith(".gdb"):
                gdbs.append(os.path.join(root, d))
                dirs.remove(d)

    print(f"\nGDBS FOUND: {len(gdbs)}")

    # ---- AOI ----------------------------------------------------------------
    aoi_desc = arcpy.Describe(AOI)
    aoi_extent = aoi_desc.extent
    aoi_sr = aoi_desc.spatialReference

    AOI_LAYER = "AOI_LAYER"
    if arcpy.Exists(AOI_LAYER):
        arcpy.management.Delete(AOI_LAYER)
    arcpy.management.MakeFeatureLayer(AOI, AOI_LAYER)

    # ---- check --------------------------------------------------------------
    gdb_rows = []
    detail_rows = []
    unmatched_ax = set()
    not_required = []

    for gdb in sorted(gdbs):
        gdb_name = os.path.basename(gdb)
        if gdb_name.startswith(PREFIX):
            continue

        print(f"\nChecking {gdb_name}")

        found_fcs = []        # hits that decide the gdb status
        other_hits = []       # hits of layers without a CSV rule
        errors = []

        for dirpath, dirnames, feature_classes in arcpy.da.Walk(
                gdb, datatype="FeatureClass"):

            for fc in feature_classes:
                fc_path = os.path.join(dirpath, fc)
                key = base_name(fc)

                if key in SKIP_FCS:
                    detail_rows.append([gdb_name, fc, key, "", "SKIPPED", "", gdb])
                    continue

                # An original with a projected copy is checked via the copy.
                # (Projected copies of feature-dataset layers sit in the gdb root.)
                if not fc.lower().endswith(PRO_SUFFIX) and arcpy.Exists(
                        os.path.join(gdb, fc + PRO_SUFFIX)):
                    detail_rows.append([gdb_name, fc, key, "", "",
                                        "checked via _new_pro copy", gdb])
                    continue

                has_rule = key in rules
                dist = rules.get(key, 0.0)
                rule_status = "RULE" if has_rule else "NO RULE"
                decides = has_rule or not DECIDE_ON_RULE_LAYERS_ONLY

                if not has_rule and key.startswith("ax_"):
                    unmatched_ax.add(fc)

                result = ""

                try:
                    desc = arcpy.Describe(fc_path)
                    fc_sr = desc.spatialReference

                    # The extent pre-filter is only valid in the same CRS.
                    same_sr = (fc_sr.name == aoi_sr.name
                               and fc_sr.name != "Unknown")

                    if same_sr and not extent_overlaps(
                            desc.extent, aoi_extent, dist):
                        result = "no extent overlap"

                    else:
                        lyr = "temp_layer"
                        if arcpy.Exists(lyr):
                            arcpy.management.Delete(lyr)
                        arcpy.management.MakeFeatureLayer(fc_path, lyr)

                        try:
                            if dist > 0:
                                arcpy.management.SelectLayerByLocation(
                                    lyr, "WITHIN_A_DISTANCE", AOI_LAYER,
                                    f"{dist} Meters", "NEW_SELECTION")
                            else:
                                arcpy.management.SelectLayerByLocation(
                                    lyr, "INTERSECT", AOI_LAYER,
                                    None, "NEW_SELECTION")

                            count = int(arcpy.management.GetCount(lyr)[0])
                        finally:
                            arcpy.management.Delete(lyr)

                        if count > 0:
                            result = f"HIT ({count})"
                            entry = f"{fc} (buffer={dist:g}m, {count} features)"
                            if decides:
                                found_fcs.append(entry)
                            else:
                                other_hits.append(entry)
                            print(f"FOUND: {fc} (buffer={dist:g}m, {rule_status})")
                        else:
                            result = "no hit"

                except Exception as e:
                    result = f"ERROR: {e}"
                    if decides:
                        errors.append(f"{fc}: {e}")
                    print(f"ERROR: {fc} -> {e}")

                detail_rows.append([gdb_name, fc, key, f"{dist:g}",
                                    rule_status, result, gdb])

        if found_fcs:
            status = "YES"
        elif errors:
            status = "UNKNOWN (errors)"
        else:
            status = "NO"
            not_required.append(gdb)

        gdb_rows.append([gdb_name, status, "; ".join(found_fcs),
                         "; ".join(other_hits), "; ".join(errors)])

    # ---- reports ------------------------------------------------------------
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("\nREPORTS WRITTEN:")
    write_csv(
        os.path.join(OUTPUT_FOLDER, f"AOI_Check_Report_{stamp}.csv"),
        ["Geodatabase", "Contains_Features_In_AOI",
         "Feature_Classes_Found", "Hits_Of_Layers_Without_Rule", "Errors"],
        gdb_rows)

    write_csv(
        os.path.join(OUTPUT_FOLDER, f"AOI_Check_Detail_{stamp}.csv"),
        ["Geodatabase", "FeatureClass", "Normalised_Name",
         "Buffer_m", "Rule", "Result", "Geodatabase_Path"],
        detail_rows)

    if unmatched_ax:
        print("\nALKIS layers in your data with NO rule in the CSV "
              "(checked with buffer 0):")
        for name in sorted(unmatched_ax):
            print(f"  {name}")

    # ---- not required gdbs --------------------------------------------------
    print(f"\nGDBS NOT REQUIRED: {len(not_required)}")
    for gdb in not_required:
        print(f"  {os.path.basename(gdb)}")

    if RENAME_NOT_REQUIRED and not_required:
        arcpy.management.Delete(AOI_LAYER)
        arcpy.ClearWorkspaceCache_management()     # release locks held by arcpy
        print("\nRENAMING:")
        for gdb in not_required:
            new_path = os.path.join(os.path.dirname(gdb),
                                    PREFIX + os.path.basename(gdb))
            try:
                os.rename(gdb, new_path)
                print(f"  {os.path.basename(gdb)} -> {os.path.basename(new_path)}")
            except OSError as e:
                print(f"  FAILED {os.path.basename(gdb)}: {e} "
                      "(gdb open in ArcGIS Pro? rename by hand)")


if __name__ == "__main__":
    main()