import rasterio
import sys
import os

def analyze_geotiff(filepath):
    """
    Analizza un file GeoTIFF e stampa le sue informazioni principali.
    """
    if not os.path.exists(filepath):
        print(f"Errore: Il file '{filepath}' non è stato trovato.")
        return

    print(f"--- Analisi del file: {os.path.basename(filepath)} ---")
    
    try:
        with rasterio.open(filepath) as src:
            # Informazioni di Georeferenziazione
            print("\n[ Informazioni di Georeferenziazione ]")
            if src.crs:
                print(f"  - Sistema di Coordinate (CRS): {src.crs.to_string()}")
                if src.crs.is_geographic:
                    print("  - Tipo: Geografico (latitudine/longitudine)")
                elif src.crs.is_projected:
                    print("  - Tipo: Proiettato (misure metriche)")
                else:
                    print("  - Tipo: Sconosciuto")
            else:
                print("  - CRS non presente. Il file non è georeferenziato.")

            # Trasformazione e Bounding Box
            if src.transform:
                print(f"  - Trasformazione Affine: I parametri che legano le coordinate pixel al CRS.")
                # Stampa i limiti (bounding box) nel sistema di coordinate del file
                bounds = src.bounds
                print(f"  - Bounding Box (limiti nel CRS):")
                print(f"    - Sinistra (X min): {bounds.left:.6f}")
                print(f"    - Sotto (Y min):   {bounds.bottom:.6f}")
                print(f"    - Destra (X max):  {bounds.right:.6f}")
                print(f"    - Sopra (Y max):   {bounds.top:.6f}")
            else:
                print("  - Informazioni di trasformazione non disponibili.")

            # Informazioni Generali del Raster
            print("\n[ Informazioni Generali del Raster ]")
            print(f"  - Dimensioni: {src.width} pixel (larghezza) x {src.height} pixel (altezza)")
            print(f"  - Numero di Bande: {src.count}")
            
            # Informazioni sulle Bande
            print(f"  - Tipi di dati per banda: {[str(dtype) for dtype in src.dtypes]}")
            
            # Risoluzione
            print(f"  - Risoluzione (dimensione pixel): {src.res[0]:.6f} (larghezza) x {src.res[1]:.6f} (altezza) unità del CRS")

    except rasterio.errors.RasterioIOError as e:
        print(f"\n--- ERRORE ---")
        print(f"Impossibile leggere il file '{filepath}'. Potrebbe non essere un formato raster valido.")
        print(f"Dettaglio errore: {e}")
    except Exception as e:
        print(f"\n--- Si è verificato un errore imprevisto: {e} ---")

def main():
    """Funzione principale per eseguire lo script da riga di comando."""
    if len(sys.argv) != 2:
        print("Uso: python analyze_geotiff.py <percorso_del_file.tif>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    analyze_geotiff(filepath)

if __name__ == "__main__":
    main()
