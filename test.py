import os
import time
import sys
import subprocess
import threading

# --- CONFIGURAZIONE DI TEST ---
# Percorso del file GeoTIFF di input per il test rapido
input_orthophoto = "sample_orthophoto.tif"

# Cartella di destinazione dove verranno salvate le tiles
output_folder = "output_tiles"

# Livelli di zoom da generare (per i test è consigliato un range contenuto come "15-18")
zoom_levels = "15-18"

# Numero di processi da usare in parallelo
num_processes = 2
# -----------------------------

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
        bufsize=1 # Assicura che l'output venga processato riga per riga
    )

    # Funzione per leggere e stampare l'output da uno stream (stdout o stderr)
    def print_stream(pipe):
        # Leggiamo carattere per carattere per catturare gli aggiornamenti sulla stessa riga (usando \r)
        for char in iter(lambda: pipe.read(1), ''):
            sys.stdout.write(char)
            sys.stdout.flush()
        pipe.close() 

    # Crea e avvia thread separati per stdout e stderr per evitare blocchi
    stdout_thread = threading.Thread(target=print_stream, args=(process.stdout,))
    stderr_thread = threading.Thread(target=print_stream, args=(process.stderr,))
    
    stdout_thread.start()
    stderr_thread.start()

    # Attendi la terminazione del processo
    return_code = process.wait()
    
    # Attendi la terminazione dei thread di lettura
    stdout_thread.join()
    stderr_thread.join()

    return return_code

def create_map_tiles_workflow(input_file, output_dir, zoom, processes):
    """
    Workflow completo e monitorabile per la creazione di map tiles.
    """
    if not os.path.exists(input_file):
        print(f"Errore: Il file di input '{input_file}' non è stato trovato.")
        return

    temp_vrt_file = os.path.splitext(input_file)[0] + "_8bit.vrt"

    print("--- PASSAGGIO 1: Conversione del file in formato 8-bit (virtuale) ---")
    translate_command = [
        'gdal_translate', '-ot', 'Byte', '-scale', '-of', 'VRT',
        input_file, temp_vrt_file
    ]
    print(f"Esecuzione del comando: {' '.join(translate_command)}\n")
    
    # Esegui la conversione e controlla l'esito
    if run_command_with_progress(translate_command) != 0:
        print("\n--- ERRORE durante la conversione a 8-bit ---")
        # La funzione run_command_with_progress ha già stampato l'errore specifico
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

    # Esegui il tiling e controlla l'esito
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

# Esecuzione della funzione
if __name__ == "__main__":
    if not os.path.exists(input_orthophoto):
        # Crea un file fittizio per testare lo script se non esiste
        print(f"Attenzione: File di esempio '{input_orthophoto}' non trovato. Lo creo per un test.")
        try:
            from osgeo import gdal
            gdal.UseExceptions() # Abilita le eccezioni per GDAL, rimuovendo il FutureWarning
            driver = gdal.GetDriverByName('GTiff')
            ds = driver.Create(input_orthophoto, 1000, 1000, 1, gdal.GDT_UInt16)
            ds = None
        except ImportError:
            print("Libreria GDAL per Python non trovata. Impossibile creare file di test.")
            
    if os.path.exists(input_orthophoto):
        # --- COSTRUZIONE DEL PERCORSO DI OUTPUT DINAMICO ---
        # Estrai il nome del file di input senza estensione
        input_basename = os.path.splitext(os.path.basename(input_orthophoto))[0]
        
        # Formatta il range di zoom per il nome della cartella (es. "15-20" -> "1520")
        zoom_folder_name = zoom_levels.replace('-', '')
        
        # Combina le parti per creare il nome della sottocartella di output
        dynamic_output_folder = os.path.join(output_folder, f"{input_basename}_{zoom_folder_name}")
        
        print(f"Le tiles verranno salvate in: {dynamic_output_folder}")
        create_map_tiles_workflow(input_orthophoto, dynamic_output_folder, zoom_levels, num_processes)