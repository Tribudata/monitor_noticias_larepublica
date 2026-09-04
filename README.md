# Monitor Hacienda y Bolsas — La República

Recoge cada 30 minutos los titulares de la portada de larepublica.co etiquetados
como **Hacienda** y **Bolsas**, y los publica como una portada estática.

```
.github/workflows/actualizar-noticias.yml   cron + commit automático
scraper/scrape.py                           extracción y fusión con el histórico
data/noticias.json                          archivo que consume la página
index.html                                  portada (GitHub Pages)
requirements.txt
```

## Montaje

1. Suba estos archivos a un repositorio nuevo con rama `main`.
2. **Settings → Actions → General → Workflow permissions**: marque
   *Read and write permissions*. Sin esto el bot no puede hacer commit.
3. **Settings → Pages**: origen *Deploy from a branch*, rama `main`, carpeta `/ (root)`.
4. **Actions → Actualizar noticias → Run workflow** para la primera corrida.

La página queda en `https://<usuario>.github.io/<repo>/`.

## Detalles

- `data/noticias.json` que viene aquí es una semilla de prueba; la primera
  corrida real la reemplaza.
- El histórico conserva hasta 60 titulares por etiqueta y descarta lo anterior
  a 30 días (`MAX_POR_ETIQUETA` y `DIAS_RETENCION` en `scrape.py`).
- El scraper devuelve código 1 si la portada carga pero no encuentra ninguna
  coincidencia: eso significa que La República cambió el HTML y hay que revisar
  los selectores `a.kicker` / `h2.tt a`.
- Para agregar secciones, edite la lista `ETIQUETAS` en `scrape.py` **y** en
  `index.html` (por ejemplo `"Agro"`, `"Laboral"`, `"Minas"`).

## Prueba local

```bash
pip install -r requirements.txt
python scraper/scrape.py
python -m http.server 8000   # abrir http://localhost:8000
```
