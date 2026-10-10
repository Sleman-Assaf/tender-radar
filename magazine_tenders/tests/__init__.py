# magazine_tenders/tests/__init__.py
#
# Test-only compatibility shim, identical to the one in tenders/tests/ and
# newspaper_tenders/tests/: Django 4.2's BaseContext.__copy__ calls
# copy(super()), which breaks on Python 3.13+ ("'super' object has no attribute
# 'dicts'") whenever the instrumented test client copies a template context.
#
# It lives in each test package rather than one shared place because a test
# package is imported before its own tests run, and only then — production code
# never imports it. Without it, running THIS app's tests on their own fails
# while the full suite passes, because another package's shim happened to load
# first; that difference is exactly the kind of thing that wastes an afternoon.
#
# Applied only when the interpreter is actually affected.

import copy as _copy


def _apply_context_copy_shim():
    from django.template.context import BaseContext

    probe = BaseContext()
    try:
        _copy.copy(probe)
        return                                    # not broken — leave Django alone
    except AttributeError:
        pass

    def _fixed_copy(self):
        duplicate = self.__class__.__new__(self.__class__)
        duplicate.__dict__ = self.__dict__.copy()
        duplicate.dicts = self.dicts[:]
        return duplicate

    BaseContext.__copy__ = _fixed_copy


_apply_context_copy_shim()
