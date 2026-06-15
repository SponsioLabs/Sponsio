"""Enable ``python -m sponsio.cli``.

The CLI is also exposed as the ``sponsio`` console script (see
``[project.scripts]`` in pyproject), but some callers — and the test
suite — invoke it via ``python -m sponsio.cli``. A package needs an
explicit ``__main__`` for that form to work.
"""

from __future__ import annotations

from sponsio.cli import main

if __name__ == "__main__":
    main()
