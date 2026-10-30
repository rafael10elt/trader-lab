"""LUMI SENTINEL VISION — vigia a tela, detecta o fechamento de cada vela
e dispara alerta visual + sonoro (GUI local e dashboard no navegador).

Execução:  python sentinel.py      (ou duplo clique no atalho .bat)
"""
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import mss  # noqa: F401  (garante que o backend de captura exista cedo)
from PIL import Image, ImageTk

import captura
import estado
import servidor
from estado import CONFIG, STATE, lock
from setup_checker import (CHECKER, dentro_da_janela, noticias_bloqueando,
                           proximo_fechamento, segundos_para_fechamento)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ----------------------------------------------------------------- áudio
def _tocar_beep(direcao):
    """Beep nativo do Windows. BUY sobe, SELL desce (sequência de frequências)."""
    try:
        import winsound
    except ImportError:
        return
    frequencias = [660, 880, 1180] if direcao == "BUY" else [1180, 880, 660]
    for f in frequencias:
        try:
            winsound.Beep(f, 180)
        except Exception:
            break


class Alarme:
    """Alarme sonoro de 30s em thread própria (padrão do sistema modelo)."""

    def __init__(self):
        self._parar = threading.Event()
        self._thread = None

    def disparar(self, direcao, duracao=30.0):
        self.parar()
        self._parar = threading.Event()
        alvo = time.time() + duracao

        def loop():
            while not self._parar.is_set() and time.time() < alvo:
                _tocar_beep(direcao)
                self._parar.wait(0.6)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def parar(self):
        self._parar.set()


ALARME = Alarme()


# ------------------------------------------------------- threads de trabalho
def loop_captura():
    """Classifica a célula de status ~3x/s e mantém previews vivos."""
    ultimo_preview = 0.0
    ultimo_status = 0.0
    while True:
        try:
            roi_status = STATE["roi_status"]
            roi_chart = STATE["roi_chart"]
            if roi_status:
                img = captura.capturar(roi_status)
                estado.set_estado(captura.classificar_estado(img))
                agora = time.time()
                if agora - ultimo_preview >= 0.5:
                    ultimo_preview = agora
                    import base64
                    jpg = captura.preview_jpeg(img, max_largura=420,
                                               qualidade=60, escala_min=320)
                    with lock():
                        STATE["preview_status_b64"] = base64.b64encode(jpg).decode()
                        STATE["preview_ts"] = agora
                        STATE["online"] = True
            if roi_chart and time.time() - ultimo_status >= 1.0:
                ultimo_status = time.time()
                import base64
                try:
                    imgc = captura.capturar(roi_chart)
                    jpg = captura.preview_jpeg(imgc, max_largura=960, qualidade=42)
                    with lock():
                        STATE["preview_chart_b64"] = base64.b64encode(jpg).decode()
                    ativo = captura.detectar_ativo_tradingview()
                    if ativo:
                        STATE["ativo"] = ativo
                except Exception as exc:
                    STATE["erro"] = f"captura do gráfico: {exc}"
            if not roi_status:
                with lock():
                    STATE["online"] = False
        except Exception as exc:
            with lock():
                STATE["online"] = False
                STATE["erro"] = f"captura: {exc}"
        time.sleep(0.25)


def loop_velas():
    """Aguarda cada fechamento de vela e roda o verificador de setup."""
    while True:
        try:
            tf = int(STATE["timeframe_seg"])
            fechamento = proximo_fechamento(tf)
            espera = fechamento - time.time() + CONFIG["delay_apos_fechamento_seg"]
            if espera > 0:
                time.sleep(espera)
            if not STATE["rodando"]:
                continue
            if not STATE["roi_status"]:
                with lock():
                    STATE["ultima_checagem"] = "calibre a célula de status"
                continue

            with lock():
                STATE["ultima_vela_ts"] = fechamento
                STATE["proxima_vela_ts"] = proximo_fechamento(tf)

            sinal, motivo = CHECKER.avaliar_janela(
                fechamento, chart_roi=STATE["roi_chart"])
            with lock():
                STATE["ultima_checagem"] = motivo
            if sinal:
                ALARME.disparar(sinal["direction"])
        except Exception as exc:
            STATE["erro"] = f"loop de velas: {exc}"
            time.sleep(2)
        # dorme pouco para reagir a mudanças de timeframe/estado
        time.sleep(0.2)


def loop_chealth():
    """Heartbeat: sem preview recente, dashboard marca 'offline'."""
    ultimo_ts = 0.0
    while True:
        time.sleep(2)
        ts = STATE["preview_ts"]
        with lock():
            if STATE["roi_status"] and ts == ultimo_ts and ts:
                STATE["online"] = False
            ultimo_ts = ts


# ------------------------------------------------------------- calibrador
class CalibradorOverlay:
    """Overlay em tela cheia para arrastar sobre a área alvo (status ou gráfico)."""

    def __init__(self, app, monitor, destino):
        self.app = app
        self.monitor = monitor
        self.destino = destino  # 'status' | 'chart'
        self.top = tk.Toplevel()
        w, h = monitor["width"], monitor["height"]
        self.top.overrideredirect(True)
        self.top.geometry(f"{w}x{h}{monitor['left']:+d}{monitor['top']:+d}")
        self.top.attributes("-alpha", 0.28)
        self.top.attributes("-topmost", True)
        self.top.config(cursor="cross")
        self.top.title(f"Calibrar — Monitor {monitor['index']} (ESC cancela)")

        self.canvas = tk.Canvas(self.top, cursor="cross", bg="#0284c7")
        self.canvas.pack(fill="both", expand=True)
        rotulo = ("CÉLULA DE STATUS" if destino == "status"
                  else "ÁREA DO GRÁFICO (velas)")
        self.canvas.create_text(
            12, 12, anchor="nw", fill="white", font=("Segoe UI", 12, "bold"),
            text=f"MONITOR {monitor['index']} • Arraste sobre o {rotulo} • ESC cancela")

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.top.bind("<Escape>", lambda e: self.cancelar())
        self.start_x = self.start_y = self.rel_x = self.rel_y = None
        self.rect = None

    def on_press(self, e):
        self.start_x, self.start_y = e.x_root, e.y_root
        self.rel_x = e.x_root - self.monitor["left"]
        self.rel_y = e.y_root - self.monitor["top"]
        self.rect = self.canvas.create_rectangle(
            self.rel_x, self.rel_y, self.rel_x + 1, self.rel_y + 1,
            outline="#f43f5e", width=3)

    def on_drag(self, e):
        rx, ry = e.x_root - self.monitor["left"], e.y_root - self.monitor["top"]
        self.canvas.coords(self.rect, self.rel_x, self.rel_y, rx, ry)

    def on_release(self, e):
        x1, x2 = sorted((self.start_x, e.x_root))
        y1, y2 = sorted((self.start_y, e.y_root))
        coords = {"left": int(x1), "top": int(y1),
                  "width": int(x2 - x1), "height": int(y2 - y1)}
        self.top.destroy()
        if coords["width"] > 8 and coords["height"] > 8:
            if self.destino == "status":
                estado.salvar_roi_status(coords)
            else:
                estado.salvar_roi_chart(coords)
            self.app.on_calibrado(self.destino, coords)
        else:
            self.app.root.deiconify()

    def cancelar(self):
        self.top.destroy()
        self.app.root.deiconify()


# ------------------------------------------------------------------- GUI
class SentinelApp:
    def __init__(self, root):
        self.root = root
        root.title("Lumi Sentinel Vision • Fechamento de Vela")
        root.geometry("520x760")
        root.configure(bg="#070b14")
        root.attributes("-topmost", True)

        header = tk.Frame(root, bg="#0d1527", pady=12, padx=16)
        header.pack(fill="x")
        tk.Label(header, text="⚡ LUMI SENTINEL VISION",
                 font=("Segoe UI", 13, "bold"), fg="#38bdf8",
                 bg="#0d1527").pack(side="left")
        self.lbl_status = tk.Label(header, text="🔴 PARADO",
                                   font=("Segoe UI", 9, "bold"), fg="#f87171",
                                   bg="#0d1527")
        self.lbl_status.pack(side="right")

        main = tk.Frame(root, bg="#070b14", padx=16, pady=10)
        main.pack(fill="both", expand=True)

        # previews lado a lado: célula + gráfico
        previews = tk.Frame(main, bg="#070b14")
        previews.pack(fill="x")
        col_status = tk.Frame(previews, bg="#070b14")
        col_status.pack(side="left", fill="x", expand=True)
        tk.Label(col_status, text="CÉLULA DE STATUS",
                 font=("Segoe UI", 8, "bold"), fg="#94a3b8",
                 bg="#070b14").pack(anchor="w")
        box_status = tk.Frame(col_status, bg="#020617", bd=2, relief="groove")
        box_status.pack(fill="x", pady=(3, 0))
        self.lbl_img_status = tk.Label(box_status, bg="#020617",
                                       text="sem calibração", fg="#64748b",
                                       font=("Segoe UI", 8))
        self.lbl_img_status.pack(padx=4, pady=4)

        col_chart = tk.Frame(previews, bg="#070b14")
        col_chart.pack(side="left", fill="x", expand=True, padx=(8, 0))
        tk.Label(col_chart, text="GRÁFICO (AO VIVO)",
                 font=("Segoe UI", 8, "bold"), fg="#94a3b8",
                 bg="#070b14").pack(anchor="w")
        box_chart = tk.Frame(col_chart, bg="#020617", bd=2, relief="groove")
        box_chart.pack(fill="x", pady=(3, 0))
        self.lbl_img_chart = tk.Label(box_chart, bg="#020617",
                                      text="sem calibração", fg="#64748b",
                                      font=("Segoe UI", 8))
        self.lbl_img_chart.pack(padx=4, pady=4)

        # painel de estado
        info = tk.Frame(main, bg="#0d1527", padx=14, pady=10)
        info.pack(fill="x", pady=(12, 10))

        linha_tf = tk.Frame(info, bg="#0d1527")
        linha_tf.pack(fill="x")
        tk.Label(linha_tf, text="Timeframe:", font=("Segoe UI", 9, "bold"),
                 fg="#94a3b8", bg="#0d1527").pack(side="left")
        self.var_tf = tk.StringVar(value=self._tf_rotulo(int(STATE["timeframe_seg"])))
        self.combo_tf = ttk.Combobox(
            linha_tf, textvariable=self.var_tf, state="readonly", width=10,
            values=["1m", "3m", "5m", "15m", "30m", "1h", "4h"])
        self.combo_tf.pack(side="left", padx=8)
        self.combo_tf.bind("<<ComboboxSelected>>", self.on_tf_change)

        self.lbl_ativo = tk.Label(linha_tf, text=f"Ativo: {STATE['ativo']}",
                                  font=("Segoe UI", 9, "bold"), fg="#c084fc",
                                  bg="#0d1527")
        self.lbl_ativo.pack(side="right")

        self.lbl_estado = tk.Label(info, text="Estado: Aguardando calibração...",
                                  font=("Segoe UI", 9, "bold"), fg="#ffffff",
                                  bg="#0d1527")
        self.lbl_estado.pack(anchor="w", pady=(8, 0))

        self.lbl_checagem = tk.Label(info, text="Última checagem: —",
                                     font=("Segoe UI", 8), fg="#94a3b8",
                                     bg="#0d1527")
        self.lbl_checagem.pack(anchor="w", pady=(2, 0))

        self.lbl_vela = tk.Label(info, text="Próxima vela: —",
                                 font=("Segoe UI", 8), fg="#34d399",
                                 bg="#0d1527")
        self.lbl_vela.pack(anchor="w", pady=(2, 0))

        self.lbl_sinal = tk.Label(info, text="Último sinal: nenhum",
                                  font=("Segoe UI", 9, "bold"), fg="#94a3b8",
                                  bg="#0d1527")
        self.lbl_sinal.pack(anchor="w", pady=(6, 0))

        # barra de alerta (fica verde/vermelha no sinal)
        self.barra = tk.Label(root, text="AGUARDANDO SETUP", font=("Segoe UI", 11, "bold"),
                              bg="#1e293b", fg="#cbd5e1", pady=8)
        self.barra.pack(fill="x", side="bottom")

        # botões
        b1 = tk.Button(main, text="🎯 1. CALIBRAR CÉLULA DE STATUS",
                       font=("Segoe UI", 10, "bold"), bg="#1e293b", fg="#38bdf8",
                       relief="flat", pady=9, cursor="hand2",
                       command=lambda: self.calibrar("status"))
        b1.pack(fill="x", pady=(0, 6))

        b2 = tk.Button(main, text="📈 2. CALIBRAR ÁREA DO GRÁFICO (opcional)",
                       font=("Segoe UI", 10, "bold"), bg="#1e293b", fg="#a78bfa",
                       relief="flat", pady=9, cursor="hand2",
                       command=lambda: self.calibrar("chart"))
        b2.pack(fill="x", pady=(0, 6))

        self.btn_run = tk.Button(main, text="▶️ 3. INICIAR SENTINELA",
                                 font=("Segoe UI", 10, "bold"), bg="#10b981",
                                 fg="#ffffff", relief="flat", pady=10,
                                 cursor="hand2", command=self.toggle_run)
        self.btn_run.pack(fill="x", pady=(0, 6))

        self.btn_dash = tk.Button(main, text="🌐 ABRIR DASHBOARD NO NAVEGADOR",
                                  font=("Segoe UI", 9, "bold"), bg="#0ea5e9",
                                  fg="#ffffff", relief="flat", pady=8,
                                  cursor="hand2", command=self.abrir_dashboard)
        self.btn_dash.pack(fill="x")

        self.lbl_erro = tk.Label(main, text="", font=("Segoe UI", 8),
                                 fg="#f87171", bg="#070b14", wraplength=470,
                                 justify="left")
        self.lbl_erro.pack(anchor="w", pady=(6, 0))

        # dashboard + threads
        porta = servidor.iniciar(int(CONFIG["porta_dashboard"]))
        if porta:
            self.url_dash = f"http://127.0.0.1:{porta}/"
            self.btn_dash.config(text=f"🌐 DASHBOARD: {self.url_dash}")
        else:
            self.url_dash = None

        for alvo in (loop_captura, loop_velas, loop_chealth):
            threading.Thread(target=alvo, daemon=True).start()

        self._repintar_previews()
        self._atualizar_ui()

    # ------------------------------------------------------------ callbacks
    @staticmethod
    def _tf_rotulo(seg):
        return {60: "1m", 180: "3m", 300: "5m", 900: "15m",
                1800: "30m", 3600: "1h", 14400: "4h"}.get(seg, f"{seg}s")

    @staticmethod
    def _tf_seg(rotulo):
        return {"1m": 60, "3m": 180, "5m": 300, "15m": 900,
                "30m": 1800, "1h": 3600, "4h": 14400}.get(rotulo, 60)

    def on_tf_change(self, _evt=None):
        seg = self._tf_seg(self.var_tf.get())
        CONFIG["timeframe_seg"] = seg
        estado.salvar_config(CONFIG)
        with lock():
            STATE["timeframe_seg"] = seg
            STATE["proxima_vela_ts"] = proximo_fechamento(seg)
        CHECKER.ultima_vela_avaliada = 0.0  # nova timeframe => reavalia na próxima
        CHECKER.ultima_sinal_vela = 0.0
        CHECKER._hash_candle_ts = 0.0

    def calibrar(self, destino):
        monitores = captura.listar_monitores()
        if len(monitores) == 1:
            self._abrir_overlay(monitores[0], destino)
        else:
            self._escolher_monitor(monitores, destino)

    def _escolher_monitor(self, monitores, destino):
        dlg = tk.Toplevel(self.root)
        dlg.title("Calibrar — monitor")
        dlg.configure(bg="#0d1527")
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        dlg.geometry("+%d+%d" % (self.root.winfo_screenwidth() // 2 - 190,
                                 self.root.winfo_screenheight() // 2 - 110))
        tk.Label(dlg, text="Em qual monitor está o TradingView?",
                 font=("Segoe UI", 10, "bold"), fg="#e2e8f0",
                 bg="#0d1527").pack(padx=20, pady=(16, 10))
        var = tk.IntVar(value=monitores[0]["index"])
        for m in monitores:
            tk.Radiobutton(
                dlg, variable=var, value=m["index"],
                text=f"Monitor {m['index']} — {m['width']}x{m['height']} "
                     f"(x={m['left']}, y={m['top']})",
                font=("Segoe UI", 9), fg="#e2e8f0", bg="#0d1527",
                selectcolor="#1e293b", activebackground="#0d1527",
                activeforeground="#38bdf8", anchor="w").pack(fill="x", padx=20, pady=2)
        btns = tk.Frame(dlg, bg="#0d1527")
        btns.pack(fill="x", padx=20, pady=14)
        tk.Button(btns, text="Cancelar", font=("Segoe UI", 9), bg="#1e293b",
                  fg="#94a3b8", relief="flat", padx=12, pady=6,
                  command=dlg.destroy).pack(side="left")
        tk.Button(btns, text="Calibrar ➜", font=("Segoe UI", 9, "bold"),
                  bg="#0284c7", fg="#ffffff", relief="flat", padx=12, pady=6,
                  command=lambda: (
                      dlg.destroy(),
                      self._abrir_overlay(
                          next(m for m in monitores if m["index"] == var.get()),
                          destino))).pack(side="right")

    def _abrir_overlay(self, monitor, destino):
        self.root.withdraw()
        self.root.after(400, lambda: CalibradorOverlay(self, monitor, destino))

    def on_calibrado(self, destino, coords):
        self.root.deiconify()
        rotulo = "status" if destino == "status" else "gráfico"
        self.lbl_erro.config(
            text=f"ROI do {rotulo} salva ({coords['width']}x{coords['height']} px)",
            fg="#34d399")
        if destino == "status":
            self.lbl_estado.config(text="Estado: calibrado — inicie a sentinela")

    def toggle_run(self):
        if not STATE["rodando"]:
            if not STATE["roi_status"]:
                messagebox.showwarning("Aviso",
                                       "Calibre a célula de status primeiro!")
                return
            with lock():
                STATE["rodando"] = True
                STATE["proxima_vela_ts"] = proximo_fechamento(STATE["timeframe_seg"])
            self.lbl_status.config(text="🟢 VIGIANDO TELA", fg="#34d399")
            self.btn_run.config(text="⏸️ PAUSAR SENTINELA", bg="#eab308")
        else:
            with lock():
                STATE["rodando"] = False
            ALARME.parar()
            self.lbl_status.config(text="🔴 PARADO", fg="#f87171")
            self.btn_run.config(text="▶️ INICIAR SENTINELA", bg="#10b981")
            self.barra.config(text="PAUSADO", bg="#1e293b", fg="#cbd5e1")

    def abrir_dashboard(self):
        if not self.url_dash:
            messagebox.showerror("Erro", "Dashboard não iniciou (porta ocupada?).")
            return
        import webbrowser
        webbrowser.open(self.url_dash)

    # ------------------------------------------------------------- repaint
    def _repintar_previews(self):
        import base64
        b64 = STATE["preview_status_b64"]
        if b64:
            try:
                from io import BytesIO
                img = Image.open(BytesIO(base64.b64decode(b64)))
                foto = ImageTk.PhotoImage(img)
                self.lbl_img_status.config(image=foto, text="")
                self.lbl_img_status.image = foto
            except Exception:
                pass
        b64c = STATE["preview_chart_b64"]
        if b64c:
            try:
                from io import BytesIO
                img = Image.open(BytesIO(base64.b64decode(b64c)))
                img.thumbnail((230, 140))
                foto = ImageTk.PhotoImage(img)
                self.lbl_img_chart.config(image=foto, text="")
                self.lbl_img_chart.image = foto
            except Exception:
                pass
        self.root.after(700, self._repintar_previews)

    def _atualizar_ui(self):
        agora = time.time()
        # estado + estabilidade
        nome, estavel = estado.estado_atual()
        if not STATE["roi_status"]:
            self.lbl_estado.config(text="Estado: calibre a célula de status")
        elif STATE["rodando"]:
            cor = {"BUY": "#34d399", "SELL": "#f87171"}.get(nome, "#eab308")
            self.lbl_estado.config(
                text=f"Estado: {nome} há {estavel:.1f}s", fg=cor)
        else:
            self.lbl_estado.config(text=f"Estado: {nome} (pausado)", fg="#94a3b8")

        # checagem / vela
        self.lbl_checagem.config(text=f"Última checagem: {STATE['ultima_checagem']}")
        if STATE["rodando"] and STATE["proxima_vela_ts"]:
            falta = max(0, STATE["proxima_vela_ts"] - agora)
            self.lbl_vela.config(
                text=f"Próxima vela em {int(falta // 60)}:{int(falta % 60):02d} "
                     f"({self._tf_rotulo(int(STATE['timeframe_seg']))})")
        else:
            self.lbl_vela.config(text="Próxima vela: —")

        # janelas/filtros rápidos
        if STATE["rodando"]:
            _, ev = noticias_bloqueando(agora)
            dentro, faixa = dentro_da_janela(agora)
            dica = []
            if ev:
                dica.append(f"⛔ NOTÍCIA {ev['country']}")
            if not dentro:
                dica.append(f"⛔ fora da janela {faixa[0]}-{faixa[1]}")
            if dica:
                self.lbl_erro.config(text=" • ".join(dica), fg="#eab308")

        # último sinal + barra de alerta
        if estado.SINAIS:
            s = estado.SINAIS[0]
            cor = "#34d399" if s["direction"] == "BUY" else "#f87171"
            hora = s["ts"][11:19]
            self.lbl_sinal.config(
                text=f"Último sinal: {s['direction']} {s['symbol']} "
                     f"{s['timeframe']} ({hora} UTC)", fg=cor)
        if STATE["flash_ate"] > agora and estado.SINAIS:
            s = estado.SINAIS[0]
            if s["direction"] == "BUY":
                self.barra.config(text="🟢 SINAL DE COMPRA — SETUP CONFIRMADO",
                                  bg="#065f46", fg="#ffffff")
            else:
                self.barra.config(text="🔴 SINAL DE VENDA — SETUP CONFIRMADO",
                                  bg="#7f1d1d", fg="#ffffff")
        elif STATE["rodando"]:
            self.barra.config(text="AGUARDANDO SETUP", bg="#1e293b", fg="#cbd5e1")

        if STATE["erro"]:
            self.lbl_erro.config(text=STATE["erro"], fg="#f87171")
            STATE["erro"] = ""

        self.lbl_ativo.config(text=f"Ativo: {STATE['ativo']}")
        if not STATE["rodando"]:
            self.lbl_status.config(text="🔴 PARADO", fg="#f87171")
        elif not STATE["online"]:
            self.lbl_status.config(text="🟡 SEM SINAL DE TELA", fg="#eab308")

        self.root.after(400, self._atualizar_ui)


def main():
    # troca o cwd para a pasta do script (ROI/config relativos)
    os.chdir(BASE_DIR)
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))
    root = tk.Tk()
    SentinelApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
