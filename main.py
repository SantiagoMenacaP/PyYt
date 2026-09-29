"""Punto de entrada de la aplicación."""

from __future__ import annotations

import sys


def main() -> int:
    if sys.version_info < (3, 10):
        print("Se necesita Python 3.10 o superior.")
        return 1

    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        print("Falta la dependencia 'yt-dlp'. Instálala con:\n\n    pip install -r requirements.txt\n")
        return 1

    try:
        from ytbatch.gui import launch
    except ImportError as exc:  # pragma: no cover
        print(f"No se pudo iniciar la interfaz: {exc}")
        return 1

    launch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
