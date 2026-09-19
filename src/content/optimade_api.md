---
title: Programmatic access via the OPTIMADE API
base_template: base_default
hosting: static
---

The [OPTIMADE API](https://www.optimade.org/) is a REST API for materials databases, developed by the [OPTIMADE consortium](https://github.com/Materials-Consortia/OPTIMADE) of materials-science data providers so that the same query language and response format work across dozens of independent databases. The Altermagnets Database (*amdb*) serves data through the OPTIMADE API v1.3.0 at:

* [https://altermagnets.anyterial.se/optimade/amdb/](https://altermagnets.anyterial.se/optimade/amdb/).

### Overview

While the standard OPTIMADE API endpoints are available, e.g., structures, the primary entry type in *amdb* is `_anyterial_altermagnet_screening_results`. These entries represent the outcome of a screened candidate material, with the same quantities as shown on the detailed information pages on this website.

Fetching a single entry by id uses the JSON:API "included" feature to include all linked `_httk_records` data records, with information about, e.g., extracted quantities from the originating DFT calculation.

Results represented as `_httk_records` provide provenance information via `_httk_runs` relationships.

### Example queries

The *amdb* OPTIMADE endpoint can be queried with command line tools such as `curl`. For example, a filter query restricted to a couple of fields and a few rows:

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
      "references": { "data": [{"id": "anyt.am.references-1-1", "type": "references"}] },
      "structures": { "data": [{"id": "anyt.am.structures-1-1", "type": "structures"}] }
    }
  },
  "included": [
    { "id": "anyt.am.records-1-1", "type": "_httk_records",
      "attributes": { "_httk_total_energy": -22.40776312 } },
    { "id": "anyt.am.records-1-135", "type": "_httk_records",
      "attributes": { "_anyterial_max_spin_splitting": 1.8724, "_anyterial_avg_spin_splitting": 0.763170313, "..." : "..." } },
    { "id": "anyt.am.references-1-1", "type": "references",
      "attributes": { "doi": "10.1039/d0dt03277h" } }
  ]
}
```

</div>
</div>

### Query *amdb* with OPTIMADE in Python using *httk*

The [high-throughput toolkit (*httk*)](https://httk.org) is a Python toolkit supporting high-throughput computations. Its provides an OPTIMADE client that lets you query *amdb* with Python.
The following instructions are fairly generic, and should be adaptable to any database that supports OPTIMADE API.

To follow the examples below, make sure to have `httk2` installed (preferably in a virtual environment):

<div class="code-sample code-sample--shell">
<p class="code-sample-label">Shell</p>

```bash
pip install httk2
```

</div>

Connect and discover what entry types the database provides:

<div class="code-pair">
<div class="code-pair-part code-pair-part--python">
<p class="code-sample-label">Python</p>

```python
from httk.store.optimade import OptimadeStore

store = OptimadeStore("https://altermagnets.anyterial.se/optimade/amdb")
print("api_version:", store.api_version)
for entry_type in store.entry_types:
    if not entry_type.name.endswith(("~revs", "~alts")): # (skip technical endpoints)
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

The OPTIMADE API filtering language can be used to extract entries you are looking for. A Pandas dataframe-type slicing syntax is supported. Conditions combine with `&` (and) and `|` (or):

<div class="code-pair">
<div class="code-pair-part code-pair-part--python">
<p class="code-sample-label">Python</p>

```python
from httk.store.optimade import OptimadeStore

store = OptimadeStore("https://altermagnets.anyterial.se/optimade/amdb/")
results = store.slicer("_anyterial_altermagnet_screening_results")
selected = materials[(results["_anyterial_max_spin_splitting"] > 0.5) & (results["_anyterial_classification"] == "collinear")]
for entry, i in selected:
    print(i, entry.id, entry._anyterial_formula, entry._anyterial_max_spin_splitting)
```

</div>
<div class="code-pair-part code-pair-part--output">
<p class="code-sample-label">Output</p>

```text
0 anyt.am-1-1 CrSb 1.8724
1 anyt.am-1-2 MnTe 0.9227
2 anyt.am-1-3 RuO2 0.8654
3 anyt.am-1-5 UCr2Si2C 0.7192
```

</div>
</div>

A more sophisticated `searcher` interface is also available, offering sorting and ways to formulate queries across relationships between entry types (for more details, see the [httk-store documentation](https://docs.httk.org/httk-store/)).

For example, fetching one material with its `_httk_records` and its crystal structure as `links` outputs, so they ride along in the same response:

<div class="code-pair">
<div class="code-pair-part code-pair-part--python">
<p class="code-sample-label">Python</p>

```python
from httk.store.optimade import OptimadeStore
from pprint import pprint

store = OptimadeStore("https://altermagnets.anyterial.se/optimade/amdb")
results = store.entry_type("_anyterial_altermagnet_screening_results")
search = store.searcher()
selected = search.variable(results)
search.add(selected.id == "anyt.am-1-1")

row = search.results(
    item=selected,
    formula=selected._anyterial_formula,
    max_ss=selected._anyterial_max_spin_splitting,
    # link outputs ride along in the same response: the client adds
    # include= for these two automatically, at no extra request.
    records=selected.links._httk_records,
    structures=selected.links.structures,
).one()

print("Entry:",row.item.id, row.formula, "with max_spin_splitting =", row.max_ss)
print("Is linked to",len(row.records),"records and",len(row.structures),"structures\n")

for record in row.records:
    print("== record:", record.id)
    pprint(dict(record["attributes"]))
    print()

for structure in row.structures:
    print("== structure:", structure.id, structure.chemical_formula_reduced, structure.elements)
```

</div>
<div class="code-pair-part code-pair-part--output">
<p class="code-sample-label">Output</p>

```text
Entry: anyt.am-1-1 CrSb with max_spin_splitting = 1.8724
Is linked to 2 records and 1 structures

== record: anyt.am.records-1-181
{'_httk_total_energy': Decimal('-22.40776312'),
 'immutable_id': 'anyt.am.records-1-181~1',
 'last_modified': None}

== record: anyt.am.records-1-1
{'_anyterial_avg_spin_splitting': Decimal('0.763170313'),
 '_anyterial_electronic_type': 'metallic',
 '_anyterial_max_spin_splitting': Decimal('1.8724'),
 '_anyterial_spin_splitting_fraction': Decimal('0.34375'),
 '_httk_dft_band_gap': Decimal('0.0'),
 'immutable_id': 'anyt.am.records-1-1~1',
 'last_modified': None}

== structure: anyt.am.structures-1-1 CrSb ('Cr', 'Sb')
```

</div>
</div>

Following provenance one step further, to the workflow run and its declared input structure and outputs, by walking `.links` on the returned record:

<div class="code-pair">
<div class="code-pair-part code-pair-part--python">
<p class="code-sample-label">Python</p>

```python
from httk.store.optimade import OptimadeStore

store = OptimadeStore("https://altermagnets.anyterial.se/optimade/amdb")
results = store.entry_type("_anyterial_altermagnet_screening_results")
search = store.searcher()
selected = search.variable(results)
search.add(selected.id == "anyt.am-1-1")
row = search.results(item=selected).one()

(run,) = row.item.links._httk_runs
print(run.id, run["attributes"]["_httk_workflow_declaration_uri"])
for edge in run.links._httk_has_input:
    print(" input:", edge.type, edge.id)
for edge in run.links._httk_has_output:
    print(" output:", edge.type, edge.id)
```

</div>
<div class="code-pair-part code-pair-part--output">
<p class="code-sample-label">Output</p>

```text
anyt.am.runs-1-1 https://schemas.anyterial.se/defs/v0.1/workflows/altermagnets-scf-httk-v1
 input: structures anyt.am.structures-1-1
 output: _httk_records anyt.am.records-1-1
 output: files anyt.am.files-1-1
 output: files anyt.am.files-1-2
 output: files anyt.am.files-1-3
```

</div>
</div>

(the `_httk_label` meta on each edge, e.g. `input_structure`, `total_energy`, `vasprun`, is dropped here: `.links.<name>` resolves the edge targets, not their labels, and `run["relationships"]["_httk_has_input"]["data"][i]["meta"]` would be needed to recover them -- the example stays clear without it.)

Each `_httk_records` calculation record also links directly to the screened structure it describes: it carries a `_httk_product_of` relationship to the `structures` entry, and that structure carries the reverse `_httk_has_product` relationship back to the record. Both are derived from the record's `product_of` edge written at build time, so the structure and its total energy can be traversed without going through the run.

A depth-1 relationship filter reaches through `structures` directly, without following any relationship at runtime:

<div class="code-pair">
<div class="code-pair-part code-pair-part--python">
<p class="code-sample-label">Python</p>

```python
from httk.store.optimade import OptimadeStore

store = OptimadeStore("https://altermagnets.anyterial.se/optimade/amdb")
results = store.entry_type("_anyterial_altermagnet_screening_results")
search = store.searcher()
selected = search.variable(results)
search.add(selected.links.structures.nelements > 3)
print("matches:", search.count())
```

</div>
<div class="code-pair-part code-pair-part--output">
<p class="code-sample-label">Output</p>

```text
matches: 63
```

</div>
</div>
