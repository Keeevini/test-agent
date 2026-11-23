"""API route package.

Each module exposes a factory that builds an ``APIRouter`` with dependency
injection, so ``src/app.py`` remains the composition root. This marker file
has no side effects and creates no global app object.
"""
