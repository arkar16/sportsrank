# Legacy experiments

The `legacy/` tree holds unsupported historical standalone experiments that
are kept for reference. They are separate from the supported CFB runtime.

## NFL

Run the preserved spread experiment from the repository root:

```sh
python legacy/nfl/nfl_spread.py
```

It prompts for a simulation count (`Enter how many simulations to run:`). For
example, enter `1`. The experiment computes an average spread internally but
currently prints no useful prediction. This behavior is intentionally
preserved; the relocation does not change the script.
