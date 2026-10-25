# webCrawler

Herramienta sencilla de crawling para **análisis SEO y técnico** de un sitio web.
Recorre enlaces internos dentro de un mismo dominio, respeta `robots.txt` y
extrae metadatos relevantes de cada página. **No** descarga imágenes ni almacena
el contenido de los artículos.

## Requisitos

- Python 3.12+
- [`httpx`](https://www.python-httpx.org/) y [`beautifulsoup4`](https://www.crummy.com/software/BeautifulSoup/)

## Instalación

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Uso

```bash
python crawler.py https://www.*.com/ --max-pages 10
```

### Windows: si `python` no funciona o no te deja ejecutar

Dos causas habituales:

- `python` abre la Microsoft Store (alias fantasma) → falta Python en el `PATH`.
- PowerShell bloquea `.venv\Scripts\activate` (`ExecutionPolicy` restringida).

Solución rápida — usa el `python.exe` del entorno virtual directamente, sin
activarlo:

```bat
.venv\Scripts\python.exe crawler.py https://www.*.com/ --max-pages 10
```

O usa el lanzador incluido (crea el `.venv` la primera vez):

```bat
run.bat https://www.*.com/ --max-pages 10
```

Para poder escribir solo `python`, abre una terminal **nueva** después de
instalar Python, o permite scripts en PowerShell con:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

Opciones:

| Opción         | Por defecto     | Descripción                              |
|----------------|-----------------|------------------------------------------|
| `--max-pages`   | `10`            | Máximo de páginas a analizar             |
| `--delay`       | `1.0`           | Segundos de espera entre peticiones      |
| `--json`        | `results.json`  | Ruta del fichero JSON de salida          |
| `--csv`         | `results.csv`   | Ruta del fichero CSV de salida           |
| `--path-prefix` | —               | Sigue solo enlaces bajo esa(s) ruta(s)   |
| `--same-path`   | —               | Deriva el prefijo de la URL inicial      |

### Limitar el crawl a una sección

Por defecto solo se comprueba el **dominio**. Para quedarte dentro de una
sección, indica el prefijo de ruta de los artículos (no el de la página de
listado, que en muchos sitios es distinto):

```bash
# En Directo al Paladar la portada de "postres" es /categoria/postres,
# pero sus artículos cuelgan de /postres/...
python crawler.py https://www.*.com/categoria/postres --path-prefix /postres/
```

La URL inicial (la *semilla*) se analiza siempre aunque no cumpla el prefijo;
a partir de ahí solo se siguen los enlaces que sí lo cumplen.

Puedes pasar **varios prefijos separados por coma**. Útil, por ejemplo, para
incluir también la paginación del listado (`/categoria/postres/2`, `/3`…)
además de los artículos:

```bash
python crawler.py https://www.*.com/categoria/postres \
    --path-prefix /postres/,/categoria/postres
```

Durante la ejecución se muestra el progreso:

```text
[1/10] 200 https://www.*.com/
[2/10] 200 https://www.*.com/...
...
```

## Salida

- `results.json` — todos los campos, con las listas (H1/H2/H3, tipos de schema…)
  como arrays.
- `results.csv` — mismos datos en formato tabla; los campos multivalor se
  separan con ` | `.

### Campos extraídos por página

`url`, `status` HTTP, `<title>`, meta description, canonical, meta robots,
H1 / H2 / H3, idioma, autor, fecha de publicación, fecha de modificación,
tipos de Schema.org / JSON-LD, breadcrumbs, número aproximado de palabras,
número de imágenes, imágenes con/sin `alt`, y número de enlaces internos y
externos.

## Reglas de crawling

- Nunca sale del dominio de la URL inicial (`www.` se ignora al comparar).
- Con `--path-prefix` / `--same-path`, además se limita a esa(s) ruta(s).
- No visita dos veces la misma URL (se ignora el fragmento `#...`).
- Respeta `Disallow` y `Crawl-delay` de `robots.txt`.
- Espera ~1 s entre peticiones (o el `Crawl-delay` si es mayor).

## Propósito

Uso exclusivo para auditoría SEO y técnica. No se reutiliza ni se republica el
contenido del sitio analizado.
