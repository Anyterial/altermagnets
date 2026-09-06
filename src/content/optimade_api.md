---
title: Programmatic access to *amdb* via the OPTIMADE API
base_template: base_default
hosting: static
---

The [OPTIMADE API](https://www.optimade.org/) is a REST API for materials databases, developed by the [OPTIMADE consortium](https://github.com/Materials-Consortia/OPTIMADE) of materials-science data providers so that the same query language and response format work across dozens of independent databases. The Altermagnets Database (*amdb*) serves data through the OPTIMADE API at [https://altermagnets.anyterial.se/optimade/amdb](https://altermagnets.anyterial.se/optimade/amdb/).

### Overview

While the most commonly used OPTIMADE API structure endpoint it available for structural information, the primary entry in *amdb* is our provider-specific `_anyterial_altermagnet_screening_results` entry type. These entries represent a screened candidate material, with the same quantities as are shown on the web pages: chemical formula and elements, space group, collinearity classification, magnetic phase and wave-class assignment, the average and maximum spin splitting and the spin-splitting fraction, electronic type, DFT band gap, minimum crustal elemental abundance, and the linked MAGNDATA symmetry variants.

Fetching a single entry by id also includes the two underlying `_httk_records` data records (the coupled DFT run's declared outputs, and the published screening analysis's values) and any associated `references` enties. The screened crystal structure itself is a standard OPTIMADE `structures` entry, reached through a `structures` relationship. The workflow producing the screening result (structure, files, total energy) are reached through a `_httk_runs` relationship. Provider-specific properties, like standard ones, can be used in `filter` expressions, including through relationships (e.g. `_httk_records.<property>`).

### Example queries

It is possible to query OPTIMADE very directly via command line tools such as `curl`. For example, a filter query restricted to a couple of fields and a few rows:

<div class="code-pair">
<div class="code-pair-part code-pair-part--shell">
<p class="code-sample-label">Shell</p>

```bash
curl -G \
  "https://altermagnets.anyterial.se/optimade/amdb/v1/"\
"_anyterial_altermagnet_screening_results" \
  --data-urlencode \
    "filter=_anyterial_max_spin_splitting > 0.5" \
  --data-urlencode \
    "response_fields=_anyterial_formula,"\
"_anyterial_max_spin_splitting" \
  --data-urlencode "page_limit=3"
```

</div>
<div class="code-pair-part code-pair-part--output">
<p class="code-sample-label">Output</p>

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

</div>
</div>

(the response copied below is trimmed: relationships and the rest of `meta` is omitted.)
We find that 7 of 180 screened materials have a maximum spin splitting above 0.5 eV.

A single-entry fetch, showing the default-included records:

<div class="code-pair">
<div class="code-pair-part code-pair-part--shell">
<p class="code-sample-label">Shell</p>

```bash
curl "https://altermagnets.anyterial.se/optimade/amdb/v1/_anyterial_altermagnet_screening_results/anyt.am-1-1"
```

</div>
<div class="code-pair-part code-pair-part--output">
<p class="code-sample-label">Output</p>

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

</div>
</div>

### Query *amdb* with OPTIMADE in Python using *httk*

The [high-throughput toolkit (*httk*)](https://httk.org) is a Python toolkit supporting high-throughput computations. Its provides an OPTIMADE client that lets you query *amdb* with Python.
To follow the examples below, make sure to have `httk2` installed (preferably in a virtual environment):

<div class="code-sample code-sample--shell">
<p class="code-sample-label">Shell</p>

```bash
pip install httk2
```

</div>

(these examples were run against `httk-serve` 2.1.0, installed via the `httk2`  package)

Connect and discover the entry types:

<div class="code-pair">
<div class="code-pair-part code-pair-part--python">
<p class="code-sample-label">Python</p>

```python
from httk.serve.optimade import OptimadeStore

with OptimadeStore("https://altermagnets.anyterial.se/optimade/amdb") as store:
    print("api_version:", store.api_version)
    for entry_type in store.entry_types:
        if entry_type.name.endswith(("~revs", "~alts")):
            continue
        print(entry_type.name, "->", entry_type.backend.__name__)
```

</div>
<div class="code-pair-part code-pair-part--output">
<p class="code-sample-label">Output</p>

```text
api_version: 1.3.0
_anyterial_altermagnet_screening_results -> OptimadeResource
structures -> OptimadeStructure
references -> OptimadeReference
_httk_runs -> OptimadeResource
_httk_records -> OptimadeResource
files -> OptimadeFile
```

</div>
</div>

A simplified bracket-based search syntax (familiar from e.g., Pandas dataframes) allow easy filtering of altermagnets screening results:

<div class="code-pair">
<div class="code-pair-part code-pair-part--python">
<p class="code-sample-label">Python</p>

```python
from httk.serve.optimade import OptimadeStore

with OptimadeStore("https://altermagnets.anyterial.se/optimade/amdb") as store:
    materials = store.slicer("_anyterial_altermagnet_screening_results")
    hits = materials[materials["_anyterial_max_spin_splitting"] > 0.5]
    print("matches:", len(hits))
    for row in hits[["id", "_anyterial_formula", "_anyterial_max_spin_splitting"]]:
        print(row.id, row._anyterial_formula, row._anyterial_max_spin_splitting)
```

</div>
<div class="code-pair-part code-pair-part--output">
<p class="code-sample-label">Output</p>

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

</div>
</div>

The simplified bracket syntax do not offer easy sorting (rows are provided in store order). A more sophistivated `searcher` interface (sorting, relationship-following, includes) is also available (see the [httk-serve documentation](https://docs.httk.org/httk-serve/)).

For example, fetching one material together with its included records and, following the `structures` relationship, its crystal structure:

<div class="code-pair">
<div class="code-pair-part code-pair-part--python">
<p class="code-sample-label">Python</p>

```python
from httk.core.optimade import optimade_document_root
from httk.serve.optimade import OptimadeStore

with OptimadeStore("https://altermagnets.anyterial.se/optimade/amdb") as store:
    results = store.entry_type("_anyterial_altermagnet_screening_results")
    search = store.searcher()
    material = search.variable(results)
    search.add(material.id == "anyt.am-1-1")
    row = search.results(
        item=material,
        formula=material._anyterial_formula,
        max_ss=material._anyterial_max_spin_splitting,
    ).one()
    resource = row.item
    print(resource.id, row.formula, "max_spin_splitting =", row.max_ss)

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
    structure_row = structure_search.results(
        id=structure_var.id,
        formula=structure_var.chemical_formula_reduced,
        elements=structure_var.elements,
    ).one()
    print("structure", structure_row.id, structure_row.formula, structure_row.elements)
```

</div>
<div class="code-pair-part code-pair-part--output">
<p class="code-sample-label">Output</p>

```text
anyt.am-1-1 CrSb max_spin_splitting = 1.8724
record anyt.am.records-1-1 {'_httk_total_energy': Decimal('-22.40776312'), 'immutable_id': 'anyt.am.records-1-1~1', 'last_modified': None}
record anyt.am.records-1-135 {'_anyterial_avg_spin_splitting': Decimal('0.763170313'), '_anyterial_electronic_type': 'metallic', '_anyterial_max_spin_splitting': Decimal('1.8724'), '_anyterial_spin_splitting_fraction': Decimal('0.34375'), '_httk_dft_band_gap': Decimal('0.0'), 'immutable_id': 'anyt.am.records-1-135~1', 'last_modified': None}
structure anyt.am.structure-1-1 CrSb ('Cr', 'Sb')
```

</div>
</div>

Following provenance one step further, to the workflow run and its other declared outputs:

<div class="code-pair">
<div class="code-pair-part code-pair-part--python">
<p class="code-sample-label">Python</p>

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
    run_row = run_search.results(item=run_var, workflow=run_var._httk_workflow_declaration_uri).one()
    print(run_row.item.id, run_row.workflow)
    for output in run_row.item.unwrap()["relationships"]["_httk_has_output"]["data"]:
        print(" output:", output["meta"]["_httk_label"], "->", output["type"], output["id"])
```

</div>
<div class="code-pair-part code-pair-part--output">
<p class="code-sample-label">Output</p>

```text
anyt.am.runs-1-1 https://schemas.anyterial.se/defs/v0.1/workflows/altermagnets-scf-httk-v1
 output: total_energy -> _httk_records anyt.am.records-1-1
 output: output_structure -> structures anyt.am.structure-1-1
 output: vasprun -> files anyt.am.files-1-1
 output: doscar -> files anyt.am.files-1-2
 output: splitting_figure -> files anyt.am.files-1-3
```

</div>
</div>
