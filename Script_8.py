# =================================================================================================================
# Kartenserie Windpotentialflächen Iserlohn
# -----------------------------------------------------------------------------------------------------------------
# Erzeugt in ArcGIS Pro eine räumliche Kartenserie mit einer Seite pro Potentialfläche, sortiert nach Rang
# (1 = beste Fläche).
#   Hauptkarte    : ausgewählte Fläche (gelb), übrige Flächen grau                        -> Kartenserie
#   Uebersicht    : Gemeindegrenze Iserlohn, ausgewählte Fläche rot umrandet, nächstes Umspannwerk
#   Info-Textfeld : Rang, Gesamtscore, Fläche [m²], Windgeschwindigkeit, Umspannwerk, Entfernung [m]
#                   (dynamischer Text, aktualisiert sich pro Seite)
#
# Modi
#   "daten"    : berechnet nur die Hilfsdaten neu; das fertige Projekt (inkl. manueller Layout-Anpassungen)
#                bleibt unverändert und zeigt beim nächsten Öffnen automatisch die neuen Daten.
#   "komplett" : baut die Projektkopie aus dem Original-Projekt vollständig neu auf.
#
# Ergebnis: Projektkopie zum Prüfen in ArcGIS Pro – es wird nichts exportiert (PDF-Export manuell).
# Voraussetzungen: ArcGIS Pro (arcpy), Projekt in Pro geschlossen, im Layout keine bestehende Kartenserie.
# =================================================================================================================
import os
import sys
import arcpy


# --- Modus -------------------------------------------------------------------------------------------------------
# "daten"    = NUR die Hilfsdaten neu berechnen (Flächen, Ränge, Info-Werte, Umspannwerk, Gemeindegrenze).
#              Das fertige Projekt wird NICHT angefasst -> das Layout bleibt erhalten,
#              beim nächsten Öffnen zeigt es automatisch die neuen Daten.
# "komplett" = Projekt aus dem Original komplett neu aufbauen (manuelle Layout-Anpassungen gehen verloren!)
modus = "daten"
ueberschreiben = False              # nur bei "komplett": True = vorhandene Projektkopie überschreiben


# --- user variables ----------------------------------------------------------------------------------------------
# Potentialflächen
flaechen_fc = r"C:\Felix Greifenstein\Projektdaten Wind\ED\Analyse_Wind_Iserlohn_weich_Windwurf_bereinigt_UWexc.gdb\Potentialflaechen_bereinigt"
f_rang = "RANG"
f_score = "SCORE_GESAMT"
f_wind = "WIND_MITTEL"
f_uw_oid = "UW_OID"                 # OBJECTID des nächsten Umspannwerks (leer -> wird neu gesucht)
wind_einheit = "m/s"

# Umspannwerke
uw_fc = r"C:\Felix Greifenstein\Projektdaten Wind\Import_All.gdb\Umspannwerke_merged"
f_uw_name = "name"
uw_im_ausschnitt = False            # True = Hauptkarte zoomt so weit raus, dass das Umspannwerk mit drauf ist
                                    # False = Hauptkarte zoomt nur auf die Fläche
uw_nur_ab_massstab = 50000          # Umspannwerk nur sichtbar, wenn kleiner als 1:50.000 (= Übersicht),
                                    # in der Hauptkarte (größerer Maßstab) ausgeblendet; None = immer sichtbar
zoom_rand = 1.15                    # Rand um die Fläche in der Hauptkarte (1.0 = randlos, 1.3 = mehr Umgebung)

# Gemeindegrenze Iserlohn = Gemarkungen aus 'Nord', die zu Iserlohn gehören
gemarkungen_fc = r"C:\Felix Greifenstein\Projektdaten Wind\Projekt_Wind_Teilung.gdb\Nord"
f_gemarkung = "gemarkungsnummer_klartext"
iserlohn_gemarkungen = ["Iserlohn"] # Namen der Iserlohner Gemarkungen, z. B. ["Iserlohn", "Letmathe", ...]
                                    #    Leer lassen -> Skript listet alle Gemarkungen auf und stoppt

max_maps = None                     # z. B. 3 = nur Rang 1–3; None = alle

# Hilfsdaten (werden bei jedem Lauf neu erzeugt – Originaldaten bleiben unverändert)
hilfs_gdb = r"C:\Felix Greifenstein\Projektdaten Wind\ED\Kartenserie_Hilfsdaten.gdb"

# Projekt / Layout
ref_aprx = r"C:\Users\GIS309\Documents\ArcGIS\Projects\Projekt_KBF\Projekt_KBF.aprx"
out_aprx = r"C:\Users\GIS309\Documents\ArcGIS\Projects\Projekt_KBF\Projekt_KBF_Kartenserie.aprx"
layout_name = "Layout1"
frame_haupt = "Hauptkarte"
frame_ueber = "Uebersicht"
info_text_name = "Info"


# --- enviromental settings ---------------------------------------------------------------------------------------
arcpy.env.overwriteOutput = True


# --- functions ---------------------------------------------------------------------------------------------------
def fmt(zahl, nachkomma=0):
    """Zahl ohne Tausenderpunkt, Komma als Dezimaltrennzeichen: 12345.6 -> '12345,6'"""
    if zahl is None:
        return "k. A."
    return f"{zahl:.{nachkomma}f}".replace(".", ",")


def color(r, g, b, a=100):
    return {"RGB": [r, g, b, a]}


def simple_symbol(layer, fill=None, outline=None, width=None, size=None):
    """Einfache Einzelsymbol-Darstellung setzen."""
    sym = layer.symbology
    sym.updateRenderer("SimpleRenderer")
    s = sym.renderer.symbol
    if fill is not None:
        s.color = fill
    if outline is not None:
        s.outlineColor = outline
    if width is not None:
        s.outlineWidth = width
    if size is not None:
        s.size = size
    layer.symbology = sym


def get_frame(layout, name):
    frames = layout.listElements("MAPFRAME_ELEMENT", name)
    if not frames:
        vorhanden = [f.name for f in layout.listElements("MAPFRAME_ELEMENT")]
        raise ValueError(f"Kartenrahmen '{name}' nicht gefunden. Vorhanden: {vorhanden}")
    return frames[0]


def set_label(layer, arcade_expression):
    layer.showLabels = True
    lc = layer.listLabelClasses()[0]
    lc.expression = arcade_expression


def quadrat(cx, cy, r, sr):
    return arcpy.Polygon(arcpy.Array([arcpy.Point(cx - r, cy - r), arcpy.Point(cx - r, cy + r),
                                      arcpy.Point(cx + r, cy + r), arcpy.Point(cx + r, cy - r)]), sr)


# --- Schutz vor versehentlichem Überschreiben --------------------------------------------------------------------
if modus not in ("daten", "komplett"):
    sys.exit(f"Unbekannter modus '{modus}' – erlaubt sind 'daten' oder 'komplett'.")
if modus == "komplett" and arcpy.Exists(out_aprx) and not ueberschreiben:
    sys.exit(f"{out_aprx} existiert schon und würde überschrieben (inkl. aller Layout-Anpassungen).\n"
             f"-> Für reine Datenaktualisierung modus = 'daten' verwenden,\n"
             f"-> oder bewusst neu aufbauen: ueberschreiben = True setzen.")


# --- 0. Gemarkungen prüfen ---------------------------------------------------------------------------------------
alle_gemarkungen = sorted({r[0] for r in arcpy.da.SearchCursor(gemarkungen_fc, [f_gemarkung]) if r[0]})
if not iserlohn_gemarkungen:
    arcpy.AddMessage("Gemarkungen in 'Nord':\n  " + "\n  ".join(alle_gemarkungen))
    raise SystemExit("\nBitte die Iserlohner Gemarkungen oben in 'iserlohn_gemarkungen' eintragen "
                     "und das Skript erneut starten.")
fehlend = [g for g in iserlohn_gemarkungen if g not in alle_gemarkungen]
if fehlend:
    raise SystemExit(f"Diese Gemarkungen gibt es in 'Nord' nicht: {fehlend}\n"
                     f"Vorhanden: {alle_gemarkungen}")


# --- 1. Hilfsdaten erzeugen --------------------------------------------------------------------------------------
arcpy.AddMessage("1/4  Hilfsdaten berechnen ...")
sr = arcpy.Describe(flaechen_fc).spatialReference

if not arcpy.Exists(hilfs_gdb):
    arcpy.management.CreateFileGDB(os.path.dirname(hilfs_gdb), os.path.basename(hilfs_gdb))

# Umspannwerke einlesen (im Koordinatensystem der Flächen)
uw = {}
with arcpy.da.SearchCursor(uw_fc, ["OID@", "SHAPE@", f_uw_name], spatial_reference=sr) as cur:
    for oid, geom, name in cur:
        if geom is not None:
            uw[oid] = (geom, name if name else "ohne Namen")
arcpy.AddMessage(f"     {len(uw)} Umspannwerke geladen")

fc_flaechen = os.path.join(hilfs_gdb, "KS_Flaechen")
fc_uw = os.path.join(hilfs_gdb, "KS_Umspannwerk")
fc_index = os.path.join(hilfs_gdb, "KS_Index")

felder = [["RANG", "LONG", "Rang"],
          ["TXT_SCORE", "TEXT", "Score", 50],
          ["TXT_FLAECHE", "TEXT", "Fläche", 50],
          ["TXT_WIND", "TEXT", "Wind", 50],
          ["TXT_DIST", "TEXT", "Distanz UW", 50],
          ["UW_NAME", "TEXT", "Umspannwerk", 255]]

for fc, typ in [(fc_flaechen, "POLYGON"), (fc_uw, "POINT"), (fc_index, "POLYGON")]:
    arcpy.management.CreateFeatureclass(hilfs_gdb, os.path.basename(fc), typ, spatial_reference=sr)
    arcpy.management.AddFields(fc, felder)

out_fields = ["SHAPE@", "RANG", "TXT_SCORE", "TXT_FLAECHE", "TXT_WIND", "TXT_DIST", "UW_NAME"]
query = f"{f_rang} IS NOT NULL" + (f" AND {f_rang} <= {max_maps}" if max_maps else "")

rows = {fc_flaechen: [], fc_uw: [], fc_index: []}   # erst sammeln, dann nacheinander schreiben

anzahl = 0
with arcpy.da.SearchCursor(flaechen_fc, ["SHAPE@", f_rang, f_score, f_wind, f_uw_oid], where_clause=query,
                           sql_clause=(None, f"ORDER BY {f_rang}")) as cur:
    for area, rang, score, wind, uw_oid in cur:
        if area is None:
            continue

        # nächstes Umspannwerk: aus UW_OID, sonst selbst suchen
        if uw_oid not in uw:
            uw_oid = min(uw, key=lambda k: area.distanceTo(uw[k][0]))
        uw_geom, uw_name = uw[uw_oid]
        uw_pt = arcpy.PointGeometry(uw_geom.centroid, sr) if uw_geom.type != "point" else uw_geom
        dist = area.distanceTo(uw_geom)

        neuer_rang = anzahl + 1          # fortlaufend 1..n in der Reihenfolge des ursprünglichen RANG
        werte = [neuer_rang,
                 fmt(score, 2),
                 f"{fmt(area.area, 0)} m²",
                 f"{fmt(wind, 1)} {wind_einheit}",
                 f"{fmt(dist, 0)} m",
                 uw_name]

        # Kartenblatt (Index-Quadrat), zentriert auf die Fläche
        c, e = area.centroid, area.extent
        halb = max(e.width, e.height) / 2
        if uw_im_ausschnitt:
            r = (dist + halb) * 1.15 + 100       # Fläche + Umspannwerk passen drauf
        else:
            r = max(halb * zoom_rand, 200)       # nur Fläche mit etwas Rand (mind. 400 m Kantenlänge)

        rows[fc_flaechen].append([area] + werte)
        rows[fc_uw].append([uw_pt] + werte)
        rows[fc_index].append([quadrat(c.X, c.Y, r, sr)] + werte)
        anzahl += 1

for fc, zeilen in rows.items():
    with arcpy.da.InsertCursor(fc, out_fields) as ins:
        for z in zeilen:
            ins.insertRow(z)
arcpy.AddMessage(f"     {anzahl} Flächen aufbereitet (Rang 1–{anzahl})")

# Gemeindegrenze Iserlohn: nur die Iserlohner Gemarkungen zu einer Fläche zusammenfassen
fc_iserlohn = os.path.join(hilfs_gdb, "KS_Iserlohn")
namen_sql = ", ".join("'" + g.replace("'", "''") + "'" for g in iserlohn_gemarkungen)
lyr_tmp = arcpy.management.MakeFeatureLayer(gemarkungen_fc, "tmp_gemarkungen",
                                            f"{f_gemarkung} IN ({namen_sql})")[0]
arcpy.management.Dissolve(lyr_tmp, fc_iserlohn)
arcpy.management.Delete(lyr_tmp)
arcpy.AddMessage(f"     Gemeindegrenze aus {len(iserlohn_gemarkungen)} Gemarkungen erzeugt")

if modus == "daten":
    arcpy.AddMessage(f"\nFertig (modus 'daten'): Hilfsdaten aktualisiert, Projekt unverändert.\n"
                     f"Jetzt {out_aprx} öffnen – ggf. Layout -> Kartenserie -> Aktualisieren.")
    sys.exit(0)


# --- 2. Projekt öffnen, Layer laden ------------------------------------------------------------------------------
arcpy.AddMessage("2/4  Layer laden ...")
aprx = arcpy.mp.ArcGISProject(ref_aprx)
layout = aprx.listLayouts(layout_name)[0]

mf_haupt = get_frame(layout, frame_haupt)
mf_ueber = get_frame(layout, frame_ueber)
karte = mf_haupt.map

# Übersicht nutzt dieselbe Karte wie die Hauptkarte -> Seitenabfrage (rote Umrandung) wirkt auch dort
mf_ueber.map = karte

# Alte Datenlayer entfernen – Grundkarten bleiben
for lyr in karte.listLayers():
    if lyr.isFeatureLayer:
        arcpy.AddMessage(f"     entferne alten Layer: {lyr.name}")
        karte.removeLayer(lyr)

# Reihenfolge: zuletzt hinzugefügt = oben
lyr_index = karte.addDataFromPath(fc_index)
lyr_index.name = "Kartenblatt-Index"
simple_symbol(lyr_index, fill=color(0, 0, 0, 0), outline=color(0, 0, 0, 0), width=0)   # unsichtbar gezeichnet

lyr_iserlohn = karte.addDataFromPath(fc_iserlohn)
lyr_iserlohn.name = "Gemeindegrenze Iserlohn"
simple_symbol(lyr_iserlohn, fill=color(0, 0, 0, 0), outline=color(40, 40, 40), width=1.5)

lyr_andere = karte.addDataFromPath(fc_flaechen)
lyr_andere.name = "Weitere Potentialflächen"
simple_symbol(lyr_andere, fill=color(130, 130, 130, 50), outline=color(90, 90, 90), width=0.8)

lyr_flaeche = karte.addDataFromPath(fc_flaechen)
lyr_flaeche.name = "Ausgewählte Potentialfläche"
simple_symbol(lyr_flaeche, fill=color(255, 235, 110, 50), outline=color(175, 130, 0), width=1.2)  # hellgelb, dunkelgelber Rand

lyr_flaeche.minThreshold = uw_nur_ab_massstab or 50000   # gelbe Fläche nur in der Hauptkarte

lyr_markierung = karte.addDataFromPath(fc_flaechen)     # rote Umrandung nur in der Übersicht
lyr_markierung.name = "Markierung Übersicht"
simple_symbol(lyr_markierung, fill=color(0, 0, 0, 0), outline=color(230, 0, 0), width=2.5)
lyr_markierung.maxThreshold = uw_nur_ab_massstab or 50000

lyr_uw = karte.addDataFromPath(fc_uw)
lyr_uw.name = "Nächstes Umspannwerk"
simple_symbol(lyr_uw, fill=color(255, 200, 0), outline=color(0, 0, 0), size=12)
set_label(lyr_uw, 'IIf($feature.UW_NAME == "ohne Namen", "", $feature.UW_NAME)')   # nur echte Namen beschriften
if uw_nur_ab_massstab:
    lyr_uw.maxThreshold = uw_nur_ab_massstab      # beim Hineinzoomen über 1:50.000 hinaus ausgeblendet

# Übersicht auf Iserlohn zoomen
ext = arcpy.Describe(fc_iserlohn).extent
dx, dy = ext.width * 0.05, ext.height * 0.05
mf_ueber.camera.setExtent(arcpy.Extent(ext.XMin - dx, ext.YMin - dy, ext.XMax + dx, ext.YMax + dy,
                                       spatial_reference=ext.spatialReference))

# Maßstabsleiste und Nordpfeil an die Hauptkarte hängen
for el in layout.listElements("MAPSURROUND_ELEMENT"):
    try:
        el.mapFrame = mf_haupt
        arcpy.AddMessage(f"     '{el.name}' an Hauptkarte gekoppelt")
    except Exception as e:
        arcpy.AddWarning(f"     '{el.name}' konnte nicht gekoppelt werden ({e})")


# --- 3. Kartenserie ----------------------------------------------------------------------------------------------
arcpy.AddMessage("3/4  Kartenserie einrichten ...")
if layout.mapSeries is not None:
    raise SystemExit("Layout1 hat schon eine Kartenserie. Bitte im ORIGINAL-Projekt in Pro entfernen "
                     "(Layout -> Kartenserie -> Keine), speichern und Skript erneut starten.")

layout.createSpatialMapSeries(mf_haupt, lyr_index, "RANG", "RANG")
arcpy.AddMessage("     Kartenserie angelegt")

# Seitenabfragen: aktuelle Fläche + ihr Umspannwerk zeigen, bei 'Weitere' genau umgekehrt
lyr_flaeche.setPageQuery("RANG", True)
lyr_uw.setPageQuery("RANG", True)
lyr_markierung.setPageQuery("RANG", True)
lyr_andere.setPageQuery("RANG", False)


# --- 4. Info-Textfeld --------------------------------------------------------------------------------------------
arcpy.AddMessage("4/4  Info-Textfeld ...")
info = ('<BOL>Potentialfläche Rang <dyn type="page" property="RANG"/></BOL>\n'
        'Gesamtscore: <dyn type="page" property="TXT_SCORE"/>\n'
        'Fläche: <dyn type="page" property="TXT_FLAECHE"/>\n'
        'Windgeschwindigkeit (Mittel): <dyn type="page" property="TXT_WIND"/>\n'
        'Nächstes Umspannwerk: <dyn type="page" property="UW_NAME"/>\n'
        'Entfernung zum Umspannwerk: <dyn type="page" property="TXT_DIST"/>')

vorhanden = layout.listElements("TEXT_ELEMENT", info_text_name)
if vorhanden:
    vorhanden[0].text = info
else:
    pos = arcpy.Point(layout.pageWidth * 0.52, layout.pageHeight * 0.22)
    aprx.createTextElement(layout, pos, "POINT", info, 9, name=info_text_name)
    arcpy.AddWarning("     Textfeld 'Info' neu angelegt – Position in Pro anpassen")


# --- Legende: Umspannwerk zeigen, obwohl es in der Hauptkarte nicht sichtbar ist ---------------------------------
for leg in layout.listElements("LEGEND_ELEMENT"):
    try:
        leg.syncLayerVisibility = False                  # Einträge nicht an Sichtbarkeit in der Hauptkarte koppeln
    except Exception as e:
        arcpy.AddWarning(f"     Legende: Sichtbarkeits-Kopplung nicht änderbar ({e})")
    for item in leg.items:
        try:
            if item.name in (lyr_index.name, lyr_markierung.name):
                item.visible = False                     # Hilfslayer nie in der Legende
            elif item.name == lyr_uw.name:
                item.visible = True
                item.showVisibleFeatures = False         # nicht nur Objekte im Kartenausschnitt berücksichtigen
        except Exception as e:
            arcpy.AddWarning(f"     Legende: Eintrag '{item.name}' nicht änderbar ({e})")
    arcpy.AddMessage(f"     Legende '{leg.name}' angepasst")


# --- speichern ---------------------------------------------------------------------------------------------------
arcpy.AddMessage("     speichere Projektkopie ...")
aprx.saveACopy(out_aprx)
arcpy.AddMessage(f"Fertig: {anzahl} Seiten. Zum Prüfen öffnen:\n{out_aprx}")
del aprx
