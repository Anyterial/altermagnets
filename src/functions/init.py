from pathlib import Path


def execute(global_data, **kwargs):
    content_root = Path(__file__).resolve().parents[1] / "content" / "blogposts"
    filterlist = {".md", ".rst", ".html"}

    global_data["blogposts"] = []
    global_data["blogposts_latest"] = []

    posts_with_dates: list[tuple[str, str]] = []
    for path in content_root.iterdir():
        if not path.is_file() or path.suffix not in filterlist:
            continue
        rel = f"blogposts/{path.stem}"
        date = str(global_data["pages"](rel, "date") or "")
        posts_with_dates.append((date, rel))

    posts_with_dates.sort(reverse=True)
    global_data["blogposts"] = [rel for _, rel in posts_with_dates]
    global_data["blogposts_latest"] = global_data["blogposts"][:5]
