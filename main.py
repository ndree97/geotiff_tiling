import os
import time
import sys
import subprocess
import threading
import argparse
from osgeo import gdal, osr

# --- CONFIGURAZIONE ---
# Cartella di destinazione dove verranno salvate le tiles
output_folder = "output_tiles"

# Livelli di zoom da generare
zoom_levels = "15-25"

# Numero di processi da usare in parallelo
num_processes = 5

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


def create_map_tiles_workflow(input_file, output_dir, zoom, processes):
    """
    Workflow completo e monitorabile per la creazione di map tiles.
    """
    if not os.path.exists(input_file):
        print(f"Errore: Il file di input '{input_file}' non è stato trovato.")
        return

    temp_vrt_file = os.path.splitext(input_file)[0] + "_8bit.vrt"

    print("\n--- PASSAGGIO 1: Conversione del file in formato 8-bit (virtuale) ---")
    translate_command = [
        'gdal_translate', '-ot', 'Byte', '-scale', '-of', 'VRT',
        input_file, temp_vrt_file
    ]

    print(f"Esecuzione del comando: {' '.join(translate_command)}\n")

    if run_command_with_progress(translate_command) != 0:
        print("\n--- ERRORE durante la conversione a 8-bit ---")
        return

    print(f"\nFile virtuale '{temp_vrt_file}' creato con successo.")

    print("\n--- PASSAGGIO 2: Generazione delle tiles dal file 8-bit ---")
    start_time = time.time()

    tiles_command = [
        'gdal2tiles',
        '--profile', 'mercator',
        '--resampling', 'near',
        '--zoom', zoom,
        '--processes', str(processes),
        '--xyz',
        temp_vrt_file,
        output_dir
    ]

    print(f"Esecuzione del comando: {' '.join(tiles_command)}\n")

    if run_command_with_progress(tiles_command) == 0:
        end_time = time.time()
        elapsed_time = end_time - start_time
        print("\n--- Generazione completata! ---")
        print(f"Tiles salvate in: {output_dir}")
        print(f"Tempo totale impiegato per il tiling: {elapsed_time:.2f} secondi")
    else:
        print("\n--- ERRORE durante la generazione delle tiles ---")

    # Pulizia finale
    if os.path.exists(temp_vrt_file):
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

    # Costruzione del percorso di output dinamico
    input_basename = os.path.splitext(os.path.basename(input_file))[0]
    zoom_folder_name = args.zoom.replace('-', '')
    dynamic_output_folder = os.path.join(args.output, f"{input_basename}_{zoom_folder_name}")

    print(f"\nLe tiles verranno salvate in: {dynamic_output_folder}")

    # Esegui il workflow di creazione delle tiles
    create_map_tiles_workflow(input_file, dynamic_output_folder, args.zoom, args.processes)


if __name__ == "__main__":
    gdal.UseExceptions()  # Abilita le eccezioni per GDAL
    main()