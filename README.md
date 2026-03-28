Example Website With httk.web
-----------------------------

This repository is a simple starter for building a semi-static website with a
blog using ``httk.web`` from ``httk-web``.

Quick start
-----------

```bash
python -m pip install -e .
make serve
```

Then open:

- http://127.0.0.1:8080/

Edit content
------------

Main pages are in ``src/content`` and blog posts are in
``src/content/blogposts``.

Small example: update the homepage title in ``src/content/index.md``:

```yaml
---
title: My Website
name: Main
template: default
base_template: base_default
---
```

Then regenerate static output:

```bash
make generate
```

This writes files to ``public/``.

GitHub Pages
------------

If this repository is hosted on GitHub, enable GitHub Pages with GitHub Actions.
The workflow in ``.github/workflows/httkweb.yaml`` builds and publishes the
site on pushes to ``main``.

More information
----------------

For full documentation, see:

- https://docs2.httk.org/httk-web/
