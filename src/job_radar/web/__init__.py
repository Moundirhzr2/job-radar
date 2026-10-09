"""The radar in the browser: a map, a radius, the offers and the employers around, the search,
the fit with my profile, and the application tracker.

It runs on the student's own computer (127.0.0.1 by default): the tracker and the profile are
personal data and never leave it. The page and the API reuse exactly what the command line
does; nothing here talks to a site the command line would not talk to.
"""

from .app import Services, create_app

__all__ = ["Services", "create_app"]
