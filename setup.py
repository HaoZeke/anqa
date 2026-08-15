"""Optional Limited API extension build (groket._listwalk).

Remote builder only (``just ext``). Product install is
``uv tool install --editable .``. Set ``GROKET_BUILD_EXT=1`` and run
this file via setuptools to compile ``native/listwalk.c``.
"""

from __future__ import annotations

import os

from setuptools import Extension, setup

_ext: list[Extension] = []
if os.environ.get("GROKET_BUILD_EXT") == "1":
    _ext = [
        Extension(
            "groket._listwalk",
            sources=["native/listwalk.c"],
            py_limited_api=True,
            define_macros=[("Py_LIMITED_API", "0x030D0000")],
            optional=True,
        )
    ]

setup(ext_modules=_ext)
