import hashlib
from typing import Dict, Iterable, Optional
from rdflib import Graph, URIRef, Literal, Namespace
from rdflib.namespace import RDF, XSD

SCHEMA = Namespace("https://schema.org/")
ORG_BASE = "https://dice-research.org/id/org/"

def mint_org_uri(
    *,
    inst_id: str,
    ror: str,
    name: str,
    cc: str,
) -> str:
    inst_id = (inst_id or "").strip()
    ror = (ror or "").strip()
    name = (name or "").strip()
    cc = (cc or "").strip()

    if ror:
        return ror

    if inst_id:
        return inst_id

    key = f"org::{name.lower()}|{cc.lower()}".strip()

    h = hashlib.sha1(key.encode("utf-8")).hexdigest()
    uri = ORG_BASE + h

    return uri

def add_affiliation(g: Graph, person_uri: str, inst: Dict[str, str]) -> None:
    s = URIRef(person_uri)

    inst_id = (inst.get("id") or "").strip()
    ror = (inst.get("ror") or "").strip()
    name = (inst.get("display_name") or "").strip()
    cc = (inst.get("country_code") or "").strip()
    
    org_uri_str = mint_org_uri(
        inst_id=inst_id,
        ror=ror,
        name=name,
        cc=cc,
    )
    o = URIRef(org_uri_str)

    g.add((s, SCHEMA.affiliation, o))
    g.add((o, RDF.type, SCHEMA.Organization))
    if name:
        g.add((o, SCHEMA.name, Literal(name)))
    if cc:
        g.add((o, SCHEMA.addressCountry, Literal(cc)))
    if inst_id and org_uri_str != inst_id:
        g.add((o, SCHEMA.sameAs, URIRef(inst_id)))
    if ror and org_uri_str != ror:
        g.add((o, SCHEMA.sameAs, URIRef(ror)))

def add_sameas(g: Graph, person_uri: str, author_id: Optional[str]) -> None:
    if not author_id:
        return
    s = URIRef(person_uri)
    g.add((s, SCHEMA.sameAs, URIRef(author_id)))

def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def add_affiliation_geo(
    g: Graph,
    affiliation_uri: str,
    *,
    city: Optional[str],
    country: Optional[str],
    country_code: Optional[str],
    latitude,
    longitude,
    continent: Optional[str],
) -> bool:
    s = URIRef(affiliation_uri)
    wrote_any = False

    city = (city or "").strip()
    country = (country or "").strip()
    country_code = (country_code or "").strip().upper()
    continent = (continent or "").strip()

    if city:
        g.add((s, SCHEMA.addressLocality, Literal(city)))
        wrote_any = True

    if country:
        g.add((s, SCHEMA.addressCountry, Literal(country)))
        wrote_any = True
    elif country_code:
        g.add((s, SCHEMA.addressCountry, Literal(country_code)))
        wrote_any = True

    lat = _to_float(latitude)
    if lat is not None:
        g.add((s, SCHEMA.latitude, Literal(lat, datatype=XSD.double)))
        wrote_any = True

    lng = _to_float(longitude)
    if lng is not None:
        g.add((s, SCHEMA.longitude, Literal(lng, datatype=XSD.double)))
        wrote_any = True

    if continent:
        g.add((s, SCHEMA.continent, Literal(continent)))
        wrote_any = True

    return wrote_any

def add_publication_about_topic(g: Graph, pub_uri: str, topic_uri: str) -> None:
    s = URIRef(pub_uri)
    o = URIRef(topic_uri)
    g.add((s, SCHEMA.about, o))

def add_identifier_doi(g: Graph, pub_uri: str, doi_value: Optional[str]) -> None:
    if not doi_value:
        return
    s = URIRef(pub_uri)

    v = (doi_value or "").strip()
    if not v:
        return

    vl = v.lower()
    if vl.startswith("https://doi.org/"):
        v = v.split("doi.org/", 1)[1].lstrip()
    elif vl.startswith("http://doi.org/"):
        v = v.split("doi.org/", 1)[1].lstrip()
    elif vl.startswith("doi:"):
        v = v.split(":", 1)[1].strip()

    v = v.strip()
    if not v:
        return

    g.add((s, SCHEMA.identifier, Literal(f"DOI:{v}")))
    print(f"{pub_uri} got DOI:{v}")
