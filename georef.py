"""Posizionamento APPROSSIMATO di un'immagine non georeferenziata (es. disegno tecnico scansionato).

Dato un punto (lat/lon) che corrisponde a un pixel, la scala del disegno e una rotazione,
crea un VRT georeferenziato in UTM. Non è una vera georeferenziazione (nessun punto di
controllo, nessuna deformazione): serve a capire se il file è utilizzabile.
"""
import math
import os
import tempfile

from osgeo import gdal, osr

gdal.UseExceptions()


def read_dpi(ds):
    """DPI dai metadati TIFF (XRESOLUTION + RESOLUTIONUNIT), None se assenti."""
    md = ds.GetMetadata()
    try:
        res = float(md["TIFFTAG_XRESOLUTION"])
    except (KeyError, ValueError):
        return None
    unit = md.get("TIFFTAG_RESOLUTIONUNIT", "2").split()[0]
    return res * 2.54 if unit == "3" else res  # 3 = pixel/cm, 2 = pixel/inch


def place_raster(src_path, lat, lon, scale=100.0, rotation=0.0, dpi=None, crop=None, anchor_px=None, out_dir=None):
    """Ritorna il percorso di un VRT georeferenziato.

    scale:     denominatore della scala del disegno (100 = 1:100)
    rotation:  gradi, in senso orario, di cui la "alto" del disegno è ruotato rispetto al nord
    crop:      (x, y, larghezza, altezza) in pixel dell'immagine intera, oppure None
    anchor_px: (x, y) pixel dell'immagine intera che cade su lat/lon; default = centro del ritaglio
    """
    src = gdal.Open(src_path)
    dpi = dpi or read_dpi(src)
    if not dpi:
        raise ValueError("DPI non presenti nel file: specificali (--dpi)")
    cx, cy, w, h = crop or (0, 0, src.RasterXSize, src.RasterYSize)
    if cx < 0 or cy < 0 or cx + w > src.RasterXSize or cy + h > src.RasterYSize:
        raise ValueError(f"ritaglio fuori dall'immagine ({src.RasterXSize}x{src.RasterYSize} px)")
    ax, ay = anchor_px or (cx + w / 2, cy + h / 2)

    # UTM della zona del punto
    zone = int((lon + 180) // 6) + 1
    epsg = (32600 if lat >= 0 else 32700) + zone
    wgs, utm = osr.SpatialReference(), osr.SpatialReference()
    wgs.ImportFromEPSG(4326); utm.ImportFromEPSG(epsg)
    wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    utm.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    e0, n0, _ = osr.CoordinateTransformation(wgs, utm).TransformPoint(lon, lat)

    s = 0.0254 / dpi * scale  # metri per pixel
    t = math.radians(rotation)
    gt1, gt2, gt4, gt5 = s * math.cos(t), -s * math.sin(t), -s * math.sin(t), -s * math.cos(t)
    ax, ay = ax - cx, ay - cy  # ancora relativa al ritaglio
    gt = (e0 - gt1 * ax - gt2 * ay, gt1, gt2, n0 - gt4 * ax - gt5 * ay, gt4, gt5)

    out_dir = out_dir or tempfile.mkdtemp(prefix="geotiff_place_")
    # stesso nome del file originale: main.py ne ricava il nome della cartella delle tiles
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(src_path))[0] + ".vrt")
    ds = gdal.Translate(out, src, format="VRT", srcWin=[cx, cy, w, h])
    ds.SetGeoTransform(gt)
    ds.SetProjection(utm.ExportToWkt())
    ds = src = None
    return out


if __name__ == "__main__":  # self-check: 1:100 a 200 dpi = 1,27 cm/px; ancora centrale sul punto
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    d = gdal.GetDriverByName("GTiff").Create(os.path.join(tempfile.mkdtemp(), "t.tif"), 1000, 500, 1, gdal.GDT_Byte)
    d.SetMetadataItem("TIFFTAG_XRESOLUTION", "200"); d.SetMetadataItem("TIFFTAG_YRESOLUTION", "200"); d.SetMetadataItem("TIFFTAG_RESOLUTIONUNIT", "2")
    p = d.GetDescription(); d = None
    g = gdal.Open(place_raster(p, 40.84022778, 14.09117778, rotation=0))
    gt = g.GetGeoTransform()
    assert abs(gt[1] - 0.0127) < 1e-9 and abs(gt[5] + 0.0127) < 1e-9, gt
    ce, cn = gt[0] + gt[1] * 500, gt[3] + gt[5] * 250
    print("ok", g.GetSpatialRef().GetAuthorityCode(None), round(ce, 2), round(cn, 2))
    g = gdal.Open(place_raster(p, 40.84022778, 14.09117778, rotation=90))
    gt = g.GetGeoTransform()
    assert abs(gt[1]) < 1e-9 and abs(gt[2] + 0.0127) < 1e-9, gt  # ruotato di 90°: la X dell'immagine punta a sud
    print("rotation ok")
