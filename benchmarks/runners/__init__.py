from .external import EXTERNAL_RUNNERS
from .grep_runner import run_grep
from .memographix_runner import run_memographix
from .naive_runner import run_naive

RUNNERS = {
    "memographix": run_memographix,
    "naive": run_naive,
    "grep": run_grep,
    **EXTERNAL_RUNNERS,
}
