import arcpy
import csv
import io
import os
from datetime import datetime

# =============================================================================
# SCRIPT 3b - EXPORT THE DATA FOR ISERLOHN (+ BUFFER ZONE AROUND IT)
# =============================================================================
# For every layer in Import_All.gdb, all features are exported that lie
#   - inside Iserlohn, or
#   - outside Iserlohn but within that layer's buffer distance of the border
#     (the largest distance for this layer in the criteria CSV, hard and soft).
#
# Features are exported WHOLE (not clipped), so their buffers are correct.
# Run after script 3, before script 4.

# =============================================================================
# SETTINGS
# =============================================================================

INPUT_GDB = r"C:\Bonnie_Winter\Wind_Project\Import_All.gdb"
OUTPUT_GDB = r"C:\Bonnie_Winter\Wind_Project\Import_Iserlohn.gdb"
BUFFER_CONFIG = r"C:\Bonnie_Winter\Wind_Project\Projektdaten Wind\SRV_Wind_DE.csv"
REPORT = r"C:\Users\GIS308\Desktop\output\export_iserlohn_report.csv"

# Analysis area (same as script 4)
AOI_SOURCE = r"C:\Bonnie_Winter\Wind_Project\Projekt_Wind_Teilung.gdb\Nord"
AOI_FIELD = "gemarkungsnummer_klartext"
AOI_NAME = "Iserlohn"

# Distance for layers that are not in the CSV (0 = only inside Iserlohn)
DEFAULT_DISTANCE = 0

COL_PATH = "FC-Name(Pfad)"
COL_BUFFER = "Bufferdistanz(Zahloderleer)"

TARGET_WKID = 25832
GEOMETRY_SUFFIXES = ("_pt", "_p", "_l")
PRO_SUFFIX = "_new_pro"


# =============================================================================
# HELPERS (same name matching as scripts 2, 3 and 4)
# =============================================================================

def base_name(name):
    n = os.path.splitext(name.strip())[0].lower()
    if n.endswith(PRO_SUFFIX):
        n = n[:-len(PRO_SUFFIX)]
    for suffix in GEOMETRY_SUFFIXES:
        if n.endswith(suffix):
            return n[:-len(suffix)]
    return n


def read_buffer_rules(path):
    """Return ({exact csv name (lower): max buffer},
               {base name: max buffer}) over ALL rows (hard and soft)."""
    text = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            with open(path, encoding=enc, newline="") as f:
                text = f.read()
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise RuntimeError(f"Could not decode {path}")

    first_line = text.splitlines()[0]
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)

    exact, by_base = {}, {}
    for row in reader:
        name = os.path.basename(
            (row.get(COL_PATH) or "").replace("\\", "/").strip())
        if not name:
            continue
        raw = (row.get(COL_BUFFER) or "").strip().replace(",", ".")
        try:
            dist = float(raw) if raw else 0.0
        except ValueError:
            dist = 0.0
        exact[name.lower()] = max(exact.get(name.lower(), 0.0), dist)
        key = base_name(name)
        by_base[key] = max(by_base.get(key, 0.0), dist)
    return exact, by_base


def distance_for(layer, exact, by_base):
    """Exact CSV name first (ax_dammwalldeich_l), then base name
    (ALKIS ax_wald -> ax_wald_p)."""
    if layer.lower() in exact:
        return exact[layer.lower()], "CSV"
    key = base_name(layer)
    if key in by_base:
        return by_base[key], "CSV (base name)"
    return DEFAULT_DISTANCE, "not in CSV - default"


def delete_if_exists(*paths):
    for p in paths:
        if p and arcpy.Exists(p):
            arcpy.management.Delete(p)


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)


# =============================================================================
# MAIN
# =============================================================================

def main():
    start = datetime.now()
    arcpy.env.overwriteOutput = True

    # ---- output gdb ---------------------------------------------------------
    if not arcpy.Exists(OUTPUT_GDB):
        arcpy.management.CreateFileGDB(os.path.dirname(OUTPUT_GDB),
                                       os.path.basename(OUTPUT_GDB))
        print(f"Created: {OUTPUT_GDB}")

    # ---- Iserlohn as one polygon --------------------------------------------
    field_sql = arcpy.AddFieldDelimiters(AOI_SOURCE, AOI_FIELD)
    where = f"{field_sql} = '{AOI_NAME.replace(chr(39), chr(39) * 2)}'"

    src_lyr = "aoi_source_lyr"
    delete_if_exists(src_lyr)
    arcpy.management.MakeFeatureLayer(AOI_SOURCE, src_lyr, where)
    if int(arcpy.management.GetCount(src_lyr)[0]) == 0:
        raise RuntimeError(f"No features selected with: {where}")

    arcpy.env.outputCoordinateSystem = arcpy.SpatialReference(TARGET_WKID)
    aoi = os.path.join(OUTPUT_GDB, "AOI_Iserlohn")
    arcpy.analysis.PairwiseDissolve(src_lyr, aoi)
    arcpy.management.Delete(src_lyr)
    arcpy.env.outputCoordinateSystem = None

    aoi_lyr = "aoi_lyr"
    delete_if_exists(aoi_lyr)
    arcpy.management.MakeFeatureLayer(aoi, aoi_lyr)
    print(f"Analysis area: {where}")

    # ---- buffer distances ---------------------------------------------------
    exact, by_base = read_buffer_rules(BUFFER_CONFIG)

    # ---- export every layer -------------------------------------------------
    arcpy.env.workspace = INPUT_GDB
    layers = sorted(arcpy.ListFeatureClasses() or [])
    print(f"\nLayers in {os.path.basename(INPUT_GDB)}: {len(layers)}")

    report = []
    exported, empty, failed = 0, 0, 0

    for layer in layers:
        in_fc = os.path.join(INPUT_GDB, layer)
        out_fc = os.path.join(OUTPUT_GDB, layer)
        dist, source = distance_for(layer, exact, by_base)
        lyr = "export_lyr"

        try:
            delete_if_exists(lyr, out_fc)
            total = int(arcpy.management.GetCount(in_fc)[0])
            arcpy.management.MakeFeatureLayer(in_fc, lyr)

            if dist > 0:
                arcpy.management.SelectLayerByLocation(
                    lyr, "WITHIN_A_DISTANCE", aoi_lyr, f"{dist} Meters",
                    "NEW_SELECTION")
            else:
                arcpy.management.SelectLayerByLocation(
                    lyr, "INTERSECT", aoi_lyr, None, "NEW_SELECTION")

            count = int(arcpy.management.GetCount(lyr)[0])

            if count == 0:
                print(f"{layer}: no features within {dist:g} m - not exported")
                report.append([layer, dist, source, total, 0,
                               "NO FEATURES"])
                empty += 1
                continue

            arcpy.conversion.ExportFeatures(lyr, out_fc)
            print(f"{layer}: {count} of {total} features "
                  f"(inside + {dist:g} m)")
            report.append([layer, dist, source, total, count, "EXPORTED"])
            exported += 1

        except Exception as e:
            print(f"{layer}: FAILED - {e}")
            report.append([layer, dist, source, "", "", f"FAILED: {e}"])
            failed += 1

        finally:
            delete_if_exists(lyr)

    write_csv(REPORT,
              ["Layer", "Distance_m", "Distance_from", "Features_total",
               "Features_exported", "Status"],
              report)

    print("\n================================================")
    print("EXPORT COMPLETE")
    print("================================================")
    print(f"Exported: {exported}  |  No features near Iserlohn: {empty}  |  "
          f"Failed: {failed}")
    print(f"\nResults: {OUTPUT_GDB}")
    print(f"Report:  {REPORT}")
    print(f"Runtime: {datetime.now() - start}")


if __name__ == "__main__":
    main()