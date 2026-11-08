#!/usr/bin/env python3
"""Génère un flux RSS des dessins quotidiens de Faro (matot-braine.fr).

Le flux officiel du site (spip.php?page=backend) mélange toutes les rubriques
et ne contient pas l'image : on part donc du dernier dessin et on remonte la
chaîne « dessin précédent » pour récupérer titre, date et image de chacun.

Usage : python3 faro_rss.py [-n 30] [-o feed.xml] [--img-dir DIR --img-base URL]
(les options --img-* nécessitent Pillow)
"""
import argparse
import html
import io
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urljoin
from xml.sax.saxutils import escape

BASE = "https://matot-braine.fr/"
SITE_FEED = BASE + "spip.php?page=backend"
UA = "Mozilla/5.0 (compatible; faro-rss/1.0)"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def latest_url() -> str:
    """URL du dernier dessin : via le flux du site, sinon la page d'accueil."""
    for src in (SITE_FEED, BASE):
        m = re.search(r"https://matot-braine\.fr/edito/Faro/le-regard-de-faro-\d+|edito/Faro/le-regard-de-faro-\d+", fetch(src))
        if m:
            return urljoin(BASE, m.group(0))
    sys.exit("Impossible de trouver le dernier dessin de Faro")


def meta(page: str, prop: str) -> str | None:
    m = re.search(rf'<meta (?:property|name)="{prop}" content="([^"]*)"', page)
    return html.unescape(m.group(1)) if m else None


def parse(url: str) -> dict:
    page = fetch(url)
    date_iso = meta(page, "article:published_time")
    date = datetime.fromisoformat(date_iso.replace("Z", "+00:00")) if date_iso else datetime.now(timezone.utc)
    # Image pleine taille dans le corps de l'article, sinon og:image
    img = None
    body = page.split('<div class="texte">', 1)
    if len(body) == 2:
        m = re.search(r"<img src='([^']+)'", body[1]) or re.search(r'<img src="([^"]+)"', body[1])
        if m:
            img = urljoin(BASE, html.unescape(m.group(1)))
    og = meta(page, "og:image")
    img = img or og
    desc = meta(page, "description") or ""
    m = re.search(r'<time datetime="[^"]+">([^<]+)</time>', page)
    day = m.group(1).strip() if m else date.strftime("%d/%m/%Y")
    prev = re.search(r'<a href="([^"]+)"[^>]*rel="prev"', page)
    return {
        "url": url,
        "title": f"Le regard de Faro – {day}",
        "date": date,
        "image": img,
        "thumb": og,
        "desc": desc,
        "prev": urljoin(BASE, prev.group(1)) if prev else None,
    }


def flatten(it: dict, img_dir: str, img_base: str, width: int = 1600) -> None:
    """Pose le dessin (PNG/WebP transparent) sur fond blanc et l'enregistre en JPEG,
    pour qu'il reste lisible dans les lecteurs RSS en mode sombre."""
    from PIL import Image

    if not it["image"]:
        return
    name = re.search(r"(\d+)$", it["url"]).group(1) + ".jpg"
    path = os.path.join(img_dir, name)
    if not os.path.exists(path):
        req = urllib.request.Request(it["image"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            im = Image.open(io.BytesIO(r.read())).convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.getchannel("A"))
        if bg.width > width:
            bg = bg.resize((width, round(bg.height * width / bg.width)), Image.LANCZOS)
        bg.save(path, "JPEG", quality=88, optimize=True, progressive=True)
    it["image"] = it["thumb"] = img_base.rstrip("/") + "/" + name


def build(items: list[dict]) -> str:
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">',
        "<channel>",
        "<title>Le regard de Faro</title>",
        f"<link>{BASE}edito/Faro/</link>",
        "<description>Le dessin quotidien de Faro dans Les Petites Affiches Matot Braine</description>",
        "<language>fr</language>",
        f"<lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>",
    ]
    for it in items:
        content = f'<p><a href="{escape(it["url"])}"><img src="{escape(it["image"] or "")}" alt="{escape(it["title"])}" style="max-width:100%"/></a></p><p>{escape(it["desc"])}</p>'
        out += [
            "<item>",
            f"<title>{escape(it['title'])}</title>",
            f"<link>{escape(it['url'])}</link>",
            f'<guid isPermaLink="true">{escape(it["url"])}</guid>',
            f"<pubDate>{format_datetime(it['date'])}</pubDate>",
            f"<description><![CDATA[{content}]]></description>",
        ]
        if it["image"]:
            out.append(f'<media:content url="{escape(it["image"])}" medium="image"/>')
        if it["thumb"]:
            out.append(f'<media:thumbnail url="{escape(it["thumb"])}"/>')
        out.append("</item>")
    out += ["</channel>", "</rss>", ""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=30, help="nombre de dessins (défaut 30)")
    ap.add_argument("-o", default="feed.xml", help="fichier de sortie (défaut feed.xml, '-' pour stdout)")
    ap.add_argument("--img-dir", help="dossier où écrire les dessins aplatis sur fond blanc")
    ap.add_argument("--img-base", help="URL publique correspondant à --img-dir")
    args = ap.parse_args()
    if bool(args.img_dir) != bool(args.img_base):
        ap.error("--img-dir et --img-base vont ensemble")

    items, url, seen = [], latest_url(), set()
    while url and url not in seen and len(items) < args.n:
        seen.add(url)
        it = parse(url)
        items.append(it)
        url = it["prev"]

    if args.img_dir:
        os.makedirs(args.img_dir, exist_ok=True)
        for it in items:
            flatten(it, args.img_dir, args.img_base)

    xml = build(items)
    if args.o == "-":
        sys.stdout.write(xml)
    else:
        with open(args.o, "w", encoding="utf-8") as f:
            f.write(xml)
        print(f"{len(items)} dessins -> {args.o}")


if __name__ == "__main__":
    main()
