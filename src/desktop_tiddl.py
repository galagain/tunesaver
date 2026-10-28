"""Portable entry point: never rely on an installation-time console script path."""
from tiddl.cli.app import app

if __name__ == "__main__":
    app()
