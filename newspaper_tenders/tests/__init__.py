# newspaper_tenders/tests/__init__.py
#
# Test-only compatibility shim: Django 4.2's BaseContext.__copy__ calls
# copy(super()), which breaks on Python 3.13+ ("'super' object has no
# attribute 'dicts'") whenever the instrumented test client copies a template
# context. Applied only when actually broken, and only in test runs (this
# package is never imported by production code).

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
