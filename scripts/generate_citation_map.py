#!/usr/bin/env python3
"""
Citation Map Generator for Google Scholar Profile

This script:
1. Scrapes citations from Google Scholar profile
2. Extracts author affiliations from citing papers
3. Geocodes institutions to get coordinates
4. Generates an interactive Folium map
5. Saves checkpoint data to resume if interrupted

Usage:
    python generate_citation_map.py

Requirements:
    pip install scholarly folium geopy requests beautifulsoup4 tqdm
"""

import json
import os
import time
import hashlib
from pathlib import Path
from datetime import datetime
from collections import defaultdict

try:
    from scholarly import scholarly
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
    exit(1)

# Configuration
SCHOLAR_ID = "nfZ5Jc0AAAAJ"  # Your Google Scholar ID
OUTPUT_DIR = Path(__file__).parent.parent / "assets"
CHECKPOINT_FILE = Path(__file__).parent / "citation_checkpoint.json"
GEOCODE_CACHE_FILE = Path(__file__).parent / "geocode_cache.json"
MAP_OUTPUT_FILE = OUTPUT_DIR / "citation_map.html"
DATA_OUTPUT_FILE = OUTPUT_DIR / "citation_data.json"

# Rate limiting
SCHOLAR_DELAY = 2  # Seconds between Scholar requests
GEOCODE_DELAY = 1  # Seconds between geocoding requests


def load_checkpoint():
    """Load checkpoint data if exists."""
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, 'r') as f:
            return json.load(f)
    return {
        "processed_citations": [],
        "authors": {},
        "institutions": {},
        "last_updated": None
    }


def save_checkpoint(data):
    """Save checkpoint data."""
    data["last_updated"] = datetime.now().isoformat()
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def load_geocode_cache():
    """Load geocoding cache."""
    if GEOCODE_CACHE_FILE.exists():
        with open(GEOCODE_CACHE_FILE, 'r') as f:
            return json.load(f)
    return {}


def save_geocode_cache(cache):
    """Save geocoding cache."""
    with open(GEOCODE_CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)


def get_citation_hash(citation):
    """Generate unique hash for a citation."""
    title = citation.get('bib', {}).get('title', '')
    return hashlib.md5(title.encode()).hexdigest()


def geocode_institution(institution_name, cache, geolocator):
    """Geocode an institution name to coordinates."""
    if institution_name in cache:
        return cache[institution_name]
    
    # Clean up institution name for better geocoding
    clean_name = institution_name.replace("University of", "").strip()
    
    try:
        time.sleep(GEOCODE_DELAY)
        location = geolocator.geocode(institution_name, timeout=10)
        
        if location:
            result = {
                "lat": location.latitude,
                "lon": location.longitude,
                "address": location.address
            }
            cache[institution_name] = result
            save_geocode_cache(cache)
            return result
        
        # Try with cleaned name
        location = geolocator.geocode(clean_name, timeout=10)
        if location:
            result = {
                "lat": location.latitude,
                "lon": location.longitude,
                "address": location.address
            }
            cache[institution_name] = result
            save_geocode_cache(cache)
            return result
            
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        print(f"Geocoding error for {institution_name}: {e}")
    
    cache[institution_name] = None
    save_geocode_cache(cache)
    return None


def extract_affiliation(author_info):
    """Extract institution affiliation from author info."""
    if isinstance(author_info, dict):
        affiliation = author_info.get('affiliation', '')
        if affiliation:
            # Clean up common patterns
            affiliation = affiliation.split(',')[0].strip()
            return affiliation
    return None


def fetch_author_profile(author_id_or_name):
    """Fetch author profile from Google Scholar."""
    try:
        time.sleep(SCHOLAR_DELAY)
        search_query = scholarly.search_author(author_id_or_name)
        author = next(search_query, None)
        if author:
            return scholarly.fill(author)
    except Exception as e:
        print(f"Error fetching author {author_id_or_name}: {e}")
    return None


def process_citations(scholar_id, checkpoint):
    """Process all citations from Google Scholar profile."""
    print(f"Fetching profile for Scholar ID: {scholar_id}")
    
    try:
        author = scholarly.search_author_id(scholar_id)
        author = scholarly.fill(author, sections=['publications', 'basics'])
    except Exception as e:
        print(f"Error fetching profile: {e}")
        return checkpoint
    
    publications = author.get('publications', [])
    print(f"Found {len(publications)} publications")
    
    total_citations = 0
    processed_citations = set(checkpoint.get("processed_citations", []))
    authors_data = checkpoint.get("authors", {})
    institutions_data = checkpoint.get("institutions", {})
    
    for pub in tqdm(publications, desc="Processing publications"):
        try:
            time.sleep(SCHOLAR_DELAY)
            filled_pub = scholarly.fill(pub)
            
            # Get citations for this publication
            num_citations = filled_pub.get('num_citations', 0)
            total_citations += num_citations
            
            # Try to get citing papers (limited by Scholar)
            if num_citations > 0:
                try:
                    citations = scholarly.citedby(filled_pub)
                    
                    for i, citation in enumerate(citations):
                        if i >= 20:  # Limit per publication to avoid rate limiting
                            break
                            
                        citation_hash = get_citation_hash(citation)
                        if citation_hash in processed_citations:
                            continue
                        
                        time.sleep(SCHOLAR_DELAY)
                        
                        # Get authors of citing paper
                        bib = citation.get('bib', {})
                        citing_authors = bib.get('author', '').split(' and ')
                        
                        for author_name in citing_authors:
                            author_name = author_name.strip()
                            if not author_name or author_name in authors_data:
                                continue
                            
                            # Try to get author's affiliation
                            author_profile = fetch_author_profile(author_name)
                            if author_profile:
                                affiliation = extract_affiliation(author_profile)
                                if affiliation:
                                    authors_data[author_name] = {
                                        "affiliation": affiliation,
                                        "scholar_id": author_profile.get('scholar_id')
                                    }
                                    
                                    # Track institution
                                    if affiliation not in institutions_data:
                                        institutions_data[affiliation] = {
                                            "authors": [],
                                            "citation_count": 0
                                        }
                                    institutions_data[affiliation]["authors"].append(author_name)
                                    institutions_data[affiliation]["citation_count"] += 1
                        
                        processed_citations.add(citation_hash)
                        
                        # Save checkpoint periodically
                        if len(processed_citations) % 10 == 0:
                            checkpoint["processed_citations"] = list(processed_citations)
                            checkpoint["authors"] = authors_data
                            checkpoint["institutions"] = institutions_data
                            checkpoint["total_citations"] = total_citations
                            save_checkpoint(checkpoint)
                            
                except Exception as e:
                    print(f"Error processing citations: {e}")
                    continue
                    
        except Exception as e:
            print(f"Error processing publication: {e}")
            continue
    
    # Final save
    checkpoint["processed_citations"] = list(processed_citations)
    checkpoint["authors"] = authors_data
    checkpoint["institutions"] = institutions_data
    checkpoint["total_citations"] = total_citations
    save_checkpoint(checkpoint)
    
    return checkpoint


def generate_map(checkpoint, geocode_cache):
    """Generate Folium map from checkpoint data."""
    print("Generating citation map...")
    
    geolocator = Nominatim(user_agent="citation_map_generator")
    institutions = checkpoint.get("institutions", {})
    
    # Create map centered on world
    m = folium.Map(
        location=[20, 0],
        zoom_start=2,
        tiles='CartoDB positron'
    )
    
    # Add marker cluster
    marker_cluster = MarkerCluster().add_to(m)
    
    geocoded_count = 0
    institution_coords = {}
    
    for institution, data in tqdm(institutions.items(), desc="Geocoding institutions"):
        coords = geocode_institution(institution, geocode_cache, geolocator)
        
        if coords:
            geocoded_count += 1
            institution_coords[institution] = coords
            
            # Create popup content
            authors_list = data.get("authors", [])[:5]  # Show first 5 authors
            authors_html = "<br>".join(authors_list)
            if len(data.get("authors", [])) > 5:
                authors_html += f"<br>... and {len(data['authors']) - 5} more"
            
            popup_html = f"""
            <div style="min-width: 200px;">
                <h4 style="margin: 0 0 10px 0; color: #667eea;">{institution}</h4>
                <p style="margin: 5px 0;"><strong>Citations:</strong> {data.get('citation_count', 0)}</p>
                <p style="margin: 5px 0;"><strong>Authors:</strong></p>
                <p style="margin: 0; font-size: 0.9em;">{authors_html}</p>
            </div>
            """
            
            # Add marker
            folium.Marker(
                location=[coords["lat"], coords["lon"]],
                popup=folium.Popup(popup_html, max_width=300),
                icon=folium.Icon(
                    color='purple',
                    icon='graduation-cap',
                    prefix='fa'
                )
            ).add_to(marker_cluster)
    
    # Add title
    title_html = '''
    <div style="position: fixed; 
                top: 10px; left: 50px; width: 300px;
                background-color: white; 
                border-radius: 10px;
                padding: 15px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                z-index: 9999;">
        <h3 style="margin: 0 0 10px 0; color: #667eea;">Citation Map</h3>
        <p style="margin: 0; font-size: 0.9em; color: #666;">
            Institutions citing my research
        </p>
    </div>
    '''
    m.get_root().html.add_child(folium.Element(title_html))
    
    # Save map
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    m.save(str(MAP_OUTPUT_FILE))
    print(f"Map saved to {MAP_OUTPUT_FILE}")
    
    return geocoded_count, institution_coords


def generate_data_json(checkpoint, institution_coords):
    """Generate JSON data file for the recognition page."""
    authors = checkpoint.get("authors", {})
    institutions = checkpoint.get("institutions", {})
    
    # Count unique authors and countries
    unique_authors = len(authors)
    unique_institutions = len(institutions)
    
    # Extract countries from geocoded data
    countries = set()
    for inst, coords in institution_coords.items():
        if coords and coords.get("address"):
            # Extract country from address (usually last part)
            parts = coords["address"].split(",")
            if parts:
                countries.add(parts[-1].strip())
    
    # Prepare institution list sorted by citation count
    institution_list = []
    for name, data in institutions.items():
        coords = institution_coords.get(name)
        institution_list.append({
            "name": name,
            "citation_count": data.get("citation_count", 0),
            "author_count": len(data.get("authors", [])),
            "lat": coords["lat"] if coords else None,
            "lon": coords["lon"] if coords else None,
            "location": coords.get("address", "") if coords else ""
        })
    
    institution_list.sort(key=lambda x: x["citation_count"], reverse=True)
    
    data = {
        "total_citations": checkpoint.get("total_citations", 0),
        "unique_authors": unique_authors,
        "unique_institutions": unique_institutions,
        "countries": len(countries),
        "country_list": list(countries),
        "institutions": institution_list[:50],  # Top 50
        "last_updated": datetime.now().isoformat()
    }
    
    with open(DATA_OUTPUT_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"Data saved to {DATA_OUTPUT_FILE}")
    return data


def main():
    print("=" * 60)
    print("Citation Map Generator")
    print("=" * 60)
    
    # Load existing data
    checkpoint = load_checkpoint()
    geocode_cache = load_geocode_cache()
    
    print(f"\nCheckpoint loaded:")
    print(f"  - Processed citations: {len(checkpoint.get('processed_citations', []))}")
    print(f"  - Known authors: {len(checkpoint.get('authors', {}))}")
    print(f"  - Known institutions: {len(checkpoint.get('institutions', {}))}")
    
    # Process citations
    print("\n" + "-" * 60)
    print("Step 1: Processing Google Scholar citations")
    print("-" * 60)
    
    try:
        checkpoint = process_citations(SCHOLAR_ID, checkpoint)
    except KeyboardInterrupt:
        print("\nInterrupted! Saving checkpoint...")
        save_checkpoint(checkpoint)
        print("Checkpoint saved. Run again to continue.")
        return
    except Exception as e:
        print(f"Error during processing: {e}")
        save_checkpoint(checkpoint)
    
    # Generate map
    print("\n" + "-" * 60)
    print("Step 2: Generating citation map")
    print("-" * 60)
    
    geocoded_count, institution_coords = generate_map(checkpoint, geocode_cache)
    
    # Generate data JSON
    print("\n" + "-" * 60)
    print("Step 3: Generating data JSON")
    print("-" * 60)
    
    data = generate_data_json(checkpoint, institution_coords)
    
    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Total citations: {data.get('total_citations', 0)}")
    print(f"Unique citing authors: {data.get('unique_authors', 0)}")
    print(f"Unique institutions: {data.get('unique_institutions', 0)}")
    print(f"Countries: {data.get('countries', 0)}")
    print(f"Geocoded institutions: {geocoded_count}")
    print(f"\nFiles generated:")
    print(f"  - {MAP_OUTPUT_FILE}")
    print(f"  - {DATA_OUTPUT_FILE}")
    print("\nDone!")


if __name__ == "__main__":
    main()
