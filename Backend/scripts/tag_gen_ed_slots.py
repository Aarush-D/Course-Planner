"""One-off migration: add a structured 'gen_ed' domain field to every
GEN ED slot item across degree_plans/*.json whose label already names a
domain, so planner_engine.py's Gen Ed recommender knows which course list
to check. Bare 'GEN ED' slots (no domain in the label) are left untagged
on purpose — they represent flexible/unspecified credit and guessing the
wrong domain could violate the plan's real category-credit distribution.
"""
import glob
import json
import re

LABEL_TO_DOMAIN = {
    "GA": "GA",
    "GH": "GH",
    "GHW": "GHW",
    "GN": "GN",
    "GS": "GS",
    "GQ": "GQ",
    "US": "US",
    "IL": "IL",
    "International/IL": "IL",
    "N": "INTER-D",
}


def domain_for_label(label: str):
    m = re.search(r"GEN ED \(([^)]+)\)", label)
    if not m:
        return None
    inner = m.group(1)
    if "/" in inner and inner not in LABEL_TO_DOMAIN:
        parts = [LABEL_TO_DOMAIN.get(p.strip()) for p in inner.split("/")]
        parts = [p for p in parts if p]
        return parts or None
    return LABEL_TO_DOMAIN.get(inner)


def main():
    total_tagged = 0
    for path in sorted(glob.glob("degree_plans/*.json")):
        with open(path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        changed = False
        for sem in plan.get("semesters", []):
            for item in sem.get("items", []):
                if item.get("type") != "slot":
                    continue
                label = item.get("label", "")
                if "GEN ED" not in label:
                    continue
                domain = domain_for_label(label)
                if domain:
                    item["gen_ed"] = domain
                    changed = True
                    total_tagged += 1
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(plan, f, indent=2)
                f.write("\n")
            print(f"{path}: tagged")
    print(f"Total slots tagged: {total_tagged}")


if __name__ == "__main__":
    main()
