import argparse
from rdflib import Graph

import fuseki_schemaorg as fuseki
from enrich_openalex import enrich_one_publication_openalex

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fuseki-query", required=True)
    ap.add_argument("--out-nt", required=True)
    ap.add_argument("--openalex-key", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.8)
    args = ap.parse_args()

    pubs = fuseki.fetch_publications(args.fuseki_query, limit=args.limit)

    all_new = Graph()
    enriched_pubs = 0

    for row in pubs:
        g = enrich_one_publication_openalex(
            args.fuseki_query,
            row,
            api_key=args.openalex_key,
            sleep_s=args.sleep,
        )
        if len(g) > 0:
            enriched_pubs += 1
            for t in g:
                all_new.add(t)

    all_new.serialize(destination=args.out_nt, format="nt")
    print(f"New triples: {len(all_new)}")
    print(f"Publications with any enrichment: {enriched_pubs}")
    print(f"Wrote: {args.out_nt}")

if __name__ == "__main__":
    main()
