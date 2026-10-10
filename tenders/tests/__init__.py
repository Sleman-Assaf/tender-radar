"""
Test package for the tenders app.

Django 4.2 on Python 3.14
-------------------------
`django.template.context.BaseContext.__copy__` is written as::

    def __copy__(self):
        duplicate = copy(super())
        duplicate.dicts = self.dicts[:]
        return duplicate

`copy(super())` no longer produces a usable instance on Python 3.14, so every
test that renders a template through the test client dies with::

    AttributeError: 'super' object has no attribute 'dicts'
      and no __dict__ for setting new attributes

It only bites under the test runner, because copying the context is part of the
test client's template instrumentation — the application itself renders fine,
which is why the running app is unaffected.

The replacement below does what the original intends: a shallow copy of the
instance with its own `dicts` list. RequestContext.__copy__ calls
super().__copy__() and still works, because `type(self)` is preserved.
"""
from copy import copy as _copy

from django.template.context import BaseContext


def _base_context_copy(self):
    duplicate = object.__new__(type(self))
    duplicate.__dict__.update(self.__dict__)
    duplicate.dicts = self.dicts[:]
    return duplicate


try:                                        # only patch if it is actually broken
    _probe = BaseContext()
    _copy(_probe)
except AttributeError:
    BaseContext.__copy__ = _base_context_copy
