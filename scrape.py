#!/usr/bin/env python3
"""
Extrae de la portada de larepublica.co los titulares etiquetados como
Hacienda y Bolsas, y los guarda en data/noticias.json.

Pensado para ejecutarse desde GitHub Actions con un cron.
El archivo JSON acumula histórico: la portada rota cada pocas horas,
así que los titulares que salen de portada se conservan aquí.
"""

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ORIGEN = "https://www.larepublica.co/"
ETIQUETAS = ["Hacienda", "Bolsas"]
MAX_POR_ETIQUETA = 60          # cuántos titulares conserva el histórico
DIAS_RETENCION = 30            # descarta lo más viejo que esto

SALIDA = Path(__file__).resolve().parent.parent / "data" / "noticias.json"
BOGOTA = timezone(timedelta(hours=-5))

CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-CO,es;q=0.9",
}

CONTENEDORES = ("news", "V_Title", "col")


def limpiar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def contenedor_de(kicker):
    """Sube por el árbol hasta el bloque que envuelve kicker + titular."""
    nodo = kicker.parent
    while nodo is not None and getattr(nodo, "get", None):
        clases = nodo.get("class") or []
        if any(c in CONTENEDORES for c in clases):
            return nodo
        nodo = nodo.parent
    return kicker.parent


def descargar(url: str) -> str:
    r = requests.get(url, headers=CABECERAS, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def extraer(html: str) -> dict:
    sopa = BeautifulSoup(html, "lxml")
    resultado = {e: [] for e in ETIQUETAS}
    urls_vistas = set()

    for kicker in sopa.select("a.kicker"):
        etiqueta = limpiar(kicker.get_text())
        if etiqueta not in ETIQUETAS:
            continue

        caja = contenedor_de(kicker)
        enlace = caja.select_one("h2.tt a") or caja.select_one("h2 a")
        if not enlace:
            continue

        titulo = limpiar(enlace.get_text())
        href = enlace.get("href") or ""
        if not titulo or not href:
            continue

        url = urljoin(ORIGEN, href)
        if url in urls_vistas:
            continue
        urls_vistas.add(url)

        seccion = caja.select_one(".date-news")
        resultado[etiqueta].append({
            "titulo": titulo,
            "url": url,
            "fecha_portada": limpiar(seccion.get_text()) if seccion else "",
        })

    return resultado


def cargar_previo() -> dict:
    if not SALIDA.exists():
        return {}
    try:
        return json.loads(SALIDA.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def fusionar(previo: dict, nuevo: dict, ahora: str) -> dict:
    etiquetas_previas = previo.get("etiquetas", {})
    limite = datetime.fromisoformat(ahora) - timedelta(days=DIAS_RETENCION)
    salida = {}
    nuevos_totales = 0

    for etiqueta in ETIQUETAS:
        por_url = {}

        # histórico primero, para conservar la fecha de primera aparición
        for item in etiquetas_previas.get(etiqueta, []):
            try:
                if datetime.fromisoformat(item["capturado"]) < limite:
                    continue
            except (KeyError, ValueError):
                pass
            por_url[item["url"]] = item

        for item in nuevo.get(etiqueta, []):
            if item["url"] in por_url:
                por_url[item["url"]]["titulo"] = item["titulo"]  # a veces lo editan
            else:
                por_url[item["url"]] = {**item, "capturado": ahora}
                nuevos_totales += 1

        ordenados = sorted(
            por_url.values(),
            key=lambda i: i.get("capturado", ""),
            reverse=True,
        )
        salida[etiqueta] = ordenados[:MAX_POR_ETIQUETA]

    return {
        "fuente": ORIGEN,
        "actualizado": ahora,
        "nuevos_en_esta_corrida": nuevos_totales,
        "etiquetas": salida,
    }


def main() -> int:
    ahora = datetime.now(BOGOTA).isoformat(timespec="seconds")

    try:
        html = descargar(ORIGEN)
    except requests.RequestException as e:
        print(f"No se pudo descargar la portada: {e}", file=sys.stderr)
        return 1

    nuevo = extraer(html)
    for etiqueta in ETIQUETAS:
        print(f"{etiqueta}: {len(nuevo[etiqueta])} en portada")

    if not any(nuevo.values()):
        print("Portada leída pero sin coincidencias: puede que cambió el HTML.",
              file=sys.stderr)
        return 1

    datos = fusionar(cargar_previo(), nuevo, ahora)
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Guardado {SALIDA} · {datos['nuevos_en_esta_corrida']} titulares nuevos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
