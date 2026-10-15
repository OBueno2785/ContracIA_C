"""Ejecución local: `python -m contractia [--contrato CARPETA] [--sin-chat]`."""
from __future__ import annotations

import argparse
import os


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="contractia", description="Auditoría contractual multi-agente con Claude.")
    p.add_argument("--contrato", help="Carpeta con los PDF/DOCX del contrato (por defecto CONTRACTIA_RUTA_CONTRATO o contrato_nuevo).")
    p.add_argument("--reporte", help="Ruta del informe Markdown de salida.")
    p.add_argument("--sin-chat", action="store_true", help="No abrir el chat al terminar la auditoría.")
    p.add_argument("--sin-cache", action="store_true", help="Ignorar grafo y checkpoints guardados.")
    return p.parse_args()


def run() -> None:
    from .config import actualizar_config
    from .main import main

    args = _parse_args()
    if args.contrato: actualizar_config(ruta_contrato=args.contrato)
    if args.reporte: actualizar_config(reporte_md=args.reporte)
    if args.sin_cache: actualizar_config(usar_cache=False)

    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("ANTHROPIC_AUTH_TOKEN"):
        print("Aviso: no hay ANTHROPIC_API_KEY. Defínela en el archivo .env (ver .env.example).")
    main(chat=not args.sin_chat)


if __name__ == "__main__":
    run()
