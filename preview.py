"""Anteprima su mappa OpenStreetMap: overview del GeoTIFF + tiles generate (Leaflet)."""
import functools
import http.server
import math
import os
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlparse

from osgeo import gdal, osr

gdal.UseExceptions()

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body,#map{height:100%%;margin:0}</style></head>
<body><div id="map"></div><script>
var b = %(bounds)s;  // [[south, west], [north, east]]
var blend = %(blend)s;  // disegni: moltiplica sulla mappa, il bianco diventa trasparente
var pick = %(pick)s;    // un clic sulla mappa imposta il punto di posizionamento
var map = L.map('map', {maxZoom: 24});
var osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxNativeZoom: 19, maxZoom: 24, attribution: '&copy; OpenStreetMap contributors'}).addTo(map);
var overlays = {};
%(overlay_js)s
%(tiles_js)s
if (overlays['Input (anteprima)'] && !overlays['Tiles generate']) overlays['Input (anteprima)'].addTo(map);
if (blend) Object.keys(overlays).forEach(function(k) {
  var l = overlays[k];
  function apply() { var el = l.getContainer ? l.getContainer() : l.getElement(); if (el) el.style.mixBlendMode = 'multiply'; }
  l.on('add', apply);
  if (map.hasLayer(l)) apply();
});
L.control.layers({'OpenStreetMap': osm}, overlays, {collapsed: false}).addTo(map);
var h = location.hash.slice(1).split('/');  // #zoom/lat/lon: mantiene la vista tra un aggiornamento e l altro
if (h.length === 3) map.setView([+h[1], +h[2]], +h[0]); else map.fitBounds(b);
map.on('moveend', function() { var c = map.getCenter(); fetch('view?lat=' + c.lat + '&lon=' + c.lng + '&z=' + map.getZoom()); });
var marker = null;
if (pick) {
  map.getContainer().style.cursor = 'crosshair';
  map.on('click', function(e) {
    if (marker) marker.setLatLng(e.latlng); else marker = L.marker(e.latlng).addTo(map);
    fetch('pick?lat=' + e.latlng.lat + '&lon=' + e.latlng.lng);
  });
}
</script></body></html>"""

OVERLAY_JS = "overlays['Input (anteprima)'] = L.imageOverlay('preview.png?v=%(v)d', b, {opacity: 0.9});"
TILES_JS = """overlays['Tiles generate'] = L.tileLayer('tiles/{z}/{x}/{y}.%(ext)s',
  {tms: %(tms)s, minNativeZoom: %(zmin)d, maxNativeZoom: %(zmax)d, maxZoom: 24}).addTo(map);
"""
ITALY = [[35.0, 6.0], [47.5, 19.0]]  # vista iniziale quando non c e ancora un raster da mostrare


def pixel_size_m(ds):
    """Dimensione del pixel in metri (approssimata se il CRS è geografico)."""
    gt, srs = ds.GetGeoTransform(), ds.GetSpatialRef()
    px = math.hypot(gt[1], gt[4])
    if srs is not None and srs.IsGeographic():
        lat = gt[3] + gt[5] * ds.RasterYSize / 2
        return px * 111320 * math.cos(math.radians(lat))
    return px * (srs.GetLinearUnits() if srs is not None else 1.0)


def native_zoom(res_m, lat):
    """Zoom Web Mercator la cui risoluzione eguaglia quella del raster."""
    return math.log2(156543.03392 * math.cos(math.radians(lat)) / res_m)


def tile_count(bounds, zmin, zmax):
    """Numero approssimato di tiles per i livelli zmin..zmax (bounding box lat/lon)."""
    (s, w), (n, e) = bounds

    def xy(lat, lon, z):
        k = 2 ** z
        return (int((lon + 180) / 360 * k),
                int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * k))

    total = 0
    for z in range(zmin, zmax + 1):
        x0, y0 = xy(n, w, z)
        x1, y1 = xy(s, e, z)
        total += (x1 - x0 + 1) * (y1 - y0 + 1)
    return total


def make_preview_png(tif_path, out_png, width=2048):
    """Crea un PNG in Web Mercator (max `width` px).
    Ritorna ([[sud, ovest], [nord, est]] in lat/lon, dimensione pixel sorgente in metri)."""
    src = gdal.Open(tif_path)
    if src.GetGeoTransform() == (0.0, 1.0, 0.0, 0.0, 0.0, 1.0) and not src.GetGCPCount():
        raise ValueError("il raster non è georeferenziato (nessun geotransform/GCP e nessun .tfw accanto): "
                         "georeferenzialo (es. QGIS), usa la modalità cartella (.tif + .tfw + .prj) oppure usa il posizionamento approssimato")
    res_m = pixel_size_m(src)
    n = src.RasterCount
    is_byte = src.GetRasterBand(1).DataType == gdal.GDT_Byte
    has_alpha = n >= 4 and is_byte and src.GetRasterBand(4).GetColorInterpretation() == gdal.GCI_AlphaBand
    bands = ([1, 2, 3] + ([4] if has_alpha else [])) if n >= 3 else [1]
    palette = src.GetRasterBand(1).GetColorTable() is not None
    has_alpha = has_alpha or palette  # con la palette espansa in RGBA l alpha esiste già

    # Come main.py: i raster non-Byte vengono riscalati a 8-bit.
    byte = gdal.Translate("/vsimem/prev_byte.vrt", src, format="VRT", bandList=bands,
                          rgbExpand="rgba" if palette else None, outputType=gdal.GDT_Byte,
                          scaleParams=None if is_byte else [[]])
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
    return [[south, west], [north, east]], res_m


class _Handler(http.server.SimpleHTTPRequestHandler):
    tiles_dir = None
    events = None  # callable(nome, {parametro: float}) per i clic e i movimenti della mappa

    def translate_path(self, path):
        # /tiles/... -> cartella delle tiles generate, il resto -> cartella dell'anteprima
        path = path.split("?", 1)[0]
        if path.startswith("/tiles/") and self.tiles_dir:
            return os.path.join(self.tiles_dir, *path[len("/tiles/"):].split("/"))
        return super().translate_path(path)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/pick", "/view"):
            try:
                q = {k: float(v[0]) for k, v in parse_qs(u.query).items()}
                if self.events:
                    self.events(u.path[1:], q)
                self.send_response(204)
            except ValueError:
                self.send_response(400)
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, *args):
        pass


class Preview:
    """Server HTTP locale (serve per il Referer richiesto dai tile server OSM) + pagina Leaflet."""

    def __init__(self, on_pick=None):
        self.dir = tempfile.mkdtemp(prefix="geotiff_preview_")
        self.server = None
        self.lock = threading.Lock()
        self.on_pick = on_pick  # callable(lat, lon)
        self.view = None        # (lat, lon, zoom) corrente della mappa
        self.info = {}          # bounds, res_m, lat dell ultima anteprima

    def _event(self, name, q):
        if name == "view":
            self.view = (q["lat"], q["lon"], int(q["z"]))
        elif self.on_pick:
            self.on_pick(q["lat"], q["lon"])

    def build(self, tif_path, tiles_dir=None, scheme="xyz", ext="png", zmin=0, zmax=22, multiply=False, pick=False):
        """tif_path=None: solo mappa (per scegliere un punto con un clic). Ritorna l URL della pagina."""
        with self.lock:
            if tif_path:
                bounds, res_m = make_preview_png(tif_path, os.path.join(self.dir, "preview.png"))
                self.info = dict(bounds=bounds, res_m=res_m, lat=(bounds[0][0] + bounds[1][0]) / 2)
                overlay_js = OVERLAY_JS % dict(v=time.time_ns())
            else:
                bounds, self.info, overlay_js = ITALY, {}, ""
            has_tiles = bool(tiles_dir) and os.path.isdir(tiles_dir)
            tiles_js = TILES_JS % dict(ext=ext, tms=str(scheme == "tms").lower(), zmin=zmin, zmax=zmax) if has_tiles else ""
            with open(os.path.join(self.dir, "index.html"), "w", encoding="utf-8") as f:
                f.write(PAGE % dict(bounds=bounds, tiles_js=tiles_js, overlay_js=overlay_js,
                                    blend=str(multiply).lower(), pick=str(pick).lower()))

            _Handler.tiles_dir = tiles_dir if has_tiles else None
            _Handler.events = self._event
            if self.server is None:
                handler = functools.partial(_Handler, directory=self.dir)
                self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
                threading.Thread(target=self.server.serve_forever, daemon=True).start()
            return f"http://127.0.0.1:{self.server.server_port}/index.html"
