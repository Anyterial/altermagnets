---
title: OPTIMADE
base_template: base_default
hosting: static
---

[OPTIMADE](https://www.optimade.org/) is a common REST API standard for materials databases, developed by the [OPTIMADE consortium](https://github.com/Materials-Consortia/OPTIMADE) of materials-science data providers so that the same query language and response format work across dozens of independent databases. This database serves its data through OPTIMADE at:

```
https://altermagnets.anyterial.se/optimade/amdb
```

`/v1/info` on that base URL describes the service and lists its entry types for discovery.

### Programmatic access

The provider-specific `_anyterial_altermagnet_screening_results` entry type carries one entry per screened candidate material, with exactly the science shown on each material's web page: chemical formula and elements, space group, collinearity classification, magnetic phase and wave-class assignment, the average and maximum spin splitting and the spin-splitting fraction, electronic type, DFT band gap, minimum crustal elemental abundance, and the linked MAGNDATA symmetry variants.

Fetching a single entry by id also includes the two underlying `_httk_records` data records (the coupled DFT run's declared outputs, and the published screening analysis's values) and the `references` entry, by default. The screened crystal structure itself is a standard OPTIMADE `structures` entry, reached through a `structures` relationship rather than embedded directly; the producing workflow run and its other outputs (structure, files, total energy) are reached through a `_httk_runs` relationship. Provider-specific properties, like standard ones, can be used in `filter` expressions, including through relationships (e.g. `_httk_records.<property>`).

A filter query, restricted to a couple of fields and a few rows:

```
curl "https://altermagnets.anyterial.se/optimade/amdb/v1/_anyterial_altermagnet_screening_results?filter=_anyterial_max_spin_splitting%20%3E%200.5&response_fields=_anyterial_formula,_anyterial_max_spin_splitting&page_limit=3"
```

```json
{
  "data": [
    { "id": "anyt.am-1-1", "type": "_anyterial_altermagnet_screening_results",
      "attributes": { "_anyterial_formula": "CrSb", "_anyterial_max_spin_splitting": 1.8724 } },
    { "id": "anyt.am-1-2", "type": "_anyterial_altermagnet_screening_results",
      "attributes": { "_anyterial_formula": "MnTe", "_anyterial_max_spin_splitting": 0.9227 } },
    { "id": "anyt.am-1-3", "type": "_anyterial_altermagnet_screening_results",
      "attributes": { "_anyterial_formula": "RuO2", "_anyterial_max_spin_splitting": 0.8654 } }
  ],
  "meta": { "data_returned": 7, "data_available": 180, "more_data_available": true }
}
```

(relationships and the rest of `meta` trimmed above — 7 of 180 screened materials have a maximum spin splitting above 0.5 eV.)

A single-entry fetch, showing the default-included records:

```
curl "https://altermagnets.anyterial.se/optimade/amdb/v1/_anyterial_altermagnet_screening_results/anyt.am-1-1"
```

```json
{
  "data": {
    "id": "anyt.am-1-1",
    "type": "_anyterial_altermagnet_screening_results",
    "attributes": {
      "_anyterial_formula": "CrSb",
      "_anyterial_classification": "collinear",
      "_anyterial_max_spin_splitting": 1.8724,
      "_anyterial_avg_spin_splitting": 0.763170313,
      "..." : "..."
    },
    "relationships": {
      "_httk_records": { "data": [{"id": "anyt.am.records-1-1", "type": "_httk_records"}, {"id": "anyt.am.records-1-135", "type": "_httk_records"}] },
      "_httk_runs": { "data": [{"id": "anyt.am.runs-1-1", "type": "_httk_runs"}] },
      "references": { "data": [{"id": "anyt.am.refs-1-1", "type": "references"}] },
      "structures": { "data": [{"id": "anyt.am.structure-1-1", "type": "structures"}] }
    }
  },
  "included": [
    { "id": "anyt.am.records-1-1", "type": "_httk_records",
      "attributes": { "_httk_total_energy": -22.40776312 } },
    { "id": "anyt.am.records-1-135", "type": "_httk_records",
      "attributes": { "_anyterial_max_spin_splitting": 1.8724, "_anyterial_avg_spin_splitting": 0.763170313, "..." : "..." } },
    { "id": "anyt.am.refs-1-1", "type": "references",
      "attributes": { "doi": "10.1039/d0dt03277h" } }
  ]
}
```

## Query OPTIMADE via httk

```
pip install httk-serve
```

(these examples were run against `httk-serve` 2.1.0, ahead of its first PyPI release)

Connect and discover the entry types:

```python
from httk.serve.optimade import OptimadeStore

with OptimadeStore("https://altermagnets.anyterial.se/optimade/amdb") as store:
    print("api_version:", store.api_version)
    for entry_type in store.entry_types:
        if entry_type.name.endswith(("~revs", "~alts")):
            continue
        print(entry_type.name, "->", entry_type.backend.__name__)
```

```text
api_version: 1.3.0
_anyterial_altermagnet_screening_results -> OptimadeResource
structures -> OptimadeStructure
references -> OptimadeReference
_httk_runs -> OptimadeResource
_httk_records -> OptimadeResource
files -> OptimadeFile
```

A filtered, sorted search over the screening results, narrowed to the fields we want:

```python
from decimal import Decimal

from httk.serve.optimade import OptimadeStore

with OptimadeStore("https://altermagnets.anyterial.se/optimade/amdb") as store:
    results = store.entry_type("_anyterial_altermagnet_screening_results")
    search = store.searcher(response_fields=["_anyterial_formula", "_anyterial_max_spin_splitting"])
    material = search.variable(results)
    search.add(material._anyterial_max_spin_splitting > Decimal("0.5"))
    search.add_sort(material._anyterial_max_spin_splitting, descending=True)

    print("matches:", search.count())
    for row in search.results(item=material):
        attrs = row.item.unwrap()["attributes"]
        print(row.item.id, attrs["_anyterial_formula"], attrs["_anyterial_max_spin_splitting"])
```

```text
matches: 7
anyt.am-1-1 CrSb 1.8724
anyt.am-1-2 MnTe 0.9227
anyt.am-1-3 RuO2 0.8654
anyt.am-1-4 CrSe 0.8002
anyt.am-1-5 UCr2Si2C 0.7192
anyt.am-1-6 Ca(Al2Fe)4 0.6284
anyt.am-1-7 Cu2O3Cl 0.553
```

Fetching one material together with its included records and, following the `structures` relationship, its crystal structure:

```python
from httk.core.optimade import optimade_document_root
from httk.serve.optimade import OptimadeStore

with OptimadeStore("https://altermagnets.anyterial.se/optimade/amdb") as store:
    results = store.entry_type("_anyterial_altermagnet_screening_results")
    search = store.searcher()
    material = search.variable(results)
    search.add(material.id == "anyt.am-1-1")
    resource = search.results(item=material).one().item

    attrs = resource.unwrap()["attributes"]
    print(resource.id, attrs["_anyterial_formula"], "max_spin_splitting =", attrs["_anyterial_max_spin_splitting"])

    # Single-entry responses default-include the material's _httk_records and references.
    root = optimade_document_root(resource.document)
    for included in root["included"]:
        if included["type"] == "_httk_records":
            print("record", included["id"], dict(included["attributes"]))

    # The crystal structure is a separate structures entry, reached through a relationship.
    structure_id = resource.unwrap()["relationships"]["structures"]["data"][0]["id"]
    structures = store.entry_type("structures")
    structure_search = store.searcher()
    structure_var = structure_search.variable(structures)
    structure_search.add(structure_var.id == structure_id)
    structure = structure_search.results(item=structure_var).one().item.unwrap()
    print("structure", structure["id"], structure["attributes"]["chemical_formula_reduced"], structure["attributes"]["elements"])
```

```text
anyt.am-1-1 CrSb max_spin_splitting = 1.8724
record anyt.am.records-1-1 {'_httk_total_energy': Decimal('-22.40776312'), 'immutable_id': 'anyt.am.records-1-1~1', 'last_modified': None}
record anyt.am.records-1-135 {'_anyterial_avg_spin_splitting': Decimal('0.763170313'), '_anyterial_electronic_type': 'metallic', '_anyterial_max_spin_splitting': Decimal('1.8724'), '_anyterial_spin_splitting_fraction': Decimal('0.34375'), '_httk_dft_band_gap': Decimal('0.0'), 'immutable_id': 'anyt.am.records-1-135~1', 'last_modified': None}
structure anyt.am.structure-1-1 CrSb ('Cr', 'Sb')
```

Following provenance one step further, to the workflow run and its other declared outputs:

```python
from httk.serve.optimade import OptimadeStore

with OptimadeStore("https://altermagnets.anyterial.se/optimade/amdb") as store:
    results = store.entry_type("_anyterial_altermagnet_screening_results")
    search = store.searcher()
    material = search.variable(results)
    search.add(material.id == "anyt.am-1-1")
    resource = search.results(item=material).one().item

    run_id = resource.unwrap()["relationships"]["_httk_runs"]["data"][0]["id"]

    runs = store.entry_type("_httk_runs")
    run_search = store.searcher()
    run_var = run_search.variable(runs)
    run_search.add(run_var.id == run_id)
    run = run_search.results(item=run_var).one().item.unwrap()
    print(run["id"], run["attributes"]["_httk_workflow_declaration_uri"])
    for output in run["relationships"]["_httk_has_output"]["data"]:
        print(" output:", output["meta"]["_httk_label"], "->", output["type"], output["id"])
```

```text
anyt.am.runs-1-1 https://schemas.anyterial.se/defs/v0.1/workflows/altermagnets-scf-httk-v1
 output: total_energy -> _httk_records anyt.am.records-1-1
 output: output_structure -> structures anyt.am.structure-1-1
 output: vasprun -> files anyt.am.files-1-1
 output: doscar -> files anyt.am.files-1-2
 output: splitting_figure -> files anyt.am.files-1-3
```
