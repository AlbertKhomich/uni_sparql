import argparse
from rdflib import Graph

import fuseki
from enrich import enrich_one_paper
from rdfout import serialize_nt, make_insert_update_from_graph

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fuseki-query", required=True)
    ap.add_argument("--fuseki-update", required=False)
    ap.add_argument("--out-nt", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.3)
    args = ap.parse_args()

    papers = fuseki.fetch_papers(args.fuseki_query, limit=args.limit)

    all_new = Graph()
    matched = 0

    for p in papers:
        g = enrich_one_paper(args.fuseki_query, p, sleep_s=args.sleep)
        if len(g) > 0:
            matched += 1
            for t in g:
                all_new.add(t)

    serialize_nt(all_new, args.out_nt)
    print(f"New triples: {len(all_new)}")
    print(f"Papers with any enrichment: {matched}")
    print(f"Wrote: {args.out_nt}")

    if args.fuseki_update and len(all_new) > 0:
        upd = make_insert_update_from_graph(all_new)
        fuseki.sparql_update(args.fuseki_update, upd)
        print("Inserted into Fuseki.")

if __name__ == "__main__":
    main()