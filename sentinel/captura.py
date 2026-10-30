"""Captura de tela e hash do gráfico (visão computacional, sem OpenCV).

Tudo com Pillow + mss, para instalar leve e rodar no Python 3.14.
A leitura das velas (cores/cores do gráfico) vive em velas.py; aqui ficam
só captura, hash de vitalidade e preview.
"""
import ctypes
import hashlib
import re
import threading

from PIL import Image

# Suporte a DPI do Windows (precisão pixel a pixel)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    import win32gui
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

_thread_local = threading.local()


def _sct():
    """Instância mss por thread (mss não é seguro entre threads)."""
    sct = getattr(_thread_local, "sct", None)
    if sct is None:
        import mss
        # MSS é a API atual; mss.mss() é o alias legado (deprecated)
        fabrica = getattr(mss, "MSS", None) or mss.mss
        sct = fabrica()
        _thread_local.sct = sct
    return sct


def listar_monitores():
    """Monitores com coordenadas GLOBAIS do desktop virtual (podem ser negativas)."""
    try:
        # ATENÇÃO: não usar `with _sct()` — o __exit__ fecha a instância
        # cacheada da thread e todo grab seguinte falha com "access denied".
        monitores = [
            {"index": i, "left": int(m["left"]), "top": int(m["top"]),
             "width": int(m["width"]), "height": int(m["height"])}
            for i, m in enumerate(_sct().monitors[1:], start=1)
        ]
        return monitores or _monitores_fallback()
    except Exception:
        return _monitores_fallback()


def _monitores_fallback():
    user32 = ctypes.windll.user32
    return [{"index": 1, "left": 0, "top": 0,
             "width": user32.GetSystemMetrics(0),
             "height": user32.GetSystemMetrics(1)}]


def capturar(roi):
    """Captura a ROI e devolve uma imagem Pillow RGB."""
    shot = _sct().grab(roi)
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def hash_faixa_direita(img, proporcao=0.10):
    """Hash da faixa mais à direita do gráfico.

    Comparado de uma vela para a outra: se NÃO mudou, o gráfico está parado
    (aba congelada/oculta) e o sinal do relógio de vela não é confiável.
    """
    w, h = img.size
    largura = max(int(w * proporcao), 8)
    faixa = img.crop((w - largura, 0, w, h))
    return hashlib.md5(faixa.tobytes()).hexdigest()


def detectar_ativo_tradingview():
    """Lê o símbolo (MNQ, NQ, NAS100...) do título da janela do gráfico."""
    if not HAS_WIN32:
        return None
    candidatos = []

    def enum_cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            tit = win32gui.GetWindowText(hwnd).strip()
            if tit and any(k in tit for k in ["TradingView", "Lumitrader", "Futures",
                                              "Index", "Nasdaq", "CME", "Chrome",
                                              "Brave", "Edge"]):
                candidatos.append(tit)

    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        return None

    for tit in candidatos:
        up = tit.upper()
        if "MICRO E-MINI NASDAQ" in up or "MNQ" in up:
            m = re.search(r"\b(MNQ[A-Z0-9!]{1,6})\b", up)
            return m.group(1) if m else "MNQ"
        if "E-MINI NASDAQ" in up or "NQ1!" in up:
            m = re.search(r"\b(NQ[A-Z0-9!]{1,6})\b", up)
            return m.group(1) if m else "NQ"
        for alias in ["NAS100", "USTEC", "US100", "QQQ"]:
            if alias in up:
                return alias
    return None


def preview_jpeg(img, max_largura=960, qualidade=45, escala_min=0):
    """JPEG compacto para o dashboard (bytes). Reduz para caber em `max_largura`."""
    from io import BytesIO

    alvo = img
    if img.size[0] > max_largura:
        fator = max_largura / img.size[0]
        alvo = img.resize((max_largura, max(1, int(img.size[1] * fator))),
                          Image.Resampling.LANCZOS)
    elif escala_min and img.size[0] < escala_min:
        fator = escala_min / img.size[0]
        alvo = img.resize((escala_min, max(1, int(img.size[1] * fator))),
                          Image.Resampling.NEAREST)
    buf = BytesIO()
    alvo.save(buf, format="JPEG", quality=qualidade, optimize=True)
    return buf.getvalue()
