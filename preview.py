"""Anteprima su mappa OpenStreetMap: overview del GeoTIFF + tiles generate (Leaflet)."""
import functools
import http.server
import os
import tempfile
import time
import threading

from osgeo import gdal, osr

gdal.UseExceptions()

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body,#map{height:100%%;margin:0}</style></head>
<body><div id="map"></div><script>
var b = %(bounds)s;  // [[south, west], [north, east]]
var map = L.map('map', {maxZoom: 24});
var osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxNativeZoom: 19, maxZoom: 24, attribution: '&copy; OpenStreetMap contributors'}).addTo(map);
var blend = %(blend)s;  // disegni: moltiplica sulla mappa, il bianco diventa trasparente
var overlays = {};
overlays['Input (anteprima)'] = L.imageOverlay('preview.png?v=%(v)d', b, {opacity: 0.9});
%(tiles_js)s
if (!overlays['Tiles generate']) overlays['Input (anteprima)'].addTo(map);  // senza tiles mostra l'input
if (blend) Object.keys(overlays).forEach(function(k) {
  var l = overlays[k];
  function apply() { var el = l.getContainer ? l.getContainer() : l.getElement(); if (el) el.style.mixBlendMode = 'multiply'; }
  l.on('add', apply);
  if (map.hasLayer(l)) apply();
});
L.control.layers({'OpenStreetMap': osm}, overlays, {collapsed: false}).addTo(map);
map.fitBounds(b);
</script></body></html>"""

TILES_JS = """overlays['Tiles generate'] = L.tileLayer('tiles/{z}/{x}/{y}.%(ext)s',
  {tms: %(tms)s, minNativeZoom: %(zmin)d, maxNativeZoom: %(zmax)d, maxZoom: 24}).addTo(map);
"""


def make_preview_png(tif_path, out_png, width=2048):
    """Crea un PNG in Web Mercator (max `width` px). Ritorna [[sud, ovest], [nord, est]] in lat/lon."""
    src = gdal.Open(tif_path)
    if src.GetGeoTransform() == (0.0, 1.0, 0.0, 0.0, 0.0, 1.0) and not src.GetGCPCount():
        raise ValueError("il raster non è georeferenziato (nessun geotransform/GCP e nessun .tfw accanto): "
                         "georeferenzialo (es. QGIS), usa la modalità cartella (.tif + .tfw + .prj) oppure abilita il posizionamento approssimato")
    n = src.RasterCount
    is_byte = src.GetRasterBand(1).DataType == gdal.GDT_Byte
    has_alpha = n >= 4 and is_byte and src.GetRasterBand(4).GetColorInterpretation() == gdal.GCI_AlphaBand
    bands = ([1, 2, 3] + ([4] if has_alpha else [])) if n >= 3 else [1]
    palette = src.GetRasterBand(1).GetColorTable() is not None
    has_alpha = has_alpha or palette  # con la palette espansa in RGBA l alpha esiste già

    # Come main.py: i raster non-Byte vengono riscalati a 8-bit.
    byte = gdal.Translate("/vsimem/prev_byte.vrt", src, format="VRT", bandList=bands,
                          rgbExpand="rgba" if palette else None, outputType=gdal.GDT_Byte, scaleParams=None if is_byte else [[]])
    warped = gdal.Warp("/vsimem/prev_3857.tif", byte, dstSRS="EPSG:3857", width=width,
                       resampleAlg="bilinear", dstAlpha=not has_alpha)
    gdal.Translate(out_png, warped, format="PNG")

    gt = warped.GetGeoTransform()
    x0, y1 = gt[0], gt[3]
    x1, y0 = x0 + gt[1] * warped.RasterXSize, y1 + gt[5] * warped.RasterYSize
    web, wgs = osr.SpatialReference(), osr.SpatialReference()
    web.ImportFromEPSG(3857); wgs.ImportFromEPSG(4326)
    wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    web.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    t = osr.CoordinateTransformation(web, wgs)
    west, south, _ = t.TransformPoint(x0, y0)
    east, north, _ = t.TransformPoint(x1, y1)

    src = byte = warped = None
    for f in ("/vsimem/prev_byte.vrt", "/vsimem/prev_3857.tif"):
        gdal.Unlink(f)
    return [[south, west], [north, east]]


class _Handler(http.server.SimpleHTTPRequestHandler):
    tiles_dir = None

    def translate_path(self, path):
        # /tiles/... -> cartella delle tiles generate, il resto -> cartella dell'anteprima
        path = path.split("?", 1)[0]
        if path.startswith("/tiles/") and self.tiles_dir:
            return os.path.join(self.tiles_dir, *path[len("/tiles/"):].split("/"))
        return super().translate_path(path)

    def log_message(self, *args):
        pass


class Preview:
    """Server HTTP locale (serve per il Referer richiesto dai tile server OSM) + pagina Leaflet."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="geotiff_preview_")
        self.server = None

    def build(self, tif_path, tiles_dir=None, scheme="xyz", ext="png", zmin=0, zmax=22, multiply=False):
        bounds = make_preview_png(tif_path, os.path.join(self.dir, "preview.png"))
        has_tiles = bool(tiles_dir) and os.path.isdir(tiles_dir)
        tiles_js = TILES_JS % dict(ext=ext, tms=str(scheme == "tms").lower(), zmin=zmin, zmax=zmax) if has_tiles else ""
        with open(os.path.join(self.dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(PAGE % dict(bounds=bounds, tiles_js=tiles_js, v=time.time_ns(), blend=str(multiply).lower()))

        _Handler.tiles_dir = tiles_dir if has_tiles else None
        if self.server is None:
            handler = functools.partial(_Handler, directory=self.dir)
            self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_port}/index.html"
