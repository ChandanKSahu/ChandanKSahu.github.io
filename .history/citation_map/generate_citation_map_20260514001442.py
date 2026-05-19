#!/usr/bin/env python3
"""
Citation Map Generator using SerpAPI for Google Scholar data.

Workflow:
  1. Fetch author profile via SerpAPI google_scholar_author endpoint
  2. For each publication, fetch citing papers via google_scholar cites endpoint
  3. For each citing paper, resolve author affiliations via google_scholar_author
  4. Geocode institutions → lat/lon
  5. Generate Folium citation map + summary JSON

Checkpoint-based: every batch of API calls is persisted so re-runs
skip already-fetched data, minimising SerpAPI credit usage.

Usage:
    conda run -n web python citation_map/generate_citation_map.py
"""

from __future__ import annotations

import json
import hashlib
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import folium
import requests
from folium.plugins import MarkerCluster
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCHOLAR_ID = "nfZ5Jc0AAAAJ"

_API_KEY_PATH = Path("/home/chandan/API Keys/serp_api.txt")
SERPAPI_KEY: str = _API_KEY_PATH.read_text().strip() if _API_KEY_PATH.exists() else ""
if not SERPAPI_KEY:
    print("ERROR: SerpAPI key not found at", _API_KEY_PATH)
    sys.exit(1)

SERPAPI_BASE = "https://serpapi.com/search.json"

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

GEOCODE_DELAY = 1.0  # seconds between Nominatim requests
SERP_DELAY = 0.5     # polite delay between SerpAPI calls
MAX_CITING_PAGES = 5  # max pages of citing papers per publication (10 results/page)


# ---------------------------------------------------------------------------
# Persistence helpers
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
        "articles": [],               # raw articles from profile
        "citing_papers": {},           # cites_id -> [citing paper dicts]
        "author_cache": {},            # author_id -> {name, affiliations}
        "processed_cites_ids": [],     # cites_ids already fully fetched
        "institutions": {},            # inst_name -> {authors:[], citation_count}
        "publications_hierarchy": [],  # enriched hierarchy for output
        "last_updated": None,
    }


# ---------------------------------------------------------------------------
# SerpAPI wrappers
# ---------------------------------------------------------------------------
def _serpapi_get(params: dict) -> dict:
    """Make a SerpAPI request with the global key, returning JSON."""
    params["api_key"] = SERPAPI_KEY
    time.sleep(SERP_DELAY)
    resp = requests.get(SERPAPI_BASE, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_author_profile(scholar_id: str) -> dict:
    """Fetch the full author profile (articles, cited_by, co_authors)."""
    data = _serpapi_get({
        "engine": "google_scholar_author",
        "author_id": scholar_id,
        "hl": "en",
        "num": 100,
    })
    return data


def fetch_citing_papers(cites_id: str, max_pages: int = MAX_CITING_PAGES) -> list[dict]:
    """Fetch all citing papers for a given cites_id, paginated."""
    all_results: list[dict] = []
    start = 0
    for _ in range(max_pages):
        data = _serpapi_get({
            "engine": "google_scholar",
            "cites": cites_id,
            "hl": "en",
            "num": 20,
            "start": start,
        })
        results = data.get("organic_results", [])
        if not results:
            break
        all_results.extend(results)
        # Check for next page
        pagination = data.get("serpapi_pagination", {})
        if "next" not in pagination:
            break
        start += 20
    return all_results


def fetch_author_affiliation(author_id: str) -> dict | None:
    """Fetch an author's profile to extract name + affiliations."""
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
# Institution / affiliation parsing
# ---------------------------------------------------------------------------
def parse_institution(affiliations_str: str) -> str:
    """Extract the primary institution name from an affiliations string.

    Typical formats:
      "Professor, Stanford University"
      "Department of CS, MIT"
      "University of Texas at Austin"
    """
    if not affiliations_str:
        return ""
    # Take the last comma-separated part (usually the institution)
    parts = [p.strip() for p in affiliations_str.split(",")]
    # Heuristic: walk from the end, pick the first chunk containing
    # university/institute/college/lab/school keywords
    keywords = ("university", "institute", "college", "école", "school",
                "polytechnic", "lab", "center", "centre", "academy",
                "research", "hospital", "corporation", "inc", "ltd",
                "department", "faculty", "national")
    for part in reversed(parts):
        if any(kw in part.lower() for kw in keywords):
            return part
    # Fallback: return the last part
    return parts[-1] if parts else ""


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------
def geocode_institution(name: str, cache: dict, geolocator) -> dict | None:
    """Geocode an institution name → {lat, lon, city, country, address}."""
    if name in cache:
        return cache[name]

    queries = [name]
    # If the name starts with a generic word, try without it
    stripped = name
    for prefix in ("Department of", "School of", "Faculty of"):
        if stripped.lower().startswith(prefix.lower()):
            stripped = stripped[len(prefix):].strip()
    if stripped != name:
        queries.append(stripped)

    for query in queries:
        try:
            time.sleep(GEOCODE_DELAY)
            loc = geolocator.geocode(query, timeout=10, addressdetails=True)
            if loc:
                addr = loc.raw.get("address", {})
                result = {
                    "lat": loc.latitude,
                    "lon": loc.longitude,
                    "address": loc.address,
                    "city": (addr.get("city") or addr.get("town")
                             or addr.get("state") or ""),
                    "country": addr.get("country", ""),
                }
                cache[name] = result
                _save_json(GEOCODE_CACHE_FILE, cache)
                return result
        except (GeocoderTimedOut, GeocoderServiceError) as exc:
            print(f"  ⚠ Geocode error for '{query}': {exc}")

    cache[name] = None
    _save_json(GEOCODE_CACHE_FILE, cache)
    return None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def format_citations_rounded(total: int) -> str:
    """945 → '900+', 1089 → '1000+', 50 → '0+'."""
    return f"{(total // 100) * 100}+"


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------
def run_pipeline(checkpoint: dict) -> dict:
    """Execute the full data-gathering pipeline with checkpoint resume."""

    # ------------------------------------------------------------------
    # Step 1: Fetch own profile
    # ------------------------------------------------------------------
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
        print(f"✅ Profile already cached ({len(checkpoint['articles'])} articles)")

    # ------------------------------------------------------------------
    # Step 2: For each article, fetch citing papers
    # ------------------------------------------------------------------
    processed_cites_ids: set[str] = set(checkpoint.get("processed_cites_ids", []))
    citing_papers_cache: dict[str, list] = checkpoint.get("citing_papers", {})

    articles_with_citations = [
        a for a in checkpoint["articles"]
        if a.get("cited_by", {}).get("cites_id")
    ]
    print(f"\n📄 {len(articles_with_citations)} articles have citations to fetch")

    for article in tqdm(articles_with_citations, desc="Fetching citing papers"):
        cites_id = article["cited_by"]["cites_id"]
        if cites_id in processed_cites_ids:
            continue

        title = article.get("title", "?")
        num_cited = article["cited_by"].get("value", 0)
        print(f"\n   → '{title}' ({num_cited} citations, cites_id={cites_id})")

        try:
            papers = fetch_citing_papers(cites_id)
            citing_papers_cache[cites_id] = papers
            processed_cites_ids.add(cites_id)
            print(f"     Fetched {len(papers)} citing papers")
        except Exception as exc:
            print(f"     ⚠ Error: {exc}")
            continue

        # Save after each article
        checkpoint["citing_papers"] = citing_papers_cache
        checkpoint["processed_cites_ids"] = list(processed_cites_ids)
        _save_json(CHECKPOINT_FILE, checkpoint)

    # ------------------------------------------------------------------
    # Step 3: Resolve author affiliations
    # ------------------------------------------------------------------
    author_cache: dict[str, dict] = checkpoint.get("author_cache", {})

    # Collect all unique author_ids from citing papers
    author_ids_to_fetch: dict[str, str] = {}  # author_id -> name
    for cites_id, papers in citing_papers_cache.items():
        for paper in papers:
            pub_info = paper.get("publication_info", {})
            authors_list = pub_info.get("authors", [])
            for author in authors_list:
                aid = author.get("author_id", "")
                aname = author.get("name", "")
                if aid and aid not in author_cache and aid not in author_ids_to_fetch:
                    author_ids_to_fetch[aid] = aname

    print(f"\n👤 {len(author_ids_to_fetch)} unique authors to look up "
          f"(already cached: {len(author_cache)})")

    fetched_count = 0
    for aid, aname in tqdm(author_ids_to_fetch.items(), desc="Resolving authors"):
        result = fetch_author_affiliation(aid)
        if result:
            author_cache[aid] = result
        else:
            # Cache miss so we don't retry
            author_cache[aid] = {"name": aname, "affiliations": ""}
        fetched_count += 1

        # Periodic checkpoint every 25 authors
        if fetched_count % 25 == 0:
            checkpoint["author_cache"] = author_cache
            _save_json(CHECKPOINT_FILE, checkpoint)

    checkpoint["author_cache"] = author_cache
    _save_json(CHECKPOINT_FILE, checkpoint)

    # ------------------------------------------------------------------
    # Step 4: Build institutions map + hierarchy
    # ------------------------------------------------------------------
    print("\n🏛  Building institution map …")
    institutions: dict[str, dict] = {}  # inst_name -> {authors: set, citation_count}
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

        papers = citing_papers_cache.get(cites_id, [])
        for paper in papers:
            pub_info = paper.get("publication_info", {})
            authors_in_paper = pub_info.get("authors", [])

            # Also parse author names from summary for authors without profiles
            summary = pub_info.get("summary", "")
            summary_authors = _parse_summary_authors(summary)

            citing_entry = {
                "title": paper.get("title", ""),
                "year": _extract_year_from_summary(summary),
                "authors": [],
            }

            # Process authors with Google Scholar profiles
            processed_names: set[str] = set()
            for author in authors_in_paper:
                aid = author.get("author_id", "")
                aname = author.get("name", "")
                cached = author_cache.get(aid, {})
                affiliation_str = cached.get("affiliations", "")
                institution = parse_institution(affiliation_str)

                author_entry = {
                    "name": cached.get("name", aname),
                    "institutions": [],
                }
                all_author_names.add(cached.get("name", aname))
                processed_names.add(aname)

                if institution:
                    author_entry["institutions"].append({"name": institution})
                    if institution not in institutions:
                        institutions[institution] = {
                            "authors": [],
                            "citation_count": 0,
                        }
                    full_name = cached.get("name", aname)
                    if full_name not in institutions[institution]["authors"]:
                        institutions[institution]["authors"].append(full_name)
                    institutions[institution]["citation_count"] += 1

                citing_entry["authors"].append(author_entry)

            # Add authors from summary that don't have profiles
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
    _save_json(CHECKPOINT_FILE, checkpoint)

    print(f"   → {len(institutions)} institutions, "
          f"{len(all_author_names)} unique authors")

    return checkpoint


def _parse_summary_authors(summary: str) -> list[str]:
    """Extract author names from a summary like 'A Smith, B Jones… - Journal, 2023'."""
    if not summary:
        return []
    # Split on " - " to separate authors from journal
    parts = summary.split(" - ")
    if not parts:
        return []
    author_part = parts[0]
    # Split by comma
    names = [n.strip().rstrip("…").strip() for n in author_part.split(",")]
    return [n for n in names if n and len(n) > 1]


def _extract_year_from_summary(summary: str) -> str:
    """Extract year from summary string like '... - Journal, 2023 - Publisher'."""
    import re
    match = re.search(r"\b(19|20)\d{2}\b", summary)
    return match.group(0) if match else ""


# ---------------------------------------------------------------------------
# Generate Folium map
# ---------------------------------------------------------------------------
def generate_map(checkpoint: dict, geocode_cache: dict) -> tuple[int, dict]:
    """Create the Folium MarkerCluster map."""
    print("\n🗺  Generating citation map …")
    geolocator = Nominatim(user_agent="citation_map_ck_sahu")
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

    for inst_name, data in tqdm(institutions.items(), desc="Geocoding institutions"):
        coords = geocode_institution(inst_name, geocode_cache, geolocator)
        if not coords:
            continue

        geocoded_count += 1
        institution_coords[inst_name] = coords

        authors_list = data.get("authors", [])
        authors_sample = authors_list[:5]
        authors_html = "<br>".join(f"• {a}" for a in authors_sample)
        if len(authors_list) > 5:
            authors_html += f"<br><i>… and {len(authors_list) - 5} more</i>"

        popup_html = f"""
        <div style="min-width:220px;font-family:system-ui,sans-serif">
          <h4 style="margin:0 0 8px;color:#494e52">{inst_name}</h4>
          <p style="margin:4px 0"><b>Citations:</b> {data.get('citation_count', 0)}</p>
          <p style="margin:4px 0"><b>Location:</b> {coords.get('city', '')}, {coords.get('country', '')}</p>
          <p style="margin:4px 0"><b>Authors ({len(authors_list)}):</b></p>
          <p style="margin:0;font-size:.85em">{authors_html}</p>
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
    title_html = f"""
    <div style="position:fixed;top:10px;left:50px;width:320px;
                background:#fff;border-radius:10px;padding:15px 20px;
                box-shadow:0 2px 12px rgba(0,0,0,.12);z-index:9999;
                font-family:system-ui,sans-serif">
      <h3 style="margin:0 0 6px;color:#494e52">Citation Map</h3>
      <p style="margin:0;font-size:.85em;color:#718096">
        {format_citations_rounded(total)} citations · h-index {h_index} ·
        {geocoded_count} institutions
      </p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    m.save(str(MAP_OUTPUT_FILE))
    print(f"   → Map saved: {MAP_OUTPUT_FILE}")
    return geocoded_count, institution_coords


# ---------------------------------------------------------------------------
# Generate data JSON (consumed by the Impact page)
# ---------------------------------------------------------------------------
def generate_data_json(checkpoint: dict, institution_coords: dict) -> dict:
    """Build the flat summary JSON for the recognition.html stats cards."""
    institutions = checkpoint.get("institutions", {})
    total_citations = checkpoint.get("total_citations", 0)

    # Collect unique authors across all institutions
    all_authors: set[str] = set()
    for data in institutions.values():
        all_authors.update(data.get("authors", []))

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
        "unique_authors": len(all_authors),
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
# Generate hierarchy JSON
# ---------------------------------------------------------------------------
def generate_hierarchy_json(checkpoint: dict, institution_coords: dict):
    """Save publications → citing papers → authors → institutions."""
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
# Generate graph JSON (D3 force-directed)
# ---------------------------------------------------------------------------
def generate_graph_json(checkpoint: dict, institution_coords: dict) -> dict:
    """Build nodes + links for a force-directed citation network."""
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
    print("  Citation Map Generator (SerpAPI)")
    print("=" * 60)

    checkpoint = _load_json(CHECKPOINT_FILE, _empty_checkpoint())
    geocode_cache = _load_json(GEOCODE_CACHE_FILE, {})

    cached_authors = len(checkpoint.get("author_cache", {}))
    cached_cites = len(checkpoint.get("processed_cites_ids", []))
    print(f"\n📦 Checkpoint: {cached_cites} cites_ids fetched, "
          f"{cached_authors} authors cached\n")

    # --- Data gathering ---
    try:
        checkpoint = run_pipeline(checkpoint)
    except KeyboardInterrupt:
        print("\n⏸  Interrupted — saving checkpoint …")
        _save_json(CHECKPOINT_FILE, checkpoint)
        print("   Saved. Re-run to continue.")
        return

    # --- Map generation ---
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
    print(f"  Total citations  : {data['total_citations']} "
          f"({data['total_citations_display']})")
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
