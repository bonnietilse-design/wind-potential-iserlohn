import arcpy
import csv
import os

# =============================================================================
# SCRIPT 5 - REMOVE POTENTIAL AREAS THAT ARE TOO SMALL OR TOO NARROW
# =============================================================================
# Works on the result of script 4 (Potentialflaechen_bewertet). The input gdb
# is only read; results go to a separate gdb.
#
# Every potential area is judged as a whole - parts attached to a larger area
# are never cut off.
#
# 1. MIN_AP_RATIO - shape index area / perimeter (SHAPE_Area / SHAPE_Length).
#    For a long strip of width w it is about w / 2, so it measures how wide
#    an area is:
#        20 m wide road strip        ->  ~10
#        100 m x 100 m square (1 ha) ->   25
#    Areas below the value are removed as "zu schmal" (road strips).
#
# 2. MIN_AREA_HA - minimum area. Smaller areas are removed as "zu klein" -
#    UNLESS they lie within NEAR_DISTANCE_M of an area that passes both
#    filters, because they can be used together with that larger area.
#    (Narrow strips are removed even if they lie next to a large area.)
#
# Output in OUTPUT_GDB:
#   Potentialflaechen_bereinigt   kept areas, new RANG; field BEZUG shows
#                                 small areas kept because of a large neighbour
#   Potentialflaechen_entfernt    removed areas, reason in AUSSCHLUSS
# plus REPORT (CSV): every area with status (kept / removed) and the reason

# =============================================================================
# SETTINGS
# =============================================================================

GDB = r"C:\Bonnie_Winter\Wind_Project\Analyse_Wind_Iserlohn_weich_Windwurf_UWexc.gdb"
OUTPUT_GDB = r"C:\Bonnie_Winter\Wind_Project\Analyse_Wind_Iserlohn_weich_Windwurf_bereinigt_UWexc.gdb"
INPUT_NAME = "Potentialflaechen_bewertet"
OUTPUT_NAME = "Potentialflaechen_bereinigt"
REMOVED_NAME = "Potentialflaechen_entfernt"

# Report listing every area with its status and the reason (opens in Excel)
REPORT = r"C:\Users\GIS308\Desktop\output\filter_report_Iserlohn_weich_Windwurf_UWexc.csv"

MIN_AP_RATIO = 15       # area / perimeter in m - below = too narrow (0 = off)
MIN_AREA_HA = 1.0       # minimum area in ha                       (0 = off)
NEAR_DISTANCE_M = 50    # small areas this close to a large one are kept
                        # (0 = only if they touch)


# =============================================================================
# HELPERS
# =============================================================================

def delete_if_exists(*paths):
    for p in paths:
        if p and arcpy.Exists(p):
            arcpy.management.Delete(p)


def field_names(fc):
    return [f.name for f in arcpy.ListFields(fc)]


def write_report(kept_fc, removed_fc):
    """CSV with one row per area: kept areas first (by RANG), then removed
    areas (largest first), each with the reason."""
    wanted = ["PF_ID", "FLAECHE_HA_NEU", "FORM_INDEX", "KLASSE",
              "SCORE_MITTEL", "SCORE_GESAMT", "RANG"]

    def read(fc, status, text_field):
        have = field_names(fc)
        cols = [c for c in wanted if c in have] + [text_field]
        rows = []
        with arcpy.da.SearchCursor(fc, cols) as cur:
            for values in cur:
                rec = dict(zip(cols, values))
                rec["STATUS"] = status
                rec["BEGRUENDUNG"] = rec.pop(text_field) or (
                    "erfuellt Mindestflaeche und Mindestbreite")
                rows.append(rec)
        return rows

    kept = read(kept_fc, "BEIBEHALTEN", "BEZUG")
    kept.sort(key=lambda r: r.get("RANG") or 10 ** 9)
    removed = read(removed_fc, "ENTFERNT", "AUSSCHLUSS")
    removed.sort(key=lambda r: -(r.get("FLAECHE_HA_NEU") or 0))
    for r in removed:
        r["RANG"] = None            # old rank no longer applies

    header = ["STATUS", "PF_ID", "RANG", "FLAECHE_HA_NEU", "FORM_INDEX",
              "KLASSE", "SCORE_MITTEL", "SCORE_GESAMT", "BEGRUENDUNG"]
    labels = {"STATUS": "Status", "PF_ID": "Flaechen-ID", "RANG": "Rang",
              "FLAECHE_HA_NEU": "Flaeche [ha]",
              "FORM_INDEX": "Formindex (Flaeche/Umfang) [m]",
              "KLASSE": "Klasse", "SCORE_MITTEL": "Score weich (Mittel)",
              "SCORE_GESAMT": "Score gesamt", "BEGRUENDUNG": "Begruendung"}

    def fmt(v):
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:.2f}".replace(".", ",")    # German Excel
        return str(v)

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([f"Filter: Formindex >= {MIN_AP_RATIO} m, Flaeche >= "
                    f"{MIN_AREA_HA} ha, kleine Flaechen innerhalb "
                    f"{NEAR_DISTANCE_M:g} m einer grossen Flaeche beibehalten"])
        w.writerow([f"Beibehalten: {len(kept)}", f"Entfernt: {len(removed)}"])
        w.writerow([])
        w.writerow([labels[h] for h in header])
        for r in kept + removed:
            w.writerow([fmt(r.get(h)) for h in header])


# =============================================================================
# MAIN
# =============================================================================

def main():
    arcpy.env.overwriteOutput = True

    if not arcpy.Exists(OUTPUT_GDB):
        arcpy.management.CreateFileGDB(os.path.dirname(OUTPUT_GDB),
                                       os.path.basename(OUTPUT_GDB))
        print(f"Created: {OUTPUT_GDB}")

    in_fc = os.path.join(GDB, INPUT_NAME)
    out_fc = os.path.join(OUTPUT_GDB, OUTPUT_NAME)
    removed_fc = os.path.join(OUTPUT_GDB, REMOVED_NAME)
    work_fc = os.path.join(arcpy.env.scratchGDB, "tmp_filter")

    if not arcpy.Exists(in_fc):
        raise RuntimeError(f"Not found: {in_fc}")
    delete_if_exists(out_fc, removed_fc, work_fc)

    arcpy.management.CopyFeatures(in_fc, work_fc)
    n_in = int(arcpy.management.GetCount(work_fc)[0])
    area_in = sum(r[0] for r in arcpy.da.SearchCursor(work_fc, ["SHAPE@AREA"]))
    print(f"Input: {n_in} potential areas, {area_in / 10000:.1f} ha")

    for name, ftype in (("FLAECHE_HA_NEU", "DOUBLE"), ("FORM_INDEX", "DOUBLE"),
                        ("KLASSE", "TEXT"), ("AUSSCHLUSS", "TEXT"),
                        ("BEZUG", "TEXT")):
        if name not in field_names(work_fc):
            arcpy.management.AddField(
                work_fc, name, ftype,
                field_length=150 if ftype == "TEXT" else None)

    # ---- 1. + 2. judge every area ------------------------------------------
    # KLASSE: GROSS (passes both), KLEIN (only too small), SCHMAL (too narrow)
    geom = {oid: (a, l) for oid, a, l in arcpy.da.SearchCursor(
        work_fc, ["OID@", "SHAPE@AREA", "SHAPE@LENGTH"])}

    with arcpy.da.UpdateCursor(
            work_fc, ["OID@", "FLAECHE_HA_NEU", "FORM_INDEX", "KLASSE",
                      "AUSSCHLUSS"]) as cur:
        for oid, *_ in cur:
            area, length = geom[oid]
            ha = area / 10000
            ratio = area / length if length else 0
            if MIN_AP_RATIO > 0 and ratio < MIN_AP_RATIO:
                klasse = "SCHMAL"
                reason = f"zu schmal (Index {ratio:.1f} < {MIN_AP_RATIO})"
            elif MIN_AREA_HA > 0 and ha < MIN_AREA_HA:
                klasse = "KLEIN"
                reason = f"zu klein ({ha:.2f} ha < {MIN_AREA_HA} ha)"
            else:
                klasse, reason = "GROSS", None
            cur.updateRow([oid, round(ha, 3), round(ratio, 1), klasse, reason])

    # ---- keep small areas next to a large area ------------------------------
    n_kept_small = 0
    small_lyr, large_lyr = "small_lyr", "large_lyr"
    delete_if_exists(small_lyr, large_lyr)
    arcpy.management.MakeFeatureLayer(work_fc, small_lyr, "KLASSE = 'KLEIN'")
    arcpy.management.MakeFeatureLayer(work_fc, large_lyr, "KLASSE = 'GROSS'")

    if (int(arcpy.management.GetCount(small_lyr)[0]) > 0
            and int(arcpy.management.GetCount(large_lyr)[0]) > 0):
        if NEAR_DISTANCE_M > 0:
            arcpy.management.SelectLayerByLocation(
                small_lyr, "WITHIN_A_DISTANCE", large_lyr,
                f"{NEAR_DISTANCE_M} Meters", "NEW_SELECTION")
        else:
            arcpy.management.SelectLayerByLocation(
                small_lyr, "INTERSECT", large_lyr, None, "NEW_SELECTION")

        note = (f"klein, aber innerhalb {NEAR_DISTANCE_M:g} m einer grossen "
                "Flaeche - beibehalten")
        with arcpy.da.UpdateCursor(small_lyr, ["AUSSCHLUSS", "BEZUG"]) as cur:
            for row in cur:      # only the selected small areas
                cur.updateRow([None, note])
                n_kept_small += 1
    delete_if_exists(small_lyr, large_lyr)

    # ---- split into kept / removed -----------------------------------------
    lyr = "work_lyr"
    delete_if_exists(lyr)
    arcpy.management.MakeFeatureLayer(work_fc, lyr, "AUSSCHLUSS IS NULL")
    arcpy.conversion.ExportFeatures(lyr, out_fc)
    arcpy.management.Delete(lyr)
    arcpy.management.MakeFeatureLayer(work_fc, lyr, "AUSSCHLUSS IS NOT NULL")
    arcpy.conversion.ExportFeatures(lyr, removed_fc)
    arcpy.management.Delete(lyr)

    # ---- new ranking ---------------------------------------------------------
    names = field_names(out_fc)
    sort_field = next((f for f in ("SCORE_GESAMT", "SCORE_MITTEL")
                       if f in names), None)
    if sort_field:
        if "RANG" not in names:
            arcpy.management.AddField(out_fc, "RANG", "LONG")
        rows = sorted(
            ((oid, score if score is not None else float("-inf"), ha)
             for oid, score, ha in arcpy.da.SearchCursor(
                 out_fc, ["OID@", sort_field, "FLAECHE_HA_NEU"])),
            key=lambda x: (-x[1], -x[2]))
        rank = {oid: i + 1 for i, (oid, _, _) in enumerate(rows)}
        with arcpy.da.UpdateCursor(out_fc, ["OID@", "RANG"]) as cur:
            for oid, _ in cur:
                cur.updateRow([oid, rank[oid]])
        print(f"New RANG by {sort_field} (ties: larger area first)")

    # ---- report: every area with status and reason --------------------------
    write_report(out_fc, removed_fc)

    # ---- summary ------------------------------------------------------------
    def count(fc, where=None):
        return sum(1 for _ in arcpy.da.SearchCursor(fc, ["OID@"], where))

    area_out = sum(r[0] for r in arcpy.da.SearchCursor(out_fc, ["SHAPE@AREA"]))
    print("\n================================================")
    print("FILTER COMPLETE")
    print("================================================")
    print(f"Settings: area/perimeter >= {MIN_AP_RATIO} | area >= "
          f"{MIN_AREA_HA} ha | small areas within {NEAR_DISTANCE_M:g} m "
          "of a large one kept")
    print(f"Before:  {n_in} areas, {area_in / 10000:.1f} ha")
    print(f"After:   {count(out_fc)} areas, {area_out / 10000:.1f} ha "
          f"(of which {n_kept_small} small areas kept next to a large one)")
    n_narrow = count(removed_fc, "KLASSE = 'SCHMAL'")
    n_small = count(removed_fc, "KLASSE = 'KLEIN'")
    print(f"Removed: {n_narrow} too narrow, {n_small} too small "
          f"-> {REMOVED_NAME}")
    print(f"\nResults: {OUTPUT_GDB}")
    print(f"Report:  {REPORT}")

    delete_if_exists(work_fc)


if __name__ == "__main__":
    main()