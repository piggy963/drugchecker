"""ChEMBL evidence retrieval and parsing. No LLM in here: pure, testable code.

Smoke test:  python -m agent.commands.evidence imatinib ABL1
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
import time 
BASE = "https://www.ebi.ac.uk/chembl/api/data"

CACHE_DIR = Path(__file__).parent / "cache"
try:
    CACHE_DIR.mkdir(exist_ok=True)
except OSError:
    pass
# Multipliers to convert a concentration into nM
UNIT_TO_NM = {"pM": 1e-3, "nM": 1.0, "uM": 1e3, "µM": 1e3, "mM": 1e6, "M": 1e9}

ACTIVITY_TYPES = ["IC50", "Ki", "Kd", "EC50"]


@dataclass
class Activity:
    activity_id: int | None
    standard_type: str | None       # IC50, Ki, Kd, EC50 ...
    relation: str | None            # "=", ">", "<", ">=", "<="
    value_raw: float | None         # as reported
    units_raw: str | None
    value_nM: float | None          # None if not convertible (e.g. ug/mL)
    pchembl: float | None
    assay_id: str | None
    assay_type: str | None          # B=binding, F=functional, A=ADMET, T=toxicity, P=physchem
    assay_description: str | None
    target_organism: str | None
    document_id: str | None
    validity_comment: str | None
    potential_duplicate: bool
    variant_mutation: str | None = None


import time  # add to imports

_client = httpx.Client(
    timeout=httpx.Timeout(60.0, connect=30.0),
    transport=httpx.HTTPTransport(retries=3),
    follow_redirects=True,
)

    
def _get(path: str, params: dict[str, Any] | None = None) -> dict:
    """GET with an on-disk cache so demos never depend on API speed."""
    params = params or {}
    key = hashlib.sha1(f"{path}|{sorted(params.items())}".encode()).hexdigest()
    f = CACHE_DIR / f"{key}.json"
    if f.exists():
        return json.loads(f.read_text())
    for attempt in range(4):
        try:
            r = _client.get(f"{BASE}/{path}.json", params=params)
            r.raise_for_status()
            break
        except (httpx.TransportError, httpx.HTTPStatusError):
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
    data = r.json()
    try:
        f.write_text(json.dumps(data))
    except OSError:
        pass  # read-only filesystem (Vercel)
    return data


def resolve_target(name: str, human_only: bool = True) -> list[dict]:
    """Return candidate targets, best first. Caller should flag ambiguity if >1 plausible hit."""
    hits = _get("target/search", {"q": name, "limit": 10}).get("targets", [])
    out = []
    for t in hits:
        if human_only and t.get("organism") != "Homo sapiens":
            continue
        out.append({
            "target_chembl_id": t["target_chembl_id"],
            "pref_name": t.get("pref_name"),
            "organism": t.get("organism"),
            "target_type": t.get("target_type"),  # want "SINGLE PROTEIN"
        })
    return out


def resolve_molecule(name: str) -> list[dict]:
    hits = _get("molecule/search", {"q": name, "limit": 5}).get("molecules", [])
    return [
        {"molecule_chembl_id": m["molecule_chembl_id"],
         "pref_name": m.get("pref_name"),
         "max_phase": m.get("max_phase")}
        for m in hits
    ]


def to_nM(value: float | None, units: str | None) -> float | None:
    if value is None or units is None:
        return None
    mult = UNIT_TO_NM.get(units)
    return value * mult if mult is not None else None


def _parse(raw: dict) -> Activity:
    def f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    value, units = f(raw.get("standard_value")), raw.get("standard_units")
    return Activity(
        activity_id=raw.get("activity_id"),
        standard_type=raw.get("standard_type"),
        relation=raw.get("standard_relation"),
        value_raw=value,
        units_raw=units,
        value_nM=to_nM(value, units),
        pchembl=f(raw.get("pchembl_value")),
        assay_id=raw.get("assay_chembl_id"),
        assay_type=raw.get("assay_type"),
        assay_description=raw.get("assay_description"),
        target_organism=raw.get("target_organism"),
        document_id=raw.get("document_chembl_id"),
        validity_comment=raw.get("data_validity_comment"),
        potential_duplicate=bool(raw.get("potential_duplicate")),
        variant_mutation=raw.get("assay_variant_mutation")
    )



def fetch_activities(target_id: str, molecule_id: str, max_records: int = 2000) -> list[Activity]:
    """All activity records for one compound-target pair, parsed."""
    params: dict[str, Any] = {
        "target_chembl_id": target_id,
        "molecule_chembl_id": molecule_id,
        "limit": 1000,
    }
    acts: list[Activity] = []
    offset = 0
    while len(acts) < max_records:
        data = _get("activity", {**params, "offset": offset})
        batch = data.get("activities", [])
        acts.extend(_parse(a) for a in batch)
        if not data.get("page_meta", {}).get("next"):
            break
        offset += params["limit"]
    return acts


# ---- smoke test -----------------------------------------------------------
if __name__ == "__main__":
    drug, target = (sys.argv + ["imatinib", "ABL1"])[1:3]
    mols, tgts = resolve_molecule(drug), resolve_target(target)
    print("molecules:", mols[:2])
    print("targets:  ", tgts[:3])
    if mols and tgts:
        acts = fetch_activities(tgts[0]["target_chembl_id"], mols[0]["molecule_chembl_id"])
        print(f"{len(acts)} activity records")
        for a in acts[:5]:
            print(asdict(a))