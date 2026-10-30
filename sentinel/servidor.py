"""Servidor HTTP local do dashboard (stdlib, sem dependências extras).

Rotas:
  GET /            -> dashboard/index.html
  GET /api/state   -> snapshot JSON completo do estado (inclui previews b64)
  GET /asset/<n>   -> arquivos estáticos do dashboard
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import estado

_SERVIDOR = None
_LOCK_SERV = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silencia console
        pass

    def _responde(self, code, corpo, content_type="application/json; charset=utf-8"):
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)
        except BrokenPipeError:
            pass

    def do_GET(self):
        rota = self.path.split("?", 1)[0]
        if rota == "/api/state":
            try:
                corpo = json.dumps(estado.snapshot(), ensure_ascii=False)
            except Exception as exc:
                corpo = json.dumps({"erro": str(exc)})
            self._responde(200, corpo.encode("utf-8"))
            return
        if rota in ("/", "/index.html"):
            self._arquivo(os.path.join(estado.DASH_DIR, "index.html"),
                          "text/html; charset=utf-8")
            return
        if rota.startswith("/asset/"):
            nome = os.path.basename(rota[len("/asset/"):])
            caminho = os.path.join(estado.DASH_DIR, "assets", nome)
            ext = os.path.splitext(nome)[1].lower()
            tipos = {".js": "text/javascript; charset=utf-8",
                     ".css": "text/css; charset=utf-8",
                     ".svg": "image/svg+xml", ".png": "image/png"}
            self._arquivo(caminho, tipos.get(ext, "application/octet-stream"))
            return
        self._responde(404, b'{"erro":"rota nao encontrada"}')

    def do_POST(self):
        rota = self.path.split("?", 1)[0]
        try:
            tamanho = int(self.headers.get("Content-Length", 0) or 0)
            corpo = self.rfile.read(tamanho) if tamanho > 0 else b"{}"
            dados = json.loads(corpo.decode("utf-8") or "{}")
        except Exception:
            self._responde(400, b'{"erro":"corpo invalido"}')
            return

        if rota == "/api/sinais/apagar":
            try:
                if dados.get("todos"):
                    n = estado.apagar_sinais(todos=True)
                else:
                    n = estado.apagar_sinais(ids=dados.get("ids") or [])
                resp = json.dumps({"ok": True, "removidos": int(n)})
                self._responde(200, resp.encode("utf-8"))
            except Exception as exc:
                self._responde(500, json.dumps({"erro": str(exc)}).encode())
            return

        if rota == "/api/ia/testar":
            try:
                import juiz
                resultado = juiz.testar_chave(dados.get("chave"))
                self._responde(200,
                               json.dumps(resultado, ensure_ascii=False).encode())
            except Exception as exc:
                self._responde(500, json.dumps({"erro": str(exc)}).encode())
            return

        self._responde(404, b'{"erro":"rota nao encontrada"}')

    def _arquivo(self, caminho, content_type):
        try:
            with open(caminho, "rb") as f:
                corpo = f.read()
            self._responde(200, corpo, content_type)
        except FileNotFoundError:
            self._responde(404, b'{"erro":"arquivo nao encontrado"}',
                           "application/json; charset=utf-8")


def iniciar(porta):
    """Sobe o servidor em thread daemon. Retorna a porta usada (pode mudar se ocupada)."""
    global _SERVIDOR
    with _LOCK_SERV:
        if _SERVIDOR is not None:
            return estado.STATE["porta_dashboard"]
        portas = [porta] + list(range(porta + 1, porta + 10))
        ultimo_erro = None
        for p in portas:
            try:
                _SERVIDOR = ThreadingHTTPServer(("127.0.0.1", p), _Handler)
                estado.STATE["porta_dashboard"] = p
                threading.Thread(target=_SERVIDOR.serve_forever,
                                 kwargs={"poll_interval": 0.4},
                                 daemon=True, name="dash-http").start()
                return p
            except OSError as exc:
                ultimo_erro = exc
        estado.STATE["erro"] = f"dashboard não subiu: {ultimo_erro}"
        return None
