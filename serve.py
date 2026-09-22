import http.server
import os

os.chdir(os.path.join(os.path.dirname(__file__), 'electron', 'renderer'))

handler = http.server.SimpleHTTPRequestHandler
handler.extensions_map.update({'.js': 'application/javascript', '.css': 'text/css'})

with http.server.HTTPServer(('localhost', 3101), handler) as httpd:
    print(f"Serving on http://localhost:3101")
    httpd.serve_forever()
