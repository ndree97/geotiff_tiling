# GeoTIFF to Map Tiles Converter

Uno strumento Python per convertire automaticamente ortofoto georeferenziate e rilievi fotogrammetrici in **Map Tiles XYZ (Web Mercator)** pronte per essere visualizzate su **Google Maps**, **Leaflet** o **OpenLayers**, utilizzando GDAL e `gdal2tiles`.

[![GitHub stars](https://img.shields.io/github/stars/ndree97/geotiff_tiling?style=flat-square)](https://github.com/ndree97/geotiff_tiling/stargazers)
[![GitHub issues](https://img.shields.io/github/issues/ndree97/geotiff_tiling?style=flat-square)](https://github.com/ndree97/geotiff_tiling/issues)
[![GitHub repo size](https://img.shields.io/github/repo-size/ndree97/geotiff_tiling?style=flat-square)](https://github.com/ndree97/geotiff_tiling)
---

## ✨ Funzionalità

- **Modalità File Singolo**: Converte direttamente un file GeoTIFF già georeferenziato (`.tif`).
- **Modalità Directory (Fotogrammetria / Droni)**: Riconosce ed unisce automaticamente i file grezzi esportati da software come DJI Terra, Pix4D, Agisoft Metashape o WebODM (`.tif` immagine + `.tfw` worldfile + `.prj` proiezione) creando un GeoTIFF georeferenziato compresso con LZW.
- **File Virtuale VRT a 8-bit**: Converte al volo il raster sorgente in un VRT 8-bit senza duplicare dati pesanti su disco.
- **Elaborazione Multiprocesso**: Sfrutta CPU multi-core (`--processes`) con monitoraggio dell'avanzamento in tempo reale.
- **Server di Preview Locale Incluso**: Script `route.py` integrato per visualizzare immediatamente la mappa nel browser.

---

## 📋 Requisiti

- **Python 3.9+**
- **GDAL** con utility da riga di comando (`gdal_translate`, `gdal2tiles`) e binding Python.

### Installazione Dipendenze

È possibile installare le dipendenze Python tramite:

```bash
pip install -r requirements.txt
```

> [!TIP]
> Su **Windows**, il modo più semplice per disporre di GDAL e dei suoi strumenti CLI è utilizzare **Conda / Mamba**.
> Il file `environment.yml` crea l'ambiente dedicato `geotiff-tiling`:
> ```bash
> conda env create -f environment.yml
> conda activate geotiff-tiling
> ```
> In alternativa è possibile utilizzare l'installer [OSGeo4W](https://trac.osgeo.org/osgeo4w/).

---

## 🚀 Utilizzo

### 0. Interfaccia grafica (Qt)
```bash
conda activate geotiff-tiling
python gui.py
```
1. **Sorgente**, due modi:
   - **Scarica online** (predefinito): cerca un luogo o inserisci `LAT, LON`, sposta/ingrandisci la mappa sull'area che ti interessa e premi *Scarica l'area visibile*. Il GeoTIFF viene salvato in `downloads/` e usato subito per il tiling. Fonti gratuite, senza chiavi: **Ortofoto Italia 2012 AGEA** (50 cm, solo Italia, servizio WMS del Geoportale Nazionale) e **Sentinel-2** (10 m, tutto il mondo, ultima scena con meno del 15% di nuvole). Area massima: circa 36 milioni di pixel.
   - **File personalizzato** (opzionale): trascina un GeoTIFF o una cartella (`.tif` + `.tfw` + `.prj`) sulla finestra, oppure usa *File…* / *Cartella…*.

   In entrambi i casi la GUI mostra CRS, dimensione e risoluzione e disegna subito l'anteprima su **OpenStreetMap**.
2. **Tiles**: lo zoom massimo è calcolato dalla risoluzione del raster (*Auto*) con la stima del numero di tiles; scegli formato (PNG/WebP/JPEG) e compatibilità (XYZ per Google Maps/OSM/Leaflet, TMS per OpenLayers/Cesium).
3. **Genera tiles**: barra di avanzamento e, a fine lavoro, le tiles compaiono nella mappa (livello *Tiles generate*). *Dettagli* mostra il log; *Opzioni avanzate* contiene processi, resampling e ripresa.

Se il raster **non è georeferenziato** (es. disegno scansionato) compare il pannello *Posizionamento approssimato*: clicca sulla mappa per scegliere il punto, poi regola scala e rotazione: l'anteprima si aggiorna da sola (il bianco del disegno diventa trasparente per confrontarlo con la mappa). Le impostazioni principali vengono ricordate tra le sessioni. La GUI lancia `main.py`, quindi i due sono sempre allineati.

> [!NOTE]
> I dati scaricati hanno le proprie licenze d'uso: Sentinel-2 è libero (Copernicus); per l'ortofoto AGEA 2012 del Geoportale Nazionale verifica le condizioni di utilizzo prima di ridistribuire le tiles. Il tool non scarica tiles di Google, Bing o OSM, i cui termini lo vietano. La ricerca dei luoghi usa Nominatim (OpenStreetMap), pensato per un uso interattivo.

Il pannello della mappa richiede internet (mappa base OSM e Leaflet). `preview.py` serve la pagina da un server HTTP locale perché i tile server di OSM rifiutano le richieste senza Referer (cioè da `file://`).

### 1. Modalità File Singolo (GeoTIFF)
Usa questa modalità se disponi già di un raster GeoTIFF georeferenziato:

```bash
python main.py -i /percorso/ortofoto.tif -z 15-22 -p 4
```

### 1b. Raster non georeferenziato (posizionamento approssimato)
Per un'immagine senza coordinate (es. disegno tecnico scansionato) puoi **stimare** la posizione: dai un punto, la scala del disegno e una rotazione. I DPI sono letti dal TIFF (`--dpi` per forzarli).

```bash
python main.py -i disegno.tif --place 40.84022778 14.09117778 --scale 100 --rotation 0 --crop 1700 250 4300 2550 -z 18-22
```

| Opzione | Significato |
| :--- | :--- |
| `--place LAT LON` | punto su cui cade il centro del raster (o del ritaglio) |
| `--anchor-px X Y` | pixel dell'immagine intera da far cadere sul punto (default: centro) |
| `--scale` | denominatore della scala del disegno (default `100` = 1:100) |
| `--rotation` | gradi in senso orario di cui la parte alta del disegno è ruotata rispetto al nord |
| `--crop X Y W H` | usa solo un ritaglio (pixel dell'immagine intera) |

> [!WARNING]
> Non è una vera georeferenziazione (nessun punto di controllo, nessuna deformazione): serve a capire se un file è utilizzabile. Per un risultato preciso usa il Georeferencer di QGIS. Nella GUI, con il posizionamento attivo, l'anteprima fonde il disegno con la mappa (il bianco diventa trasparente) per verificare la posizione.

### 2. Modalità Directory (File Grezzi Fotogrammetria)
Usa questa modalità se hai una cartella contenente i tre file separati (`.tif`, `.tfw`, `.prj`):

```bash
python main.py -d ./cartella_rilievo -o output_tiles -z 15-22
```

### Parametri CLI

| Argomento | Descrizione | Default |
| :--- | :--- | :--- |
| `-i, --input` | Percorso del file GeoTIFF di input (`.tif`) | *(mutuamente esclusivo con `-d`)* |
| `-d, --directory` | Cartella con i file grezzi (`.tif`, `.tfw`, `.prj`) | *(mutuamente esclusivo con `-i`)* |
| `-o, --output` | Cartella di destinazione delle tiles | `output_tiles` |
| `-z, --zoom` | Intervallo dei livelli di zoom (es. `15-20`, `15-24`) | `15-25` |
| `-p, --processes` | Numero di processi paralleli | n. CPU |
| `--scheme` | `xyz` = Google Maps, **OpenStreetMap**, Leaflet, MapLibre; `tms` = Y invertita (OpenLayers TMS, Cesium) | `xyz` |
| `-f, --format` | Formato tiles: `png`, `webp`, `jpeg` (webp/jpeg più leggeri e veloci, senza trasparenza per jpeg) | `png` |
| `-r, --resampling` | `near`, `bilinear`, `cubic`, `cubicspline`, `lanczos`, `average` | `near` |
| `--resume` | Riprende una generazione interrotta (solo tiles mancanti) | off |
| `--target-crs` | Codice CRS atteso per verifica (es. `EPSG:32633`) | `None` (rileva automaticamente) |

---

## 🗺️ Visualizzazione delle Mappe

`gdal2tiles` genera automaticamente file HTML pronti all'uso all'interno della cartella di output delle tiles:
- `leaflet.html` (consigliato: open-source, nessun account o chiave richiesta)
- `googlemaps.html`
- `openlayers.html`

### 1. Avviare il Server Locale
Per testare e visualizzare le tiles nel browser, usa il server locale `route.py`:

```bash
# Specifica la cartella contenente le tiles appena generate:
python route.py -d output_tiles/nome_ortofoto_1522 --port 8080
```

Apri il browser su `http://localhost:8080`.

### 2. Configurazione Google Maps (Opzionale)
Se desideri utilizzare `googlemaps.html`:

1. Apri il file `googlemaps.html` generato nella cartella di output.
2. Inserisci la tua Google Maps JavaScript API Key:
   ```html
   <script src="https://maps.googleapis.com/maps/api/js?key=YOUR_API_KEY_HERE"></script>
   ```
3. Se necessario, verifica che la funzione `getTileUrl` utilizzi la coordinata Y corretta:
   ```javascript
   if (mapBounds.intersects(tileBounds)) {
       return zoom + "/" + tile.x + "/" + y + ".png";
   } else {
       return "https://gdal.org/resources/gdal2tiles/none.png";
   }
   ```

---

## 📁 Struttura dell'Output

```text
output_tiles/
└── <nome_file>_<zoom_levels>/
    ├── googlemaps.html
    ├── leaflet.html
    ├── openlayers.html
    ├── tilemapresource.xml
    └── [zoom_level]/
        └── [x]/
            └── [y].png
```

---

## 🔍 Script Ausiliari

- **`analyze_geotiff.py`**: Analizza rapidamente un file GeoTIFF stampando CRS, bounding box, risoluzione pixel e bande:
  ```bash
  python analyze_geotiff.py percorso/ortofoto.tif
  ```
- **`test.py`**: Script di test per verificare rapidamente l'ambiente di esecuzione e la pipeline su zoom ridotti.

---

## 📄 Licenza

Distribuito sotto licenza MIT. Consulta il file `LICENSE` per ulteriori informazioni.
