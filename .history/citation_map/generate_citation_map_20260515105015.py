#!/usr/bin/env python3
"""
Citation Map Generator using SerpAPI + Nominatim.

Workflow:
  1. Fetch author profile via SerpAPI google_scholar_author endpoint
  2. For each publication, fetch ALL citing papers via google_scholar cites (full pagination)
  3. For each citing paper, resolve author affiliations via google_scholar_author
  4. Geocode institutions via Nominatim (OpenStreetMap) with accept-language=en
  5. Generate Folium citation map + summary/hierarchy/graph JSONs

Checkpoint-based: every batch of API calls is persisted so re-runs
skip already-fetched data, minimising API credit usage.

Usage:
    conda run -n web python citation_map/generate_citation_map.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import folium
import requests
from folium.plugins import MarkerCluster
from tqdm import tqdm

# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------
_SERP_KEY_PATH = Path("/home/chandan/API Keys/serp_api.txt")

SERPAPI_KEY: str = _SERP_KEY_PATH.read_text().strip() if _SERP_KEY_PATH.exists() else ""

if not SERPAPI_KEY:
    sys.exit(f"ERROR: SerpAPI key not found at {_SERP_KEY_PATH}")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCHOLAR_ID = "nfZ5Jc0AAAAJ"
SERPAPI_BASE = "https://serpapi.com/search.json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
OUTPUT_DIR = ROOT_DIR / "assets"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CHECKPOINT_FILE = SCRIPT_DIR / "citation_checkpoint.json"
GEOCODE_CACHE_FILE = SCRIPT_DIR / "geocode_cache.json"
MAP_OUTPUT_FILE = OUTPUT_DIR / "citation_map.html"
DATA_OUTPUT_FILE = OUTPUT_DIR / "citation_data.json"
HIERARCHY_OUTPUT_FILE = OUTPUT_DIR / "citation_hierarchy.json"
GRAPH_OUTPUT_FILE = OUTPUT_DIR / "citation_graph.json"

SERP_DELAY = 1.2          # seconds between SerpAPI calls (avoid 429)
NOMINATIM_DELAY = 1.1     # Nominatim requires ≥1 req/s
RESULTS_PER_PAGE = 20     # SerpAPI max per page for google_scholar
MAX_CITING_PAGES = 50     # up to 1000 citing papers per publication
MAX_RETRIES = 4           # retries on 429 / transient errors


# ---------------------------------------------------------------------------
# JSON persistence
# ---------------------------------------------------------------------------
def _load_json(path: Path, default=None):
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return default if default is not None else {}


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _empty_checkpoint() -> dict:
    return {
        "profile": {},
        "h_index": 0,
        "total_citations": 0,
        "articles": [],
        "citing_papers": {},           # cites_id -> [paper dicts]
        "cites_id_expected": {},       # cites_id -> expected count
        "author_cache": {},            # author_id -> {name, affiliations}
        "processed_cites_ids": [],     # fully-fetched cites_ids
        "institutions": {},
        "publications_hierarchy": [],
        "last_updated": None,
    }


# ---------------------------------------------------------------------------
# SerpAPI helpers
# ---------------------------------------------------------------------------
def _serpapi_get(params: dict) -> dict:
    params["api_key"] = SERPAPI_KEY
    for attempt in range(MAX_RETRIES + 1):
        time.sleep(SERP_DELAY)
        resp = requests.get(SERPAPI_BASE, params=params, timeout=60)
        if resp.status_code == 429:
            wait = min(2 ** (attempt + 2), 120)  # 4, 8, 16, 32 … max 120s
            print(f"  ⏳ Rate-limited (429). Waiting {wait}s … (attempt {attempt + 1})")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    # Final attempt failed — raise
    resp.raise_for_status()
    return {}


def fetch_author_profile(scholar_id: str) -> dict:
    """Fetch full author profile (articles, cited_by, etc.)."""
    return _serpapi_get({
        "engine": "google_scholar_author",
        "author_id": scholar_id,
        "hl": "en",
        "num": 100,
    })


def fetch_citing_papers(
    cites_id: str,
    expected: int,
    existing: list[dict] | None = None,
) -> list[dict]:
    """Fetch ALL citing papers for a cites_id with full pagination.

    If *existing* is provided, resumes from len(existing) offset to avoid
    re-fetching pages we already have.
    """
    all_results: list[dict] = list(existing) if existing else []
    start = len(all_results)
    pages_needed = min(MAX_CITING_PAGES, (expected // RESULTS_PER_PAGE) + 2)
    pages_done = start // RESULTS_PER_PAGE

    for _ in range(pages_done, pages_needed):
        data = _serpapi_get({
            "engine": "google_scholar",
            "cites": cites_id,
            "hl": "en",
            "num": RESULTS_PER_PAGE,
            "start": start,
        })
        results = data.get("organic_results", [])
        if not results:
            break
        all_results.extend(results)

        # Stop if no next page
        pagination = data.get("serpapi_pagination", {})
        if "next" not in pagination:
            break
        start += RESULTS_PER_PAGE

    return all_results


def fetch_author_affiliation(author_id: str) -> dict | None:
    """Fetch an author's name + affiliations via google_scholar_author."""
    if not author_id or len(author_id) < 5:
        return None
    try:
        data = _serpapi_get({
            "engine": "google_scholar_author",
            "author_id": author_id,
            "hl": "en",
        })
        author = data.get("author", {})
        if author:
            return {
                "name": author.get("name", ""),
                "affiliations": author.get("affiliations", ""),
            }
    except Exception as exc:
        print(f"  ⚠ Error fetching author {author_id}: {exc}")
    return None


# ---------------------------------------------------------------------------
# Nominatim (OpenStreetMap) geocoding
# ---------------------------------------------------------------------------
_NOMINATIM_HEADERS = {
    "User-Agent": "CitationMapGenerator/1.0 (academic-research)",
    "Accept-Language": "en",
}


def _needs_regeocode(entry: dict | None) -> bool:
    """Return True if a cached entry is missing or has non-English names."""
    if entry is None:
        return True
    country = entry.get("country", "")
    city = entry.get("city", "")
    return not country.isascii() or not city.isascii()


def geocode_institution(name: str, cache: dict) -> dict | None:
    """Geocode an institution via Nominatim (OpenStreetMap).

    Returns {lat, lon, city, country, address} with English names.
    Retries previously-failed (None) and non-English entries.
    """
    if name in cache and not _needs_regeocode(cache[name]):
        return cache[name]

    query = _clean_institution_name(name)
    if not query or len(query) < 3:
        cache[name] = None
        _save_json(GEOCODE_CACHE_FILE, cache)
        return None

    try:
        time.sleep(NOMINATIM_DELAY)
        resp = requests.get(NOMINATIM_URL, params={
            "q": query,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
            "accept-language": "en",
        }, headers=_NOMINATIM_HEADERS, timeout=15)
        resp.raise_for_status()
        results = resp.json()

        if results:
            res = results[0]
            addr = res.get("address", {})
            country = addr.get("country", "")
            city = (addr.get("city", "")
                    or addr.get("town", "")
                    or addr.get("village", "")
                    or addr.get("state", ""))
            result = {
                "lat": float(res.get("lat", 0)),
                "lon": float(res.get("lon", 0)),
                "address": res.get("display_name", ""),
                "city": city,
                "country": country,
            }
            cache[name] = result
            _save_json(GEOCODE_CACHE_FILE, cache)
            return result
    except Exception as exc:
        print(f"  ⚠ Nominatim geocode error for '{query}': {exc}")

    cache[name] = None
    _save_json(GEOCODE_CACHE_FILE, cache)
    return None


def _clean_institution_name(raw: str) -> str:
    """Strip job titles, prefixes, and noise from an affiliation string.

    e.g. 'Research Scientist @ ABB US Corporate Research Center'
         -> 'ABB US Corporate Research Center'
         'TU Delft - Senior Lecturer - Zurich University of Applied Sciences'
         -> 'TU Delft'
         'at Binghamton University' -> 'Binghamton University'
    """
    # Remove everything before '@'
    if "@" in raw:
        raw = raw.split("@", 1)[1].strip()
    # Handle " - Title - " patterns: take the first institution-looking part
    if " - " in raw:
        raw = raw.split(" - ")[0].strip()
    # Strip leading 'at '
    if raw.lower().startswith("at "):
        raw = raw[3:].strip()
    # Remove common title prefixes
    prefixes = (
        "professor", "associate professor", "assistant professor",
        "postdoc", "post-doc", "postdoctoral",
        "phd student", "ph.d. student", "doctoral student",
        "research scientist", "senior researcher", "researcher",
        "lecturer", "reader", "fellow",
    )
    lower = raw.lower().strip()
    for prefix in prefixes:
        if lower.startswith(prefix):
            raw = raw[len(prefix):].strip().lstrip(",").lstrip("-").strip()
            lower = raw.lower().strip()
    return raw.strip()


# ---------------------------------------------------------------------------
# Institution parsing from affiliation string
# ---------------------------------------------------------------------------
def parse_institution(affiliations_str: str) -> str:
    """Extract primary institution name from a Scholar affiliations string."""
    if not affiliations_str:
        return ""

    # Handle "@" separator (e.g. "Research Scientist @ ABB")
    if "@" in affiliations_str:
        affiliations_str = affiliations_str.split("@", 1)[1].strip()

    parts = [p.strip() for p in affiliations_str.split(",")]
    # Walk from end, pick first chunk with institutional keywords
    keywords = (
        "university", "universit", "institut", "college", "école",
        "school", "polytechnic", "lab", "center", "centre",
        "academy", "research", "hospital", "corporation",
        "department", "faculty", "national", "federal",
        "ministry", "council", "agency", "organization",
    )
    for part in reversed(parts):
        if any(kw in part.lower() for kw in keywords):
            return part.strip()
    # Fallback: return the last part
    return parts[-1].strip() if parts else ""


# ---------------------------------------------------------------------------
# Summary-based author/year parsing
# ---------------------------------------------------------------------------
def _parse_summary_authors(summary: str) -> list[str]:
    """Extract author names from 'A Smith, B Jones… - Journal, 2023'."""
    if not summary:
        return []
    parts = summary.split(" - ")
    if not parts:
        return []
    author_part = parts[0]
    names = [n.strip().rstrip("…").strip() for n in author_part.split(",")]
    return [n for n in names if n and len(n) > 1]


def _extract_year(summary: str) -> str:
    match = re.search(r"\b(19|20)\d{2}\b", summary or "")
    return match.group(0) if match else ""


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def format_citations_rounded(total: int) -> str:
    """945 → '900+', 1089 → '1000+', 50 → '0+'."""
    return f"{(total // 100) * 100}+"


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------
def run_pipeline(checkpoint: dict) -> dict:
    # ── Step 1: Fetch own profile ─────────────────────────────────────
    if not checkpoint.get("articles"):
        print("📥 Fetching author profile …")
        profile_data = fetch_author_profile(SCHOLAR_ID)
        author_info = profile_data.get("author", {})
        cited_by_table = profile_data.get("cited_by", {}).get("table", [])

        total_citations = 0
        h_index = 0
        for row in cited_by_table:
            if "citations" in row:
                total_citations = row["citations"].get("all", 0)
            if "h_index" in row:
                h_index = row["h_index"].get("all", 0)

        checkpoint["profile"] = author_info
        checkpoint["h_index"] = h_index
        checkpoint["total_citations"] = total_citations
        checkpoint["articles"] = profile_data.get("articles", [])
        checkpoint["last_updated"] = datetime.now().isoformat()
        _save_json(CHECKPOINT_FILE, checkpoint)
        print(f"   → {author_info.get('name')}, h-index={h_index}, "
              f"citations={total_citations}, articles={len(checkpoint['articles'])}")
    else:
        print(f"✅ Profile cached ({len(checkpoint['articles'])} articles, "
              f"h-index={checkpoint.get('h_index')}, "
              f"citations={checkpoint.get('total_citations')})")

    # ── Step 2: Fetch citing papers (full pagination) ─────────────────
    processed_cites_ids: set[str] = set(checkpoint.get("processed_cites_ids", []))
    citing_papers_cache: dict[str, list] = checkpoint.get("citing_papers", {})
    cites_id_expected: dict[str, int] = checkpoint.get("cites_id_expected", {})

    articles_with_citations = [
        a for a in checkpoint["articles"]
        if a.get("cited_by", {}).get("cites_id")
    ]

    # Determine which cites_ids need (re-)fetching
    to_fetch: list[dict] = []
    for article in articles_with_citations:
        cites_id = article["cited_by"]["cites_id"]
        expected = article["cited_by"].get("value", 0)
        cites_id_expected[cites_id] = expected

        if cites_id not in processed_cites_ids:
            to_fetch.append(article)
        elif len(citing_papers_cache.get(cites_id, [])) < expected:
            # Previously incomplete — re-fetch
            print(f"   ↻ Re-fetching '{article.get('title', '?')[:50]}' "
                  f"(had {len(citing_papers_cache.get(cites_id, []))}/{expected})")
            processed_cites_ids.discard(cites_id)
            to_fetch.append(article)

    checkpoint["cites_id_expected"] = cites_id_expected

    total_scraped_before = sum(len(v) for v in citing_papers_cache.values())
    print(f"\n📄 {len(to_fetch)} articles to fetch "
          f"({total_scraped_before} papers already cached)")

    for article in tqdm(to_fetch, desc="Fetching citing papers"):
        cites_id = article["cited_by"]["cites_id"]
        expected = article["cited_by"].get("value", 0)
        title = article.get("title", "?")[:55]
        print(f"\n   → '{title}' (expected={expected})")

        existing = citing_papers_cache.get(cites_id, [])
        try:
            papers = fetch_citing_papers(cites_id, expected, existing=existing)
            citing_papers_cache[cites_id] = papers
            processed_cites_ids.add(cites_id)
            print(f"     Fetched {len(papers)} citing papers")
        except Exception as exc:
            print(f"     ⚠ Error: {exc}")
            continue

        checkpoint["citing_papers"] = citing_papers_cache
        checkpoint["processed_cites_ids"] = list(processed_cites_ids)
        _save_json(CHECKPOINT_FILE, checkpoint)

    # Summary of citing papers
    total_scraped = sum(len(v) for v in citing_papers_cache.values())
    total_expected = sum(cites_id_expected.values())
    print(f"\n   Total citing papers: {total_scraped} scraped / {total_expected} expected")

    # ── Step 3: Resolve author affiliations ───────────────────────────
    author_cache: dict[str, dict] = checkpoint.get("author_cache", {})

    # Collect unique author_ids not yet cached
    author_ids_to_fetch: dict[str, str] = {}
    for papers in citing_papers_cache.values():
        for paper in papers:
            for author in paper.get("publication_info", {}).get("authors", []):
                aid = author.get("author_id", "")
                aname = author.get("name", "")
                if aid and aid not in author_cache and aid not in author_ids_to_fetch:
                    author_ids_to_fetch[aid] = aname

    print(f"\n👤 {len(author_ids_to_fetch)} new authors to look up "
          f"({len(author_cache)} already cached)")

    fetched = 0
    for aid, aname in tqdm(author_ids_to_fetch.items(), desc="Resolving authors"):
        result = fetch_author_affiliation(aid)
        author_cache[aid] = result if result else {"name": aname, "affiliations": ""}
        fetched += 1
        if fetched % 25 == 0:
            checkpoint["author_cache"] = author_cache
            _save_json(CHECKPOINT_FILE, checkpoint)

    checkpoint["author_cache"] = author_cache
    _save_json(CHECKPOINT_FILE, checkpoint)

    # ── Step 4: Build institutions + hierarchy ────────────────────────
    print("\n🏛  Building institution map …")
    institutions: dict[str, dict] = {}
    publications_hierarchy: list[dict] = []
    all_author_names: set[str] = set()

    for article in checkpoint["articles"]:
        cites_id = article.get("cited_by", {}).get("cites_id", "")
        pub_entry = {
            "title": article.get("title", ""),
            "year": article.get("year", ""),
            "publication": article.get("publication", ""),
            "num_citations": article.get("cited_by", {}).get("value", 0),
            "citing_papers": [],
        }

        for paper in citing_papers_cache.get(cites_id, []):
            pub_info = paper.get("publication_info", {})
            authors_in_paper = pub_info.get("authors", [])
            summary = pub_info.get("summary", "")
            summary_authors = _parse_summary_authors(summary)

            citing_entry = {
                "title": paper.get("title", ""),
                "year": _extract_year(summary),
                "authors": [],
            }

            processed_names: set[str] = set()
            for author in authors_in_paper:
                aid = author.get("author_id", "")
                aname = author.get("name", "")
                cached = author_cache.get(aid, {})
                full_name = cached.get("name", aname) if cached else aname
                affiliation_str = cached.get("affiliations", "") if cached else ""
                institution = parse_institution(affiliation_str)

                author_entry = {"name": full_name, "institutions": []}
                all_author_names.add(full_name)
                processed_names.add(aname)

                if institution:
                    author_entry["institutions"].append({"name": institution})
                    if institution not in institutions:
                        institutions[institution] = {"authors": [], "citation_count": 0}
                    if full_name not in institutions[institution]["authors"]:
                        institutions[institution]["authors"].append(full_name)
                    institutions[institution]["citation_count"] += 1

                citing_entry["authors"].append(author_entry)

            # Add summary-only authors
            for sa_name in summary_authors:
                if sa_name not in processed_names:
                    all_author_names.add(sa_name)
                    citing_entry["authors"].append({
                        "name": sa_name,
                        "institutions": [],
                    })

            pub_entry["citing_papers"].append(citing_entry)
        publications_hierarchy.append(pub_entry)

    checkpoint["institutions"] = {
        k: {"authors": v["authors"], "citation_count": v["citation_count"]}
        for k, v in institutions.items()
    }
    checkpoint["publications_hierarchy"] = publications_hierarchy
    checkpoint["all_author_count"] = len(all_author_names)
    _save_json(CHECKPOINT_FILE, checkpoint)

    print(f"   → {len(institutions)} institutions, "
          f"{len(all_author_names)} unique citing authors")

    return checkpoint


# ---------------------------------------------------------------------------
# Folium map
# ---------------------------------------------------------------------------
def generate_map(checkpoint: dict, geocode_cache: dict) -> tuple[int, dict]:
    print("\n🗺  Generating citation map …")
    institutions = checkpoint.get("institutions", {})

    m = folium.Map(location=[20, 0], zoom_start=2, tiles="CartoDB positron")
    cluster = MarkerCluster(
        options={
            "maxClusterRadius": 50,
            "spiderfyOnMaxZoom": True,
            "showCoverageOnHover": True,
            "zoomToBoundsOnClick": True,
        }
    ).add_to(m)

    geocoded_count = 0
    institution_coords: dict[str, dict] = {}

    for inst_name, data in tqdm(institutions.items(), desc="Geocoding"):
        coords = geocode_institution(inst_name, geocode_cache)
        if not coords:
            continue

        geocoded_count += 1
        institution_coords[inst_name] = coords

        popup_html = f"""
        <div style="min-width:220px;font-family:system-ui,sans-serif">
          <h4 style="margin:0 0 8px;color:#494e52">{inst_name}</h4>
          <p style="margin:4px 0"><b>Citations:</b> {data.get('citation_count', 0)}</p>
          <p style="margin:4px 0"><b>Location:</b> {coords.get('city', '')}, {coords.get('country', '')}</p>
        </div>
        """
        folium.Marker(
            location=[coords["lat"], coords["lon"]],
            popup=folium.Popup(popup_html, max_width=320),
            icon=folium.Icon(color="purple", icon="graduation-cap", prefix="fa"),
        ).add_to(cluster)

    # Title overlay
    h_index = checkpoint.get("h_index", 0)
    total = checkpoint.get("total_citations", 0)
    n_countries = len({
        c["country"] for c in institution_coords.values() if c.get("country")
    })
    title_html = f"""
    <div style="position:fixed;top:10px;left:50px;width:320px;
                background:#fff;border-radius:10px;padding:15px 20px;
                box-shadow:0 2px 12px rgba(0,0,0,.12);z-index:9999;
                font-family:system-ui,sans-serif">
      <h3 style="margin:0 0 6px;color:#494e52">Citation Map</h3>
      <p style="margin:0;font-size:.85em;color:#718096">
        {format_citations_rounded(total)} citations · h-index {h_index} ·
        {geocoded_count} institutions · {n_countries} countries
      </p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    m.save(str(MAP_OUTPUT_FILE))
    print(f"   → Map saved: {MAP_OUTPUT_FILE}")
    return geocoded_count, institution_coords


# ---------------------------------------------------------------------------
# Data JSON for Impact page
# ---------------------------------------------------------------------------
def generate_data_json(checkpoint: dict, institution_coords: dict) -> dict:
    institutions = checkpoint.get("institutions", {})
    total_citations = checkpoint.get("total_citations", 0)

    # All unique authors: from author_cache + summary-parsed
    all_authors_count = checkpoint.get("all_author_count", 0)

    countries: set[str] = set()
    institution_list: list[dict] = []

    for name, data in institutions.items():
        coords = institution_coords.get(name)
        country = coords["country"] if coords else ""
        city = coords.get("city", "") if coords else ""
        if country:
            countries.add(country)

        institution_list.append({
            "name": name,
            "citation_count": data.get("citation_count", 0),
            "author_count": len(data.get("authors", [])),
            "lat": coords["lat"] if coords else None,
            "lon": coords["lon"] if coords else None,
            "city": city,
            "country": country,
            "location": coords.get("address", "") if coords else "",
        })

    institution_list.sort(key=lambda x: x["citation_count"], reverse=True)

    output = {
        "total_citations": total_citations,
        "total_citations_display": format_citations_rounded(total_citations),
        "h_index": checkpoint.get("h_index", 0),
        "unique_authors": all_authors_count,
        "unique_institutions": len(institutions),
        "countries": len(countries),
        "country_list": sorted(countries),
        "institutions": institution_list[:50],
        "last_updated": datetime.now().isoformat(),
    }

    _save_json(DATA_OUTPUT_FILE, output)
    print(f"   → Data JSON saved: {DATA_OUTPUT_FILE}")
    return output


# ---------------------------------------------------------------------------
# Hierarchy JSON
# ---------------------------------------------------------------------------
def generate_hierarchy_json(checkpoint: dict, institution_coords: dict):
    pub_hierarchy = checkpoint.get("publications_hierarchy", [])
    for pub in pub_hierarchy:
        for citing in pub.get("citing_papers", []):
            for author in citing.get("authors", []):
                for inst in author.get("institutions", []):
                    coords = institution_coords.get(inst["name"])
                    if coords:
                        inst["city"] = coords.get("city", "")
                        inst["country"] = coords.get("country", "")
                        inst["lat"] = coords.get("lat")
                        inst["lon"] = coords.get("lon")

    _save_json(HIERARCHY_OUTPUT_FILE, {
        "scholar_id": SCHOLAR_ID,
        "last_updated": datetime.now().isoformat(),
        "publications": pub_hierarchy,
    })
    print(f"   → Hierarchy JSON saved: {HIERARCHY_OUTPUT_FILE}")


# ---------------------------------------------------------------------------
# Graph JSON (D3 force-directed)
# ---------------------------------------------------------------------------
def generate_graph_json(checkpoint: dict, institution_coords: dict) -> dict:
    nodes: dict[str, dict] = {}
    links: list[dict] = []

    def add_node(nid: str, label: str, ntype: str, **extra):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": label, "type": ntype, **extra}

    for pub in checkpoint.get("publications_hierarchy", []):
        pub_id = f"pub_{_md5(pub['title'])}"
        add_node(pub_id, pub["title"], "my_paper", year=pub.get("year", ""))

        for citing in pub.get("citing_papers", []):
            c_id = f"cite_{_md5(citing['title'])}"
            add_node(c_id, citing["title"], "citing_paper",
                     year=citing.get("year", ""))
            links.append({"source": c_id, "target": pub_id, "type": "cites"})

            for author in citing.get("authors", []):
                a_id = f"author_{_md5(author['name'])}"
                add_node(a_id, author["name"], "author")
                links.append({"source": a_id, "target": c_id, "type": "authored"})

                for inst in author.get("institutions", []):
                    i_id = f"inst_{_md5(inst['name'])}"
                    coords = institution_coords.get(inst["name"])
                    country = (coords.get("country", "") if coords
                               else inst.get("country", ""))
                    city = (coords.get("city", "") if coords
                            else inst.get("city", ""))
                    add_node(i_id, inst["name"], "institution",
                             city=city, country=country)
                    links.append({"source": a_id, "target": i_id,
                                  "type": "affiliated"})

                    if country:
                        co_id = f"country_{_md5(country)}"
                        add_node(co_id, country, "country")
                        links.append({"source": i_id, "target": co_id,
                                      "type": "located_in"})

    graph = {
        "nodes": list(nodes.values()),
        "links": links,
        "last_updated": datetime.now().isoformat(),
    }
    _save_json(GRAPH_OUTPUT_FILE, graph)
    print(f"   → Graph JSON saved: {GRAPH_OUTPUT_FILE}")
    return graph


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("  Citation Map Generator (SerpAPI + Nominatim)")
    print("=" * 60)

    checkpoint = _load_json(CHECKPOINT_FILE, _empty_checkpoint())
    geocode_cache = _load_json(GEOCODE_CACHE_FILE, {})

    cached_authors = len(checkpoint.get("author_cache", {}))
    cached_cites = len(checkpoint.get("processed_cites_ids", []))
    cached_papers = sum(len(v) for v in checkpoint.get("citing_papers", {}).values())
    print(f"\n📦 Checkpoint: {cached_cites} cites_ids, "
          f"{cached_papers} citing papers, {cached_authors} authors\n")

    # --- Data gathering ---
    try:
        checkpoint = run_pipeline(checkpoint)
    except KeyboardInterrupt:
        print("\n⏸  Interrupted — saving checkpoint …")
        _save_json(CHECKPOINT_FILE, checkpoint)
        print("   Saved. Re-run to continue.")
        return

    # --- Outputs ---
    print("\n" + "-" * 60)
    print("  Generating outputs")
    print("-" * 60)

    geocoded_count, institution_coords = generate_map(checkpoint, geocode_cache)
    data = generate_data_json(checkpoint, institution_coords)
    generate_hierarchy_json(checkpoint, institution_coords)
    graph = generate_graph_json(checkpoint, institution_coords)

    # --- Summary ---
    print("\n" + "=" * 60)
    print("  Summary")
    print("=" * 60)
    total_scraped = sum(len(v) for v in checkpoint.get("citing_papers", {}).values())
    print(f"  Total citations  : {data['total_citations']} "
          f"({data['total_citations_display']})")
    print(f"  Papers scraped   : {total_scraped}")
    print(f"  h-index          : {data['h_index']}")
    print(f"  Citing authors   : {data['unique_authors']}")
    print(f"  Institutions     : {data['unique_institutions']}")
    print(f"  Countries        : {data['countries']}")
    print(f"  Geocoded on map  : {geocoded_count}")
    print(f"  Graph nodes      : {len(graph['nodes'])}")
    print(f"  Graph links      : {len(graph['links'])}")
    print(f"\n  Files:")
    print(f"    {MAP_OUTPUT_FILE}")
    print(f"    {DATA_OUTPUT_FILE}")
    print(f"    {HIERARCHY_OUTPUT_FILE}")
    print(f"    {GRAPH_OUTPUT_FILE}")
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
