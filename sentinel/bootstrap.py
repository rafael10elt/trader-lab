"""Auto-bootstrap do ambiente do Sentinel.

Resolve o erro clássico ao rodar `python sentinel/sentinel.py` com o Python
do sistema (ModuleNotFoundError: No module named 'mss'):

  1. dependências OK -> segue direto;
  2. fora do venv -> cria `sentinel/venv` se não existir e **relança** o
     script com o interpretador do venv;
  3. dentro do venv mas faltando pacotes -> instala do requirements e segue.

Sem loop: o relanço cai sempre no passo 3 (ou falha com mensagem clara).
"""
import importlib
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(BASE_DIR, "venv")
VENV_PYTHON = os.path.join(VENV_DIR, "Scripts", "python.exe")
REQUIREMENTS = os.path.join(BASE_DIR, "requirements.txt")


def _log(fase, **extra):
    """Rastro do bootstrap; ative com SENTINEL_BOOT_LOG=1 (útil p/ debug)."""
    if os.environ.get("SENTINEL_BOOT_LOG") != "1":
        return
    dados = " ".join(f"{k}={v}" for k, v in extra.items())
    linha = (f"[pid={os.getpid()} ppid={os.getppid()} {fase}] {dados}\n")
    try:
        with open(os.path.join(BASE_DIR, "bootstrap.log"), "a",
                  encoding="utf-8") as f:
            f.write(linha)
    except Exception:
        pass


def _tem_dependencias():
    try:
        import mss  # noqa: F401
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def _dentro_do_venv():
    return os.path.abspath(sys.prefix) == os.path.abspath(VENV_DIR)


def _msg_ajuda(erro):
    return (
        "[SENTINEL] Nao consegui preparar o ambiente automaticamente.\n"
        f"  motivo: {erro}\n\n"
        "  Correcao manual (uma vez so):\n"
        f"    cd {BASE_DIR}\n"
        "    py -3 -m venv venv\n"
        "    venv\\Scripts\\python.exe -m pip install -r requirements.txt\n\n"
        "  Ou simplesmente rode: sentinel\\INICIAR_SENTINELA.bat\n"
    )


def _pip_install():
    subprocess.check_call(
        [VENV_PYTHON, "-m", "pip", "install", "--disable-pip-version-check",
         "-q", "-r", REQUIREMENTS],
        cwd=BASE_DIR)
    importlib.invalidate_caches()  # pacotes novos passam a ser enxergados


def _quote(arg):
    # os.execv no Windows monta a linha de comando juntando os argumentos;
    # caminhos com espaços (ex.: C:\Meu Projetos\...) quebrariam sem aspas.
    if arg and " " in arg and not (arg.startswith('"') and arg.endswith('"')):
        return f'"{arg}"'
    return arg


def _relancar(script, argv):
    print(f"[SENTINEL] Reabrindo com o ambiente do venv: {VENV_PYTHON}")
    os.execv(VENV_PYTHON,
             [VENV_PYTHON] + [_quote(a) for a in [script] + list(argv)])


def garantir_ambiente(script, argv=None):
    """Garante dependências antes dos imports pesados. Não retorna em erro."""
    argv = sys.argv[1:] if argv is None else argv
    _log("avalia", deps=_tem_dependencias(), no_venv=_dentro_do_venv(),
         exe=sys.executable, argv=" ".join(argv))

    if _tem_dependencias():
        return

    if not _dentro_do_venv():
        # passo 2: interp. global sem as libs -> usa/cria o venv e relança
        try:
            if not os.path.exists(VENV_PYTHON):
                _log("cria-venv", alvo=VENV_PYTHON)
                print("[SENTINEL] Criando ambiente Python (1a execucao)...")
                subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR],
                                      cwd=BASE_DIR)
                _pip_install()
                print("[SENTINEL] Ambiente pronto.")
            _log("relanca", alvo=VENV_PYTHON)
            _relancar(script, argv)
        except SystemExit:
            raise
        except Exception as exc:
            sys.stderr.write(_msg_ajuda(exc))
            sys.exit(1)

    # passo 3: dentro do venv, faltando pacotes
    try:
        _log("pip-install", dentro_venv=True)
        _pip_install()
        if not _tem_dependencias():
            raise RuntimeError("pacotes instalados mas seguem indisponíveis")
    except Exception as exc:
        sys.stderr.write(_msg_ajuda(exc))
        sys.exit(1)


def relatorio():
    """Diagnóstico do ambiente (`python sentinel.py --check`)."""
    from importlib import metadata

    def ver(pkg):
        try:
            return metadata.version(pkg)
        except Exception:
            return "AUSENTE"

    print("=== LUMI SENTINEL VISION — checagem de ambiente ===")
    print(f"  interpretador : {sys.executable}")
    print(f"  python        : {sys.version.split()[0]}")
    print(f"  venv esperado : {VENV_PYTHON} "
          f"({'existe' if os.path.exists(VENV_PYTHON) else 'NÃO existe'})")
    print(f"  dentro do venv: {_dentro_do_venv()}")
    print(f"  mss           : {ver('mss')}")
    print(f"  pillow        : {ver('pillow')}")
    print(f"  deps OK       : {_tem_dependencias()}")
    print(f"  dashboard     : sobe em http://127.0.0.1:8777/ ao iniciar a GUI")
