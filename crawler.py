#!/usr/bin/env python3
"""SEO crawler sencillo para análisis técnico de un sitio web.

Descarga páginas dentro de un mismo dominio, sigue enlaces internos y extrae
información relevante para SEO. No descarga imágenes ni almacena el contenido
de los artículos.

Uso:
    python crawler.py https://www.directoalpaladar.com/ --max-pages 10
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    "SEO-Crawler/1.0 (+analisis tecnico SEO; contacto wasabitech.dev@gmail.com)"
)
DEFAULT_MAX_PAGES = 10
DEFAULT_DELAY = 1.0
REQUEST_TIMEOUT = 20.0

MULTIVALUE_SEP = " | "


# --------------------------------------------------------------------------- #
# Utilidades de URL
# --------------------------------------------------------------------------- #
def normalize_host(host: str) -> str:
    return host.lower().split(":")[0].removeprefix("www.")


def clean_url(url: str) -> str:
    """Quita el fragmento (#...) y espacios sobrantes."""
    url, _ = urldefrag(url.strip())
    return url


def same_domain(url: str, root_host: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return normalize_host(parsed.netloc) == root_host


def in_scope(url: str, root_host: str,
             path_prefixes: tuple[str, ...] | None) -> bool:
    """Mismo dominio y, si se indica, bajo alguno de los prefijos de ruta."""
    if not same_domain(url, root_host):
        return False
    if not path_prefixes:
        return True
    path = urlparse(url).path
    return any(path.startswith(p) for p in path_prefixes)


# --------------------------------------------------------------------------- #
# Helpers JSON-LD
# --------------------------------------------------------------------------- #
def parse_jsonld(soup: BeautifulSoup) -> list:
    blocks: list = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text()
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, list):
            blocks.extend(data)
        else:
            blocks.append(data)
    return blocks


def iter_nodes(obj):
    """Recorre recursivamente dicts y listas de una estructura JSON-LD."""
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from iter_nodes(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_nodes(item)


def collect_schema_types(blocks: list) -> list:
    types: list = []
    for block in blocks:
        for node in iter_nodes(block):
            raw_type = node.get("@type")
            if isinstance(raw_type, str):
                candidates = [raw_type]
            elif isinstance(raw_type, list):
                candidates = [t for t in raw_type if isinstance(t, str)]
            else:
                candidates = []
            for t in candidates:
                if t not in types:
                    types.append(t)
    return types


def find_first_value(blocks: list, key: str):
    for block in blocks:
        for node in iter_nodes(block):
            if key in node and node[key]:
                return node[key]
    return None


def find_node_by_type(blocks: list, type_name: str):
    for block in blocks:
        for node in iter_nodes(block):
            raw_type = node.get("@type")
            if raw_type == type_name or (
                isinstance(raw_type, list) and type_name in raw_type
            ):
                return node
    return None


def author_to_str(value) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        name = value.get("name")
        return name.strip() if isinstance(name, str) and name.strip() else None
    if isinstance(value, list):
        names = [author_to_str(item) for item in value]
        names = [n for n in names if n]
        return ", ".join(names) or None
    return None


# --------------------------------------------------------------------------- #
# Extracción de datos de una página
# --------------------------------------------------------------------------- #
def meta_content(soup: BeautifulSoup, *, name: str | None = None,
                 prop: str | None = None) -> str | None:
    attrs = {}
    if name is not None:
        attrs["name"] = name
    if prop is not None:
        attrs["property"] = prop
    tag = soup.find("meta", attrs=attrs)
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def extract_breadcrumbs(soup: BeautifulSoup, jsonld_blocks: list) -> str | None:
    # 1) JSON-LD BreadcrumbList
    node = find_node_by_type(jsonld_blocks, "BreadcrumbList")
    if node and isinstance(node.get("itemListElement"), list):
        names = []
        for element in node["itemListElement"]:
            if not isinstance(element, dict):
                continue
            name = element.get("name")
            if not name and isinstance(element.get("item"), dict):
                name = element["item"].get("name")
            if name:
                names.append(str(name).strip())
        if names:
            return " > ".join(names)

    # 2) Marcado HTML habitual
    selectors = [
        "nav[aria-label*='readcrumb']",
        "[class*='readcrumb']",
        "[id*='readcrumb']",
        "ol.breadcrumb",
    ]
    for selector in selectors:
        container = soup.select_one(selector)
        if container:
            parts = [
                part.strip()
                for part in container.get_text(">", strip=True).split(">")
                if part.strip()
            ]
            if len(parts) >= 2:
                return " > ".join(parts)
    return None


def detect_language(soup: BeautifulSoup) -> str | None:
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        return html_tag["lang"].strip()
    meta_lang = soup.find("meta", attrs={"http-equiv": re.compile(
        "content-language", re.I)})
    if meta_lang and meta_lang.get("content"):
        return meta_lang["content"].strip()
    og_locale = meta_content(soup, prop="og:locale")
    if og_locale:
        return og_locale
    return None


def approximate_word_count(soup: BeautifulSoup) -> int:
    body = soup.body or soup
    for tag in body.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = body.get_text(" ", strip=True)
    return len(re.findall(r"\b[\wÀ-ÿ'-]+\b", text))


def extract_page_data(url: str, response: httpx.Response, root_host: str) -> dict:
    soup = BeautifulSoup(response.text, "html.parser")
    jsonld_blocks = parse_jsonld(soup)

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    canonical_tag = soup.find("link", rel=lambda v: v and "canonical" in v)
    canonical = canonical_tag.get("href").strip() if (
        canonical_tag and canonical_tag.get("href")) else None

    headings = {
        level: [
            h.get_text(" ", strip=True)
            for h in soup.find_all(level)
            if h.get_text(strip=True)
        ]
        for level in ("h1", "h2", "h3")
    }

    # Autor
    author = (
        meta_content(soup, name="author")
        or meta_content(soup, prop="article:author")
        or author_to_str(find_first_value(jsonld_blocks, "author"))
    )
    if author and author.startswith("http"):
        author = None  # article:author suele ser una URL de perfil, no un nombre

    # Fechas
    published = (
        meta_content(soup, prop="article:published_time")
        or meta_content(soup, name="date")
        or find_first_value(jsonld_blocks, "datePublished")
    )
    modified = (
        meta_content(soup, prop="article:modified_time")
        or meta_content(soup, name="last-modified")
        or find_first_value(jsonld_blocks, "dateModified")
    )
    # <time> dentro de un <article> como último recurso (evita fechas de
    # los teasers en portadas y páginas de listado).
    if not published:
        article_tag = soup.find("article")
        time_tag = (article_tag or soup).find(
            "time", attrs={"datetime": True}) if article_tag else None
        if time_tag:
            published = time_tag["datetime"].strip()

    # Imágenes
    images = soup.find_all("img")
    images_with_alt = sum(1 for img in images if img.get("alt", "").strip())
    images_without_alt = len(images) - images_with_alt

    # Enlaces
    internal_links: set[str] = set()
    external_links: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = clean_url(urljoin(url, href))
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if normalize_host(parsed.netloc) == root_host:
            internal_links.add(absolute)
        else:
            external_links.add(absolute)

    return {
        "url": url,
        "status": response.status_code,
        "title": title,
        "meta_description": meta_content(soup, name="description"),
        "canonical": canonical,
        "meta_robots": meta_content(soup, name="robots"),
        "h1": headings["h1"],
        "h2": headings["h2"],
        "h3": headings["h3"],
        "lang": detect_language(soup),
        "author": author,
        "date_published": str(published) if published else None,
        "date_modified": str(modified) if modified else None,
        "schema_types": collect_schema_types(jsonld_blocks),
        "breadcrumbs": extract_breadcrumbs(soup, jsonld_blocks),
        "word_count": approximate_word_count(BeautifulSoup(response.text,
                                                          "html.parser")),
        "images_total": len(images),
        "images_with_alt": images_with_alt,
        "images_without_alt": images_without_alt,
        "internal_links_count": len(internal_links),
        "external_links_count": len(external_links),
        "_internal_links": sorted(internal_links),
    }


# --------------------------------------------------------------------------- #
# robots.txt
# --------------------------------------------------------------------------- #
def load_robots(client: httpx.Client, base_url: str) -> RobotFileParser:
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        resp = client.get(robots_url)
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        else:
            rp.parse([])  # sin robots.txt -> todo permitido
    except httpx.HTTPError:
        rp.parse([])
    return rp


# --------------------------------------------------------------------------- #
# Crawler
# --------------------------------------------------------------------------- #
def crawl(start_url: str, max_pages: int, delay: float,
          path_prefixes: tuple[str, ...] | None = None) -> list[dict]:
    start_url = clean_url(start_url)
    root_host = normalize_host(urlparse(start_url).netloc)
    if not root_host:
        raise SystemExit(f"URL inicial no válida: {start_url}")

    if path_prefixes:
        path_prefixes = tuple(
            p if p.startswith("/") else "/" + p for p in path_prefixes
        )
        print("Restringido a rutas que empiezan por: "
              f"{', '.join(path_prefixes)}\n")

    headers = {"User-Agent": USER_AGENT}
    results: list[dict] = []

    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=REQUEST_TIMEOUT,
    ) as client:
        robots = load_robots(client, start_url)
        crawl_delay = robots.crawl_delay(USER_AGENT)
        if crawl_delay:
            delay = max(delay, float(crawl_delay))

        queue: deque[str] = deque([start_url])
        seen: set[str] = {start_url}
        processed = 0

        while queue and processed < max_pages:
            url = queue.popleft()

            if not robots.can_fetch(USER_AGENT, url):
                print(f"[{processed + 1}/{max_pages}] --- BLOQUEADO por robots.txt "
                      f"{url}")
                continue

            try:
                response = client.get(url)
            except httpx.HTTPError as exc:
                processed += 1
                print(f"[{processed}/{max_pages}] ERR {url} -> {exc}")
                results.append({
                    "url": url,
                    "status": None,
                    "error": str(exc),
                })
                time.sleep(delay)
                continue

            processed += 1
            print(f"[{processed}/{max_pages}] {response.status_code} {url}")

            content_type = response.headers.get("content-type", "")
            if "html" not in content_type.lower():
                results.append({
                    "url": url,
                    "status": response.status_code,
                    "note": f"contenido no HTML ({content_type})",
                })
                time.sleep(delay)
                continue

            data = extract_page_data(str(response.url), response, root_host)
            new_links = data.pop("_internal_links", [])
            results.append(data)

            if processed < max_pages:
                for link in new_links:
                    if link not in seen and in_scope(link, root_host,
                                                     path_prefixes):
                        seen.add(link)
                        queue.append(link)

            time.sleep(delay)

    return results


# --------------------------------------------------------------------------- #
# Salida
# --------------------------------------------------------------------------- #
CSV_FIELDS = [
    "url", "status", "title", "meta_description", "canonical", "meta_robots",
    "h1", "h2", "h3", "lang", "author", "date_published", "date_modified",
    "schema_types", "breadcrumbs", "word_count", "images_total",
    "images_with_alt", "images_without_alt", "internal_links_count",
    "external_links_count", "error", "note",
]


def write_json(results: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)


def write_csv(results: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            flat = dict(row)
            for key, value in flat.items():
                if isinstance(value, list):
                    flat[key] = MULTIVALUE_SEP.join(str(v) for v in value)
            writer.writerow(flat)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Crawler SEO sencillo (httpx + BeautifulSoup).",
    )
    parser.add_argument("url", help="URL inicial (debe incluir http/https)")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"Máximo de páginas a analizar (por defecto "
                             f"{DEFAULT_MAX_PAGES})")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"Retraso entre peticiones en segundos (por "
                             f"defecto {DEFAULT_DELAY})")
    parser.add_argument("--json", default="results.json",
                        help="Ruta del fichero JSON de salida")
    parser.add_argument("--csv", default="results.csv",
                        help="Ruta del fichero CSV de salida")
    parser.add_argument("--path-prefix", default=None,
                        help="Sigue solo enlaces cuya ruta empiece por este "
                             "prefijo. Admite varios separados por coma, "
                             "p. ej. /postres/,/categoria/postres")
    parser.add_argument("--same-path", action="store_true",
                        help="Restringe el crawl a la sección de la URL inicial "
                             "(deriva el prefijo de su ruta)")
    args = parser.parse_args(argv)

    if not urlparse(args.url).scheme:
        parser.error("La URL debe incluir el esquema, p. ej. https://")

    prefixes: list[str] = []
    if args.path_prefix:
        prefixes = [p.strip() for p in args.path_prefix.split(",") if p.strip()]
    if args.same_path and not prefixes:
        start_path = urlparse(args.url).path
        prefixes = [start_path.rsplit("/", 1)[0] + "/" if "/" in start_path
                    else "/"]

    print(f"Analizando {args.url} (máx. {args.max_pages} páginas, "
          f"retraso {args.delay}s)\n")

    results = crawl(args.url, args.max_pages, args.delay,
                    tuple(prefixes) or None)

    write_json(results, args.json)
    write_csv(results, args.csv)

    ok = sum(1 for r in results if r.get("status") == 200)
    print(f"\nHecho. {len(results)} páginas registradas ({ok} con status 200).")
    print(f"  - {args.json}")
    print(f"  - {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
