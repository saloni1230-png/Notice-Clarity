"""Query exact runtime versions against OSV. A clean result is not a security certification."""

import argparse
import json
import re
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dependency-audit.json")
    args = parser.parse_args()
    items = re.findall(r"^([\w.-]+)==([^\s]+)", Path("requirements.lock").read_text(), re.M)
    with httpx.Client(timeout=45, follow_redirects=False) as client:
        response = client.post(
            "https://api.osv.dev/v1/querybatch",
            json={
                "queries": [
                    {"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
                    for name, version in items
                ]
            },
        )
        response.raise_for_status()
        results = response.json()["results"]
    if len(results) != len(items):
        raise SystemExit("Incomplete vulnerability response")
    rows = [
        {
            "name": name,
            "version": version,
            "vulnerabilities": [item["id"] for item in result.get("vulns", [])],
        }
        for (name, version), result in zip(items, results)
    ]
    output = {
        "scanner": "OSV exact runtime versions",
        "packages": len(rows),
        "dependencies": rows,
        "known_advisories": sum(len(row["vulnerabilities"]) for row in rows),
    }
    Path(args.output).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({"packages": len(rows), "known_advisories": output["known_advisories"]}))
    if output["known_advisories"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
