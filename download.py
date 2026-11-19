"""Ricerca di un luogo e download di un raster di riferimento (GeoTIFF) per un'area, da fonti gratuite.

bbox = (ovest, sud, est, nord) in gradi (EPSG:4326).
"""
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request

from osgeo import gdal

gdal.UseExceptions()

UA = "geotiff-tiling-gui/1.0"  # Nominatim richiede uno User-Agent che identifichi l applicazione
PCN = ("http://wms.pcn.minambiente.it/ogc?map=/ms_ogc/WMS_v1.3/raster/ortofoto_colore_12.map"
       "&SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS=OI.ORTOIMMAGINI.2012&STYLES=&SRS=EPSG:4326&FORMAT=image/png")
STAC = "https://earth-search.aws.element84.com/v1/search"
MAX_PIXELS = 36_000_000  # ~6000 x 6000 px: oltre, download e tiling diventano molto pesanti
GTIFF = ["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"]

SOURCES = {
    "pcn": dict(label="Ortofoto Italia 2012 (AGEA)  ·  50 cm  ·  solo Italia", res=0.5),
    "s2": dict(label="Sentinel-2  ·  10 m  ·  tutto il mondo", res=10.0),
}


def size_m(bbox):
    w, s, e, n = bbox
    return (e - w) * 111320 * math.cos(math.radians((s + n) / 2)), (n - s) * 110574


def size_px(bbox, res):
    wm, hm = size_m(bbox)
    return round(wm / res), round(hm / res)


def geocode(query):
    """'lat, lon' oppure il nome di un luogo. Ritorna (nome, bbox)."""
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[,; ]\s*(-?\d+(?:\.\d+)?)\s*", query)
    if m:
        lat, lon = float(m[1]), float(m[2])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Coordinate fuori intervallo (LAT, LON)")
        d = 0.0015  # ~150 m intorno al punto
        return f"{lat}, {lon}", (lon - d, lat - d, lon + d, lat + d)
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(dict(q=query, format="jsonv2", limit=1))
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20) as r:
        found = json.load(r)
    if not found:
        raise ValueError(f"Nessun risultato per «{query}»")
    s, n, w, e = (float(x) for x in found[0]["boundingbox"])
    return found[0]["display_name"], (w, s, e, n)


def _s2(bbox, out, cb):
    w, s, e, n = bbox
    body = {"collections": ["sentinel-2-l2a"], "intersects": {"type": "Point", "coordinates": [(w + e) / 2, (s + n) / 2]},
            "limit": 1, "sortby": [{"field": "properties.datetime", "direction": "desc"}],
            "query": {"eo:cloud_cover": {"lt": 15}}}
    req = urllib.request.Request(STAC, json.dumps(body).encode(), {"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        scenes = json.load(r)["features"]
    if not scenes:
        raise ValueError("Nessuna scena Sentinel-2 recente e quasi senza nuvole per quest area")
    gdal.Translate(out, "/vsicurl/" + scenes[0]["assets"]["visual"]["href"], projWin=[w, n, e, s],
                   projWinSRS="EPSG:4326", creationOptions=GTIFF, callback=cb)
    return f"Sentinel-2 del {scenes[0]['properties']['datetime'][:10]}"


def _pcn(bbox, out, cb):
    w, s, e, n = bbox
    wpx, hpx = size_px(bbox, SOURCES["pcn"]["res"])
    nodata = ValueError("Nessun dato per quest area: l ortofoto copre solo l Italia")
    try:
        gdal.Translate(out, f"WMS:{PCN}&BBOX={w},{s},{e},{n}", width=wpx, height=hpx, creationOptions=GTIFF, callback=cb)
        ds = gdal.Open(out)
        empty = ds.ReadAsArray(buf_xsize=64, buf_ysize=64).max() == 0  # senza copertura: tutto nero
    except RuntimeError as err:  # fuori dall Italia il server risponde con un errore interno
        raise nodata from err
    ds = None
    if empty:
        os.remove(out)
        raise nodata
    return "Ortofoto AGEA 2012"


def fetch(source, bbox, out_dir, progress=None):
    """Scarica l area in un GeoTIFF dentro out_dir. progress(0..1). Ritorna (percorso, descrizione)."""
    wpx, hpx = size_px(bbox, SOURCES[source]["res"])
    if wpx < 1 or hpx < 1:
        raise ValueError("Area troppo piccola")
    if wpx * hpx > MAX_PIXELS:
        raise ValueError(f"Area troppo grande ({wpx} × {hpx} px, massimo {MAX_PIXELS // 1_000_000} milioni di pixel): ingrandisci la mappa")
    os.makedirs(out_dir, exist_ok=True)
    w, s, e, n = bbox
    out = os.path.join(out_dir, f"{source}_{(s + n) / 2:.4f}_{(w + e) / 2:.4f}_{time.strftime('%Y%m%d_%H%M%S')}.tif")

    def cb(complete, message, data):
        if progress:
            progress(complete)
        return 1

    return out, (_pcn if source == "pcn" else _s2)(bbox, out, cb)


if __name__ == "__main__":  # self-check senza rete
    assert geocode("40.84022778, 14.09117778")[1][0] < 14.0912 < geocode("40.84022778, 14.09117778")[1][2]
    assert geocode(" -33.9,18.4 ")[0] == "-33.9, 18.4"
    try:
        geocode("95, 10")
        raise SystemExit("atteso ValueError")
    except ValueError:
        pass
    px = size_px((14.0885, 40.8385, 14.094, 40.842), 0.5)
    assert 900 < px[0] < 950 and 750 < px[1] < 800, px  # ~465 m x ~387 m a 50 cm
    print("ok", px)
