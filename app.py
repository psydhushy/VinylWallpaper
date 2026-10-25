"""Entry point for hosts that run a Streamlit script from the repository root,
such as Streamlit Community Cloud (set its main file to ``app.py``).

Running the real app by path, on every rerun, keeps ``import canvas2we``
working without installing the package. Hosted copies are shared with other
people, so this turns on public mode unless CANVAS2WE_PUBLIC says otherwise;
on your own computer use ``canvas2we ui``.
"""

import os
import runpy
from pathlib import Path

os.environ.setdefault("CANVAS2WE_PUBLIC", "1")

runpy.run_path(str(Path(__file__).with_name("canvas2we") / "streamlit_app.py"), run_name="__main__")
