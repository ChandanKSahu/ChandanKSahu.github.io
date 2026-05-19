#!/usr/bin/env python3
"""
Citation Map & Graph Generator for Google Scholar Profile

This script:
1. Scrapes citations from a Google Scholar profile
2. Builds a hierarchical data model:
   my papers → citing papers → authors → institutions → countries
3. Geocodes institutions to get coordinates, city, and country
4. Generates an interactive Folium map with MarkerCluster
5. Generates a D3.js force-directed graph JSON
6. Saves all data in structured JSON for the website

Usage:
    python generate_citation_map.py [--proxy-key YOUR_KEY]

Requirements:
    pip install scholarly folium geopy requests beautifulsoup4 tqdm
"""

import argparse
import json
import os
import sys
import time
import hashlib
from pathlib import Path
from datetime import datetime
from collections import defaultdict

try:
    from scholarly import scholarly, ProxyGenerator
    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut, GeocoderServiceError
    import folium
    from folium.plugins import MarkerCluster
    from tqdm import tqdm
    import requests
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip install scholarly folium geopy requests beautifulsoup4 tqdm")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCHOLAR_ID = "nfZ5Jc0AAAAJ"
SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
OUTPUT_DIR = ROOT_DIR / "assets"

CHECKPOINT_FILE = SCRIPT_DIR / "citation_checkpoint.json"
GEOCODE_CACHE_FILE = SCRIPT_DIR / "geocode_cache.json"
MAP_OUTPUT_FILE = OUTPUT_DIR / "citation_map.html"
DATA_OUTPUT_FILE = OUTPUT_DIR / "citation_data.json"
HIERARCHY_OUTPUT_FILE = OUTPUT_DIR / "citation_hierarchy.json"
GRAPH_OUTPUT_FILE = OUTPUT_DIR / "citation_graph.json"

SCHOLAR_DELAY = 2   # seconds between Scholar requests
GEOCODE_DELAY = 1   # seconds between geocoding requests
MAX_CITATIONS_PER_PUB = 20  # cap to avoid rate-limiting


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def format_citations_rounded(total: int) -> str:
    """Format total citations to the highest 100x multiplier below, with '+'.

    Examples:
        945  -> '900+'
        1089 -> '1000+'
        100  -> '100+'
        50   -> '0+'
    """
    return f"{(total // 100) * 100}+"


def _hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Checkpoint & cache persistence
# ---------------------------------------------------------------------------
def load_json(path: Path, default=None):
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return default if default is not None else {}


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _empty_checkpoint():
    return {
        "processed_citations": [],
        "authors": {},
        "institutions": {},
        "publications_hierarchy": [],
        "h_index": 0,
        "total_citations": 0,
        "last_updated": None,
    }


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------
def geocode_institution(name: str, cache: dict, geolocator) -> dict | None:
    """Geocode an institution; returns {lat, lon, address, city, country} or None."""
    if name in cache:
        return cache[name]

    for query in (name, name.replace("University of", "").strip()):
        try:
            time.sleep(GEOCODE_DELAY)
            loc = geolocator.geocode(query, timeout=10, addressdetails=True)
            if loc:
                addr = loc.raw.get("address", {})
                result = {
                    "lat": loc.latitude,
                    "lon": loc.longitude,
                    "address": loc.address,
                    "city": addr.get("city") or addr.get("town") or addr.get("state", ""),
                    "country": addr.get("country", ""),
                }
                cache[name] = result
                save_json(GEOCODE_CACHE_FILE, cache)
                return result
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            print(f"  Geocoding error for {name}: {e}")

    cache[name] = None
    save_json(GEOCODE_CACHE_FILE, cache)
    return None


# ---------------------------------------------------------------------------
# Scholar helpers
# ---------------------------------------------------------------------------
def extract_affiliation(author_info: dict) -> str | None:
    if isinstance(author_info, dict):
        aff = author_info.get("affiliation", "")
        if aff:
            return aff.split(",")[0].strip()
    return None


def fetch_author_profile(name: str, author_id: str = "") -> dict | None:
    """Fetch author profile from Google Scholar.

    Prefers author_id (direct page load) over name search.
    Abbreviated names like 'Y Luo' are too ambiguous for search_author()
    and almost always return wrong results or get rate-limited.
    """
    if author_id:
        try:
            time.sleep(SCHOLAR_DELAY)
            # Direct ID lookup — fast, accurate, includes affiliation
            profile = scholarly.search_author_id(author_id)
            if profile and profile.get("affiliation"):
                return profile
            # If basic profile lacks affiliation, try filling
            if profile:
                filled = scholarly.fill(profile, sections=["basics"])
                return filled
        except Exception as e:
            print(f"  Error fetching author '{name}' (id={author_id}): {e}")
        return None

    # Name-only search: only attempt for non-abbreviated names (≥2 word first name)
    parts = name.split()
    if len(parts) < 2 or len(parts[0]) <= 2:
        return None  # skip abbreviated names like "Y Luo", "MR Abidian"

    try:
        time.sleep(SCHOLAR_DELAY)
        results = scholarly.search_author(name)
        author = next(results, None)
        if author:
            return scholarly.fill(author, sections=["basics"])
    except Exception as e:
        print(f"  Error searching author '{name}': {e}")
    return None


# ---------------------------------------------------------------------------
# Core: process citations and build hierarchical data
# ---------------------------------------------------------------------------
def process_citations(scholar_id: str, checkpoint: dict) -> dict:
    """Iterate over every publication, every citing paper, every author.

    Builds:
      checkpoint["publications_hierarchy"] -- list of dicts:
        { title, year, venue, num_citations, citing_papers: [
            { title, year, authors: [
                { name, institutions: [ { name, city, country } ] }
            ] }
        ] }
      checkpoint["authors"]       -- flat dict  author_name -> {affiliation, scholar_id}
      checkpoint["institutions"]  -- flat dict  inst_name -> {authors:[], citation_count}
    """
    print(f"Fetching profile for Scholar ID: {scholar_id}")

    try:
        author = scholarly.search_author_id(scholar_id)
        author = scholarly.fill(author, sections=["publications", "basics", "indices"])
    except Exception as e:
        print(f"Error fetching profile: {e}")
        return checkpoint

    checkpoint["h_index"] = author.get("hindex", 0)
    print(f"h-index: {checkpoint['h_index']}")

    publications = author.get("publications", [])
    print(f"Found {len(publications)} publications")

    processed_hashes: set[str] = set(checkpoint.get("processed_citations", []))
    authors_data: dict = checkpoint.get("authors", {})
    institutions_data: dict = checkpoint.get("institutions", {})
    pub_hierarchy: list = checkpoint.get("publications_hierarchy", [])

    # Index existing hierarchy by title hash for resume-ability
    existing_pub_hashes = {_hash(p["title"]): idx for idx, p in enumerate(pub_hierarchy)}

    total_citations = 0

    for pub in tqdm(publications, desc="Processing publications"):
        try:
            time.sleep(SCHOLAR_DELAY)
            filled = scholarly.fill(pub)

            pub_title = filled.get("bib", {}).get("title", "Untitled")
            pub_year = filled.get("bib", {}).get("pub_year", "")
            pub_venue = filled.get("bib", {}).get("venue", "")
            num_citations = filled.get("num_citations", 0)
            total_citations += num_citations

            pub_hash = _hash(pub_title)
            if pub_hash in existing_pub_hashes:
                pub_entry = pub_hierarchy[existing_pub_hashes[pub_hash]]
            else:
                pub_entry = {
                    "title": pub_title,
                    "year": pub_year,
                    "venue": pub_venue,
                    "num_citations": num_citations,
                    "citing_papers": [],
                }
                pub_hierarchy.append(pub_entry)
                existing_pub_hashes[pub_hash] = len(pub_hierarchy) - 1

            # Update num_citations in case it changed
            pub_entry["num_citations"] = num_citations

            if num_citations == 0:
                continue

            try:
                citations_iter = scholarly.citedby(filled)

                for i, citation in enumerate(citations_iter):
                    if i >= MAX_CITATIONS_PER_PUB:
                        break

                    c_hash = _hash(citation.get("bib", {}).get("title", ""))
                    if c_hash in processed_hashes:
                        continue

                    time.sleep(SCHOLAR_DELAY)

                    bib = citation.get("bib", {})
                    c_title = bib.get("title", "")
                    c_year = bib.get("pub_year", "")
                    raw_authors = bib.get("author", [])
                    if isinstance(raw_authors, str):
                        c_author_names = [a.strip() for a in raw_authors.split(" and ") if a.strip()]
                    elif isinstance(raw_authors, list):
                        c_author_names = [a.strip() for a in raw_authors if isinstance(a, str) and a.strip()]
                    else:
                        c_author_names = []

                    # Extract author_id list (Scholar profile IDs) aligned with author names
                    raw_author_ids = citation.get("author_id", [])
                    if not isinstance(raw_author_ids, list):
                        raw_author_ids = []

                    citing_paper_entry = {
                        "title": c_title,
                        "year": c_year,
                        "authors": [],
                    }

                    for idx, author_name in enumerate(c_author_names):
                        author_entry = {"name": author_name, "institutions": []}

                        # Get the author's Scholar ID if available
                        author_gs_id = ""
                        if idx < len(raw_author_ids):
                            aid = raw_author_ids[idx]
                            if isinstance(aid, str) and aid.strip():
                                author_gs_id = aid.strip()

                        if author_name not in authors_data:
                            profile = fetch_author_profile(author_name, author_id=author_gs_id)
                            if profile:
                                aff = extract_affiliation(profile)
                                if aff:
                                    authors_data[author_name] = {
                                        "affiliation": aff,
                                        "scholar_id": profile.get("scholar_id", author_gs_id),
                                    }

                        if author_name in authors_data:
                            aff = authors_data[author_name]["affiliation"]
                            author_entry["institutions"].append({"name": aff})

                            if aff not in institutions_data:
                                institutions_data[aff] = {"authors": [], "citation_count": 0}
                            if author_name not in institutions_data[aff]["authors"]:
                                institutions_data[aff]["authors"].append(author_name)
                            institutions_data[aff]["citation_count"] += 1

                        citing_paper_entry["authors"].append(author_entry)

                    pub_entry["citing_papers"].append(citing_paper_entry)
                    processed_hashes.add(c_hash)

                    # Periodic checkpoint
                    if len(processed_hashes) % 10 == 0:
                        checkpoint.update({
                            "processed_citations": list(processed_hashes),
                            "authors": authors_data,
                            "institutions": institutions_data,
                            "publications_hierarchy": pub_hierarchy,
                            "total_citations": total_citations,
                        })
                        save_json(CHECKPOINT_FILE, checkpoint)

            except Exception as e:
                print(f"  Error iterating citations for '{pub_title}': {e}")
                continue

        except Exception as e:
            print(f"  Error processing publication: {e}")
            continue

    # Final save
    checkpoint.update({
        "processed_citations": list(processed_hashes),
        "authors": authors_data,
        "institutions": institutions_data,
        "publications_hierarchy": pub_hierarchy,
        "total_citations": total_citations,
    })
    save_json(CHECKPOINT_FILE, checkpoint)
    return checkpoint


# ---------------------------------------------------------------------------
# Generate Folium map
# ---------------------------------------------------------------------------
def generate_map(checkpoint: dict, geocode_cache: dict) -> tuple[int, dict]:
    print("Generating citation map...")
    geolocator = Nominatim(user_agent="citation_map_generator")
    institutions = checkpoint.get("institutions", {})

    m = folium.Map(location=[20, 0], zoom_start=2, tiles="CartoDB positron")
    cluster = MarkerCluster().add_to(m)

    geocoded_count = 0
    institution_coords: dict = {}

    for inst_name, data in tqdm(institutions.items(), desc="Geocoding institutions"):
        coords = geocode_institution(inst_name, geocode_cache, geolocator)
        if not coords:
            continue

        geocoded_count += 1
        institution_coords[inst_name] = coords

        authors_sample = data.get("authors", [])[:5]
        authors_html = "<br>".join(authors_sample)
        if len(data.get("authors", [])) > 5:
            authors_html += f"<br>… and {len(data['authors']) - 5} more"

        popup = f"""
        <div style="min-width:200px">
          <h4 style="margin:0 0 10px;color:#667eea">{inst_name}</h4>
          <p style="margin:5px 0"><b>Citations:</b> {data.get('citation_count',0)}</p>
          <p style="margin:5px 0"><b>Location:</b> {coords.get('city','')}, {coords.get('country','')}</p>
          <p style="margin:5px 0"><b>Authors:</b></p>
          <p style="margin:0;font-size:.9em">{authors_html}</p>
        </div>
        """
        folium.Marker(
            location=[coords["lat"], coords["lon"]],
            popup=folium.Popup(popup, max_width=300),
            icon=folium.Icon(color="purple", icon="graduation-cap", prefix="fa"),
        ).add_to(cluster)

    title_html = """
    <div style="position:fixed;top:10px;left:50px;width:300px;
                background:#fff;border-radius:10px;padding:15px;
                box-shadow:0 2px 10px rgba(0,0,0,.1);z-index:9999">
      <h3 style="margin:0 0 10px;color:#667eea">Citation Map</h3>
      <p style="margin:0;font-size:.9em;color:#666">Institutions citing my research</p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    m.save(str(MAP_OUTPUT_FILE))
    print(f"Map saved -> {MAP_OUTPUT_FILE}")

    return geocoded_count, institution_coords


# ---------------------------------------------------------------------------
# Generate flat summary JSON (consumed by recognition.html stats cards)
# ---------------------------------------------------------------------------
def generate_data_json(checkpoint: dict, institution_coords: dict) -> dict:
    authors = checkpoint.get("authors", {})
    institutions = checkpoint.get("institutions", {})
    total_citations = checkpoint.get("total_citations", 0)

    countries: set[str] = set()
    institution_list: list[dict] = []

    for name, data in institutions.items():
        coords = institution_coords.get(name)
        country = coords["country"] if coords else ""
        if country:
            countries.add(country)

        institution_list.append({
            "name": name,
            "citation_count": data.get("citation_count", 0),
            "author_count": len(data.get("authors", [])),
            "lat": coords["lat"] if coords else None,
            "lon": coords["lon"] if coords else None,
            "city": coords.get("city", "") if coords else "",
            "country": country,
            "location": coords.get("address", "") if coords else "",
        })

    institution_list.sort(key=lambda x: x["citation_count"], reverse=True)

    data = {
        "total_citations": total_citations,
        "total_citations_display": format_citations_rounded(total_citations),
        "h_index": checkpoint.get("h_index", 0),
        "unique_authors": len(authors),
        "unique_institutions": len(institutions),
        "countries": len(countries),
        "country_list": sorted(countries),
        "institutions": institution_list[:50],
        "last_updated": datetime.now().isoformat(),
    }

    save_json(DATA_OUTPUT_FILE, data)
    print(f"Data JSON saved -> {DATA_OUTPUT_FILE}")
    return data


# ---------------------------------------------------------------------------
# Generate hierarchical JSON
# ---------------------------------------------------------------------------
def generate_hierarchy_json(checkpoint: dict, institution_coords: dict):
    """Save the full hierarchy: my papers -> citing papers -> authors -> institutions -> countries."""
    pub_hierarchy = checkpoint.get("publications_hierarchy", [])

    # Enrich institution entries with city / country from geocode cache
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

    save_json(HIERARCHY_OUTPUT_FILE, {
        "scholar_id": SCHOLAR_ID,
        "last_updated": datetime.now().isoformat(),
        "publications": pub_hierarchy,
    })
    print(f"Hierarchy JSON saved -> {HIERARCHY_OUTPUT_FILE}")


# ---------------------------------------------------------------------------
# Generate D3.js graph JSON  (nodes + links)
# ---------------------------------------------------------------------------
def generate_graph_json(checkpoint: dict, institution_coords: dict):
    """Build a force-directed graph: my_paper -> citing_paper -> author -> institution -> country."""
    nodes: dict[str, dict] = {}   # id -> node dict
    links: list[dict] = []

    def add_node(nid: str, label: str, ntype: str, **extra):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": label, "type": ntype, **extra}

    pub_hierarchy = checkpoint.get("publications_hierarchy", [])

    for pub in pub_hierarchy:
        pub_id = f"pub_{_hash(pub['title'])}"
        add_node(pub_id, pub["title"], "my_paper", year=pub.get("year", ""))

        for citing in pub.get("citing_papers", []):
            c_id = f"cite_{_hash(citing['title'])}"
            add_node(c_id, citing["title"], "citing_paper", year=citing.get("year", ""))
            links.append({"source": c_id, "target": pub_id, "type": "cites"})

            for author in citing.get("authors", []):
                a_id = f"author_{_hash(author['name'])}"
                add_node(a_id, author["name"], "author")
                links.append({"source": a_id, "target": c_id, "type": "authored"})

                for inst in author.get("institutions", []):
                    i_id = f"inst_{_hash(inst['name'])}"
                    coords = institution_coords.get(inst["name"])
                    country = coords.get("country", "") if coords else inst.get("country", "")
                    city = coords.get("city", "") if coords else inst.get("city", "")
                    add_node(i_id, inst["name"], "institution", city=city, country=country)
                    links.append({"source": a_id, "target": i_id, "type": "affiliated"})

                    if country:
                        co_id = f"country_{_hash(country)}"
                        add_node(co_id, country, "country")
                        links.append({"source": i_id, "target": co_id, "type": "located_in"})

    graph = {
        "nodes": list(nodes.values()),
        "links": links,
        "last_updated": datetime.now().isoformat(),
    }

    save_json(GRAPH_OUTPUT_FILE, graph)
    print(f"Graph JSON saved -> {GRAPH_OUTPUT_FILE}")
    return graph


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Citation Map & Graph Generator")
    parser.add_argument("--proxy-key", help="ScraperAPI key for proxy (recommended)")
    args = parser.parse_args()

    # Set up proxy if provided (avoids Google Scholar blocking)
    if args.proxy_key:
        pg = ProxyGenerator()
        pg.ScraperAPI(args.proxy_key)
        scholarly.use_proxy(pg)
        print("Proxy configured via ScraperAPI.")

    print("=" * 60)
    print("Citation Map & Graph Generator")
    print("=" * 60)

    checkpoint = load_json(CHECKPOINT_FILE, _empty_checkpoint())
    geocode_cache = load_json(GEOCODE_CACHE_FILE, {})

    print(f"\nCheckpoint loaded:")
    print(f"  Processed citations : {len(checkpoint.get('processed_citations', []))}")
    print(f"  Known authors       : {len(checkpoint.get('authors', {}))}")
    print(f"  Known institutions  : {len(checkpoint.get('institutions', {}))}")
    print(f"  Publications tracked: {len(checkpoint.get('publications_hierarchy', []))}")

    # Step 1 -- Scrape Google Scholar
    print("\n" + "-" * 60)
    print("Step 1: Processing Google Scholar citations")
    print("-" * 60)

    try:
        checkpoint = process_citations(SCHOLAR_ID, checkpoint)
    except KeyboardInterrupt:
        print("\nInterrupted -- saving checkpoint ...")
        save_json(CHECKPOINT_FILE, checkpoint)
        print("Saved. Re-run to continue.")
        return
    except Exception as e:
        print(f"Error during processing: {e}")
        save_json(CHECKPOINT_FILE, checkpoint)

    # Step 2 -- Generate Folium map
    print("\n" + "-" * 60)
    print("Step 2: Generating citation map")
    print("-" * 60)

    geocoded_count, institution_coords = generate_map(checkpoint, geocode_cache)

    # Step 3 -- Flat summary JSON
    print("\n" + "-" * 60)
    print("Step 3: Generating summary JSON")
    print("-" * 60)

    data = generate_data_json(checkpoint, institution_coords)

    # Step 4 -- Hierarchical JSON
    print("\n" + "-" * 60)
    print("Step 4: Generating hierarchical JSON")
    print("-" * 60)

    generate_hierarchy_json(checkpoint, institution_coords)

    # Step 5 -- D3 graph JSON
    print("\n" + "-" * 60)
    print("Step 5: Generating D3 graph JSON")
    print("-" * 60)

    graph = generate_graph_json(checkpoint, institution_coords)

    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Total citations  : {data['total_citations']} ({data['total_citations_display']})")
    print(f"h-index          : {data['h_index']}")
    print(f"Citing authors   : {data['unique_authors']}")
    print(f"Institutions     : {data['unique_institutions']}")
    print(f"Countries        : {data['countries']}")
    print(f"Geocoded         : {geocoded_count}")
    print(f"Graph nodes      : {len(graph['nodes'])}")
    print(f"Graph links      : {len(graph['links'])}")
    print(f"\nFiles:")
    print(f"  {MAP_OUTPUT_FILE}")
    print(f"  {DATA_OUTPUT_FILE}")
    print(f"  {HIERARCHY_OUTPUT_FILE}")
    print(f"  {GRAPH_OUTPUT_FILE}")
    print("\nDone!")


if __name__ == "__main__":
    main()
