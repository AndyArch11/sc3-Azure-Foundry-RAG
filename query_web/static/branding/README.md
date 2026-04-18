# Branding Assets

Place custom branding files in this directory (or point `BRANDING_STATIC_PATH` to an
alternative directory at runtime).  The application looks for the following **fixed
filenames** at the root of whichever directory is configured:

| File | Purpose |
|---|---|
| `favicon.ico` | Classic browser tab icon (16 × 16 / 32 × 32 / 48 × 48) |
| `favicon-16.png` | 16 × 16 PNG favicon |
| `favicon-32.png` | 32 × 32 PNG favicon |
| `favicon-180.png` | 180 × 180 Apple touch icon |
| `favicon.svg` | Scalable vector favicon (used by modern browsers when present) |
| `logo.svg` | Primary page header logo (SVG preferred; displayed at 160 × auto) |
| `logo.png` | Fallback page header logo if `logo.svg` is absent |

## Drop-in replacement

Set the environment variable `BRANDING_STATIC_PATH` to the **absolute path** of a
directory containing your replacement files.  Only the files listed above are served;
the directory does not need to contain all of them.

```
BRANDING_STATIC_PATH=/mnt/my-branding
```

Files present in the override directory will be served from `/static/branding/<file>`.
Files absent from the override directory will fall back to the files shipped in this
`query_web/static/branding/` directory (which are intentionally left as generic
placeholders).

## Naming contract

The HTML template references these paths directly.  If you rename any file you must
also update `query_web/templates/index.html` accordingly — the filenames above are the
stable interface between the template and the branding directory.
