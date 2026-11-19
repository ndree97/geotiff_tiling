import os
import sys
import threading
from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
    QDoubleSpinBox, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
    QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

import georef
from preview import Preview

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.dirname(sys.executable)  # radice dell'ambiente conda


class Window(QWidget):
    preview_ready = Signal(str)  # URL, oppure messaggio d'errore con prefisso '!'

    def __init__(self):
        super().__init__()
        self.setWindowTitle("GeoTIFF Tiling")
        self.resize(1300, 720)
        self.proc = None
        self.preview = Preview()

        self.file_mode = QRadioButton("File GeoTIFF")
        self.dir_mode = QRadioButton("Cartella (.tif + .tfw + .prj)")
        self.file_mode.setChecked(True)
        self.input = QLineEdit()
        self.output = QLineEdit(os.path.join(HERE, "output_tiles"))
        self.zmin = QSpinBox(); self.zmin.setRange(0, 30); self.zmin.setValue(15)
        self.zmax = QSpinBox(); self.zmax.setRange(0, 30); self.zmax.setValue(20)
        self.procs = QSpinBox(); self.procs.setRange(1, 64); self.procs.setValue(os.cpu_count() or 4)
        self.scheme = QComboBox(); self.scheme.addItems(["xyz", "tms"])
        self.scheme.setToolTip("xyz: Google Maps, OpenStreetMap, Leaflet, MapLibre\ntms: Y invertita (OpenLayers TMS, Cesium)")
        self.fmt = QComboBox(); self.fmt.addItems(["png", "webp", "jpeg"])
        self.resampling = QComboBox(); self.resampling.addItems(["near", "bilinear", "cubic", "cubicspline", "lanczos", "average"])
        self.resume = QCheckBox("Riprendi (solo tiles mancanti)")

        # Posizionamento approssimato di raster non georeferenziati (disegni scansionati)
        self.place_on = QCheckBox("Posiziona raster non georeferenziato (approssimato)")
        self.place_ll = QLineEdit(); self.place_ll.setPlaceholderText("LAT, LON   es. 40.84022778, 14.09117778")
        self.scale = QDoubleSpinBox(); self.scale.setRange(1, 1_000_000); self.scale.setDecimals(0); self.scale.setValue(100)
        self.scale.setPrefix("1:")
        self.rot = QDoubleSpinBox(); self.rot.setRange(-360, 360); self.rot.setDecimals(1); self.rot.setSuffix(" ° orari dal nord")
        self.crop = QLineEdit(); self.crop.setPlaceholderText("X Y W H in pixel (vuoto = tutta l immagine)")
        self.anchor = QLineEdit(); self.anchor.setPlaceholderText("X Y del pixel sul punto (vuoto = centro)")
        self.place_box = QWidget(); pform = QFormLayout(self.place_box); pform.setContentsMargins(0, 0, 0, 0)
        for label, w in (("Punto", self.place_ll), ("Scala disegno", self.scale), ("Rotazione", self.rot),
                         ("Ritaglio", self.crop), ("Ancora", self.anchor)):
            pform.addRow(label, w)
        self.place_box.setEnabled(False)
        self.place_on.toggled.connect(self.place_box.setEnabled)
        self.dir_mode.toggled.connect(lambda on: (self.place_on.setChecked(False), self.place_on.setEnabled(not on)))

        form = QFormLayout()
        mode = QHBoxLayout(); mode.addWidget(self.file_mode); mode.addWidget(self.dir_mode)
        form.addRow("Sorgente", mode)
        form.addRow("Input", self._with_browse(self.input, self._pick_input))
        form.addRow("Output", self._with_browse(self.output, self._pick_output))
        zoom = QHBoxLayout(); zoom.addWidget(self.zmin); zoom.addWidget(self.zmax); zoom.addStretch()
        form.addRow("Zoom min / max", zoom)
        form.addRow("Processi", self.procs)
        form.addRow("Schema", self.scheme)
        form.addRow("Formato", self.fmt)
        form.addRow("Resampling", self.resampling)
        form.addRow("", self.resume)
        form.addRow(self.place_on)
        form.addRow(self.place_box)

        self.start = QPushButton("Avvia"); self.start.clicked.connect(self.run)
        self.cancel = QPushButton("Annulla"); self.cancel.setEnabled(False); self.cancel.clicked.connect(self.stop)
        self.open_out = QPushButton("Apri cartella output"); self.open_out.clicked.connect(self.open_output)
        self.show_prev = QPushButton("Anteprima"); self.show_prev.clicked.connect(self.refresh_preview)
        buttons = QHBoxLayout()
        for b in (self.start, self.cancel, self.show_prev, self.open_out):
            buttons.addWidget(b)

        self.bar = QProgressBar(); self.bar.setRange(0, 100); self.bar.setValue(0)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True)

        left = QWidget()
        layout = QVBoxLayout(left)
        layout.addLayout(form); layout.addLayout(buttons); layout.addWidget(self.bar); layout.addWidget(self.log)

        self.web = QWebEngineView()
        self.web.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        self.web.setHtml("<body style='font-family:sans-serif;color:#666'><p>Premi <b>Anteprima</b> per vedere "
                         "il GeoTIFF e le tiles generate su OpenStreetMap.</p></body>")
        self.preview_ready.connect(self.on_preview_ready)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left); split.addWidget(self.web)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1); split.setSizes([480, 820])
        outer = QHBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.addWidget(split)

    def _with_browse(self, edit, slot):
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0)
        btn = QPushButton("Sfoglia…"); btn.clicked.connect(slot)
        row.addWidget(edit); row.addWidget(btn)
        w = QWidget(); w.setLayout(row)
        return w

    def _pick_input(self):
        if self.dir_mode.isChecked():
            path = QFileDialog.getExistingDirectory(self, "Cartella sorgente")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "GeoTIFF", "", "GeoTIFF (*.tif *.tiff)")
        if path:
            self.input.setText(os.path.normpath(path))

    def _pick_output(self):
        path = QFileDialog.getExistingDirectory(self, "Cartella output")
        if path:
            self.output.setText(os.path.normpath(path))

    def _place_args(self):
        """None se il posizionamento è spento, altrimenti i parametri per georef.place_raster. ValueError se non validi."""
        if not self.place_on.isChecked():
            return None

        def nums(text, n, what):
            try:
                v = [float(x) for x in text.replace(",", " ").split()]
            except ValueError:
                v = []
            if len(v) != n:
                raise ValueError(f"{what}: servono {n} numeri")
            return v

        lat, lon = nums(self.place_ll.text(), 2, "Punto (LAT, LON)")
        crop = self.crop.text().strip()
        anchor = self.anchor.text().strip()
        return dict(lat=lat, lon=lon, scale=self.scale.value(), rotation=self.rot.value(),
                    crop=tuple(int(x) for x in nums(crop, 4, "Ritaglio")) if crop else None,
                    anchor_px=tuple(nums(anchor, 2, "Ancora")) if anchor else None)

    def run(self):
        if not self.input.text().strip():
            self.log.appendPlainText("Seleziona un input.")
            return
        try:
            place = self._place_args()
        except ValueError as e:
            self.log.appendPlainText(str(e))
            return
        if self.zmin.value() > self.zmax.value():
            self.log.appendPlainText("Zoom min deve essere <= zoom max.")
            return
        args = [
            "-u", os.path.join(HERE, "main.py"),
            "-d" if self.dir_mode.isChecked() else "-i", self.input.text().strip(),
            "-o", self.output.text().strip(),
            "-z", f"{self.zmin.value()}-{self.zmax.value()}",
            "-p", str(self.procs.value()),
            "--scheme", self.scheme.currentText(),
            "-f", self.fmt.currentText(),
            "-r", self.resampling.currentText(),
        ]
        if self.resume.isChecked():
            args.append("--resume")
        if place:
            args += ["--place", str(place["lat"]), str(place["lon"]), "--scale", str(place["scale"]),
                     "--rotation", str(place["rotation"])]
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

        self.log.clear(); self.bar.setValue(0)
        self.proc = QProcess(self)
        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(HERE)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.on_output)
        self.proc.finished.connect(self.on_finished)
        self.log.appendPlainText(f"> python {' '.join(args)}\n")
        self.start.setEnabled(False); self.cancel.setEnabled(True)
        self.proc.start(sys.executable, args)

    def on_output(self):
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        # gdal2tiles stampa "0...10...20...100 - done."; l'ultimo numero è l'avanzamento
        for token in text.replace("\r", "\n").replace(".", " ").split():
            if token.isdigit() and int(token) <= 100:
                self.bar.setValue(int(token))
        self.log.moveCursor(QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.moveCursor(QTextCursor.End)

    def on_finished(self, code, _status):
        ok = code == 0
        if ok:
            self.bar.setValue(100)
            self.refresh_preview()
        self.log.appendPlainText("\nCompletato." if ok else f"\nTerminato con errore (codice {code}).")
        self.start.setEnabled(True); self.cancel.setEnabled(False)

    def _tif_and_tiles(self):
        """Stessi percorsi che calcola main.py: (tif sorgente, cartella tiles)."""
        src = self.input.text().strip()
        zoom = f"{self.zmin.value()}{self.zmax.value()}"
        if self.dir_mode.isChecked():
            name = os.path.basename(os.path.normpath(src))
            tif = os.path.join(src, f"{name}_tile.tif")  # creato da main.py alla prima esecuzione
        else:
            tif = src
        base = os.path.splitext(os.path.basename(tif))[0]
        return tif, os.path.join(self.output.text().strip(), f"{base}_{zoom}")

    def refresh_preview(self):
        try:
            place = self._place_args()
        except ValueError as e:
            self.log.appendPlainText(str(e))
            return
        tif, tiles = self._tif_and_tiles()
        if not os.path.isfile(tif):
            self.log.appendPlainText(f"Anteprima: file non trovato ({tif}). In modalità cartella esiste dopo la prima generazione.")
            return
        self.show_prev.setEnabled(False)
        self.log.appendPlainText("Anteprima: preparo l'overview del GeoTIFF…")
        args = dict(scheme=self.scheme.currentText(), ext=self.fmt.currentText().replace("jpeg", "jpg"),
                    zmin=self.zmin.value(), zmax=self.zmax.value(), multiply=bool(place))

        def work():  # l'overview di un raster grande può richiedere secondi: fuori dal thread GUI
            try:
                src = georef.place_raster(tif, **place) if place else tif
                self.preview_ready.emit(self.preview.build(src, tiles, **args))
            except Exception as e:
                self.preview_ready.emit(f"!{e}")
        threading.Thread(target=work, daemon=True).start()

    def on_preview_ready(self, msg):
        self.show_prev.setEnabled(True)
        if msg.startswith("!"):
            self.log.appendPlainText(f"Anteprima fallita: {msg[1:]}")
        else:
            self.web.setUrl(QUrl(msg))

    def stop(self):
        if self.proc:
            self.proc.kill()  # gdal2tiles ha processi figli: kill() ferma il padre, --resume riprende da lì

    def open_output(self):
        os.makedirs(self.output.text(), exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.output.text()))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = Window()
    w.show()
    sys.exit(app.exec())
