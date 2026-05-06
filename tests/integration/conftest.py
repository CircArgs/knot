# Integration-test conftest.
#
# The session-scoped _apply_control_schema autouse fixture in tests/conftest.py
# applies the control schema against postgres before any test.  Integration tests
# DO need a live stack, so we do not shadow or override that fixture here.
