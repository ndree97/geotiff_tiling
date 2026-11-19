import glob
import os
import sys
import threading
import time

from osgeo import gdal
from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox,
    QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

import download
import georef
from preview import Preview, native_zoom, pixel_size_m, tile_count

gdal.UseExceptions()

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.dirname(sys.executable)  # radice dell'ambiente conda
IDENTITY = (0.0, 1.0, 0.0, 0.0, 0.0, 1.0)  # geotransform di un raster non georeferenziato

FORMATS = [("PNG  ·  qualità massima, con trasparenza", "png"),
           ("WebP  ·  leggero, con trasparenza", "webp"),
           ("JPEG  ·  il più piccolo, senza trasparenza", "jpeg")]
SCHEMES = [("Google Maps / OpenStreetMap / Leaflet  (XYZ)", "xyz"),
           ("TMS  ·  OpenLayers TMS, Cesium, GeoServer", "tms")]
RESAMPLINGS = ["near", "bilinear", "cubic", "cubicspline", "lanczos", "average"]


class Page(QWebEnginePage):
    """Impedisce che un file trascinato sulla mappa venga aperto: lo usa come nuova sorgente."""

    def __init__(self, parent, on_file):
        super().__init__(parent)
        self.on_file = on_file

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.isLocalFile():
            self.on_file(url.toLocalFile())
            return False
        return True


def find_tif(path):
    """File .tif da ispezionare: il file stesso, o il primo .tif (non *_tile.tif) di una cartella."""
    if os.path.isfile(path):
        return path
    tifs = [f for f in sorted(glob.glob(os.path.join(path, "*.tif*"))) if not f.lower().endswith("_tile.tif")]
    return tifs[0] if tifs else None


def inspect(path):
    """Legge le proprietà del raster. Solleva ValueError con un messaggio comprensibile."""
    is_dir = os.path.isdir(path)
    if is_dir:
        exts = {os.path.splitext(f)[1].lower() for f in os.listdir(path)}
        missing = [e for e in (".tif", ".tfw", ".prj") if e not in exts]
        if missing:
            raise ValueError("La cartella deve contenere .tif, .tfw e .prj: manca " + ", ".join(missing))
    elif not os.path.isfile(path):
        raise ValueError("File o cartella non trovati")
    tif = find_tif(path)
    if not tif:
        raise ValueError("Nessun file .tif trovato")
    try:
        d = gdal.Open(tif)
    except RuntimeError as e:
        raise ValueError(f"File non leggibile: {e}")
    ok = not (d.GetGeoTransform() == IDENTITY and not d.GetGCPCount())
    info = dict(tif=tif, is_dir=is_dir, w=d.RasterXSize, h=d.RasterYSize, georef=ok, dpi=georef.read_dpi(d))
    if ok:
        srs = d.GetSpatialRef()
        code = srs.GetAuthorityCode(None) if srs is not None else None
        info["crs"] = f"EPSG:{code}" if code else "CRS personalizzato"
        info["res_m"] = pixel_size_m(d)
    return info


class Window(QWidget):
    inspected = Signal(int, object, str)       # generazione, info, errore
    preview_ready = Signal(int, str, str)      # generazione, URL, errore
    picked = Signal(float, float)              # punto scelto con un clic sulla mappa
    view_changed = Signal()                    # la mappa è stata spostata o ingrandita
    located = Signal(str, object, str)         # nome, bbox, errore (ricerca luogo)
    downloaded = Signal(str, str, str)         # percorso, descrizione, errore
    dl_progress = Signal(float)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("GeoTIFF Tiling")
        self.resize(1320, 800)
        self.setAcceptDrops(True)
        self.settings = QSettings("geotiff_tiling", "gui")
        self.preview = Preview(on_pick=self.picked.emit, on_view=self.view_changed.emit)
        self.proc = None
        self.info = None           # risultato di inspect() sulla sorgente corrente
        self.gen = 0               # scarta i risultati di lavori superati
        self.auto_zoom_pending = False
        self.keep_view = False
        self.pass_no = 0           # gdal2tiles stampa 0..100 una volta per fase
        self.cancelled = False
        self.showing_tiles = False

        left = self._build_left()
        self.web = QWebEngineView()
        self.web.setPage(Page(self.web, self.set_source))
        self.web.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        self.web.setUrl(QUrl(self.preview.build(None)))  # mappa navigabile da cui scegliere l area

        scroll = QScrollArea(); scroll.setWidget(left); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame); scroll.setMinimumWidth(470)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(scroll); split.addWidget(self.web)
        split.setStretchFactor(1, 1); split.setSizes([480, 840])
        outer = QHBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.addWidget(split)

        self.inspected.connect(self.on_inspected)
        self.preview_ready.connect(self.on_preview_ready)
        self.picked.connect(self.on_picked)
        self.view_changed.connect(self.update_area)
        self.located.connect(self.on_located)
        self.downloaded.connect(self.on_downloaded)
        self.dl_progress.connect(lambda p: self.dl_bar.setValue(int(p * 100)))
        self.debounce = QTimer(self, singleShot=True, interval=500)  # anteprima live mentre si regolano i campi
        self.debounce.timeout.connect(lambda: self.refresh_preview(keep_view=True))
        self._load_settings()

    # ---------------------------------------------------------------- interfaccia
    def _build_left(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        # 1 · Sorgente: scarica online (predefinito) oppure file personalizzato
        self.search = QLineEdit(); self.search.setPlaceholderText("Cerca un luogo o inserisci LAT, LON  (es. Pozzuoli  oppure  40.84, 14.09)")
        self.search.returnPressed.connect(self.do_search)
        b_search = QPushButton("Cerca"); b_search.clicked.connect(self.do_search)
        srow = QHBoxLayout(); srow.addWidget(self.search, 1); srow.addWidget(b_search)
        self.dl_source = QComboBox()
        for key, src in download.SOURCES.items():
            self.dl_source.addItem(src["label"], key)
        self.dl_source.currentIndexChanged.connect(self.update_area)
        self.area_label = QLabel("Sposta e ingrandisci la mappa sull area che ti interessa."); self.area_label.setWordWrap(True)
        self.dl_button = QPushButton("Scarica l area visibile"); self.dl_button.setMinimumHeight(32)
        self.dl_button.clicked.connect(self.download_area)
        self.dl_bar = QProgressBar(); self.dl_bar.setRange(0, 100); self.dl_bar.setVisible(False)
        online = QWidget(); ol = QVBoxLayout(online)
        ol.addLayout(srow); ol.addWidget(self.dl_source); ol.addWidget(self.area_label)
        ol.addWidget(self.dl_button); ol.addWidget(self.dl_bar)

        self.source = QLineEdit(); self.source.setPlaceholderText("Trascina qui un GeoTIFF o una cartella (.tif + .tfw + .prj)")
        self.source.editingFinished.connect(lambda: self.set_source(self.source.text()))
        b_file = QPushButton("File…"); b_file.clicked.connect(self._pick_file)
        b_dir = QPushButton("Cartella…"); b_dir.clicked.connect(self._pick_dir)
        custom = QWidget(); cl = QVBoxLayout(custom)
        row = QHBoxLayout(); row.addWidget(self.source, 1); row.addWidget(b_file); row.addWidget(b_dir)
        cl.addLayout(row); cl.addStretch()

        self.tabs = QTabWidget(); self.tabs.addTab(online, "Scarica online"); self.tabs.addTab(custom, "File personalizzato")
        self.summary = QLabel(); self.summary.setWordWrap(True); self.summary.setTextFormat(Qt.RichText)
        g1 = QGroupBox("Sorgente"); l1 = QVBoxLayout(g1); l1.addWidget(self.tabs); l1.addWidget(self.summary)
        lay.addWidget(g1)

        # 2 · Posizionamento (solo per raster non georeferenziati)
        self.point = QLineEdit(); self.point.setPlaceholderText("LAT, LON  —  oppure clicca sulla mappa")
        self.scale = QDoubleSpinBox(); self.scale.setRange(1, 1_000_000); self.scale.setDecimals(0)
        self.scale.setValue(100); self.scale.setPrefix("1:")
        self.rot = QDoubleSpinBox(); self.rot.setRange(-360, 360); self.rot.setDecimals(1); self.rot.setSuffix(" °")
        self.rot_slider = QSlider(Qt.Horizontal); self.rot_slider.setRange(-180, 180)
        self.rot_slider.valueChanged.connect(self.rot.setValue)
        self.rot.valueChanged.connect(lambda v: self.rot_slider.setValue(int(v)) if -180 <= v <= 180 else None)
        rrow = QHBoxLayout(); rrow.addWidget(self.rot); rrow.addWidget(self.rot_slider, 1)
        self.dpi = QSpinBox(); self.dpi.setRange(0, 2400); self.dpi.setSpecialValueText("dal file")
        self.crop = QLineEdit(); self.crop.setPlaceholderText("X Y larghezza altezza in pixel (vuoto = tutta l immagine)")
        self.anchor = QLineEdit(); self.anchor.setPlaceholderText("X Y del pixel sul punto (vuoto = centro)")
        self.place_box = QGroupBox("Posizionamento approssimato")
        pf = QFormLayout(self.place_box)
        hint = QLabel("Il raster non ha coordinate: indica dove si trova. Il risultato è una stima, "
                      "non una vera georeferenziazione.")
        hint.setWordWrap(True)
        pf.addRow(hint)
        for label, widget in (("Punto", self.point), ("Scala disegno", self.scale), ("Rotazione", rrow),
                              ("DPI scansione", self.dpi), ("Ritaglio", self.crop), ("Ancora", self.anchor)):
            pf.addRow(label, widget)
        for sig in (self.point.textChanged, self.scale.valueChanged, self.rot.valueChanged,
                    self.dpi.valueChanged, self.crop.textChanged, self.anchor.textChanged):
            sig.connect(lambda *_: self.debounce.start())
        self.place_box.setVisible(False)
        lay.addWidget(self.place_box)

        # 3 · Tiles
        self.zmin = QSpinBox(); self.zmin.setRange(0, 30)
        self.zmax = QSpinBox(); self.zmax.setRange(0, 30)
        auto = QPushButton("Auto"); auto.setToolTip("Zoom massimo = risoluzione nativa del raster"); auto.clicked.connect(self.apply_auto_zoom)
        zrow = QHBoxLayout()
        for x in (QLabel("da"), self.zmin, QLabel("a"), self.zmax, auto):
            zrow.addWidget(x)
        zrow.addStretch()
        for sp in (self.zmin, self.zmax):
            sp.valueChanged.connect(self.update_estimate)
        self.estimate = QLabel(); self.estimate.setWordWrap(True)
        self.fmt = QComboBox()
        for label, value in FORMATS:
            self.fmt.addItem(label, value)
        self.scheme = QComboBox()
        for label, value in SCHEMES:
            self.scheme.addItem(label, value)
        self.output = QLineEdit(os.path.join(HERE, "output_tiles"))
        b_out = QPushButton("…"); b_out.setFixedWidth(34); b_out.clicked.connect(self._pick_output)
        orow = QHBoxLayout(); orow.addWidget(self.output, 1); orow.addWidget(b_out)
        g3 = QGroupBox("Tiles"); f3 = QFormLayout(g3)
        f3.addRow("Livelli di zoom", zrow)
        f3.addRow("", self.estimate)
        f3.addRow("Formato", self.fmt)
        f3.addRow("Compatibile con", self.scheme)
        f3.addRow("Cartella output", orow)
        lay.addWidget(g3)

        # Opzioni avanzate (chiuse di default)
        self.procs = QSpinBox(); self.procs.setRange(1, 64); self.procs.setValue(os.cpu_count() or 4)
        self.resampling = QComboBox(); self.resampling.addItems(RESAMPLINGS)
        self.resampling.setToolTip("near: il più veloce. bilinear/cubic/lanczos: più morbido, più lento")
        self.resume = QPushButton("Riprendi un lavoro interrotto (solo tiles mancanti)"); self.resume.setCheckable(True)
        adv = QGroupBox("Opzioni avanzate"); adv.setCheckable(True); adv.setChecked(False)
        inner = QWidget(); fa = QFormLayout(inner); fa.setContentsMargins(0, 0, 0, 0)
        fa.addRow("Processi", self.procs); fa.addRow("Resampling", self.resampling); fa.addRow(self.resume)
        va = QVBoxLayout(adv); va.addWidget(inner); inner.setVisible(False)
        adv.toggled.connect(inner.setVisible)
        lay.addWidget(adv)

        # Azioni
        self.start = QPushButton("Genera tiles"); self.start.setObjectName("primary"); self.start.setMinimumHeight(38)
        self.start.setStyleSheet("QPushButton#primary{background:#2d7ff9;color:white;font-weight:600;border:0;border-radius:4px}"
                                 "QPushButton#primary:disabled{background:#7a8aa3;color:#ddd}")
        self.start.clicked.connect(self.run); self.start.setEnabled(False)
        self.cancel = QPushButton("Annulla"); self.cancel.setMinimumHeight(38); self.cancel.setVisible(False)
        self.cancel.clicked.connect(self.stop)
        arow = QHBoxLayout(); arow.addWidget(self.start, 1); arow.addWidget(self.cancel)
        lay.addLayout(arow)
        self.bar = QProgressBar(); self.bar.setRange(0, 100); self.bar.setVisible(False)
        lay.addWidget(self.bar)
        self.status = QLabel(); self.status.setWordWrap(True)
        lay.addWidget(self.status)

        b_open = QPushButton("Apri cartella output"); b_open.clicked.connect(self.open_output)
        b_browser = QPushButton("Apri mappa nel browser"); b_browser.clicked.connect(self.open_browser)
        b_log = QPushButton("Dettagli"); b_log.setCheckable(True)
        brow = QHBoxLayout()
        for b in (b_open, b_browser, b_log):
            brow.addWidget(b)
        lay.addLayout(brow)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setVisible(False); self.log.setMinimumHeight(160)
        b_log.toggled.connect(self.log.setVisible)
        self.b_log = b_log
        lay.addWidget(self.log)
        lay.addStretch()
        return w

    def set_status(self, text, error=False):
        self.status.setStyleSheet("color:#e5484d" if error else "")
        self.status.setText(text)

    # ---------------------------------------------------------------- sorgente
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.set_source(e.mimeData().urls()[0].toLocalFile())

    def _start_dir(self):
        return self.settings.value("last_dir", HERE)

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "GeoTIFF", self._start_dir(), "GeoTIFF (*.tif *.tiff)")
        if path:
            self.set_source(path)

    def _pick_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Cartella con .tif + .tfw + .prj", self._start_dir())
        if path:
            self.set_source(path)

    def _pick_output(self):
        path = QFileDialog.getExistingDirectory(self, "Cartella output", self.output.text())
        if path:
            self.output.setText(os.path.normpath(path))

    # ---------------------------------------------------------------- ricerca e download online
    def do_search(self):
        q = self.search.text().strip()
        if not q:
            return
        self.set_status("Cerco…")

        def work():
            try:
                name, bbox = download.geocode(q)
                self.located.emit(name, bbox, "")
            except Exception as e:
                self.located.emit("", None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def on_located(self, name, bbox, err):
        if err:
            self.set_status(f"Ricerca non riuscita: {err}", error=True)
            return
        w, s, e, n = bbox
        self.web.page().runJavaScript(f"map.fitBounds([[{s},{w}],[{n},{e}]]);")
        self.set_status(f"Trovato: {name}. Regola la vista e premi «Scarica l area visibile».")

    def update_area(self):
        b = self.preview.view_bounds
        if not b:
            return
        res = download.SOURCES[self.dl_source.currentData()]["res"]
        wm, hm = download.size_m(b)
        wpx, hpx = download.size_px(b, res)
        big = wpx * hpx > download.MAX_PIXELS
        text = f"Area visibile: {wm / 1000:.2f} × {hm / 1000:.2f} km  →  {wpx:,} × {hpx:,} px".replace(",", ".")
        if big:
            text = f"<span style='color:#e5484d'>{text}<br>troppo grande: ingrandisci la mappa</span>"
        self.area_label.setText(text)
        self.dl_button.setEnabled(not big and not self.dl_bar.isVisible())

    def download_area(self):
        bbox, key = self.preview.view_bounds, self.dl_source.currentData()
        if not bbox:
            return
        self.dl_button.setEnabled(False); self.dl_bar.setValue(0); self.dl_bar.setVisible(True)
        self.set_status("Download in corso…")

        def work():
            try:
                path, desc = download.fetch(key, bbox, os.path.join(HERE, "downloads"), progress=self.dl_progress.emit)
                self.downloaded.emit(path, desc, "")
            except Exception as e:
                self.downloaded.emit("", "", str(e))
        threading.Thread(target=work, daemon=True).start()

    def on_downloaded(self, path, desc, err):
        self.dl_bar.setVisible(False)
        self.dl_button.setEnabled(True)
        if err:
            self.set_status(f"Download non riuscito: {err}", error=True)
            return
        self.set_status(f"Scaricato: {desc}")
        self.set_source(path)

    def set_source(self, path):
        path = os.path.normpath(path.strip().strip('"')) if path.strip() else ""
        self.source.setText(path)
        self.gen += 1
        gen = self.gen
        self.info = None
        self.start.setEnabled(False)
        self.place_box.setVisible(False)
        if not path:
            self.summary.setText(""); return
        self.settings.setValue("last_dir", os.path.dirname(path))
        self.summary.setText("Lettura del raster…")

        def work():
            try:
                self.inspected.emit(gen, inspect(path), "")
            except Exception as e:
                self.inspected.emit(gen, None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def on_inspected(self, gen, info, err):
        if gen != self.gen:
            return
        if err:
            self.summary.setText(f"<span style='color:#e5484d'>{err}</span>")
            return
        self.info = info
        size = f"{info['w']:,} × {info['h']:,} px".replace(",", ".")
        kind = "cartella con .tfw/.prj" if info["is_dir"] else "file"
        if info["georef"]:
            res = info["res_m"]
            res_txt = f"{res * 100:.1f} cm/px" if res < 1 else f"{res:.1f} m/px"
            self.summary.setText(f"<b>✔ Georeferenziato</b> · {info['crs']} · {size} · {res_txt}<br><small>{kind}</small>")
        else:
            self.summary.setText(f"<b>⚠ Non georeferenziato</b> · {size}<br><small>Indica sotto dove si trova: "
                                 f"la GUI lo posiziona in modo approssimato.</small>")
            self.dpi.setValue(0)
            self.resampling.setCurrentText("average")  # con "near" le linee sottili dei disegni spariscono rimpicciolendo
        self.place_box.setVisible(not info["georef"])
        self.start.setEnabled(True)
        self.auto_zoom_pending = True
        self.refresh_preview()

    # ---------------------------------------------------------------- anteprima
    def _place_args(self):
        """None se manca ancora il punto. ValueError se i valori non sono validi."""
        def nums(text, n, what):
            try:
                v = [float(x) for x in text.replace(",", " ").split()]
            except ValueError:
                v = []
            if len(v) != n:
                raise ValueError(f"{what}: servono {n} numeri")
            return v

        if not self.point.text().strip():
            return None
        lat, lon = nums(self.point.text(), 2, "Punto (LAT, LON)")
        crop, anchor = self.crop.text().strip(), self.anchor.text().strip()
        return dict(lat=lat, lon=lon, scale=self.scale.value(), rotation=self.rot.value(),
                    dpi=self.dpi.value() or None,
                    crop=tuple(int(x) for x in nums(crop, 4, "Ritaglio")) if crop else None,
                    anchor_px=tuple(nums(anchor, 2, "Ancora")) if anchor else None)

    def _tiles_dir(self):
        src = self.source.text()
        base = f"{os.path.basename(src)}_tile" if self.info["is_dir"] else os.path.splitext(os.path.basename(src))[0]
        return os.path.join(self.output.text().strip(), f"{base}_{self.zmin.value()}{self.zmax.value()}")

    def refresh_preview(self, show_tiles=False, keep_view=False):
        if not self.info:
            return
        try:
            place = None if self.info["georef"] else self._place_args()
        except ValueError as e:
            self.set_status(str(e), error=True)
            return
        self.gen += 1
        gen, info = self.gen, self.info
        self.keep_view = keep_view
        self.showing_tiles = show_tiles
        needs_point = not info["georef"] and place is None
        if not show_tiles:
            self.set_status("Clicca sulla mappa per scegliere il punto." if needs_point else "Preparo l anteprima…")
        tiles = self._tiles_dir() if show_tiles else None
        kw = dict(scheme=self.scheme.currentData(), ext=self.fmt.currentData().replace("jpeg", "jpg"),
                  zmin=self.zmin.value(), zmax=self.zmax.value(), multiply=not info["georef"], pick=not info["georef"])

        def work():  # l overview di un raster grande richiede secondi: fuori dal thread della GUI
            try:
                if needs_point:
                    url = self.preview.build(None, **kw)
                else:
                    src = georef.place_raster(info["tif"], **place) if place else info["tif"]
                    url = self.preview.build(src, tiles, **kw)
                self.preview_ready.emit(gen, url, "")
            except Exception as e:
                self.preview_ready.emit(gen, "", str(e))
        threading.Thread(target=work, daemon=True).start()

    def on_preview_ready(self, gen, url, err):
        if gen != self.gen:
            return
        if err:
            self.auto_zoom_pending = False  # imposta lo zoom a mano
            self.set_status(f"Anteprima non disponibile: {err}", error=True)
            return
        view = self.preview.view
        frag = f"#{view[2]}/{view[0]}/{view[1]}" if self.keep_view and view else ""
        self.web.setUrl(QUrl(f"{url}?v={time.time_ns()}{frag}"))  # il parametro forza il ricaricamento
        if self.auto_zoom_pending and self.preview.info:
            self.apply_auto_zoom()
        self.update_estimate()
        if self.info and not self.info["georef"] and not self.preview.info:
            return
        if not self.showing_tiles:
            self.set_status("Anteprima pronta. Puoi generare le tiles.")

    def on_picked(self, lat, lon):
        self.point.setText(f"{lat:.8f}, {lon:.8f}")  # il cambio del testo aggiorna l anteprima

    # ---------------------------------------------------------------- zoom e stima
    def apply_auto_zoom(self):
        i = self.preview.info
        if not i:
            return
        self.auto_zoom_pending = False
        zmax = max(0, min(24, round(native_zoom(i["res_m"], i["lat"]))))
        self.zmax.setValue(zmax); self.zmin.setValue(max(0, zmax - 4))

    def update_estimate(self):
        i = self.preview.info
        if not i:
            self.estimate.setText(""); return
        zmin, zmax = self.zmin.value(), self.zmax.value()
        if zmin > zmax:
            self.estimate.setText("<span style='color:#e5484d'>Lo zoom minimo supera il massimo</span>"); return
        n = tile_count(i["bounds"], zmin, zmax)
        native = round(native_zoom(i["res_m"], i["lat"]))
        text = f"≈ {n:,} tiles".replace(",", ".")
        if zmax > native:
            text += f" · sopra lo zoom {native} l immagine è solo ingrandita"
        if n > 200_000:
            text = f"<span style='color:#e5484d'>{text} · molto grande: riduci lo zoom massimo</span>"
        self.estimate.setText(text)

    # ---------------------------------------------------------------- esecuzione
    def run(self):
        if not self.info:
            return
        try:
            place = None if self.info["georef"] else self._place_args()
        except ValueError as e:
            self.set_status(str(e), error=True); return
        if not self.info["georef"] and place is None:
            self.set_status("Imposta il punto (LAT, LON) o cliccalo sulla mappa.", error=True); return
        if self.auto_zoom_pending:
            self.set_status("Sto ancora leggendo il raster per calcolare lo zoom: riprova tra un momento.", error=True); return
        if self.zmin.value() > self.zmax.value():
            self.set_status("Lo zoom minimo supera il massimo.", error=True); return

        args = [
            "-u", os.path.join(HERE, "main.py"),
            "-d" if self.info["is_dir"] else "-i", self.source.text().strip(),
            "-o", self.output.text().strip(),
            "-z", f"{self.zmin.value()}-{self.zmax.value()}",
            "-p", str(self.procs.value()),
            "--scheme", self.scheme.currentData(),
            "-f", self.fmt.currentData(),
            "-r", self.resampling.currentText(),
        ]
        if self.resume.isChecked():
            args.append("--resume")
        if place:
            args += ["--place", str(place["lat"]), str(place["lon"]), "--scale", str(place["scale"]),
                     "--rotation", str(place["rotation"])]
            if place["dpi"]:
                args += ["--dpi", str(place["dpi"])]
            if place["crop"]:
                args += ["--crop", *map(str, place["crop"])]
            if place["anchor_px"]:
                args += ["--anchor-px", *map(str, place["anchor_px"])]

        # Rende gdal2tiles/gdal_translate trovabili anche senza "conda activate".
        env = QProcessEnvironment.systemEnvironment()
        extra = os.pathsep.join([ENV, os.path.join(ENV, "Scripts"), os.path.join(ENV, "Library", "bin")])
        env.insert("PATH", extra + os.pathsep + env.value("PATH"))
        env.insert("GDAL_DATA", os.path.join(ENV, "Library", "share", "gdal"))
        env.insert("PROJ_DATA", os.path.join(ENV, "Library", "share", "proj"))
        env.insert("PYTHONIOENCODING", "utf-8")

        self.log.clear(); self.bar.setValue(0); self.pass_no = 0; self.cancelled = False
        self.proc = QProcess(self)
        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(HERE)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.on_output)
        self.proc.finished.connect(self.on_finished)
        self.log.appendPlainText(f"> python {' '.join(args)}\n")
        self._set_running(True)
        self.set_status("Generazione in corso…")
        self.proc.start(sys.executable, args)

    def _set_running(self, running):
        self.tabs.setEnabled(not running)
        for w in (self.point, self.scale, self.rot, self.rot_slider, self.dpi, self.crop, self.anchor,
                  self.zmin, self.zmax, self.fmt, self.scheme, self.output, self.procs, self.resampling, self.resume):
            w.setEnabled(not running)
        self.start.setVisible(not running); self.cancel.setVisible(running); self.bar.setVisible(running)

    def on_output(self):
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        # gdal2tiles stampa "0...10...100 - done." una volta per fase: tiles di base (~80%) e piramide (~20%)
        for token in text.replace("\r", "\n").replace(".", " ").split():
            if token.isdigit() and int(token) <= 100:
                p = int(token)
                self.bar.setValue(int(0.8 * p) if self.pass_no == 0 else int(80 + 0.2 * p))
            elif token == "done":
                self.pass_no += 1
        self.log.moveCursor(QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.moveCursor(QTextCursor.End)

    def on_finished(self, code, _status):
        self._set_running(False)
        self.settings.setValue("output", self.output.text())
        if self.cancelled:
            return
        if code == 0:
            self.bar.setValue(100)
            self.set_status("Completato. Le tiles sono visibili nella mappa (livello «Tiles generate»).")
            self.refresh_preview(show_tiles=True, keep_view=True)
        else:
            self.set_status(f"Terminato con errore (codice {code}). Apri «Dettagli» per il log.", error=True)
            self.b_log.setChecked(True)

    def stop(self):
        if self.proc:
            self.cancelled = True
            self.proc.kill()  # gdal2tiles ha processi figli: kill() ferma il padre, «Riprendi» completa da lì
            self.set_status("Annullato. Con «Riprendi» (opzioni avanzate) puoi completare il lavoro.")

    def open_output(self):
        os.makedirs(self.output.text(), exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.output.text()))

    def open_browser(self):
        if self.web.url().scheme() == "http":
            QDesktopServices.openUrl(self.web.url())

    # ---------------------------------------------------------------- impostazioni
    def _load_settings(self):
        s = self.settings
        self.output.setText(s.value("output", self.output.text()))
        for combo, key in ((self.fmt, "fmt"), (self.scheme, "scheme")):
            i = combo.findData(s.value(key, ""))
            if i >= 0:
                combo.setCurrentIndex(i)
        self.resampling.setCurrentText(s.value("resampling", "near"))
        self.procs.setValue(int(s.value("procs", self.procs.value())))

    def closeEvent(self, e):
        s = self.settings
        s.setValue("output", self.output.text()); s.setValue("fmt", self.fmt.currentData())
        s.setValue("scheme", self.scheme.currentData()); s.setValue("resampling", self.resampling.currentText())
        s.setValue("procs", self.procs.value())
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self.proc.kill()
        super().closeEvent(e)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = Window()
    w.show()
    sys.exit(app.exec())
