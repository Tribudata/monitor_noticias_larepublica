#!/usr/bin/env python3
"""
Extrae de la portada de larepublica.co los titulares etiquetados como
Hacienda y Bolsas, y los guarda en data/noticias.json.

Pensado para ejecutarse desde GitHub Actions con un cron.
El archivo JSON acumula histórico: la portada rota cada pocas horas,
así que los titulares que salen de portada se conservan aquí.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ORIGEN = "https://www.larepublica.co/"
ETIQUETAS = ["Hacienda", "Bolsas"]
MAX_POR_ETIQUETA = 60          # cuántos titulares conserva el histórico
DIAS_RETENCION = 30            # descarta lo más viejo que esto

# --- Reescritura de titulares -------------------------------------------
# No se publica el titular literal de La República: se guarda como
# referencia y se muestra una redacción propia enlazada al original.
API_MODELO = "gemini-2.5-flash"
API_URL = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{API_MODELO}:generateContent")
API_CLAVE = os.environ.get("GEMINI_API_KEY", "")
LOTE = 15        # titulares por llamada
REINTENTOS = 4   # ante 429/503, que son transitorios

INSTRUCCION = """Reformula cada titular de prensa económica colombiana con
palabras distintas, conservando exactamente el mismo significado.

Reglas estrictas:
- No agregues ningún dato, cifra, nombre o matiz que no esté en el original.
- No omitas cifras ni nombres que sí estén.
- Nada de adjetivos valorativos, interpretaciones ni opiniones.
- Máximo 110 caracteres. Español de Colombia. Sin comillas ni punto final.
- Si un titular no se puede reformular sin cambiar el sentido, devuélvelo igual.

Responde ÚNICAMENTE con un arreglo JSON de cadenas, en el mismo orden y con
la misma cantidad de elementos que recibiste. Sin explicaciones ni markdown."""

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


def adaptar(titulares: list) -> list:
    """Devuelve una redacción propia de cada titular, o [] si no se pudo.

    Se llama solo con los titulares nuevos de la corrida, así que son unos
    pocos por hora. Si falla, el llamador conserva los que ya tenía y vuelve
    a intentar en la siguiente ejecución.
    """
    if not titulares:
        return []
    if not API_CLAVE:
        print("  falta GEMINI_API_KEY: no se adaptan titulares", file=sys.stderr)
        return []

    salida = []
    for i in range(0, len(titulares), LOTE):
        trozo = titulares[i:i + LOTE]
        cuerpo = {
            "system_instruction": {"parts": [{"text": INSTRUCCION}]},
            "contents": [{
                "role": "user",
                "parts": [{"text": json.dumps(trozo, ensure_ascii=False)}],
            }],
            "generationConfig": {
                "temperature": 0.4,
                "responseMimeType": "application/json",
            },
        }
        adaptados = None
        for intento in range(1, REINTENTOS + 1):
            try:
                r = requests.post(
                    API_URL,
                    headers={
                        "x-goog-api-key": API_CLAVE,
                        "content-type": "application/json",
                    },
                    json=cuerpo,
                    timeout=90,
                )
                # 429 (cuota) y 5xx (saturación) son transitorios: se reintenta.
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
                r.raise_for_status()

                partes = r.json()["candidates"][0]["content"]["parts"]
                texto = "".join(x.get("text", "") for x in partes)
                texto = re.sub(r"^```(?:json)?|```$", "", texto.strip()).strip()
                adaptados = json.loads(texto)
                break

            except requests.HTTPError as e:
                codigo = e.response.status_code if e.response is not None else 0
                if codigo not in (429,) and codigo < 500:
                    print(f"  no se pudieron adaptar los titulares ({e})",
                          file=sys.stderr)
                    return []
                if intento == REINTENTOS:
                    print(f"  no se pudieron adaptar los titulares tras "
                          f"{REINTENTOS} intentos ({e})", file=sys.stderr)
                    return []
                espera = 2 ** intento          # 2, 4, 8 segundos
                print(f"  {e}; reintento {intento}/{REINTENTOS - 1} "
                      f"en {espera}s", file=sys.stderr)
                time.sleep(espera)

            except (requests.RequestException, json.JSONDecodeError,
                    KeyError, IndexError) as e:
                print(f"  no se pudieron adaptar los titulares ({e})",
                      file=sys.stderr)
                return []

        if adaptados is None:
            return []

        if not isinstance(adaptados, list) or len(adaptados) != len(trozo):
            print("  respuesta inesperada al adaptar titulares", file=sys.stderr)
            return []

        salida.extend(limpiar(str(a)) for a in adaptados)

    return salida


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

        # Solo entran los titulares que aún no están en el histórico.
        pendientes = [i for i in nuevo.get(etiqueta, []) if i["url"] not in por_url]
        adaptados = adaptar([i["titulo"] for i in pendientes])

        if pendientes and not adaptados:
            print(f"  {etiqueta}: {len(pendientes)} titulares quedan para la "
                  f"próxima corrida (sin adaptación)", file=sys.stderr)

        for item, propio in zip(pendientes, adaptados):
            por_url[item["url"]] = {
                **item,
                "titulo": propio,               # redacción propia, es la que se publica
                "titulo_fuente": item["titulo"],  # original, solo como referencia
                "capturado": ahora,
            }
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
