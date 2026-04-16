# uni_sparql

Utilities for enriching publication data, converting BibTeX and CSV to RDF/N-Triples, and post-processing RDF in the `sblp_enrich` package.

## Install

Requirements:

- Python 3.10+
- `pip`

From the repository root:

```bash
python3 -m pip install -e .
```

This installs:

- the `sblp_enrich` Python package from `src/`
- Python dependencies declared in [`pyproject.toml`](pyproject.toml)
- console commands such as `sblp-enrich`, `sblp-openalex-enrich`, `sblp-bib-to-nt`, and `sblp-canonicalize-iris`

## Quick Start

After installation, commands are available directly:

```bash
sblp-enrich --help
sblp-openalex-enrich --help
sblp-openalex-offline --help
sblp-bib-to-nt --help
sblp-canonicalize-iris --help
```

Shell scripts remain in the repository under `scripts/sblp_enrich/`:

```bash
scripts/sblp_enrich/bib_validate.sh your.bib
scripts/sblp_enrich/link.sh -i input.nt -o links.tsv
```

## Installed Commands

Main enrichment / workflow commands:

- `sblp-enrich`
- `sblp-openalex-enrich`
- `sblp-openalex-offline`
- `sblp-openalex-affiliation-geo`
- `sblp-pdf-github-prompt`
- `sblp-answers-to-nt`
- `sblp-llm-from-prompts`
- `sblp-migrate-cache-to-db`

BibTeX utilities:

- `sblp-bib-keys-and-fields`
- `sblp-bib-make-keys-unique-dbid`
- `sblp-bib-repair-all`
- `sblp-bib-to-nt`
- `sblp-fix-bib-entry-keys`
- `sblp-fix-bib-fields-latex`

RDF / N-Triples utilities:

- `sblp-canonicalize-iris`
- `sblp-clean-names-nt`
- `sblp-csv-to-rdf`
- `sblp-filter-schema-name-symbol-combo`
- `sblp-fix-problem-names-nt`

## How The KG Is Built From A Spreadsheet

The repository does not build the KG directly from an `.xlsx` file. The spreadsheet is first exported to a semicolon-delimited CSV and then converted to RDF / N-Triples with:

```bash
sblp-csv-to-rdf input.csv output.nt
```

The conversion works row by row:

- each row with an `id` becomes one publication resource
- all original CSV columns are preserved as raw triples under a local `vocab/csv/...` namespace
- selected columns are also mapped to a semantic layer based on `schema.org` and `dcterms`

This semantic layer turns spreadsheet fields such as titles, years, languages, abstracts, keywords, journals, publishers, page ranges, licenses, and access rights into RDF properties. Author and editor cells are parsed into linked `schema:Person` nodes. If available, ORCID is used as the person identifier; otherwise `UNI-ID` is used, and if neither is present a stable hash-based local URI is minted. Journals and publishers are also turned into separate resources so they can be linked instead of repeated as plain text.

The result is an `.nt` file that represents the initial knowledge graph. That graph can then be loaded into a triple store such as Fuseki and enriched further with the other commands in this repository.

## Project Layout

The package uses a shallow `src/` layout:

```text
src/sblp_enrich/
  cli/        entrypoints and workflow commands
  providers/  OpenAlex, DBLP, Fuseki adapters
  bib/        BibTeX repair and conversion utilities
  rdf/        RDF and N-Triples processing utilities
```

Supporting shell scripts live in:

```text
scripts/sblp_enrich/
```

## Runtime Files And Cache

Runtime state defaults to:

```text
var/sblp_enrich/
var/sblp_enrich/cache/
```

This is controlled by [`src/sblp_enrich/paths.py`](src/sblp_enrich/paths.py).

Optional overrides:

- `SBLP_ENRICH_RUNTIME_DIR`
- `SBLP_ENRICH_CACHE_DIR`

The cache helpers also still recognize legacy cache files in `src/sblp_enrich/` if they already exist.

## Notes

- [`scripts/sblp_enrich/bib_validate.sh`](scripts/sblp_enrich/bib_validate.sh) is a thin wrapper around `biber --tool --validate-datamodel`, so `biber` must be installed separately.
- Some workflows depend on external services such as OpenAlex, DBLP, or a Fuseki endpoint.
- Some RDF workflows use external Unix tools such as `sort`, `sed`, `awk`, and `tr`.
