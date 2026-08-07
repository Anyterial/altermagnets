---
title: OPTIMADE search
template: optimade_search
base_template: base_default
hosting: dynamic
---
{{ widget("optimade_table", base_url="/optimade", entry_type="structures", columns=[{"key": "id", "label": "Material id"}, {"key": "chemical_formula_reduced", "label": "Formula"}, {"key": "_anyterial_magnetic_phase", "label": "Magnetic phase"}, {"key": "_anyterial_max_spin_splitting", "label": "Max spin splitting (eV)", "align": "end"}], page_size=50, filter_query="filter", detail_route="material", detail_column="chemical_formula_reduced", detail_query="id") }}
