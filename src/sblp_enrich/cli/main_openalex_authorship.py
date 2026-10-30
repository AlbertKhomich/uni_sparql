"""Export paper-scoped authorships from the local OpenAlex SQLite cache only."""
from __future__ import annotations

import argparse
import csv
import itertools
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
import zlib

BASE = 'http://upbkg.data.dice-research.org/'
VOCAB = BASE + 'vocab/'
SCHEMA = 'https://schema.org/'
RDF_TYPE = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type'
PROV = 'http://www.w3.org/ns/prov#wasDerivedFrom'
XSD = 'http://www.w3.org/2001/XMLSchema#'
ROOT = Path(__file__).resolve().parents[3]


def title_key(title):
    # Use the same whitespace- and symbol-free key for input and cached titles.
    return ''.join(c for c in unicodedata.normalize('NFKC', title).casefold() if c.isalnum())


def iri(value):
    if not isinstance(value, str) or not re.match(r'^https?://[^/]+', value) or re.search(r'[\s<>"{}|^`\\\x00-\x20]', value):
        raise ValueError(f'Invalid HTTP(S) IRI: {value!r}')
    return f'<{value}>'


def literal(value, datatype=None):
    result = json.dumps(str(value), ensure_ascii=False)
    return result + ('^^' + iri(XSD + datatype) if datatype else '')


def cached_matches(db_path, titles):
    """Scan once; retain newest copy per work ID, reject ambiguous titles later."""
    matches = {title: {} for title in titles if title}
    with sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True) as db:
        for key, blob, timestamp in db.execute('SELECT k, v, t FROM work_cache ORDER BY t, k'):
            try:
                if isinstance(blob, bytes):
                    try:
                        blob = zlib.decompress(blob)
                    except zlib.error:
                        pass  # Also accept uncompressed cache records.
                work = json.loads(blob)
            except (ValueError, UnicodeError) as exc:
                raise ValueError(f'Cannot decode work_cache row {key!r}') from exc
            if not isinstance(work, dict) or not work.get('id'):
                continue
            for title in {title_key(work.get('title') or ''), title_key(work.get('display_name') or '')}:
                if title in matches:
                    matches[title][work['id']] = work
    return matches


def read_author_map(path):
    mapping = {}
    if path:
        with open(path, newline='', encoding='utf-8-sig') as stream:
            rows = csv.DictReader(stream)
            if not {'openalex_author', 'person'} <= set(rows.fieldnames or []):
                raise ValueError('Author map needs openalex_author,person columns')
            for row in rows:
                author, person = row['openalex_author'].strip(), row['person'].strip()
                iri(author)
                iri(person)
                if author in mapping and mapping[author] != person:
                    raise ValueError(f'Conflicting person mappings for {author}')
                mapping[author] = person
    return mapping


def name_parts(name):
    if ',' in name:
        surname, given = name.split(',', 1)
    else:
        parts = name.split()
        surname, given = (parts[-1], ' '.join(parts[:-1])) if parts else ('', '')
    def clean(value):
        value = unicodedata.normalize('NFKD', value.casefold())
        return ''.join(c for c in value if c.isalpha() and not unicodedata.combining(c))
    return clean(surname), tuple(clean(word) for word in given.split() if clean(word))


def names_match(left, right):
    ls, lg = name_parts(left)
    rs, rg = name_parts(right)
    return bool(ls and ls == rs and lg and len(lg) == len(rg) and all(
        a == b or (min(len(a), len(b)) == 1 and a[0] == b[0]) for a, b in zip(lg, rg)))


def load_people(path, people):
    records = {person: {'names': set(), 'ids': {person}} for person in people}
    if path:
        # Read only the relevant name / identity triples, without loading the KG.
        pattern = re.compile(r'^<([^>]+)>\s+<https?://schema.org/(name|sameAs)>\s+(.+?)\s*\.\s*$')
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                match = pattern.match(line)
                if not match or match[1] not in records:
                    continue
                obj = match[3]
                if match[2] == 'sameAs' and obj.startswith('<'):
                    records[match[1]]['ids'].add(obj[1:-1])
                elif match[2] == 'name' and obj.startswith('"'):
                    value, _ = json.JSONDecoder().raw_decode(obj)
                    records[match[1]]['names'].add(value)
    return records


def resolve_authors(work, people, records, explicit):
    mapping, reports, proposals = {}, [], []
    for entry in work.get('authorships') or []:
        author = entry.get('author') or {}
        aid = author.get('id')
        if not aid:
            reports.append(('', '', 'missing_author_id'))
            continue
        ids = {value for value in (aid, author.get('orcid')) if value}
        candidates = [person for person in people if ids & records[person]['ids'] or
                      (author.get('orcid') and person.endswith('/orcid/' + author['orcid'].rsplit('/', 1)[-1]))]
        method = 'identifier'
        if aid in explicit:
            candidates = [explicit[aid]] if explicit[aid] in people else []
            method = 'explicit_map'
        elif not candidates:
            candidates = [person for person in people if any(names_match(name, author.get('display_name') or '')
                          for name in records[person]['names'])]
            method = 'unique_name'
        proposals.append((aid, candidates, method))
    for aid, candidates, method in proposals:
        if len(candidates) == 1 and sum(candidates[0] in other for _, other, _ in proposals) == 1:
            mapping[aid] = candidates[0]
            reports.append((aid, candidates[0], method))
        else:
            reports.append((aid, ';'.join(candidates), 'ambiguous' if candidates else 'unresolved'))
    return mapping, reports


def work_triples(paper, work, author_map, *, mapped_only=False):
    triples = set()

    def add(s, p, o, *, text=False, datatype=None):
        triples.add(f'{iri(s)} {iri(p)} {literal(o, datatype) if text else iri(o)} .')

    add(paper, PROV, work['id'])
    # Keep source dates separate from existing KG dates, which can disagree.
    for index, entry in enumerate(work.get('authorships') or [], 1):
        author = entry.get('author') or {}
        author_id = author.get('id')
        if mapped_only and author_id not in author_map:
            continue
        person = author_map.get(author_id, author_id)
        author_tail = person.rstrip('/').rsplit('/', 1)[-1] if person else f'position-{index}'
        node = paper.rstrip('/') + '/authorship/' + author_tail
        add(paper, VOCAB + 'authorship', node)
        add(node, RDF_TYPE, VOCAB + 'Authorship')
        add(node, VOCAB + 'paper', paper)
        add(node, PROV, work['id'])
        if author_id:
            person = author_map.get(author_id, author_id)
            add(node, VOCAB + 'author', person)
            add(paper, SCHEMA + 'author', person)
            add(person, RDF_TYPE, SCHEMA + 'Person')
            if author.get('display_name'):
                add(person, SCHEMA + 'name', author['display_name'], text=True)
        add(node, VOCAB + 'authorOrder', index, text=True, datatype='integer')
        if entry.get('author_position'):
            add(node, VOCAB + 'authorPosition', entry['author_position'], text=True)
        if isinstance(entry.get('is_corresponding'), bool):
            add(node, VOCAB + 'isCorresponding', str(entry['is_corresponding']).lower(), text=True, datatype='boolean')
        if work.get('publication_date'):
            add(node, VOCAB + 'sourcePublicationDate', work['publication_date'], text=True, datatype='date')
        if work.get('publication_year'):
            add(node, VOCAB + 'sourcePublicationYear', work['publication_year'], text=True, datatype='gYear')
        raw_strings = set(entry.get('raw_affiliation_strings') or [])
        raw_strings.update(a['raw_affiliation_string'] for a in entry.get('affiliations') or [] if a.get('raw_affiliation_string'))
        for raw in raw_strings:
            add(node, VOCAB + 'rawAffiliation', raw, text=True)
        for institution in entry.get('institutions') or []:
            ror = institution.get('ror')
            inst_id = institution.get('id')
            if ror:
                org = BASE + 'ror/' + ror.rstrip('/').rsplit('/', 1)[-1]
            elif inst_id:
                org = inst_id
            else:
                continue
            add(node, VOCAB + 'affiliation', org)
            add(org, RDF_TYPE, SCHEMA + 'Organization')
            for external in (ror, inst_id):
                if external and external != org:
                    add(org, SCHEMA + 'sameAs', external)
            if institution.get('display_name'):
                add(org, SCHEMA + 'name', institution['display_name'], text=True)
    return triples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-file', type=Path, default=ROOT / 'data/authorship/papers.csv')
    parser.add_argument('--cache-db', type=Path, default=ROOT / 'var/sblp_enrich/cache/.openalex_cache.sqlite')
    parser.add_argument('--out-nt', type=Path, default=ROOT / 'data/authorship/authorships.nt')
    parser.add_argument('--report', type=Path, help='CSV match report (default: output path with .matches.csv suffix)')
    parser.add_argument('--kg-nt', type=Path, default=ROOT / 'data/23/23-aff-geo-canonicalize-22.nt', help='Local KG N-Triples containing person names and sameAs links')
    parser.add_argument('--author-map', type=Path, help='CSV columns: openalex_author,person')
    parser.add_argument('--limit', type=int, default=0, help='First N input rows; 0 processes all rows')
    args = parser.parse_args()
    if args.limit < 0:
        parser.error('--limit must be nonnegative')
    report_path = args.report or args.out_nt.with_suffix('.matches.csv')
    author_report = args.out_nt.with_suffix('.authors.csv')
    inputs = {p.resolve() for p in (args.input_file, args.cache_db, args.author_map, args.kg_nt) if p}
    outputs = [args.out_nt.resolve(), report_path.resolve(), author_report.resolve()]
    if len(set(outputs)) != 3 or inputs.intersection(outputs):
        parser.error('Outputs must be distinct from each other and all inputs')
    if args.out_nt.resolve() in inputs or report_path.resolve() in inputs or args.out_nt.resolve() == report_path.resolve():
        parser.error('Outputs must be distinct from each other and all inputs')
    with args.input_file.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not {'s', 'paperName'} <= set(reader.fieldnames or []):
            parser.error('Input CSV needs s,paperName columns')
        rows = list(itertools.islice(reader, args.limit or None))
    if 'authors' not in (reader.fieldnames or []):
        parser.error('Input CSV needs an authors column with comma-separated KG person IRIs')
    for row in rows:
        row['people'] = list(dict.fromkeys(p.strip() for p in row['authors'].split(',') if p.strip()))
    records = load_people(args.kg_nt, {p for row in rows for p in row['people']})
    author_map = read_author_map(args.author_map)
    matches = cached_matches(args.cache_db, {title_key(row['paperName']) for row in rows})
    triples, reports, author_reports = set(), [], []
    for row in rows:
        candidates = matches.get(title_key(row['paperName']), {})
        status = 'matched' if len(candidates) == 1 else 'ambiguous' if candidates else 'not_found'
        if status == 'matched':
            work = next(iter(candidates.values()))
            mapping, resolutions = resolve_authors(work, row['people'], records, author_map)
            triples.update(work_triples(row['s'], work, mapping, mapped_only=True))
            author_reports.extend((row['s'], aid, person, method) for aid, person, method in resolutions)
            author_reports.extend((row['s'], '', person, 'unmatched_kg_person') for person in row['people'] if person not in mapping.values())
        reports.append(dict(s=row['s'], paperName=row['paperName'], status=status, openalex_works=';'.join(sorted(candidates))))
    args.out_nt.parent.mkdir(parents=True, exist_ok=True)
    args.out_nt.write_text(''.join(line + '\n' for line in sorted(triples)), encoding='utf-8')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['s', 'paperName', 'status', 'openalex_works'])
        writer.writeheader()
        writer.writerows(reports)
    with author_report.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['paper', 'openalex_author', 'person', 'match_method'])
        writer.writerows(author_reports)
    print(f'Author report: {author_report}')
    counts = {status: sum(row['status'] == status for row in reports) for status in ('matched', 'ambiguous', 'not_found')}
    print(f'Processed {len(rows)} papers: {counts}; wrote {len(triples)} triples to {args.out_nt}')
    print(f'Match report: {report_path}')


if __name__ == '__main__':
    main()
