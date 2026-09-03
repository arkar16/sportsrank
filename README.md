# sportsrank

a work in process

https://www.sportsrank.top

## Generate the static site

The generator writes plain HTML files under `website/`. Set the current CFBD
bearer token in your shell, then run the existing Python entry point:

```sh
export CFBD_API_KEY="your-key-here"
python cfb/main.py single_week 2025 1
```

Do not put the real key in `cfb/api.py` or commit it anywhere in the repository.
