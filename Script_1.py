import arcpy
import os
import csv

# =============================================================================
# SETTINGS
# =============================================================================

# Root folder containing all geodatabases
ROOT_FOLDER = r"C:\Bonnie_Winter\Wind_Project\Projektdaten Wind"

# Reports
COORD_REPORT = r"C:\Users\GIS308\Desktop\output\coordinate_system_report.csv"
PROJECT_REPORT = r"C:\Users\GIS308\Desktop\output\projection_report.csv"

# Target coordinate system (ETRS89 / UTM zone 32N)
TARGET_WKID = 25832
TARGET_SR = arcpy.SpatialReference(TARGET_WKID)

# Suffix for projected outputs
SUFFIX = "_new_pro"

# gdbs marked as not required are not projected
PREFIX = "NOT_REQUIRED_"

# Transformations to prefer whenever they apply, in order of preference.
# DHDN (Gauss-Krueger) -> ETRS89: BeTA2007 grid (NTv2) - decimetre accuracy
# instead of the metre-level error of the simple parameter methods.
PREFERRED_TRANSFORMS = ["DHDN_To_ETRS_1989_8_NTv2"]

arcpy.env.overwriteOutput = True


# =============================================================================
# HELPERS
# =============================================================================

def find_gdbs(root_folder):
    """Return all file geodatabases below root_folder (without walking into them)."""
    gdbs = []
    for root, dirs, files in os.walk(root_folder):
        for d in dirs:
            if d.lower().endswith(".gdb"):
                gdbs.append(os.path.join(root, d))
        # Don't descend into .gdb folders
        dirs[:] = [d for d in dirs if not d.lower().endswith(".gdb")]
    return sorted(gdbs)


def list_feature_classes(gdb):
    """Return (full_path, relative_name, dataset_or_None) for every FC in a gdb."""
    arcpy.env.workspace = gdb
    result = []

    # Standalone feature classes
    for fc in arcpy.ListFeatureClasses() or []:
        result.append((os.path.join(gdb, fc), fc, None))

    # Feature classes inside feature datasets
    for ds in arcpy.ListDatasets(feature_type="feature") or []:
        for fc in arcpy.ListFeatureClasses(feature_dataset=ds) or []:
            result.append((os.path.join(gdb, ds, fc), f"{ds}/{fc}", ds))

    return result


def pick_transformation(in_sr, out_sr, extent):
    """Return (transformation, warning).
    transformation is '' if none is needed or none could be found;
    warning is non-empty when a datum shift is needed but none was found."""

    # Same datum -> no transformation needed
    if in_sr.GCS.name == out_sr.GCS.name:
        return "", ""

    transforms = []
    try:
        transforms = arcpy.ListTransformations(in_sr, out_sr, extent) or []
    except Exception:
        pass

    # Empty feature classes have no valid extent - ask without it
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


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")     # opens correctly in German Excel
        writer.writerow(header)
        writer.writerows(rows)


gdbs = [g for g in find_gdbs(ROOT_FOLDER)
        if not os.path.basename(g).startswith(PREFIX)]
print(f"\nFound {len(gdbs)} geodatabases (without {PREFIX}*).")

# =============================================================================
# STAGE 1 - INVENTORY COORDINATE SYSTEMS
# =============================================================================

print("\n================================================")
print("STAGE 1 - INVENTORY COORDINATE SYSTEMS")
print("================================================")

coordinate_results = []

for gdb in gdbs:

    print(f"\nChecking: {gdb}")

    for fc_path, rel_name, ds in list_feature_classes(gdb):

        # Outputs of a previous run are not part of the inventory
        if rel_name.lower().endswith(SUFFIX):
            continue

        try:
            sr = arcpy.Describe(fc_path).spatialReference
            coordinate_results.append([gdb, rel_name, sr.name, sr.factoryCode])

        except Exception as e:
            print(f"Error reading {rel_name}: {e}")
            coordinate_results.append([gdb, rel_name, "ERROR", ""])

write_csv(COORD_REPORT,
          ["Geodatabase", "FeatureClass", "CoordinateSystem", "WKID"],
          coordinate_results)

print(f"\nCoordinate report written to:\n{COORD_REPORT}")

# =============================================================================
# STAGE 2 - PROJECT TO ETRS89 / UTM 32N
# =============================================================================

print("\n================================================")
print("STAGE 2 - PROJECT TO ETRS89 UTM 32N")
print("================================================")

projected, skipped, failed, warnings = 0, 0, 0, 0
project_rows = []

for gdb in gdbs:

    print(f"\n=== GDB: {gdb} ===")

    for fc_path, rel_name, ds in list_feature_classes(gdb):

        # Don't reproject outputs from a previous run
        if rel_name.lower().endswith(SUFFIX):
            continue

        try:
            desc = arcpy.Describe(fc_path)
            sr = desc.spatialReference

            print(f"\nChecking: {rel_name}")
            print(f"Projection: {sr.name} (WKID {sr.factoryCode})")

            if sr.factoryCode == TARGET_WKID:
                print("Already ETRS_1989_UTM_Zone_32N. Skipping.")
                skipped += 1
                project_rows.append([gdb, rel_name, sr.name, sr.factoryCode,
                                     "", "", "SKIPPED (already 25832)", ""])
                continue

            # Only a truly undefined coordinate system is skipped.
            # A custom definition without a WKID (factoryCode 0) can be projected.
            if sr.name == "Unknown":
                print("Unknown coordinate system. Skipping - define the projection first.")
                skipped += 1
                project_rows.append([gdb, rel_name, sr.name, sr.factoryCode,
                                     "", "", "SKIPPED (unknown CRS)", ""])
                continue

            if sr.factoryCode == 0:
                print("Custom coordinate system without WKID - projecting anyway.")

            # A feature dataset forces its own coordinate system on its
            # feature classes, so projected outputs go to the gdb root.
            out_name = os.path.basename(fc_path) + SUFFIX
            out_fc = os.path.join(gdb, out_name)

            transform, warning = pick_transformation(sr, TARGET_SR, desc.extent)
            if transform:
                print(f"Transformation: {transform}")
            if warning:
                print(warning)
                warnings += 1

            if arcpy.Exists(out_fc):
                print(f"Deleting existing {out_name}")
                arcpy.management.Delete(out_fc)

            print(f"Projecting -> {out_name}")

            arcpy.management.Project(
                in_dataset=fc_path,
                out_dataset=out_fc,
                out_coor_system=TARGET_SR,
                transform_method=transform
            )

            print("SUCCESS")
            projected += 1
            project_rows.append([gdb, rel_name, sr.name, sr.factoryCode,
                                 out_name, transform, "PROJECTED", warning])

        except Exception as e:
            print("\nFAILED")
            print(f"Feature Class: {rel_name}")
            print(f"Error: {e}")
            failed += 1
            project_rows.append([gdb, rel_name, "", "", "", "", "FAILED", str(e)])

write_csv(PROJECT_REPORT,
          ["Geodatabase", "FeatureClass", "CoordinateSystem", "WKID",
           "Output", "Transformation", "Status", "Note"],
          project_rows)

# =============================================================================
# COMPLETE
# =============================================================================

print("\n================================================")
print("PROCESS COMPLETE")
print("================================================")
print(f"Projected: {projected}  |  Skipped: {skipped}  |  Failed: {failed}  |  "
      f"Without datum transformation: {warnings}")
print(f"\nCoordinate System Report:\n{COORD_REPORT}")
print(f"Projection Report:\n{PROJECT_REPORT}")