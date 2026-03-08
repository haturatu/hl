import json
import time
from typing import Callable, TypeVar

from rich.console import Console

console = Console()
T = TypeVar("T")


def watch_loop(
    fetcher: Callable[[], T],
    renderer: Callable[[T], None],
    *,
    as_json: bool,
    interval: float = 1.0,
) -> None:
    try:
        while True:
            data = fetcher()
            if as_json:
                print(json.dumps(data, ensure_ascii=False))
            else:
                console.clear()
                renderer(data)
            time.sleep(interval)
    except KeyboardInterrupt:
        return
