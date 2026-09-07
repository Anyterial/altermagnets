"""Initialize static search controls shared by the site templates."""


def execute(global_data, **kwargs) -> None:
    """Provide the authored search option lists without opening the data store."""
    del kwargs
    global_data["search_options"] = {
        "classifications": [
            {"value": "", "label": "Any collinearity"},
            {"value": "collinear", "label": "Collinear"},
            {"value": "noncollinear-derived", "label": "Based on noncollinear"},
            {"value": "mixed", "label": "Both"},
            {"value": "unclassified", "label": "Not classified yet"},
        ],
        "electronic_types": [
            {"value": "", "label": "Any type"},
            {"value": "metallic", "label": "Metallic"},
            {"value": "semiconducting", "label": "Semiconducting"},
            {"value": "unknown", "label": "KS gap unavailable"},
        ],
        "magnetic_phases": [
            {"value": "", "label": "Any phase"},
            {"value": "AM", "label": "AM"},
            {"value": "FiM", "label": "FiM"},
        ],
        "wave_classes": [
            {"value": "", "label": "Any wave class"},
            {"value": "d", "label": "d"},
            {"value": "g", "label": "g"},
            {"value": "s", "label": "s"},
        ],
        "sorts": [
            {"value": "max_ss_desc", "label": "Largest maximum spin splitting"},
            {"value": "avg_ss_desc", "label": "Largest average spin splitting"},
            {"value": "bandgap_desc", "label": "Largest KS gap"},
            {"value": "abundance_desc", "label": "Most abundant constituents"},
            {"value": "", "label": "ID order"},
        ],
        # Space-group symbols present in the screening data, in International Tables
        # order, feeding the Space group combobox's <datalist> (native type-ahead + a
        # pick list). Values match the stored `Space group` notation exactly, so a pick
        # feeds the CONTAINS filter directly; the field stays free-text, so typing a
        # symbol not listed here still works. Regenerate on a data refresh with:
        #   python3 -c "import csv,spglib,collections; \
        #     p=collections.Counter((r['Space group'] or '').strip() for r in \
        #     csv.DictReader(open('data/tables/high_throughput_screening_results_fixed.csv'),delimiter=';')); \
        #     n={spglib.get_spacegroup_type(h).international_short:spglib.get_spacegroup_type(h).number for h in range(1,531) if spglib.get_spacegroup_type(h)}; \
        #     print([s for s in sorted(filter(None,p),key=lambda s:n.get(s,999))])"
        "space_groups": [
            "P2_1", "Cc", "C2/m", "P2_1/c", "C2/c", "P2_12_12_1", "C222_1", "Pmc2_1",
            "Pca2_1", "Pna2_1", "Cmc2_1", "Imm2", "Pbam", "Pnnm", "Pbcn", "Pbca", "Pnma",
            "Cmcm", "Cmmm", "I4_1cd", "P-42_1m", "P-42_1c", "I-4c2", "I-42d", "P4/mmm",
            "P4/nbm", "P4_2/mbc", "P4_2/mnm", "P4_2/ncm", "I4/mmm", "R3c", "R-3c", "P6_3",
            "P6_3/m", "P6_322", "P6_3cm", "P6_3mc", "P-62m", "P6_3/mmc", "Pa-3", "P4_132",
            "I-43d", "Fd-3m",
        ],
    }
