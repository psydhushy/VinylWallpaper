"""Child-process entry point for headless-browser tasks (see spotify._run_worker).

    python -m canvas2we._browser_worker auth   '{"sp_dc": null}'
    python -m canvas2we._browser_worker search '{"query": "Opeth", "kind": "artist"}'

Prints one JSON line: {"value": ...} or {"error": "..."}.
"""

import dataclasses
import json
import sys

from .spotify import (SpotifyError, _browser_login_inprocess, _browser_search_inprocess, _capture_auth_inprocess,
                      _chrome_login_inprocess)


def main() -> int:
    task, kwargs = sys.argv[1], json.loads(sys.argv[2])
    try:
        if task == "auth":
            value = dataclasses.asdict(_capture_auth_inprocess(**kwargs))
        elif task == "search":
            value = _browser_search_inprocess(**kwargs)
        elif task == "login":
            value = _browser_login_inprocess(**kwargs)
        elif task == "chrome_login":
            value = _chrome_login_inprocess(**kwargs)
        else:
            raise SpotifyError(f"unknown task {task!r}")
    except SpotifyError as e:
        print(json.dumps({"error": str(e)}))
        return 1
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        return 1
    print(json.dumps({"value": value}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
