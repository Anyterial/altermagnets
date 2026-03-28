from typing import Any

MOCK_MATERIALS: list[dict[str, Any]] = [
    {
        "id": "AM-0001",
        "name": "MnTe layered phase",
        "formula": "MnTe",
        "elements": ["Mn", "Te"],
        "spacegroup": "P4/nmm (129)",
        "order": "Collinear altermagnetic",
        "neel_temperature": "307 K",
        "notes": "Prototype entry for tetragonal transition-metal chalcogenides.",
    },
    {
        "id": "AM-0002",
        "name": "CrSb polymorph A",
        "formula": "CrSb",
        "elements": ["Cr", "Sb"],
        "spacegroup": "Pnma (62)",
        "order": "Non-collinear altermagnetic",
        "neel_temperature": "412 K",
        "notes": "Mock screening hit with strong anisotropic transport markers.",
    },
    {
        "id": "AM-0003",
        "name": "Fe2As candidate",
        "formula": "Fe2As",
        "elements": ["Fe", "As"],
        "spacegroup": "I4/mmm (139)",
        "order": "Compensated altermagnetic",
        "neel_temperature": "355 K",
        "notes": "Included to exercise formula + element search patterns.",
    },
    {
        "id": "AM-0004",
        "name": "Mn3SiN thin-film motif",
        "formula": "Mn3SiN",
        "elements": ["Mn", "Si", "N"],
        "spacegroup": "Pm-3m (221)",
        "order": "Collinear altermagnetic",
        "neel_temperature": "278 K",
        "notes": "Mock perovskite-like entry for nitride families.",
    },
    {
        "id": "AM-0005",
        "name": "RuO2 distorted cell",
        "formula": "RuO2",
        "elements": ["Ru", "O"],
        "spacegroup": "P42/mnm (136)",
        "order": "Altermagnetic-like antiferromagnetic",
        "neel_temperature": "300 K",
        "notes": "Mock oxide entry kept for broad query coverage.",
    },
]


def execute(global_data, **kwargs):
    global_data["materials_db"] = [dict(entry) for entry in MOCK_MATERIALS]
