import argparse
import time
from typing import Any, Dict, Optional, Tuple

from rdflib import Graph

from sblp_enrich.country_continent import continent_from_country_code
from sblp_enrich.providers import fuseki_schemaorg as fuseki
from sblp_enrich.providers import openalex
from sblp_enrich.rdf.rdfout_schemaorg import add_affiliation_geo


def _extract_geo_fields(inst: Dict[str, Any]) -> Tuple[str, str, str, Optional[float], Optional[float], Optional[str]]:
    geo = inst.get("geo") or {}
    if not isinstance(geo, dict):
        geo = {}

    city = (geo.get("city") or "").strip()
    country = (geo.get("country") or "").strip()
    country_code = (geo.get("country_code") or inst.get("country_code") or "").strip().upper()
    latitude = geo.get("latitude")
    longitude = geo.get("longitude")
    continent = continent_from_country_code(country_code)
    return city, country, country_code, latitude, longitude, continent


def _fetch_or_cache_affiliation_payload(
    affiliation_uri: str,
    openalex_sameas: str,
    api_key: str,
) -> Tuple[Dict[str, Any], bool]:
    norm_id = openalex.normalize_openalex_institution_id(openalex_sameas or "")

    cached = openalex.get_cached_affiliation_geo_payload(affiliation_uri)
    if cached is not None:
        cached_norm_id = openalex.normalize_openalex_institution_id(
            str(cached.get("openalex_sameas") or "")
        )
        # If the affiliation's OpenAlex sameAs changed, refresh cache for this URI.
        if not (norm_id and cached_norm_id and norm_id != cached_norm_id):
            return cached, True

    if not norm_id:
        payload = {
            "status": "invalid_openalex_id",
            "openalex_sameas": (openalex_sameas or "").strip(),
        }
        openalex.set_cached_affiliation_geo_payload(affiliation_uri, payload)
        return payload, False

    inst = openalex.get_institution_by_openalex_id(norm_id, api_key=api_key)
    if inst is None:
        payload = {"status": "not_found", "openalex_sameas": norm_id}
        openalex.set_cached_affiliation_geo_payload(affiliation_uri, payload)
        return payload, False

    payload = {"status": "ok", "openalex_sameas": norm_id, "institution": inst}
    openalex.set_cached_affiliation_geo_payload(affiliation_uri, payload)
    return payload, False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fuseki-query", required=True)
    ap.add_argument("--out-nt", required=True)
    ap.add_argument("--openalex-key", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.8)
    ap.add_argument(
        "--include-already-enriched",
        action="store_true",
        help="Also fetch affiliations that already have some geo triples in the triplestore.",
    )
    args = ap.parse_args()

    aff_rows = fuseki.fetch_affiliations_with_openalex(
        args.fuseki_query,
        limit=args.limit,
        offset=args.offset,
        only_missing_geo=not args.include_already_enriched,
    )

    all_new = Graph()
    affiliations_processed = 0
    affiliations_enriched = 0
    cache_hits = 0
    api_calls = 0
    invalid_openalex_ids = 0
    not_found = 0

    for row in aff_rows:
        affiliation_uri = row["affiliation"]
        openalex_sameas = row.get("openalex_sameas") or ""
        affiliations_processed += 1

        payload, cache_hit = _fetch_or_cache_affiliation_payload(
            affiliation_uri=affiliation_uri,
            openalex_sameas=openalex_sameas,
            api_key=args.openalex_key,
        )
        if cache_hit:
            cache_hits += 1
        else:
            api_calls += 1
            if args.sleep > 0:
                time.sleep(args.sleep)

        status = (payload.get("status") or "").strip().lower()
        if status == "invalid_openalex_id":
            invalid_openalex_ids += 1
            continue
        if status == "not_found":
            not_found += 1
            continue

        inst = payload.get("institution")
        if not isinstance(inst, dict):
            if payload.get("id"):
                inst = payload
            else:
                continue

        city, country, country_code, latitude, longitude, continent = _extract_geo_fields(inst)
        wrote_any = add_affiliation_geo(
            all_new,
            affiliation_uri,
            city=city,
            country=country,
            country_code=country_code,
            latitude=latitude,
            longitude=longitude,
            continent=continent,
        )
        if wrote_any:
            affiliations_enriched += 1

    all_new.serialize(destination=args.out_nt, format="nt")
    print(f"Affiliations fetched from endpoint: {len(aff_rows)}")
    print(f"Affiliations processed: {affiliations_processed}")
    print(f"Affiliations with any geo enrichment: {affiliations_enriched}")
    print(f"Cache hits: {cache_hits}")
    print(f"OpenAlex API calls: {api_calls}")
    print(f"Invalid OpenAlex IDs: {invalid_openalex_ids}")
    print(f"OpenAlex not found: {not_found}")
    print(f"New triples: {len(all_new)}")
    print(f"Wrote: {args.out_nt}")


if __name__ == "__main__":
    main()
