from ddtrace.contrib.starlette import patch, unpatch
from tests.contrib.patch import PatchTestCase


class TestStarlettePatch(PatchTestCase.Base):
    __integration_name__ = "starlette"
    __module_name__ = "starlette"
    __patch_func__ = patch
    __unpatch_func__ = unpatch

    def assert_module_patched(self, starlette):
        self.assert_wrapped(starlette.applications.Starlette.__init__)

    def assert_not_module_patched(self, starlette):
        self.assert_not_wrapped(starlette.applications.Starlette.__init__)

    def assert_not_module_double_patched(self, starlette):
        self.assert_not_double_wrapped(starlette.applications.Starlette.__init__)
