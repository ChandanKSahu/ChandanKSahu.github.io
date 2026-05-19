#!/usr/bin/env python3
"""Quick test to understand SerpAPI response shapes."""
import json
import requests

API_KEY = "fb0d42b9e5f051c0f2eaea1bfd1d7ea80553576a65201f0f37c9b832674c9145"
AUTHOR_ID = "nfZ5Jc0AAAAJ"

def test_author_profile():
    """Test google_scholar_author endpoint."""
    params = {
        "engine": "google_scholar_author",
        "author_id": AUTHOR_ID,
        "api_key": API_KEY,
        "num": 100,
    }
    resp = requests.get("https://serpapi.com/search.json", params=params)
    data = resp.json()
    with open("citation_map/test_author_response.json", "w") as f:
        json.dump(data, f, indent=2)
    print(f"Author keys: {list(data.keys())}")
    print(f"Author name: {data.get('author', {}).get('name')}")
    print(f"Cited by: {data.get('cited_by', {})}")
    articles = data.get("articles", [])
    print(f"Articles count: {len(articles)}")
    if articles:
        print(f"First article keys: {list(articles[0].keys())}")
        print(f"First article: {json.dumps(articles[0], indent=2)}")
    return data

def test_citing_papers(cites_id):
    """Test google_scholar endpoint with cites param."""
    params = {
        "engine": "google_scholar",
        "cites": cites_id,
        "api_key": API_KEY,
        "num": 5,
    }
    resp = requests.get("https://serpapi.com/search.json", params=params)
    data = resp.json()
    with open("citation_map/test_citations_response.json", "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nCitations keys: {list(data.keys())}")
    results = data.get("organic_results", [])
    print(f"Organic results count: {len(results)}")
    if results:
        print(f"First result keys: {list(results[0].keys())}")
        print(f"First result: {json.dumps(results[0], indent=2)}")
    return data

def test_author_lookup(author_id):
    """Look up a specific author by their scholar ID."""
    params = {
        "engine": "google_scholar_author",
        "author_id": author_id,
        "api_key": API_KEY,
    }
    resp = requests.get("https://serpapi.com/search.json", params=params)
    data = resp.json()
    with open("citation_map/test_author_lookup.json", "w") as f:
        json.dump(data, f, indent=2)
    author = data.get("author", {})
    print(f"\nAuthor lookup: {author.get('name')} - {author.get('affiliations')}")
    return data

if __name__ == "__main__":
    data = test_author_profile()
    
    # Find first article with citations
    for article in data.get("articles", []):
        cited_by = article.get("cited_by", {})
        cites_id = cited_by.get("cites_id")
        if cites_id:
            print(f"\nTesting citations for: {article.get('title')}")
            cdata = test_citing_papers(cites_id)
            
            # Try looking up an author from the citing paper
            results = cdata.get("organic_results", [])
            if results:
                inline_links = results[0].get("inline_links", {})
                cited_by_info = inline_links.get("cited_by", {}) if inline_links else {}
                print(f"Inline links: {json.dumps(inline_links, indent=2)}")
                
                # Check for author_id in the result
                resources = results[0].get("resources", [])
                print(f"Resources: {resources}")
            break
