# Path: TenderRadar/serve_http_redirect.py
"""
Tiny HTTP→HTTPS redirect server on port 80.
Run alongside serve_https.py so http://radar.AJGroup.com.local
redirects to https://radar.AJGroup.com.local automatically.

Usage: python serve_http_redirect.py
"""
import http.server
import socketserver

class RedirectHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(301)
        self.send_header('Location', f'https://radar.AJGroup.com.local{self.path}')
        self.end_headers()

    def do_POST(self):
        self.send_response(308)  # Permanent redirect preserving method
        self.send_header('Location', f'https://radar.AJGroup.com.local{self.path}')
        self.end_headers()

    def log_message(self, fmt, *args):
        print(f'[HTTP→HTTPS] {self.address_string()} {fmt % args}')

if __name__ == '__main__':
    PORT = 80
    print(f'HTTP→HTTPS redirect server on port {PORT}')
    with socketserver.TCPServer(('0.0.0.0', PORT), RedirectHandler) as srv:
        srv.serve_forever()
