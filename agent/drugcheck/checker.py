"""Turns raw ChEMBL records into a verdict on a drug-target potency claim.

No LLM in here. The verdict is computed by code; the LLM only extracts the claim
beforehand and explains the verdict afterwards.

Smoke test:  python -m agent.commands.checker imatinib ABL1 IC50 100 nM
"""
from __future__ import annotations

import math
import re
import statistics
import sys
from collections import Counter

from .evidence import (
    Activity,
    fetch_activities,
    resolve_molecule,
    resolve_target,
    to_nM,
)

POTENCY_TYPES = {"IC50", "Ki", "Kd", "EC50"}
OK_ASSAY_TYPES = {"B", "F"}     # B = binding, F = functional. A/T/P (ADMET/tox/physchem) excluded.
MATCH_FACTOR = 3.0              # claim "matches" a record if within 3-fold
CONFLICT_SPREAD = 10.0          # max/min above this = records disagree
ACTIVE_NM = 10_000.0            # "active" if <= 10 uM (used when no value is claimed)

AA = "ACDEFGHIKLMNPQRSTVWY"
_POINT_MUTATION = re.compile(rf"\b[{AA}]\d{{2,4}}[{AA}*]\b")          # T790M, L858R, E255K
_MUTANT_WORDS = re.compile(
    r"mutant|mutation|variant|fusion|chimer|truncat|deletion|insertion|\bdel\d|\bexon\b|\bbcr[-/ ]?abl",
    re.I,
)
_VIRAL = re.compile(r"\bv-[a-z]+|\bviral\b|\bvirus\b|\bviridae\b", re.I)  # v-Abl, v-Src ...


def exclusion_reason(a: Activity) -> str | None:
    """Why a record can NOT be used as evidence for the wild-type human target (None = usable)."""
    desc = a.assay_description or ""
    if a.potential_duplicate:
        return "duplicate"
    if a.validity_comment:
        return "validity_flag"
    if a.target_organism != "Homo sapiens":
        return "non_human"
    if _VIRAL.search(desc):
        return "viral"
    if getattr(a, "variant_mutation", None) or _POINT_MUTATION.search(desc) or _MUTANT_WORDS.search(desc):
        return "mutant_or_variant"          # conservative: excludes when in doubt
    if a.assay_type not in OK_ASSAY_TYPES:
        return "wrong_assay_type"
    if a.standard_type not in POTENCY_TYPES:
        return "not_potency_measure"
    if a.value_nM is None:
        return "no_convertible_value"
    if a.relation != "=":
        return "censored"                   # ">10000 nM" is a bound, not a measurement
    return None


def _within(x: float, y: float, factor: float) -> bool:
    return x > 0 and y > 0 and (1 / factor) <= x / y <= factor


def _evidence_row(a: Activity) -> dict:
    return {
        "activity_id": a.activity_id,
        "assay_id": a.assay_id,
        "document_id": a.document_id,
        "type": a.standard_type,
        "value_nM": a.value_nM,
        "assay_type": a.assay_type,
        "assay": (a.assay_description or "")[:120],
    }


def check_claim(
    drug: str,
    target: str,
    activity_type: str | None = None,
    claimed_value: float | None = None,
    claimed_units: str = "nM",
) -> dict:
    drug = drug.strip().lower()
    target = target.strip()
    if " " not in target:
        target = target.upper()
    flags: list[str] = []
    result: dict = {
        "claim": {
            "drug": drug, "target": target, "activity_type": activity_type,
            "claimed_value": claimed_value, "claimed_units": claimed_units,
        },
        "verdict": "NO_DATA", "flags": flags,
    }

    # 1. Identity: resolve names to ChEMBL IDs, human single proteins only
    mols, tgts = resolve_molecule(drug), resolve_target(target)
    singles = [t for t in tgts if t["target_type"] == "SINGLE PROTEIN"]
    if not mols:
        flags.append("molecule_not_found")
        return result
    if not singles:
        flags.append("no_single_protein_human_target_found")
        result["target_candidates"] = tgts[:5]
        return result
    chosen, mol = singles[0], mols[0]
    result["target"] = chosen
    result["molecule"] = mol
    result["target_candidates"] = singles[:5]

    # 2. Retrieve and filter
    acts = fetch_activities(chosen["target_chembl_id"], mol["molecule_chembl_id"])
    result["n_records_total"] = len(acts)
    excluded: Counter = Counter()
    usable: list[Activity] = []
    censored: list[Activity] = []
    for a in acts:
        reason = exclusion_reason(a)
        if reason is None:
            usable.append(a)
        else:
            excluded[reason] += 1
            if reason == "censored":
                censored.append(a)

    # 3. Pick ONE activity type (never pool IC50 with Kd)
    if usable:
        counts = Counter(a.standard_type for a in usable)
        used_type = activity_type if activity_type in counts else counts.most_common(1)[0][0]
        if activity_type and activity_type not in counts:
            flags.append(f"no_{activity_type}_records_using_{used_type}")
        for a in usable:
            if a.standard_type != used_type:
                excluded["other_activity_type"] += 1
        usable = [a for a in usable if a.standard_type == used_type]
        result["activity_type_used"] = used_type
    result["excluded"] = dict(excluded)

    # 4. Nothing usable: maybe only "inactive" bounds
    if not usable:
        inactive = [a for a in censored if a.relation in (">", ">=") and a.value_nM and a.value_nM >= ACTIVE_NM]
        if inactive:
            flags.append("only_censored_inactive_records")
            result["verdict"] = "UNSUPPORTED"
            result["evidence"] = [_evidence_row(a) for a in inactive[:5]]
        return result

    # 5. Aggregate
    vals = [a.value_nM for a in usable]
    med, lo, hi = statistics.median(vals), min(vals), max(vals)
    qs = statistics.quantiles(vals, n=10) if len(vals) >= 5 else None
    spread = (qs[-1] / qs[0]) if qs and qs[0] > 0 else (hi / lo if lo > 0 else float("inf"))
    docs = {a.document_id for a in usable}
    result["stats"] = {
        "n_usable": len(usable), "n_documents": len(docs),
        "median_nM": med, "min_nM": lo, "max_nM": hi, "spread_fold": round(spread, 1),
    }
    if len(usable) < 3:
        flags.append("few_records")
    if len(docs) == 1:
        flags.append("single_source")
    if not any(a.assay_type == "B" for a in usable):
        flags.append("only_functional_assays")
    if spread > CONFLICT_SPREAD:
        flags.append("values_span_>10x")

    # 6. Compare the claim to the evidence
    if claimed_value is None:
        verdict = "SUPPORTED" if med <= ACTIVE_NM else "UNSUPPORTED"
    else:
        claimed_nM = to_nM(claimed_value, claimed_units)
        if claimed_nM is None:
            flags.append("claimed_units_unrecognised")
            verdict = "NO_DATA"
        else:
            frac = sum(_within(claimed_nM, v, MATCH_FACTOR) for v in vals) / len(vals)
            med_match = _within(claimed_nM, med, MATCH_FACTOR)
            result["stats"]["claimed_nM"] = claimed_nM
            result["stats"]["fraction_records_matching_claim"] = round(frac, 2)
            if med_match and frac >= 0.5 and spread <= CONFLICT_SPREAD:
                verdict = "SUPPORTED"
            elif med_match:
                verdict = "PARTIALLY_SUPPORTED"      # claim matches the typical value, but the literature is spread out
            elif frac > 0:
                verdict = "CONFLICTING"              # some records agree, but the typical value doesn't
            else:
                verdict = "UNSUPPORTED"
                for factor in (1e3, 1e6, 1e-3, 1e-6):
                    if _within(claimed_nM * factor, med, MATCH_FACTOR):
                        flags.append(f"possible_unit_error_x{factor:g}")
                        break
    result["verdict"] = verdict

    # 7. Evidence trail: the records closest to the median, with IDs the user can look up
    closest = sorted(usable, key=lambda a: abs(math.log(a.value_nM / med)) if a.value_nM > 0 else 99)
    result["evidence"] = [_evidence_row(a) for a in closest[:5]]
    return result


if __name__ == "__main__":
    import json

    args = sys.argv[1:]
    drug, tgt = args[0], args[1]
    atype = args[2] if len(args) > 2 else None
    val = float(args[3]) if len(args) > 3 else None
    units = args[4] if len(args) > 4 else "nM"
    print(json.dumps(check_claim(drug, tgt, atype, val, units), indent=2))