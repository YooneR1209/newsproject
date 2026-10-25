"""Recolector del dataset de Clasificación.

Lee los feeds RSS por sección de clasificacion/data/feeds.csv y usa la
sección como etiqueta: todo lo que sale del feed de deportes es "deportes".
Agrega cada noticia nueva a clasificacion/data/dataset.csv sin duplicar.

Uso (desde la raíz del repositorio):
    pip install -r clasificacion/tools/requirements.txt
    python clasificacion/tools/collect_dataset.py --check          # prueba los feeds, no guarda nada
    python clasificacion/tools/collect_dataset.py                  # una pasada
    python clasificacion/tools/collect_dataset.py --loop 60        # una pasada cada 60 minutos
    python clasificacion/tools/collect_dataset.py --sample-test 100  # muestra para revisar a mano

La categoría "otros" no se recolecta: el servicio la asigna cuando la
confianza del modelo es baja (MIN_SCORE).
"""
import argparse
import csv
import random
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FEEDS_FILE = DATA_DIR / "feeds.csv"
DATASET_FILE = DATA_DIR / "dataset.csv"
TEST_FILE = DATA_DIR / "test_set.csv"

CATEGORIES = {"politica", "economia", "tecnologia", "deportes", "salud", "cultura", "internacional"}
COLUMNS = ["url", "category", "source", "published_at", "title", "text", "collected_at"]
USER_AGENT = "newsproject-unl/1.0 (proyecto academico, Universidad Nacional de Loja)"
MIN_WORDS = 15          # textos más cortos no sirven para entrenar
PAUSE_BETWEEN_FEEDS = 2  # segundos, para no saturar a los medios


def normalize_url(url: str) -> str:
    """Misma normalización que Ingesta: sin utm_*, fbclid, fragmento ni / final."""
    s = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(s.query) if not k.startswith("utm_") and k not in ("fbclid", "gclid")]
    path = s.path.rstrip("/") or "/"
    return urlunsplit((s.scheme.lower(), s.netloc.lower(), path, urlencode(query), ""))


def clean_html(html: str) -> str:
    return " ".join(BeautifulSoup(html or "", "html.parser").get_text(" ").split())


def entry_text(entry) -> str:
    """Título + el texto más largo disponible (contenido completo o resumen)."""
    parts = [c.get("value", "") for c in entry.get("content", [])] + [entry.get("summary", "")]
    body = max((clean_html(p) for p in parts), key=len, default="")
    title = " ".join(entry.get("title", "").split())
    return f"{title}. {body}".strip() if body else title


def entry_date(entry) -> str:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return ""
    return datetime(*parsed[:6], tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def load_feeds() -> list[dict]:
    with FEEDS_FILE.open(encoding="utf-8") as f:
        feeds = [row for row in csv.DictReader(f) if row["url"].strip()]
    unknown = {f["category"] for f in feeds} - CATEGORIES
    if unknown:
        sys.exit(f"Categorías no válidas en feeds.csv: {unknown}")
    return feeds


def load_dataset() -> dict[str, dict]:
    if not DATASET_FILE.exists():
        return {}
    with DATASET_FILE.open(encoding="utf-8", newline="") as f:
        return {row["url"]: row for row in csv.DictReader(f)}


def fetch(client: httpx.Client, url: str):
    response = client.get(url)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"no es un feed válido: {feed.bozo_exception}")
    return feed.entries


def check_feeds() -> None:
    feeds = load_feeds()
    with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        for feed in feeds:
            try:
                entries = fetch(client, feed["url"])
                long_enough = sum(len(entry_text(e).split()) >= MIN_WORDS for e in entries)
                print(f"OK    {feed['category']:<14} {feed['source']:<16} {len(entries):>3} entradas, {long_enough:>3} útiles")
            except Exception as exc:  # noqa: BLE001 - se reporta y se sigue con el siguiente feed
                print(f"FALLA {feed['category']:<14} {feed['source']:<16} {exc}")
            time.sleep(PAUSE_BETWEEN_FEEDS)


def collect_once() -> None:
    feeds = load_feeds()
    dataset = load_dataset()
    new_rows, conflicts = [], 0
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        for feed in feeds:
            try:
                entries = fetch(client, feed["url"])
            except Exception as exc:  # noqa: BLE001
                print(f"FALLA {feed['category']} {feed['source']}: {exc}")
                continue
            for entry in entries:
                if not entry.get("link"):
                    continue
                url = normalize_url(entry["link"])
                if url in dataset:
                    # La misma noticia en dos secciones distintas: etiqueta ambigua, se conserva la primera.
                    conflicts += dataset[url]["category"] != feed["category"]
                    continue
                text = entry_text(entry)
                if len(text.split()) < MIN_WORDS:
                    continue
                row = {
                    "url": url,
                    "category": feed["category"],
                    "source": feed["source"],
                    "published_at": entry_date(entry),
                    "title": " ".join(entry.get("title", "").split()),
                    "text": text,
                    "collected_at": now,
                }
                dataset[url] = row
                new_rows.append(row)
            time.sleep(PAUSE_BETWEEN_FEEDS)

    write_header = not DATASET_FILE.exists()
    with DATASET_FILE.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)

    totals = Counter(row["category"] for row in dataset.values())
    print(f"\n{now}: {len(new_rows)} nuevas; {conflicts} vistas en esta pasada con otra sección (se conserva la primera)")
    print("Total por categoría: " + ", ".join(f"{c}={totals.get(c, 0)}" for c in sorted(CATEGORIES)))


def sample_test(n: int) -> None:
    """Muestra estratificada para revisar a mano: será el conjunto de prueba del informe."""
    dataset = list(load_dataset().values())
    if not dataset:
        sys.exit("dataset.csv está vacío: primero recolecta noticias.")
    by_category: dict[str, list[dict]] = {}
    for row in dataset:
        by_category.setdefault(row["category"], []).append(row)
    per_category = max(1, n // len(by_category))
    random.seed(42)
    sample = [row for rows in by_category.values() for row in random.sample(rows, min(per_category, len(rows)))]
    with TEST_FILE.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["url", "title", "text", "category", "category_revisada"])
        writer.writeheader()
        for row in sample:
            writer.writerow({k: row[k] for k in ("url", "title", "text", "category")} | {"category_revisada": ""})
    print(f"{len(sample)} noticias en {TEST_FILE.name}: completen la columna category_revisada a mano.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="probar los feeds sin guardar nada")
    parser.add_argument("--loop", type=int, metavar="MIN", help="repetir cada MIN minutos")
    parser.add_argument("--sample-test", type=int, metavar="N", help="generar test_set.csv con N noticias")
    args = parser.parse_args()

    if args.check:
        check_feeds()
    elif args.sample_test:
        sample_test(args.sample_test)
    elif args.loop:
        while True:
            collect_once()
            time.sleep(args.loop * 60)
    else:
        collect_once()


if __name__ == "__main__":
    main()
