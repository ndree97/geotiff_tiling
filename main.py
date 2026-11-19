import os
import time
import sys
import subprocess
import threading
import argparse
from osgeo import gdal, osr
import georef

# --- CONFIGURAZIONE ---
# Cartella di destinazione dove verranno salvate le tiles
output_folder = "output_tiles"

# Livelli di zoom da generare
zoom_levels = "15-25"

# Numero di processi da usare in parallelo
num_processes = os.cpu_count() or 4

# --------------------

def run_command_with_progress(command):
    """
    Esegue un comando in un sottoprocesso e stampa il suo output (stdout e stderr)
    in tempo reale, riga per riga.
    Questo permette di monitorare l'avanzamento di processi lunghi come gdal2tiles.
    """
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        bufsize=1
    )

    def print_stream(pipe):
        for char in iter(lambda: pipe.read(1), ''):
            sys.stdout.write(char)
            sys.stdout.flush()
        pipe.close()

    stdout_thread = threading.Thread(target=print_stream, args=(process.stdout,))
    stderr_thread = threading.Thread(target=print_stream, args=(process.stderr,))

    stdout_thread.start()
    stderr_thread.start()

    return_code = process.wait()

    stdout_thread.join()
    stderr_thread.join()

    return return_code


def validate_directory_files(directory):
    """
    Valida che la directory contenga almeno un file per ciascuna estensione: .tif, .tfw, .prj
    Restituisce un dizionario con i percorsi dei file trovati o None se la validazione fallisce.
    """
    required_extensions = ['.tif', '.tfw', '.prj']
    found_files = {ext: None for ext in required_extensions}

    if not os.path.isdir(directory):
        print(f"Errore: '{directory}' non è una directory valida.")
        return None

    # Scansiona i file nella directory
    for filename in os.listdir(directory):
        file_path = os.path.join(directory, filename)
        if os.path.isfile(file_path):
            _, ext = os.path.splitext(filename)
            ext_lower = ext.lower()
            if ext_lower in found_files and found_files[ext_lower] is None:
                found_files[ext_lower] = file_path

    # Verifica che tutti i file richiesti siano presenti
    missing = [ext for ext, path in found_files.items() if path is None]
    if missing:
        print(f"Errore: La directory '{directory}' non contiene tutti i file richiesti.")
        print(f"File mancanti: {', '.join(missing)}")
        return None

    return found_files


def get_crs_from_prj(prj_file):
    """
    Legge il file .prj e restituisce il CRS in formato EPSG (se disponibile) e l'oggetto SpatialReference.
    """
    try:
        with open(prj_file, 'r') as f:
            prj_text = f.read()

        srs = osr.SpatialReference()
        srs.ImportFromWkt(prj_text)

        # Prova a ottenere il codice EPSG
        epsg_code = None
        if srs.AutoIdentifyEPSG() == 0:
            authority = srs.GetAuthorityName(None)
            code = srs.GetAuthorityCode(None)
            if authority == 'EPSG' and code:
                epsg_code = f"EPSG:{code}"

        return epsg_code, srs, prj_text
    except Exception as e:
        print(f"Errore durante la lettura del file .prj: {e}")
        return None, None, None


def create_georeferenced_tif(directory, output_path, target_epsg=None):
    """
    Crea un GeoTIFF georeferenziato combinando i file .tif, .tfw e .prj presenti nella directory.
    Questo workflow replica il processo tipico di QGIS: carica i 3 file e li salva come un unico raster GeoTIFF.
    """
    print("\n--- CREAZIONE GEOTIFF GEOREFERENZIATO ---")

    # Valida la presenza dei file richiesti
    files = validate_directory_files(directory)
    if files is None:
        return None

    tif_file = files['.tif']
    tfw_file = files['.tfw']
    prj_file = files['.prj']

    print(f"File TIF trovato: {tif_file}")
    print(f"File TFW trovato: {tfw_file}")
    print(f"File PRJ trovato: {prj_file}")

    # Leggi il CRS dal file .prj
    epsg_code, srs, prj_text = get_crs_from_prj(prj_file)

    if srs is None:
        print("Errore: Impossibile leggere il sistema di riferimento dal file .prj")
        return None

    print(f"\nCRS rilevato: {epsg_code if epsg_code else 'Custom/Unknown'}")
    print(f"Descrizione CRS: {srs.ExportToPrettyWkt()[:200]}...")

    # Se specificato, confronta con il CRS atteso
    if target_epsg:
        if epsg_code and epsg_code.upper() == target_epsg.upper():
            print(f"✓ Il CRS corrisponde al target atteso ({target_epsg})")
        else:
            print(f"⚠ Attenzione: Il CRS rilevato ({epsg_code if epsg_code else 'sconosciuto'}) differisce dal target {target_epsg}")

    # Usa GDAL per creare il GeoTIFF georeferenziato
    print(f"\nCreazione del GeoTIFF georeferenziato: {output_path}")

    try:
        # Apri il file TIF sorgente
        src_ds = gdal.Open(tif_file, gdal.GA_ReadOnly)
        if src_ds is None:
            print(f"Errore: Impossibile aprire il file {tif_file}")
            return None

        # Leggi i parametri del worldfile (.tfw)
        with open(tfw_file, 'r') as f:
            tfw_params = [float(line.strip()) for line in f.readlines()]

        if len(tfw_params) != 6:
            print(f"Errore: Il file worldfile {tfw_file} non contiene 6 parametri validi")
            return None

        # I parametri del worldfile sono:
        # Line 1: pixel size in the x-direction (A)
        # Line 2: rotation about y-axis (D)
        # Line 3: rotation about x-axis (B)
        # Line 4: pixel size in the y-direction (E, negative)
        # Line 5: x-coordinate of the center of the upper left pixel (C)
        # Line 6: y-coordinate of the center of the upper left pixel (F)

        pixel_width = tfw_params[0]
        rot_y = tfw_params[1]
        rot_x = tfw_params[2]
        pixel_height = tfw_params[3]
        upper_left_x = tfw_params[4]
        upper_left_y = tfw_params[5]

        # Calcola la geotransform per GDAL
        # GeoTransform = [upper_left_x, pixel_width, rot_x, upper_left_y, rot_y, pixel_height]
        geotransform = [upper_left_x, pixel_width, rot_x, upper_left_y, rot_y, pixel_height]

        # Crea il driver per GeoTIFF
        driver = gdal.GetDriverByName('GTiff')

        # Crea il dataset di output
        dst_ds = driver.CreateCopy(output_path, src_ds, strict=0, 
                                    options=['COMPRESS=LZW', 'TILED=YES'])

        if dst_ds is None:
            print(f"Errore: Impossibile creare il file di output {output_path}")
            src_ds = None
            return None

        # Imposta la geotransform
        dst_ds.SetGeoTransform(geotransform)

        # Imposta la proiezione
        dst_ds.SetProjection(prj_text)

        # Chiudi i dataset
        src_ds = None
        dst_ds = None

        print(f"✓ GeoTIFF georeferenziato creato con successo: {output_path}")
        return output_path

    except Exception as e:
        print(f"Errore durante la creazione del GeoTIFF: {e}")
        return None


def create_map_tiles_workflow(input_file, output_dir, zoom, processes,
                              scheme="xyz", tile_format="png", resampling="near", resume=False):
    """
    Workflow completo e monitorabile per la creazione di map tiles.
    scheme: 'xyz' (Google Maps, OpenStreetMap, Leaflet, MapLibre) o 'tms' (Y invertita).
    """
    if not os.path.exists(input_file):
        print(f"Errore: Il file di input '{input_file}' non è stato trovato.")
        return

    # Il VRT a 8-bit serve solo se la sorgente non è già Byte: -scale su un Byte altera i colori.
    src = gdal.Open(input_file)
    if src.GetGeoTransform() == (0.0, 1.0, 0.0, 0.0, 0.0, 1.0) and not src.GetGCPCount():
        print("Errore: il raster non è georeferenziato (nessun geotransform/GCP e nessun .tfw accanto).")
        print("Georeferenzialo (es. QGIS), usa -d con una cartella .tif + .tfw + .prj, oppure --place LAT LON per un posizionamento approssimato.")
        return
    is_byte = src.GetRasterBand(1).DataType == gdal.GDT_Byte
    palette = src.GetRasterBand(1).GetColorTable() is not None  # gdal2tiles vuole RGB/RGBA
    src = None

    temp_vrt_file = None
    tiling_input = input_file
    if palette or not is_byte:
        temp_vrt_file = os.path.splitext(input_file)[0] + "_8bit.vrt"
        print("\n--- PASSAGGIO 1: Conversione del file in formato 8-bit (virtuale) ---")
        translate_command = [
            'gdal_translate', '-of', 'VRT',
            *(['-expand', 'rgba'] if palette else ['-ot', 'Byte', '-scale']),
            input_file, temp_vrt_file
        ]
        print(f"Esecuzione del comando: {' '.join(translate_command)}\n")
        if run_command_with_progress(translate_command) != 0:
            print("\n--- ERRORE durante la conversione a 8-bit ---")
            return
        tiling_input = temp_vrt_file

    print("\n--- Generazione delle tiles ---")
    start_time = time.time()

    tiles_command = [
        'gdal2tiles',
        '--profile', 'mercator',
        '--resampling', resampling,
        '--zoom', zoom,
        '--processes', str(processes),
        '--tiledriver', tile_format.upper(),
        '--exclude',  # salta le tiles completamente trasparenti: meno I/O e meno file
    ]
    if scheme == "xyz":
        tiles_command.append('--xyz')
    if resume:
        tiles_command.append('--resume')
    tiles_command += [tiling_input, output_dir]

    print(f"Esecuzione del comando: {' '.join(tiles_command)}\n")

    if run_command_with_progress(tiles_command) == 0:
        elapsed_time = time.time() - start_time
        print("\n--- Generazione completata! ---")
        print(f"Tiles salvate in: {output_dir}")
        print(f"Tempo totale impiegato per il tiling: {elapsed_time:.2f} secondi")
    else:
        print("\n--- ERRORE durante la generazione delle tiles ---")

    # Pulizia finale
    if temp_vrt_file and os.path.exists(temp_vrt_file):
        os.remove(temp_vrt_file)
        print(f"\nFile temporaneo '{temp_vrt_file}' rimosso.")


def main():
    parser = argparse.ArgumentParser(
        description='Genera map tiles da un GeoTIFF o da esportazioni grezze (.tif + .tfw + .prj)'
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-i', '--input', type=str,
                      help='Percorso del file GeoTIFF di input (.tif)')
    group.add_argument('-d', '--directory', type=str,
                      help='Percorso della cartella contenente i file grezzi (.tif, .tfw, .prj)')

    parser.add_argument('-o', '--output', type=str, default=output_folder,
                       help=f'Cartella di output per le tiles (default: {output_folder})')
    parser.add_argument('-z', '--zoom', type=str, default=zoom_levels,
                       help=f'Livelli di zoom (default: {zoom_levels})')
    parser.add_argument('-p', '--processes', type=int, default=num_processes,
                       help=f'Numero di processi paralleli (default: {num_processes})')
    parser.add_argument('--scheme', choices=['xyz', 'tms'], default='xyz',
                       help='Numerazione tiles: xyz = Google Maps / OpenStreetMap / Leaflet / MapLibre (default), '
                            'tms = Y invertita (OpenLayers TMS, Cesium, GeoServer)')
    parser.add_argument('-f', '--format', choices=['png', 'webp', 'jpeg'], default='png',
                       help='Formato delle tiles (default: png; webp/jpeg sono molto più leggere e veloci)')
    parser.add_argument('-r', '--resampling', default='near',
                       choices=['near', 'bilinear', 'cubic', 'cubicspline', 'lanczos', 'average'],
                       help='Algoritmo di resampling (default: near, il più veloce)')
    parser.add_argument('--resume', action='store_true',
                       help='Riprende una generazione interrotta creando solo le tiles mancanti')
    parser.add_argument('--place', type=float, nargs=2, metavar=('LAT', 'LON'),
                       help='Posiziona in modo APPROSSIMATO un raster non georeferenziato (es. disegno scansionato): '
                            'il centro (o --anchor-px) cade su LAT LON. Solo con -i')
    parser.add_argument('--scale', type=float, default=100,
                       help='Con --place: denominatore della scala del disegno (default: 100 = 1:100)')
    parser.add_argument('--rotation', type=float, default=0,
                       help='Con --place: gradi in senso orario di cui la parte alta del disegno è ruotato rispetto al nord')
    parser.add_argument('--dpi', type=float, default=None,
                       help='Con --place: DPI della scansione (default: letti dal TIFF)')
    parser.add_argument('--crop', type=int, nargs=4, metavar=('X', 'Y', 'W', 'H'),
                       help='Con --place: usa solo questo ritaglio (pixel dell immagine intera)')
    parser.add_argument('--anchor-px', type=float, nargs=2, metavar=('X', 'Y'),
                       help='Con --place: pixel (immagine intera) che cade su LAT LON (default: centro del ritaglio)')
    parser.add_argument('--target-crs', type=str, default=None,
                       help='Verifica corrispondenza CRS atteso (es. EPSG:32633 o EPSG:3857, opzionale)')

    args = parser.parse_args()

    # Determina il file di input
    if args.directory:
        # Modalità directory: crea GeoTIFF dai file grezzi
        directory_name = os.path.basename(os.path.normpath(args.directory))
        output_tif_name = f"{directory_name}_tile.tif"
        output_tif_path = os.path.join(args.directory, output_tif_name)

        # Crea il GeoTIFF georeferenziato
        input_file = create_georeferenced_tif(args.directory, output_tif_path, target_epsg=args.target_crs)

        if input_file is None:
            print("\nErrore: Impossibile creare il GeoTIFF georeferenziato.")
            sys.exit(1)
    else:
        # Modalità file singolo
        input_file = args.input
        if not os.path.exists(input_file):
            print(f"Errore: Il file '{input_file}' non esiste.")
            sys.exit(1)
        if args.place:
            try:
                input_file = georef.place_raster(input_file, *args.place, scale=args.scale, rotation=args.rotation,
                                                 dpi=args.dpi, crop=args.crop, anchor_px=args.anchor_px)
            except ValueError as e:
                print(f"Errore: {e}")
                sys.exit(1)
            print("\nATTENZIONE: posizionamento APPROSSIMATO (senza punti di controllo): verifica sempre la posizione sulla mappa.")

    # Costruzione del percorso di output dinamico
    input_basename = os.path.splitext(os.path.basename(input_file))[0]
    zoom_folder_name = args.zoom.replace('-', '')
    dynamic_output_folder = os.path.join(args.output, f"{input_basename}_{zoom_folder_name}")

    print(f"\nLe tiles verranno salvate in: {dynamic_output_folder}")

    # Esegui il workflow di creazione delle tiles
    create_map_tiles_workflow(input_file, dynamic_output_folder, args.zoom, args.processes,
                              args.scheme, args.format, args.resampling, args.resume)


if __name__ == "__main__":
    gdal.UseExceptions()  # Abilita le eccezioni per GDAL
    main()