"""Export organization and geography triples for missing-aff.csv institutions.

Reuses the OpenAlex affiliation_geo_cache; no SPARQL endpoint is needed.
"""
import argparse
import csv
import os
from pathlib import Path
import time

from rdflib import Graph, Literal, Namespace, RDF, URIRef

from sblp_enrich.cli.main_openalex_authorship import ROOT, iri
from sblp_enrich.cli.main_openalex_affiliation_geo import (
    _extract_geo_fields, _fetch_or_cache_affiliation_payload,
)
from sblp_enrich.providers import openalex
from sblp_enrich.rdf.rdfout_schemaorg import add_affiliation_geo

SCHEMA = Namespace('https://schema.org/')


def read_rows(path):
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not {'institution', 'name', 'external_ids'} <= set(reader.fieldnames or []):
            raise ValueError('Input CSV needs institution,name,external_ids columns')
        rows = list(reader)
    for row in rows:
        row['institution'] = row['institution'].strip()
        iri(row['institution'])
        row['aliases'] = {v.strip() for v in row['external_ids'].split(';') if v.strip()}
        for alias in row['aliases']:
            iri(alias)
        candidates = {openalex.normalize_openalex_institution_id(value)
                      for value in row['aliases'] | {row['institution']}
                      if value.startswith(('https://openalex.org/', 'http://openalex.org/'))}
        candidates.discard(None)
        if len(candidates) > 1:
            raise ValueError(f"Conflicting OpenAlex IDs for {row['institution']}")
        row['openalex_id'] = next(iter(candidates), '')
    return rows


def add_institution(graph, row, institution):
    subject = URIRef(row['institution'])
    graph.add((subject, RDF.type, SCHEMA.Organization))
    name = institution.get('display_name') or row['name']
    if name:
        graph.add((subject, SCHEMA.name, Literal(name)))
    aliases = row['aliases'] | {institution.get('id'), institution.get('ror')}
    for alias in aliases - {None, '', row['institution']}:
        iri(alias)
        graph.add((subject, SCHEMA.sameAs, URIRef(alias)))
    city, country, code, lat, lon, continent = _extract_geo_fields(institution)
    add_affiliation_geo(graph, row['institution'], city=city, country=country,
                        country_code=code, latitude=lat, longitude=lon, continent=continent)
    if code:
        graph.add((subject, SCHEMA.addressCountry, Literal(code)))


def export(rows, api_key, *, sleep=0.8, cache_only=False):
    graph, reports = Graph(), []
    for row in rows:
        org, aid = row['institution'], row['openalex_id']
        if cache_only:
            payload = openalex.get_cached_affiliation_geo_payload(org)
            cached_id = openalex.normalize_openalex_institution_id(
                (payload or {}).get('openalex_sameas') or '')
            if aid and cached_id and aid != cached_id:
                payload = None
            hit = payload is not None
        else:
            payload, hit = _fetch_or_cache_affiliation_payload(org, aid, api_key)
            if not hit and sleep:
                time.sleep(sleep)
        status = (payload or {}).get('status', 'cache_miss')
        institution = (payload or {}).get('institution')
        if not institution and isinstance(payload, dict) and payload.get('id'):
            institution, status = payload, 'ok'
        if status == 'ok':
            if not isinstance(institution, dict) or not institution.get('id'):
                raise ValueError(f'Malformed institution cache payload for {org}')
            add_institution(graph, row, institution)
        reports.append({'institution': org, 'status': status, 'cache_hit': hit})
    return graph, reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-csv', type=Path, default=ROOT / 'data/authorship/missing-aff.csv')
    parser.add_argument('--out-nt', type=Path, default=ROOT / 'data/authorship/missing-aff.nt')
    parser.add_argument('--report', type=Path, help='Defaults to output path with .report.csv suffix')
    parser.add_argument('--openalex-key', default=os.getenv('OPENALEX_API_KEY', ''))
    parser.add_argument('--cache-only', action='store_true', help='Use cached responses without API calls')
    parser.add_argument('--sleep', type=float, default=0.8)
    args = parser.parse_args()
    if args.sleep < 0:
        parser.error('--sleep must be nonnegative')
    if not args.cache_only and not args.openalex_key:
        parser.error('Provide --openalex-key or OPENALEX_API_KEY, or use --cache-only')
    report = args.report or args.out_nt.with_suffix('.report.csv')
    inputs = {args.input_csv.resolve(), Path(openalex.DB_PATH).resolve()}
    outputs = {args.out_nt.resolve(), report.resolve()}
    if len(outputs) != 2 or inputs & outputs:
        parser.error('Outputs must differ from each other, input CSV, and cache database')
    rows = read_rows(args.input_csv)
    graph, reports = export(rows, args.openalex_key, sleep=args.sleep, cache_only=args.cache_only)
    args.out_nt.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(args.out_nt), format='nt')
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['institution', 'status', 'cache_hit'])
        writer.writeheader()
        writer.writerows(reports)
    enriched = sum(r['status'] == 'ok' for r in reports)
    print(f'Exported {enriched}/{len(rows)} institutions, {len(graph)} triples to {args.out_nt}')
    print(f'Cache: {openalex.DB_PATH} (affiliation_geo_cache)')
    print(f'Report: {report}')


if __name__ == '__main__':
    main()
