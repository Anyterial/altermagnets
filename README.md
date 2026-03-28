Anyterial altermagnets (httk-web app)
------------------------------------

This repository is a dynamic httk-web app prototype for browsing and searching
mock altermagnetic material records.

Current functionality
---------------------

- Welcome page at `/index`
- Persistent left-side search form on all main pages
- Search result page at `/search`
- Material detail page at `/material?id=<ID>`
- Dark / twilight / light theme selector (stored in localStorage)
- No cookies are used

Quick start
-----------

```bash
python -m pip install -e .
make serve
```

Then open:

- http://127.0.0.1:8080/

Try queries such as:

- `Mn`
- `Fe As`
- `P4/nmm`

Static publish mode is available for layout preview (`make generate`), but core
search/detail behavior relies on dynamic httk-web functions in `src/functions/`.
