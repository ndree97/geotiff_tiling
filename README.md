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
> Su **Windows**, il modo più semplice per disporre di GDAL e dei suoi strumenti CLI è utilizzare **Conda / Mamba**:
> ```bash
> conda install -c conda-forge gdal rasterio flask
> ```
> In alternativa è possibile utilizzare l'installer [OSGeo4W](https://trac.osgeo.org/osgeo4w/).

---

## 🚀 Utilizzo

### 1. Modalità File Singolo (GeoTIFF)
Usa questa modalità se disponi già di un raster GeoTIFF georeferenziato:

```bash
python main.py -i /percorso/ortofoto.tif -z 15-22 -p 4
```

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
| `-p, --processes` | Numero di processi paralleli | `5` |
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
