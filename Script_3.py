import arcpy
import csv
import glob
import io
import os

# =============================================================================
# SETTINGS
# =============================================================================

# Folder (with subfolders) to search for feature data
SOURCE_FOLDER = r"C:\Bonnie_Winter\Wind_Project\Projektdaten Wind"

# Target geodatabase (created if it doesn't exist)
TARGET_GDB = r"C:\Bonnie_Winter\Wind_Project\Import_All.gdb"

# Log of what was imported
IMPORT_REPORT = r"C:\Users\GIS308\Desktop\output\import_report.csv"

# Criteria list - only layers named in this file are imported
BUFFER_CONFIG = r"C:\Bonnie_Winter\Wind_Project\Projektdaten Wind\SRV_Wind_DE.csv"

# Must match the projection and AOI scripts
TARGET_WKID = 25832
TARGET_SR = arcpy.SpatialReference(TARGET_WKID)
PREFIX = "NOT_REQUIRED_"

COL_PATH = "FC-Name(Pfad)"
COL_BUFFER = "Bufferdistanz(Zahloderleer)"

# Repair geometries after import
# True  = features with empty (null) geometry are deleted
# False = they are kept
DELETE_NULL = True

# True  = only layers with a HIT in the AOI check (script 2) are imported
# False = every CSV layer from gdbs not marked NOT_REQUIRED_
ONLY_AOI_HITS = True

# Detail report of script 2. Empty = use the newest AOI_Check_Detail_*.csv
# in AOI_REPORT_FOLDER. Run script 2 again after changing the data!
AOI_DETAIL_REPORT = ""
AOI_REPORT_FOLDER = r"C:\Users\GIS308\Desktop\output"

# Same as script 1: used when a layer without a _new_pro copy still has
# to be projected. DHDN (Gauss-Krueger) -> ETRS89: BeTA2007 grid.
PREFERRED_TRANSFORMS = ["DHDN_To_ETRS_1989_8_NTv2"]

GEOMETRY_SUFFIXES = ("_pt", "_p", "_l")
PRO_SUFFIX = "_new_pro"


# =============================================================================
# SHARED HELPERS (identical in scripts 2 and 3 - change both together)
# =============================================================================

def base_name(name):
    """ax_wohnbauflaeche_p, AX_Wohnbauflaeche, ax_wohnbauflaeche_new_pro
    all become 'ax_wohnbauflaeche'."""
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
    """Return the .gdb folder a path lies in, or None."""
    p = path
    while p and os.path.dirname(p) != p:
        if p.lower().endswith(".gdb"):
            return p
        p = os.path.dirname(p)
    return None


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")     # opens correctly in German Excel
        writer.writerow(header)
        writer.writerows(rows)


# =============================================================================
# HELPERS
# =============================================================================

def in_not_required_gdb(path):
    """True if any .gdb in the path starts with the NOT_REQUIRED_ prefix."""
    parts = os.path.normpath(path).split(os.sep)
    return any(
        part.lower().endswith(".gdb") and part.startswith(PREFIX)
        for part in parts
    )


def pick_transformation(in_sr, out_sr, extent):
    """Same as script 1. Returns (transformation, warning)."""
    if in_sr.GCS.name == out_sr.GCS.name:
        return "", ""

    transforms = []
    try:
        transforms = arcpy.ListTransformations(in_sr, out_sr, extent) or []
    except Exception:
        pass
    if not transforms:
        try:
            transforms = arcpy.ListTransformations(in_sr, out_sr) or []
        except Exception:
            transforms = []

    for t in PREFERRED_TRANSFORMS:
        if t in transforms:
            return t, ""
    if transforms:
        return transforms[0], ""

    return "", (f"WARNING: no transformation {in_sr.GCS.name} -> "
                f"{out_sr.GCS.name} found - datum shift NOT applied")


def read_aoi_results():
    """Return {(gdb, feature class): result} from script 2's detail report.
    gdb is the full gdb path if the report has it, else the gdb name."""
    path = AOI_DETAIL_REPORT
    if not path:
        candidates = glob.glob(os.path.join(AOI_REPORT_FOLDER,
                                            "AOI_Check_Detail_*.csv"))
        if not candidates:
            raise RuntimeError(
                f"No AOI_Check_Detail_*.csv in {AOI_REPORT_FOLDER} - "
                "run script 2 first or set ONLY_AOI_HITS = False")
        path = max(candidates)          # time stamp in name -> newest last

    print(f"AOI check results from: {path}")

    results = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            gdb = (row.get("Geodatabase_Path") or row["Geodatabase"]).strip()
            gdb = os.path.normcase(os.path.normpath(gdb))
            results[(gdb, row["FeatureClass"].strip().lower())] = row["Result"]
    return results


def aoi_key(gdb, fc, by_path):
    g = gdb if by_path else os.path.basename(gdb)
    return (os.path.normcase(os.path.normpath(g)), fc.lower())


def check_and_repair(fc):
    """Count geometry problems, repair them, and return (problems, status)."""
    check_table = r"memory\geom_check"
    if arcpy.Exists(check_table):
        arcpy.management.Delete(check_table)

    arcpy.management.CheckGeometry(fc, check_table)
    problems = int(arcpy.management.GetCount(check_table)[0])
    arcpy.management.Delete(check_table)

    if problems == 0:
        return 0, "No problems"

    arcpy.management.RepairGeometry(
        fc,
        "DELETE_NULL" if DELETE_NULL else "KEEP_NULL"
    )
    return problems, "Repaired"


# =============================================================================
# MAIN
# =============================================================================

def main():
    arcpy.env.overwriteOutput = True
    scratch = arcpy.env.scratchGDB      # temporary projected copies

    # ---- layer names from the CSV (same reading as the AOI script) ---------
    rules, sources = read_buffer_rules(BUFFER_CONFIG)
    print(f"\nLoaded {len(rules)} layer names from the CSV.")

    # ---- target gdb ---------------------------------------------------------
    if not arcpy.Exists(TARGET_GDB):
        arcpy.management.CreateFileGDB(
            os.path.dirname(TARGET_GDB),
            os.path.basename(TARGET_GDB)
        )
        print(f"Created: {TARGET_GDB}")

    target_norm = os.path.normcase(os.path.abspath(TARGET_GDB))

    # ---- AOI check results --------------------------------------------------
    aoi_results, by_path = {}, False
    if ONLY_AOI_HITS:
        aoi_results = read_aoi_results()
        by_path = any(os.sep in g for g, _ in aoi_results)
        hits = sum(1 for r in aoi_results.values() if r.startswith("HIT"))
        print(f"Layers with a HIT in the AOI check: {hits}")

    # =========================================================================
    # STEP 1 - COLLECT SOURCES PER LAYER
    # =========================================================================
    # Rules:
    #   - only layers named in the CSV are imported
    #   - gdbs starting with NOT_REQUIRED_ are skipped
    #   - with ONLY_AOI_HITS: only layers with a HIT in script 2's report
    #   - a layer with a _new_pro copy is imported from the copy
    #     (under its original name, so buffer lookups still match)
    #   - a layer not in 25832 without a copy is projected here (as in script 1)
    #   - a layer with an unknown CRS is NOT imported (define it first)
    #   - the same layer found in several gdbs is merged into ONE output

    groups = {}      # lower-case name -> {"name": str, "sources": [dict, ...]}
    report = []
    replaced, unknown, no_hit, not_checked = 0, 0, 0, 0

    for dirpath, dirnames, filenames in arcpy.da.Walk(
            SOURCE_FOLDER, datatype="FeatureClass"):

        # Don't read from the target gdb itself
        if os.path.normcase(os.path.abspath(dirpath)).startswith(target_norm):
            continue

        # Skip gdbs marked as not required
        if in_not_required_gdb(dirpath):
            continue

        gdb = containing_gdb(dirpath)

        for fc in filenames:

            in_fc = os.path.join(dirpath, fc)
            base = os.path.splitext(fc)[0]

            # Only layers named in the CSV
            if base_name(base) not in rules:
                continue

            if base.lower().endswith(PRO_SUFFIX):
                # Projected copy -> import under the original name
                base = base[:-len(PRO_SUFFIX)]

            elif gdb and arcpy.Exists(os.path.join(gdb, base + PRO_SUFFIX)):
                # Original with a projected copy (copies sit in the gdb root)
                replaced += 1
                continue

            # Only layers that hit the AOI (as checked by script 2)
            if ONLY_AOI_HITS:
                result = (aoi_results.get(aoi_key(gdb, fc, by_path))
                          if gdb else None)
                if result is None:
                    print(f"SKIPPED (not in AOI check report): {in_fc}")
                    report.append([in_fc, "", "", "NOT AOI-CHECKED",
                                   "not in the AOI check report - "
                                   "run script 2 again", "", ""])
                    not_checked += 1
                    continue
                if not result.startswith("HIT"):
                    report.append([in_fc, "", "", "NO AOI HIT",
                                   f"AOI check: {result}", "", ""])
                    no_hit += 1
                    continue

            # Coordinate system
            try:
                desc = arcpy.Describe(in_fc)
                sr = desc.spatialReference
                sr_name, wkid = sr.name, sr.factoryCode
            except Exception as e:
                sr, sr_name, wkid = None, f"ERROR ({e})", None

            if sr is None or sr_name == "Unknown":
                print(f"SKIPPED (unknown coordinate system): {in_fc}")
                report.append([in_fc, "", "", "SKIPPED",
                               f"coordinate system: {sr_name} - "
                               "run Define Projection first", "", ""])
                unknown += 1
                continue

            source = {"path": in_fc, "project": False,
                      "transform": "", "note": ""}

            if wkid != TARGET_WKID:
                transform, warning = pick_transformation(
                    sr, TARGET_SR, desc.extent)
                source.update(project=True, transform=transform)
                source["note"] = (f"projected on import from {sr_name} "
                                  f"(WKID {wkid})"
                                  + (f", {transform}" if transform else "")
                                  + (f"; {warning}" if warning else ""))

            group = groups.setdefault(base.lower(),
                                      {"name": base, "sources": []})
            group["sources"].append(source)

    # =========================================================================
    # STEP 2 - IMPORT (one output per layer) AND REPAIR
    # =========================================================================

    imported, failed, repaired, merged = 0, 0, 0, 0
    used_names = set()

    for key in sorted(groups):
        group = groups[key]
        srcs = group["sources"]
        source_list = " | ".join(s["path"] for s in srcs)
        note = "; ".join(s["note"] for s in srcs if s["note"])

        out_name = arcpy.ValidateTableName(group["name"], TARGET_GDB)
        if out_name.lower() in used_names:
            out_name = os.path.basename(
                arcpy.CreateUniqueName(out_name, TARGET_GDB))
            note = (note + "; " if note else "") + "name changed (clash)"
        used_names.add(out_name.lower())
        out_fc = os.path.join(TARGET_GDB, out_name)

        temp_fcs = []

        # ---------------------------------------------------------------------
        # Import
        # ---------------------------------------------------------------------

        try:
            # Project sources that are not yet in 25832 into temporary copies
            inputs = []
            for i, s in enumerate(srcs):
                if s["project"]:
                    tmp = os.path.join(scratch, f"tmp_proj_{i}")
                    if arcpy.Exists(tmp):
                        arcpy.management.Delete(tmp)
                    print(f"Projecting: {s['path']}")
                    arcpy.management.Project(
                        in_dataset=s["path"],
                        out_dataset=tmp,
                        out_coor_system=TARGET_SR,
                        transform_method=s["transform"]
                    )
                    temp_fcs.append(tmp)
                    inputs.append(tmp)
                else:
                    inputs.append(s["path"])

            if arcpy.Exists(out_fc):
                arcpy.management.Delete(out_fc)

            if len(inputs) == 1:
                print(f"Importing: {srcs[0]['path']}  ->  {out_name}")
                arcpy.conversion.ExportFeatures(inputs[0], out_fc)
            else:
                print(f"Merging {len(inputs)} sources  ->  {out_name}")
                for s in srcs:
                    print(f"    {s['path']}")
                arcpy.management.Merge(inputs, out_fc,
                                       add_source="ADD_SOURCE_INFO")
                merged += 1

            if note:
                print(f"    {note}")

        except Exception as e:
            print(f"    FAILED: {e}")
            report.append([source_list, out_name, "", "FAILED", str(e), "", ""])
            failed += 1
            continue

        finally:
            for tmp in temp_fcs:
                try:
                    if arcpy.Exists(tmp):
                        arcpy.management.Delete(tmp)
                except Exception:
                    pass

        # ---------------------------------------------------------------------
        # Repair geometry of the imported copy (source data is untouched)
        # ---------------------------------------------------------------------

        try:
            problems, repair_status = check_and_repair(out_fc)
            if problems:
                print(f"    Geometry: {problems} problem(s) found and repaired")
                repaired += 1
        except Exception as e:
            problems, repair_status = "", f"REPAIR FAILED: {e}"
            print(f"    {repair_status}")

        count = int(arcpy.management.GetCount(out_fc)[0])
        report.append([source_list, out_name, count, "OK", note,
                       problems, repair_status])
        imported += 1

    # =========================================================================
    # WRITE REPORT
    # =========================================================================

    write_csv(IMPORT_REPORT,
              ["Source(s)", "ImportedAs", "FeatureCount", "Status", "Note",
               "GeometryProblems", "Repair"],
              report)

    print("\n================================================")
    print("IMPORT COMPLETE")
    print("================================================")
    print(f"Layers imported: {imported}  (of which merged from several "
          f"sources: {merged})  |  Failed: {failed}")
    print(f"Originals replaced by _new_pro: {replaced}  |  "
          f"Skipped, unknown CRS: {unknown}")
    if ONLY_AOI_HITS:
        print(f"Skipped, no AOI hit: {no_hit}  |  "
              f"Skipped, not in AOI check report: {not_checked}")
    print(f"Layers with repaired geometry: {repaired}")
    print(f"\nReport:\n{IMPORT_REPORT}")


if __name__ == "__main__":
    main()