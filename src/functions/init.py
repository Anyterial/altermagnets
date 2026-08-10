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
            {"value": "screening_rank", "label": "ID"},
            {"value": "max_ss_desc", "label": "Largest maximum spin splitting"},
            {"value": "avg_ss_desc", "label": "Largest average spin splitting"},
            {"value": "bandgap_desc", "label": "Largest KS gap"},
            {"value": "abundance_desc", "label": "Most abundant constituents"},
        ],
    }
