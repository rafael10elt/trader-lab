"""Calibrador standalone da ROI (status ou gráfico) — uso alternativo à GUI.

    python calibrador.py status
    python calibrador.py chart
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bootstrap  # noqa: E402

bootstrap.garantir_ambiente(os.path.abspath(__file__))

import tkinter as tk  # noqa: E402

import captura  # noqa: E402
import estado  # noqa: E402


class SeletorArea:
    def __init__(self, monitor, destino):
        self.monitor = monitor
        self.destino = destino
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        w, h = monitor["width"], monitor["height"]
        self.root.geometry(f"{w}x{h}{monitor['left']:+d}{monitor['top']:+d}")
        self.root.attributes("-alpha", 0.3)
        self.root.attributes("-topmost", True)
        self.root.config(cursor="cross")
        alvo = "CÉLULA DE STATUS" if destino == "status" else "ÁREA DO GRÁFICO"
        self.root.title(f"Calibrar {alvo} — monitor {monitor['index']} (ESC cancela)")

        self.canvas = tk.Canvas(self.root, cursor="cross", bg="grey")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            12, 12, anchor="nw", fill="white", font=("Segoe UI", 11, "bold"),
            text=f"MONITOR {monitor['index']} • Arraste sobre {alvo} • ESC cancela")
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.start_x = self.start_y = self.rel_x = self.rel_y = None
        self.rect = None

    def on_press(self, e):
        self.start_x, self.start_y = e.x_root, e.y_root
        self.rel_x = e.x_root - self.monitor["left"]
        self.rel_y = e.y_root - self.monitor["top"]
        self.rect = self.canvas.create_rectangle(
            self.rel_x, self.rel_y, self.rel_x + 1, self.rel_y + 1,
            outline="red", width=2)

    def on_drag(self, e):
        rx, ry = e.x_root - self.monitor["left"], e.y_root - self.monitor["top"]
        self.canvas.coords(self.rect, self.rel_x, self.rel_y, rx, ry)

    def on_release(self, e):
        x1, x2 = sorted((self.start_x, e.x_root))
        y1, y2 = sorted((self.start_y, e.y_root))
        coords = {"left": int(x1), "top": int(y1),
                  "width": int(x2 - x1), "height": int(y2 - y1)}
        if coords["width"] < 8 or coords["height"] < 8:
            print("[!] Área muito pequena, nada salvo.")
            self.root.destroy()
            return
        if self.destino == "status":
            estado.salvar_roi_status(coords)
        else:
            estado.salvar_roi_chart(coords)
        print(f"[OK] ROI {self.destino} salva: {coords}")
        self.root.destroy()


if __name__ == "__main__":
    destino = sys.argv[1] if len(sys.argv) > 1 else "status"
    if destino not in ("status", "chart"):
        print("Uso: python calibrador.py [status|chart]")
        sys.exit(1)

    monitores = captura.listar_monitores()
    print(f"Monitores: {len(monitores)}")
    for m in monitores:
        print(f"  [{m['index']}] {m['width']}x{m['height']} "
              f"(x={m['left']}, y={m['top']})")
    if len(monitores) > 1:
        while True:
            try:
                escolha = int(input("Calibrar em qual monitor? [1/%s]: "
                                    % "/".join(str(m["index"]) for m in monitores))
                              or monitores[0]["index"])
                monitor = next(m for m in monitores if m["index"] == escolha)
                break
            except (ValueError, StopIteration):
                print("Opção inválida.")
    else:
        monitor = monitores[0]

    SeletorArea(monitor, destino).root.mainloop()
