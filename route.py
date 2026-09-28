import os
import sys
import argparse
from flask import Flask, send_from_directory, abort

app = Flask(__name__)

# Default directory to serve
base_folder = os.path.abspath(os.path.dirname(__file__))

@app.route('/')
def index():
    """Serve the primary map viewer if available, prioritizing leaflet or googlemaps."""
    for default_file in ['leaflet.html', 'googlemaps.html', 'openlayers.html']:
        target_path = os.path.join(base_folder, default_file)
        if os.path.isfile(target_path):
            return send_from_directory(base_folder, default_file)
    
    # If no standard map file is in root, list files or inform user
    return f"""
    <h2>Map Tiles Preview Server</h2>
    <p>Serving directory: <code>{base_folder}</code></p>
    <p>Open one of the viewer files if generated:</p>
    <ul>
        <li><a href="/leaflet.html">Leaflet Viewer</a></li>
        <li><a href="/googlemaps.html">Google Maps Viewer</a></li>
        <li><a href="/openlayers.html">OpenLayers Viewer</a></li>
    </ul>
    """

@app.route('/<path:filename>')
def serve_files(filename):
    """Dynamically serve tile images and associated map assets."""
    target_path = os.path.join(base_folder, filename)
    if os.path.exists(target_path):
        return send_from_directory(base_folder, filename)
    abort(404)

def parse_args():
    parser = argparse.ArgumentParser(description='Local HTTP server to preview generated map tiles.')
    parser.add_argument('-d', '--dir', type=str, default=base_folder,
                        help=f'Directory containing generated tiles (default: {base_folder})')
    parser.add_argument('-p', '--port', type=int, default=8080,
                        help='Port to listen on (default: 8080)')
    parser.add_argument('--host', type=str, default='127.0.0.1',
                        help='Host address (default: 127.0.0.1)')
    return parser.parse_args()

if __name__ == '__main__':
    args = parse_args()
    base_folder = os.path.abspath(args.dir)
    print(f"[*] Serving tiles directory: {base_folder}")
    print(f"[*] Preview available at: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)
